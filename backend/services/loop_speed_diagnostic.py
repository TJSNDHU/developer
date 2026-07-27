"""
services/loop_speed_diagnostic.py — Iter 309 · Speed Diagnostic Part 1

Read-only aggregation for the founder's speed-diagnostic prompt.

Pulls the last N completed loop sessions and reconstructs:
  • Per-phase wall-clock duration (from loop_events timestamps if
    present; falls back to session.created_at/updated_at bounds for
    total loop duration).
  • Inside EXECUTE: per-file generation call duration + queue-wait
    signal for MAX_PARALLEL_GENS (=3) contention.
  • Council/Parliament LLM call count per phase (from ora_chat_usage
    filtered by phase_tag).
  • Self-heal trigger rate + resolve-on-attempt-1 rate + avg per-round
    wall time (from context.self_heals_performed + loop_events state
    transitions).
  • Outlier calls (p95 per phase).

DISCIPLINE:
  • Zero writes. Zero side effects.
  • Excludes test/dogfood sessions via user_id pattern match (matches
    the FAILED OWNERS classification used in loop-metrics).
  • Honestly reports `sample_too_small` when N < 15.
  • No architecture speculation — only what the data supports.

Callers:
  • CLI:    backend/scripts/loop_speed_report.py (permanent artifact)
  • HTTP:   GET /api/aurem-dev/admin/speed-diagnostic (admin-gated)
"""
from __future__ import annotations
import re
import statistics as stats
from datetime import datetime, timezone, timedelta
from typing import Any


# Exclude sessions from these user_id prefixes — matches loop-metrics
# FAILED OWNERS classification for "founder / test / e2e".
_EXCLUDE_USER_PREFIXES = ("test_", "e2e_", "founder_", "dogfood_")


def _is_real_user(user_id: str | None) -> bool:
    if not user_id:
        return False
    for pref in _EXCLUDE_USER_PREFIXES:
        if user_id.startswith(pref):
            return False
    return True


def _pct(numerator: int, denominator: int) -> float:
    if not denominator:
        return 0.0
    return round(100.0 * numerator / denominator, 1)


def _stats_line(values: list[float]) -> dict:
    if not values:
        return {"n": 0, "avg_s": None, "median_s": None, "max_s": None, "p95_s": None}
    return {
        "n":        len(values),
        "avg_s":    round(sum(values) / len(values), 2),
        "median_s": round(stats.median(values), 2),
        "max_s":    round(max(values), 2),
        "p95_s":    round(sorted(values)[int(0.95 * (len(values) - 1))], 2),
    }


def _calls_stats_line(values: list[float]) -> dict:
    """Iter 315 · Fix 2 — COUNT-labelled stats.

    The previous `_stats_line` above uses `avg_s`/`median_s`/`max_s`/
    `p95_s` field names — semantically SECONDS. `llm_calls_by_phase`
    aggregates row COUNTS from `ora_chat_usage` (how many loop.<phase>
    rows per loop), not durations. Reusing `_stats_line` there
    produced `avg_s: 0` for what was actually "avg number of calls
    per loop = 0" — the field lied about units. This helper returns
    the identical numeric shape with correctly-labelled unit suffix.
    """
    if not values:
        return {
            "n": 0, "avg_calls": None, "median_calls": None,
            "max_calls": None, "p95_calls": None,
        }
    return {
        "n":             len(values),
        "avg_calls":     round(sum(values) / len(values), 2),
        "median_calls":  round(stats.median(values), 2),
        "max_calls":     round(max(values), 2),
        "p95_calls":     round(sorted(values)[int(0.95 * (len(values) - 1))], 2),
    }


def _iso_dt(v) -> Any:
    """Iter 315 · Option-(a) — normalize created_at/updated_at values
    for the per-loop metadata list. Mongo may store these as
    datetime OR as ISO string depending on write path — the JSON
    consumer wants a stable string either way. Falsy → None."""
    if not v:
        return None
    try:
        return v.isoformat() if hasattr(v, "isoformat") else str(v)
    except Exception:
        return None


