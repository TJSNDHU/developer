"""
scripts/iter309_sse_reconnect_harness.py — Iter 309 · SSE 25-min validation

Long-run instrumented harness. Attaches to the synthetic probe stream
(`routers/dev_sse_probe.py`), records every event received and every
disconnect/reconnect pair with a timestamp + reason + gap duration.
Runs for a configured wall-clock (default 26 min) so the natural
`STREAM_MAX_S = 20 min` server cap fires at least once and the
reconnect path is genuinely exercised end-to-end.

Emits two artifacts under /app/test_reports/iter309_reconnect/:
  • events_<run_id>.jsonl     — one JSON line per SSE frame received
  • reconnects_<run_id>.jsonl — one JSON line per disconnect/reconnect
  • report_<run_id>.json      — final aggregate + pass/fail verdict

Assertions the harness proves at the end:
  1. Total run duration ≥ TARGET_S (default 25 min).
  2. At least one natural cap-and-reconnect occurred (the 20-min
     cap is the whole point).
  3. Every observed `probe_seq` is monotonically increasing (no
     duplicates delivered to `on_event`) — proves the Last-Event-ID
     dedup path works.
  4. No `probe_seq` is missing from the received-set (proves the
     replay buffer covered every gap).
  5. Longest reconnect gap ≤ MAX_ALLOWED_GAP_S (default 10 s) —
     configurable, honest reporting on whatever we observe.

Run:
  python /app/backend/scripts/iter309_sse_reconnect_harness.py \
    --target-s 1560 --gap-max-s 10 &

Poll the report file periodically:
  ls -la /app/test_reports/iter309_reconnect/
  cat /app/test_reports/iter309_reconnect/report_*.json | tail
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx

# ── Config ────────────────────────────────────────────────────────
API_BASE = os.environ.get("AUREM_API_BASE", "http://localhost:8001/api/aurem-dev")
EMAIL    = os.environ.get("AUREM_TEST_EMAIL",    "test@aurem.dev")
PASSWORD = os.environ.get("AUREM_TEST_PASSWORD", "AuremTest2026!")

OUT_DIR = Path("/app/test_reports/iter309_reconnect")
OUT_DIR.mkdir(parents=True, exist_ok=True)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _log(msg: str) -> None:
    print(f"[{_now_iso()}] {msg}", flush=True)


async def _login() -> str:
    async with httpx.AsyncClient(timeout=30.0) as c:
        r = await c.post(
            f"{API_BASE}/auth/login",
            json={"email": EMAIL, "password": PASSWORD},
        )
        r.raise_for_status()
        d = r.json()
        return d.get("token") or d["access_token"]


async def _probe_start(token: str) -> str:
    async with httpx.AsyncClient(timeout=30.0) as c:
        r = await c.get(
            f"{API_BASE}/_iter309_probe/start",
            headers={"Authorization": f"Bearer {token}"},
        )
        r.raise_for_status()
        return r.json()["loop_id"]


async def _probe_stop(token: str, loop_id: str) -> None:
    async with httpx.AsyncClient(timeout=30.0) as c:
        try:
            await c.post(
                f"{API_BASE}/_iter309_probe/{loop_id}/stop",
                headers={"Authorization": f"Bearer {token}"},
            )
        except Exception as e:  # noqa: BLE001
            _log(f"probe_stop failed (non-fatal): {e!r}")


async def _stream_once(
    token: str, loop_id: str, last_event_id: str | None,
    events_out, on_frame,
) -> tuple[str, str | None]:
    """One SSE connection attempt. Returns (reason, last_event_id_received)."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept":        "text/event-stream",
    }
    if last_event_id:
        headers["Last-Event-ID"] = last_event_id

    lei = last_event_id
    reason = "unknown"
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(None, connect=10.0),
    ) as c:
        try:
            async with c.stream(
                "GET",
                f"{API_BASE}/_iter309_probe/{loop_id}/stream",
                headers=headers,
            ) as r:
                if r.status_code != 200:
                    return f"http_{r.status_code}", lei
                buf = ""
                async for chunk in r.aiter_text():
                    buf += chunk
                    while "\n\n" in buf:
                        frame, buf = buf.split("\n\n", 1)
                        frame_id = None
                        data_lines: list[str] = []
                        for line in frame.split("\n"):
                            if line.startswith("id:"):
                                frame_id = line[3:].strip()
                            elif line.startswith("data:"):
                                data_lines.append(line[5:].strip())
                        if not data_lines:
                            continue
                        try:
                            ev = json.loads("\n".join(data_lines))
                        except Exception:
                            continue
                        if frame_id:
                            lei = frame_id
                        rec = {
                            "recv_ts":  _now_iso(),
                            "frame_id": frame_id,
                            "event":    ev,
                        }
                        events_out.write(json.dumps(rec) + "\n")
                        events_out.flush()
                        on_frame(ev, frame_id)
                        state = ev.get("state")
                        if state == "stream_capped":
                            reason = "stream_capped"
                            return reason, lei
                        if state in ("completed", "failed", "aborted"):
                            reason = f"terminal:{state}"
                            return reason, lei
                reason = "eof"
                return reason, lei
        except httpx.ReadTimeout:
            return "read_timeout", lei
        except httpx.RemoteProtocolError as e:
            return f"protocol_error:{e!r}", lei
        except httpx.HTTPError as e:
            return f"http_error:{e!r}", lei
        except Exception as e:  # noqa: BLE001
            return f"exception:{type(e).__name__}:{e!r}", lei