async def _phase_durations_from_events(
    db, loop_id: str,
) -> dict[str, float]:
    """Reconstruct per-phase wall-clock by finding the earliest event
    per phase and the earliest event of the NEXT phase. If no events
    exist for this loop_id, returns empty dict — caller falls back to
    session-level bounds."""
    cursor = db.loop_events.find(
        {"loop_id": loop_id},
        {"_id": 0, "phase": 1, "state": 1, "ts": 1, "timestamp": 1},
    ).sort([("ts", 1)])
    events = []
    async for ev in cursor:
        ts = ev.get("ts") or ev.get("timestamp")
        # Normalise ts to datetime.
        if isinstance(ts, (int, float)):
            ts = datetime.fromtimestamp(ts, tz=timezone.utc)
        elif isinstance(ts, str):
            try:
                ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except Exception:
                continue
        if not isinstance(ts, datetime):
            continue
        events.append({"phase": (ev.get("phase") or "").lower(), "ts": ts})
    if not events:
        return {}
    # Merge state transitions into 5 canonical phases.
    def _canonical(p: str) -> str:
        if p in ("planning", "plan"):        return "plan"
        if p in ("executing", "execute"):    return "execute"
        if p in ("verifying", "verify",
                 "self_healing"):            return "verify"
        if p in ("scanning", "scan",
                 "security"):                return "scan"
        if p in ("shipping", "ship"):        return "ship"
        return ""
    # First-seen timestamp per canonical phase.
    first_ts: dict[str, datetime] = {}
    for ev in events:
        c = _canonical(ev["phase"])
        if c and c not in first_ts:
            first_ts[c] = ev["ts"]
    # Phase duration = next_phase_first_ts - this_phase_first_ts.
    # Ship's end = last event ts of the loop.
    order = ["plan", "execute", "verify", "scan", "ship"]
    durations: dict[str, float] = {}
    for i, ph in enumerate(order):
        if ph not in first_ts:
            continue
        # Find the earliest ts of any later phase that also exists.
        end_ts = None
        for later in order[i + 1:]:
            if later in first_ts:
                end_ts = first_ts[later]
                break
        if end_ts is None:
            end_ts = events[-1]["ts"]
        durations[ph] = (end_ts - first_ts[ph]).total_seconds()
    return durations


async def _llm_calls_by_phase(db, loop_id: str) -> dict[str, int]:
    out = {"plan": 0, "execute": 0, "verify": 0, "scan": 0, "ship": 0}
    cursor = db.ora_chat_usage.find(
        {"session_id": loop_id, "route": {"$regex": r"^loop\."}},
        {"_id": 0, "route": 1, "phase_tag": 1},
    )
    async for row in cursor:
        # Phase resolves from `phase_tag` (Item 4 canonical) or route suffix.
        tag = (row.get("phase_tag") or "").lower()
        if tag not in out:
            m = re.match(r"^loop\.(\w+)", str(row.get("route") or ""))
            if m:
                tag = m.group(1)
        if tag in out:
            out[tag] += 1
    return out


async def _execute_per_file_calls(db, loop_id: str) -> list[dict]:
    """From loop_events, extract per-file generation windows.
    Each window is (file_path, start_ts, end_ts) where start is a
    `sub_step: "generating"` event and end is the matching "Wrote {file}"
    event or a timeout/error terminal for that file."""
    cursor = db.loop_events.find(
        {"loop_id": loop_id, "phase": {"$in": ["execute", "executing"]}},
        {"_id": 0, "data": 1, "message": 1, "state": 1, "ts": 1, "timestamp": 1},
    ).sort([("ts", 1)])
    starts: dict[str, datetime] = {}
    completed: list[dict] = []
    async for ev in cursor:
        ts = ev.get("ts") or ev.get("timestamp")
        if isinstance(ts, (int, float)):
            ts = datetime.fromtimestamp(ts, tz=timezone.utc)
        elif isinstance(ts, str):
            try:
                ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except Exception:
                continue
        if not isinstance(ts, datetime):
            continue
        d = ev.get("data") or {}
        file_path = d.get("file")
        sub = d.get("sub_step") or ""
        if not file_path:
            continue
        if sub == "generating":
            starts[file_path] = ts
        elif sub in ("timeout", "error") or (
                ev.get("message") and str(ev["message"]).startswith("Wrote ")):
            if file_path in starts:
                completed.append({
                    "file":   file_path,
                    "start":  starts[file_path],
                    "end":    ts,
                    "sec":    (ts - starts[file_path]).total_seconds(),
                    "outcome": sub or "success",
                })
                starts.pop(file_path, None)
    return completed


def _queue_wait_signal(per_file: list[dict], max_parallel: int = 3) -> dict:
    """If files > max_parallel: sort by start; for file N, queue-wait =
    max(0, file_N.start - file_(N-max_parallel).end). Report total
    queue-wait vs total execute time."""
    if len(per_file) <= max_parallel:
        return {"applicable": False,
                "reason": f"only {len(per_file)} files, ≤ MAX_PARALLEL_GENS={max_parallel}"}
    sorted_files = sorted(per_file, key=lambda f: f["start"])
    total_wait = 0.0
    for i in range(max_parallel, len(sorted_files)):
        prev_end = sorted_files[i - max_parallel]["end"]
        this_start = sorted_files[i]["start"]
        wait = (this_start - prev_end).total_seconds()
        total_wait += max(0, wait)
    total_generation = sum(f["sec"] for f in sorted_files)
    return {
        "applicable":        True,
        "files":             len(sorted_files),
        "max_parallel":      max_parallel,
        "total_queue_wait_s": round(total_wait, 2),
        "total_gen_time_s":  round(total_generation, 2),
        "queue_wait_pct_of_gen": _pct(int(total_wait), int(total_generation)),
    }


def _self_heal_breakdown(session_ctx: dict) -> dict:
    heals = session_ctx.get("self_heals_performed") or []
    if not heals:
        return {"triggered": False, "attempts": 0}
    attempts = max((h.get("attempt") or 0) for h in heals)
    resolved_at = None
    for h in heals:
        if h.get("ok") is True:
            resolved_at = h.get("attempt")
            break
    return {
        "triggered":            True,
        "attempts":             attempts,
        "resolved_at_attempt":  resolved_at,
        "per_file_heals":       sum(1 for h in heals if h.get("file")),
    }