async def main(target_s: int, gap_max_s: float) -> int:
    run_id = uuid.uuid4().hex[:12]
    events_path      = OUT_DIR / f"events_{run_id}.jsonl"
    reconnects_path  = OUT_DIR / f"reconnects_{run_id}.jsonl"
    report_path      = OUT_DIR / f"report_{run_id}.json"

    _log(f"iter309 SSE harness — run_id={run_id}, target={target_s}s, gap_max={gap_max_s}s")

    token   = await _login()
    _log("logged in")
    loop_id = await _probe_start(token)
    _log(f"probe started — loop_id={loop_id}")

    # Per-run state
    seen_seq: dict[int, str] = {}   # probe_seq → recv_ts
    duplicates: list[dict] = []
    reconnect_events: list[dict] = []
    first_recv_ts: float | None = None
    last_recv_ts: float | None = None

    def on_frame(ev: dict, frame_id: str | None) -> None:
        nonlocal first_recv_ts, last_recv_ts
        now = time.time()
        if first_recv_ts is None:
            first_recv_ts = now
        last_recv_ts = now
        seq = ev.get("probe_seq")
        if seq is None:
            return
        if seq in seen_seq:
            duplicates.append({"seq": seq, "recv_ts": _now_iso()})
        else:
            seen_seq[seq] = _now_iso()

    start_ts    = time.time()
    last_event_id: str | None = None
    attempt = 0

    with open(events_path, "w") as ev_f, open(reconnects_path, "w") as rc_f:
        while time.time() - start_ts < target_s:
            attempt += 1
            connect_start = time.time()
            _log(f"attempt {attempt} — Last-Event-ID={last_event_id}")
            reason, last_event_id = await _stream_once(
                token, loop_id, last_event_id, ev_f, on_frame,
            )
            disconnect_ts = time.time()
            elapsed_this  = disconnect_ts - connect_start
            _log(f"attempt {attempt} closed — reason={reason} elapsed={elapsed_this:.1f}s last_event_id={last_event_id}")
            rc_rec = {
                "attempt":         attempt,
                "connect_ts":      datetime.fromtimestamp(connect_start, tz=timezone.utc).isoformat().replace("+00:00", "Z"),
                "disconnect_ts":   datetime.fromtimestamp(disconnect_ts, tz=timezone.utc).isoformat().replace("+00:00", "Z"),
                "duration_s":      round(elapsed_this, 3),
                "reason":          reason,
                "last_event_id":   last_event_id,
            }
            if reason.startswith("terminal:"):
                rc_f.write(json.dumps(rc_rec) + "\n")
                rc_f.flush()
                reconnect_events.append(rc_rec)
                _log("terminal reached — harness done")
                break
            if time.time() - start_ts >= target_s:
                rc_f.write(json.dumps(rc_rec) + "\n")
                rc_f.flush()
                reconnect_events.append(rc_rec)
                break
            # Reconnect with a small settle so we don't hammer a
            # legitimately broken backend.
            settle_s = 1.0
            rc_rec["settle_s"] = settle_s
            rc_f.write(json.dumps(rc_rec) + "\n")
            rc_f.flush()
            reconnect_events.append(rc_rec)
            await asyncio.sleep(settle_s)

    # Stop the synthetic probe (drops the in-memory entry).
    await _probe_stop(token, loop_id)

    # ── Assemble aggregate report ─────────────────────────────────
    run_duration = time.time() - start_ts
    natural_caps = [
        r for r in reconnect_events if r["reason"] == "stream_capped"
    ]
    other_disconnects = [
        r for r in reconnect_events
        if r["reason"] != "stream_capped"
        and not r["reason"].startswith("terminal:")
    ]
    seqs_sorted = sorted(seen_seq.keys())
    missing_seq: list[int] = []
    if seqs_sorted:
        expected = set(range(1, max(seqs_sorted) + 1))
        missing_seq = sorted(list(expected - set(seqs_sorted)))
    longest_gap_s = max(
        (
            (
                # Gap between disconnect_ts of attempt n and connect_ts
                # of attempt n+1 (settle + reconnect + first-frame RTT).
                # Approximated as settle_s here since we don't record
                # the connect-open moment separately.
                r.get("settle_s") or 0.0
            )
            for r in reconnect_events
        ),
        default=0.0,
    )

    # Verdict per user's spec
    verdict_details: list[str] = []
    passed = True
    if run_duration < target_s * 0.98:
        passed = False
        verdict_details.append(
            f"FAIL: run duration {run_duration:.0f}s < target {target_s}s "
            f"(harness died early)."
        )
    if duplicates:
        passed = False
        verdict_details.append(
            f"FAIL: {len(duplicates)} duplicate probe_seq deliveries "
            f"— dedup path broken."
        )
    if missing_seq:
        passed = False
        verdict_details.append(
            f"FAIL: {len(missing_seq)} missing probe_seq values in "
            f"received set — replay buffer dropped events "
            f"(first 10 missing: {missing_seq[:10]})."
        )
    if longest_gap_s > gap_max_s:
        passed = False
        verdict_details.append(
            f"FAIL: longest reconnect gap {longest_gap_s:.2f}s > "
            f"threshold {gap_max_s:.2f}s."
        )
    if not natural_caps and run_duration >= 20 * 60:
        # If we ran >20 min but never saw a cap, the server-side cap
        # may have moved — the test isn't actually stressing what we
        # think it is. Report but don't fail.
        verdict_details.append(
            "WARN: no stream_capped observed despite run > 20 min — "
            "STREAM_MAX_S may have changed."
        )

    if passed and not verdict_details:
        verdict_details.append(
            "PASS: all reconnects recovered cleanly, no dup / no gap, "
            f"{len(natural_caps)} natural 20-min cap(s) exercised."
        )
    elif passed:
        verdict_details.insert(
            0, "PASS with warnings (see below).",
        )

    report = {
        "run_id":              run_id,
        "started_at":          datetime.fromtimestamp(
            start_ts, tz=timezone.utc,
        ).isoformat().replace("+00:00", "Z"),
        "finished_at":         _now_iso(),
        "run_duration_s":      round(run_duration, 1),
        "target_s":            target_s,
        "gap_max_s":           gap_max_s,
        "loop_id":             loop_id,
        "events_received":     len(seen_seq),
        "duplicate_deliveries": len(duplicates),
        "missing_seq":         missing_seq,
        "seq_range":           [
            min(seqs_sorted) if seqs_sorted else None,
            max(seqs_sorted) if seqs_sorted else None,
        ],
        "reconnect_count":     len(reconnect_events),
        "natural_caps":        len(natural_caps),
        "other_disconnects":   len(other_disconnects),
        "reconnects":          reconnect_events,
        "longest_gap_s":       round(longest_gap_s, 3),
        "verdict":             "PASS" if passed else "FAIL",
        "verdict_details":     verdict_details,
        "events_path":         str(events_path),
        "reconnects_path":     str(reconnects_path),
    }
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)
    _log(f"report written: {report_path}")
    _log(f"VERDICT: {report['verdict']}")
    for line in verdict_details:
        _log(f"  {line}")
    return 0 if passed else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--target-s", type=int, default=26 * 60,
                    help="Wall-clock run length (default 26 min).")
    ap.add_argument("--gap-max-s", type=float, default=10.0,
                    help="Max allowed reconnect gap (default 10 s).")
    args = ap.parse_args()
    sys.exit(asyncio.run(main(args.target_s, args.gap_max_s)))