async def compute_speed_report(
    db, window_days: int = 30, sample_target: int = 20,
) -> dict[str, Any]:
    """Main entry point. Returns a structured report."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    query: dict = {"state": "completed", "updated_at": {"$gte": cutoff}}
    # Pull a bit more than sample_target so we can filter test users
    # and still reach the target.
    cursor = db.loop_sessions.find(query).sort(
        [("updated_at", -1)]).limit(sample_target * 3)
    real_sessions: list[dict] = []
    async for doc in cursor:
        if _is_real_user(doc.get("user_id")):
            real_sessions.append(doc)
        if len(real_sessions) >= sample_target:
            break

    n = len(real_sessions)
    if n == 0:
        return {
            "ok":              True,
            "window_days":     window_days,
            "sample_size":     0,
            "sample_too_small": True,
            "note": "No real completed loops in window. Try a longer window "
                    "or run against prod Mongo.",
        }

    per_phase: dict[str, list[float]] = {
        "plan": [], "execute": [], "verify": [], "scan": [], "ship": [],
    }
    total_durations: list[float] = []
    execute_share_pct: list[float] = []
    per_file_all: list[dict] = []
    queue_wait_summaries: list[dict] = []
    llm_calls_by_phase_agg: dict[str, list[int]] = {
        k: [] for k in per_phase
    }
    self_heal_rows: list[dict] = []
    # ── Iter 315 · Option-(a) — per-loop metadata for RCA verification.
    # Diagnostic aggregates alone can hide whether "n:10, avg:0" is
    # (1) instrumentation predates these loops, or (2) a genuine
    # write bug. Emitting per-loop created_at + state means the
    # ambiguity is resolvable from the JSON without a second query.
    sample_loop_ids: list[dict] = []

    for doc in real_sessions:
        lid = doc.get("loop_id")
        # Collect per-loop metadata first — safe read, no I/O.
        sample_loop_ids.append({
            "loop_id":    lid,
            "created_at": _iso_dt(doc.get("created_at")),
            "updated_at": _iso_dt(doc.get("updated_at")),
            "state":      doc.get("state"),
            "user_id":    doc.get("user_id"),
        })
        durations = await _phase_durations_from_events(db, lid)
        # Fallback: total session duration only.
        session_total = None
        if doc.get("created_at") and doc.get("updated_at"):
            try:
                session_total = (
                    doc["updated_at"] - doc["created_at"]
                ).total_seconds()
            except Exception:
                pass
        # Aggregate durations.
        for ph, dur in durations.items():
            if dur >= 0:
                per_phase[ph].append(dur)
        if durations:
            phase_total = sum(durations.values())
            if phase_total > 0:
                total_durations.append(phase_total)
                if "execute" in durations:
                    execute_share_pct.append(
                        100.0 * durations["execute"] / phase_total)
        elif session_total is not None:
            total_durations.append(session_total)
        # Execute per-file
        per_file = await _execute_per_file_calls(db, lid)
        if per_file:
            per_file_all.extend(per_file)
            qw = _queue_wait_signal(per_file)
            if qw.get("applicable"):
                queue_wait_summaries.append(qw)
        # LLM calls by phase
        calls = await _llm_calls_by_phase(db, lid)
        for ph, ct in calls.items():
            llm_calls_by_phase_agg[ph].append(ct)
        # Self-heal
        sh = _self_heal_breakdown(doc.get("context") or {})
        self_heal_rows.append(sh)

    # Aggregate stats
    phase_stats = {
        ph: _stats_line(vals) for ph, vals in per_phase.items()
    }
    total_stats = _stats_line(total_durations)
    execute_share = (
        _stats_line(execute_share_pct) if execute_share_pct
        else {"n": 0, "avg_pct": None}
    )
    if execute_share_pct:
        execute_share["avg_pct"] = round(
            sum(execute_share_pct) / len(execute_share_pct), 1,
        )

    per_file_stats = _stats_line([f["sec"] for f in per_file_all])
    per_file_outcomes: dict[str, int] = {}
    for f in per_file_all:
        per_file_outcomes[f["outcome"]] = per_file_outcomes.get(
            f["outcome"], 0) + 1

    triggered_heals = [r for r in self_heal_rows if r.get("triggered")]
    resolved_at_1 = [r for r in triggered_heals
                     if r.get("resolved_at_attempt") == 1]

    # Iter 315 · Fix 2 — use _calls_stats_line so the field labels
    # match the actual unit (COUNTS, not seconds). See helper docstring.
    llm_call_stats = {
        ph: _calls_stats_line([float(x) for x in vals])
        for ph, vals in llm_calls_by_phase_agg.items()
    }

    return {
        "ok":                       True,
        "generated_at":             datetime.now(timezone.utc).isoformat(),
        "window_days":              window_days,
        "sample_size":              n,
        "sample_target":            sample_target,
        "sample_too_small":         n < 15,
        "phase_wall_clock":         phase_stats,
        "total_loop_duration":      total_stats,
        "execute_share_of_total":   execute_share,
        "per_file_generation":      per_file_stats,
        "per_file_outcomes":        per_file_outcomes,
        "max_parallel_gens":        3,
        "queue_wait_signal":        {
            "loops_with_queue_wait": len(queue_wait_summaries),
            "samples":               queue_wait_summaries[:5],
        },
        "llm_calls_by_phase":       llm_call_stats,
        # Iter 315 · Option-(a) — per-loop metadata so "n:10 avg:0"
        # ambiguity can be resolved from a single JSON pull. If every
        # loop's created_at predates the token-ledger cutoff
        # (2026-07-26 05:16 UTC) the zero counts are "predates
        # instrumentation" (fix time, not a runtime bug). If any
        # post-cutoff loop shows zero, it's a real ledger write bug.
        "sample_loop_ids":          sample_loop_ids,
        "self_heal": {
            "loops_analysed":              len(self_heal_rows),
            "triggered":                   len(triggered_heals),
            "triggered_pct":               _pct(len(triggered_heals),
                                                len(self_heal_rows)),
            "resolved_on_attempt_1":       len(resolved_at_1),
            "resolved_on_attempt_1_pct":   _pct(len(resolved_at_1),
                                                max(len(triggered_heals), 1)),
        },
        "notes": [
            ("Per-phase durations come from loop_events state_transition "
             "rows (kind='state_transition'). Iter 315 fixed the missing "
             "write in loop_engine._emit(); loops completed BEFORE Iter "
             "315 deployed have no rows and contribute only to "
             "total_loop_duration. Check sample_loop_ids[].created_at "
             "against the Iter 315 deploy time to disambiguate."),
            ("`ora_chat_usage` join keys off session_id == loop_id. Zero "
             "counts mean either loops without Item-4 token tracking "
             "(loop_token_ledger shipped 2026-07-26 05:16 UTC) or a "
             "genuine ledger write bug. Cross-check sample_loop_ids to "
             "distinguish."),
            ("llm_calls_by_phase fields are labeled *_calls (COUNTS), "
             "NOT *_s (seconds). The previous avg_s label was a units "
             "lie fixed in Iter 315."),
            ("Sample excludes user_id prefixes: "
             + ", ".join(_EXCLUDE_USER_PREFIXES)),
        ],
    }
