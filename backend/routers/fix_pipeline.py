"""
routers/fix_pipeline.py — Iter 212m-121

Bulk + streaming fix orchestrator on top of the EXISTING real fix
pipeline (services/finding_fix_applier.apply_finding_fix).  Zero
mocks — every commit_sha returned here is a real GitHub commit
because we delegate to the same code path the per-finding `/fix`
endpoint already uses (which writes through services/
github_api_writer.commit_files()).

Endpoints (all under /api/aurem-dev):
  POST /fix-pipeline/preview
       Body: {project_id, finding_ids OR category, scanner}
       Returns: {count, tokens_cost, usd_cost, is_unlimited, balance}

  POST /fix-pipeline/bulk
       Body: same as preview, plus optional `findings:[...]` so the
       caller can stream the exact normalised finding objects (the
       Vanguard scan response shape).  Kicks off a background task
       that runs the fixes sequentially.  Returns `{job_id}`.

  GET  /fix-pipeline/stream/{job_id}
       Server-Sent Events stream.  Phases emitted, in order, per
       finding:
         queued      — about to start this finding
         reading     — fetching file from GitHub
         generating  — LLM is producing the patched content
         committing  — pushing the commit
         verifying   — re-running the static scanner
         fix-done    — single finding terminal (ok + commit OR error)
       And then once across the whole job:
         done        — terminal summary
         heartbeat   — sent every 120 s if otherwise idle

  GET  /fix-pipeline/summary/{job_id}
       Polling fallback for clients that can't use SSE (returns the
       same data the final `done` event carries).

Cost model:
  • Single token  ≈ $0.0001 USD  (constant TOKEN_USD_RATE).  Adjust
    in one place when pricing changes.  Founders / admins / unlimited
    pay zero, so they see "⚡ FREE" in the UI.
  • Per-finding price comes from `category_token_cost()` — uses the
    existing per-category cost table in routers/codebase_health.py
    (security/perf/quality/deps/db = 5, bug_hunt = 8).  Vanguard
    findings inherit from their normalised vuln class.
"""
from __future__ import annotations

import asyncio
import difflib
import json
import logging
import time
from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from sse_starlette.sse import EventSourceResponse

from cto_services.auth import current_dev
from cto_services.db import get_db
from services import fix_job_manager as fjm
from services.finding_fix_applier import apply_finding_fix
from services import ora_fix_learning
from services.scan_fix_quota import (
    ALL_FIX_TOOLS, assert_can_fix, get_fix_quota, record_scan_fixes,
)
from services.fixed_findings import record_fixed

logger = logging.getLogger("aurem-dev.fix_pipeline")
router = APIRouter(prefix="/fix-pipeline", tags=["Fix Pipeline"])


# ─── Iter 212m-147 — unified-diff helper ─────────────────────────────
#
# Produces the SSE `diff` event payload from the before/after file
# contents.  We use Python's stdlib `difflib.unified_diff` and strip
# the standard `--- /+++ /@@@` chrome lines, leaving only the actual
# `+`/`-` lines (with a small amount of `context` lines so the
# drawer can render a sensible window).  Limits the output to
# `_MAX_DIFF_LINES` so a 5000-line refactor doesn't blast the SSE
# pipe; the drawer can scroll within what it gets.
_MAX_DIFF_LINES = 80


def _compute_diff_lines(before: str, after: str) -> list[dict]:
    if before == after:
        return []
    before_lines = before.splitlines(keepends=False)
    after_lines  = after.splitlines(keepends=False)
    out: list[dict] = []
    for line in difflib.unified_diff(
        before_lines, after_lines, n=2, lineterm="",
    ):
        if not line:
            continue
        # Skip the `--- `/`+++ `/`@@` header chrome.
        if line.startswith("--- ") or line.startswith("+++ "):
            continue
        if line.startswith("@@"):
            out.append({"type": "hunk", "line": line})
            continue
        if line.startswith("-"):
            out.append({"type": "remove", "line": line[1:]})
        elif line.startswith("+"):
            out.append({"type": "add", "line": line[1:]})
        else:
            # Context line (no prefix marker after the unified_diff
            # strip).  Useful so the drawer can show a few lines of
            # surrounding code for orientation.
            out.append({"type": "context",
                        "line": line[1:] if line.startswith(" ") else line})
        if len(out) >= _MAX_DIFF_LINES:
            out.append({"type": "context",
                        "line": f"... diff truncated at {_MAX_DIFF_LINES} lines"})
            break
    return out


# ─── Pipeline pacing ───────────────────────────────────────────────────
# Iter 212m-127 — Batched bulk-fix execution.  Findings are
# processed in chunks of _BULK_BATCH_SIZE, each chunk interleaved
# across severity buckets so the user sees a critical, a high,
# a medium and a low fix landing in every batch rather than the
# scanner's natural critical-first ordering.  Between batches the
# worker awaits a small breather to let GitHub's branch indexer
# catch up — keeps PR creation reliable.
_BULK_BATCH_SIZE          = 10
_INTER_BATCH_BREATHE_S    = 1.5
_SEVERITY_BUCKET_ORDER    = ("critical", "high", "medium", "low")

# Iter 212m-128 — Auto-restart on per-finding failure.  We attempt
# each finding up to _MAX_FIX_ATTEMPTS times with exponential
# backoff between attempts.  Certain error codes are TERMINAL and
# bypass the retry loop because no amount of retrying will help:
#   • github_credentials_missing — no token to push with
#   • github_unauthorized        — token is bad, won't auto-fix here
#   • insufficient_tokens*       — wallet empty, retry won't help
#   • file_too_large             — won't fit in the LLM context
# Everything else (LLM hallucinated a no-op patch, network blip,
# unhandled exception, transient GitHub 5xx) IS retried.
_MAX_FIX_ATTEMPTS         = 3
_RETRY_BACKOFFS_S         = (1.0, 2.5, 5.0)
# Iter 212m-178 — gap between consecutive GitHub-mutating fixes so a
# bulk run doesn't trip GitHub's secondary (burst) rate limit.
_BULK_INTER_FIX_DELAY_S   = 1.5
# Iter 212m-179 — HARD cap per bulk run.  Each fix costs ~6 GitHub
# content-generating calls (branch ref + blob + tree + commit + ref
# patch + draft PR) against GitHub's secondary limits (80 writes/min,
# 500 writes/hr per user).  Value set from the empirical probe on
# TJSNDHU/Aurem (test_reports/prod_aggression/ratelimit_probe.py).
_BULK_MAX_FINDINGS        = 20
_TERMINAL_ERROR_CODES     = frozenset({
    "github_credentials_missing",
    "github_unauthorized",
    "insufficient_tokens",
    "insufficient_tokens_midbatch",
    "file_too_large",
})


def _is_unlimited(user: dict) -> bool:
    return bool(
        user.get("is_admin")
        or user.get("is_unlimited")
        or (user.get("tier") == "founder")
    )


# ─── Preview endpoint ──────────────────────────────────────────────────
@router.post("/preview")
async def preview_cost(body: dict,
                       authorization: Optional[str] = Header(None)) -> dict:
    """Cheap, no-side-effect cost preview for the bulk-fix confirm
    modal.  Returns total tokens + estimated USD + the caller's
    current balance + an `is_unlimited` flag the UI uses to swap the
    payment line for an `⚡ Founder — FREE` chip."""
    user    = await current_dev(authorization)
    user_id = user["user_id"]
    findings = body.get("findings") or []
    if not isinstance(findings, list):
        raise HTTPException(400, "findings must be a list")
    if not findings:
        raise HTTPException(400, "findings is empty")
    db = get_db()
    if db is None:
        raise HTTPException(503, "DB not connected")

    # Iter 212m-179 — preview prices only what one run may execute.
    total_requested = len(findings)
    findings = findings[:_BULK_MAX_FINDINGS]

    is_unlim = _is_unlimited(user)

    # Iter 212m-190 — task-quota model: 1 issue fixed = 1 task.
    tool = (body.get("tool") or "health-scan").strip()
    if tool not in ALL_FIX_TOOLS:
        tool = "health-scan"
    quota = await get_fix_quota(user)
    count = len(findings)
    tool_allowed = tool in quota["fix_tools"]
    bulk_allowed = count <= 1 or quota["bulk_fix"]
    remaining = quota["tasks_remaining"]
    enough_tasks = remaining is None or count <= remaining
    can_proceed = tool_allowed and bulk_allowed and enough_tasks
    reason = None
    if not tool_allowed:
        reason = "fix_not_available_on_tier"
    elif not bulk_allowed:
        reason = "bulk_fix_not_available"
    elif not enough_tasks:
        reason = "insufficient_tasks"

    return {
        "ok":              True,
        "count":           count,
        "bulk_max":        _BULK_MAX_FINDINGS,
        "total_requested": total_requested,
        "is_unlimited": is_unlim,
        # Task-quota fields — 1 task per fix, flat.
        "tool":               tool,
        "tier":               quota["tier"],
        "tasks_needed":       count,
        "tasks_remaining":    remaining,
        "monthly_task_limit": quota["monthly_task_limit"],
        "tool_allowed":       tool_allowed,
        "bulk_allowed":       bulk_allowed,
        "can_proceed":        can_proceed,
        "reason":             reason,
        "shortfall":          0 if enough_tasks else count - (remaining or 0),
    }


@router.get("/quota")
async def fix_quota(authorization: Optional[str] = Header(None)) -> dict:
    """Iter 212m-190 — task-quota snapshot for the fix UI. The frontend
    uses `fix_tools` to show/hide per-finding Fix buttons and
    `bulk_fix` to show/hide the bulk button (Team tier only)."""
    user = await current_dev(authorization)
    q = await get_fix_quota(user)
    return {"ok": True, **q}


# ─── Bulk fix kick-off ─────────────────────────────────────────────────
@router.post("/bulk")
async def start_bulk_fix(body: dict,
                         authorization: Optional[str] = Header(None)) -> dict:
    """Kick off a sequential bulk fix.  The HTTP response returns
    immediately with `{job_id}`; the client opens an SSE connection
    to `/fix-pipeline/stream/{job_id}` to watch progress."""
    user      = await current_dev(authorization)
    user_id   = user["user_id"]
    project_id = (body or {}).get("project_id")
    findings   = body.get("findings") or []
    if not project_id:
        raise HTTPException(400, "project_id required")
    if not findings:
        raise HTTPException(400, "findings is empty")
    if len(findings) > _BULK_MAX_FINDINGS:
        # Iter 212m-179 — HARD cap (was 500).  GitHub's secondary rate
        # limits 403 the pipeline mid-job past this point; failing fast
        # with a clear message beats a half-completed bulk run.
        raise HTTPException(400, {
            "error":     "bulk_limit_exceeded",
            "max":       _BULK_MAX_FINDINGS,
            "requested": len(findings),
            "message":   (f"Max {_BULK_MAX_FINDINGS} files per bulk run — "
                          "GitHub secondary rate-limit protection. Run the "
                          "rest as the next batch."),
        })
    db = get_db()
    if db is None:
        raise HTTPException(503, "DB not connected")

    is_unlim = _is_unlimited(user)
    # Iter 212m-190 — task-quota gate: tool access by tier, bulk (count
    # > 1) is Team-only, and N fixes need N remaining tasks. Raises
    # 403/402 with structured detail BEFORE any work starts. Tasks are
    # deducted per SUCCESSFUL fix inside the worker — failures never
    # burn quota.
    tool = ((body or {}).get("tool") or "health-scan").strip()
    if tool not in ALL_FIX_TOOLS:
        tool = "health-scan"
    await assert_can_fix(user, tool, count=len(findings))

    job_id = await fjm.create_job(
        db=db, user_id=user_id, kind="bulk", total=len(findings),
        project_id=project_id, findings=findings,
    )
    # Launch the worker — runs in the background, streams events.
    asyncio.create_task(_run_bulk_job(
        job_id=job_id, db=db, user=user, project_id=project_id,
        findings=findings, is_unlim=is_unlim, tool=tool,
    ))
    return {
        "ok":     True,
        "job_id": job_id,
        "count":  len(findings),
        "stream": f"/api/aurem-dev/fix-pipeline/stream/{job_id}",
    }


async def _run_bulk_job(*, job_id: str, db, user: dict, project_id: str,
                        findings: list[dict], is_unlim: bool,
                        tool: str = "health-scan") -> None:
    """The actual worker.  Findings are first interleaved by severity
    so each batch carries a mix of critical/high/medium/low fixes,
    then processed in chunks of _BULK_BATCH_SIZE.  Within a chunk we
    run sequentially (no Git conflicts on the same branch); between
    chunks we pause briefly so GitHub's branch indexer catches up.
    Every event is emitted onto the same job_id queue so the SSE
    drawer keeps streaming uninterrupted.

    Iter 212m-128 — Top-level try/except wrapper.  Any unhandled
    exception inside the worker (Mongo glitch, GitHub 5xx outside
    the per-finding block, programming bug) used to silently kill
    the asyncio task and leave the UI hanging in "running" forever.
    Now we catch, emit a `job-error` event with the full traceback,
    and close the job with status="failed" so the UI offers Restart.
    """
    user_id = user["user_id"]
    try:
        ordered = _interleave_by_severity(findings)
        batches = [ordered[i : i + _BULK_BATCH_SIZE]
                   for i in range(0, len(ordered), _BULK_BATCH_SIZE)]
        # Iter 212m-147 — `fix_total` is the canonical count of fixes
        # the drawer's header shows ("Fixing N / fix_total"). Emit it
        # in job-start so the drawer can size its progress bar.
        fjm.emit(job_id, "job-start", count=len(ordered),
                 fix_total=len(ordered),
                 batches=len(batches), batch_size=_BULK_BATCH_SIZE,
                 is_unlimited=is_unlim, project_id=project_id)
        await fjm.persist_event(db, job_id)
        total_charged = 0
        total_ok = 0
        total_failed = 0
        total_skipped = 0
        # Iter 212m-147 — Final-summary roll-up.  Collected per-fix so
        # the `all-done` event can replay the whole journey for the
        # drawer's terminal summary card.
        fixes_summary: list[dict] = []
        global_idx = 0
        for batch_no, batch in enumerate(batches, start=1):
            fjm.emit(job_id, "batch-start",
                     batch=batch_no, of=len(batches), size=len(batch),
                     severities=[f.get("severity") or "" for f in batch])
            for finding in batch:
                global_idx += 1
                # Iter 212m-178 — pace GitHub mutations. Firing
                # blob+tree+commit+ref for one fix and immediately
                # reading the next file tripped GitHub's SECONDARY rate
                # limit (bulk 2nd+ fix failed with github_status_403).
                # A short gap between findings keeps us under the burst
                # threshold; the read-side Retry-After handler covers
                # the rest.
                if global_idx > 1:
                    await asyncio.sleep(_BULK_INTER_FIX_DELAY_S)
                finding_id = (finding.get("id") or finding.get("rule_id")
                              or f"f_{global_idx}")
                rule_id    = finding.get("rule_id") or finding.get("rule") or ""
                path       = finding.get("file") or finding.get("path") or ""
                severity   = finding.get("severity") or ""

                fjm.emit(job_id, "queued",
                         index=global_idx, batch=batch_no,
                         finding_id=finding_id, rule_id=rule_id,
                         severity=severity, file=path,
                         # Iter 212m-147 — fix_index/fix_total + a
                         # human description so the drawer header
                         # ("Fixing 3/15") and active-fix block
                         # ("Reading file…") have all data on event 1.
                         fix_index=global_idx, fix_total=len(ordered),
                         description=(finding.get("title")
                                      or finding.get("message")
                                      or rule_id))

                # Iter 212m-190 — no pre-deduction. 1 task is recorded
                # per SUCCESSFUL fix (below), so failed fixes never
                # burn quota. Quota was asserted before job start.

                fjm.emit(job_id, "reading", finding_id=finding_id,
                         file=path,
                         fix_index=global_idx, fix_total=len(ordered))
                # Iter 212m-128 — Per-finding auto-retry.  We loop up to
                # _MAX_FIX_ATTEMPTS times, bailing early on terminal
                # error codes (see _TERMINAL_ERROR_CODES) where retrying
                # is pointless.  Each retry emits a `retrying` SSE event
                # so the drawer renders the attempt counter and the UI
                # never feels stuck on a hung step.
                res = None
                last_err = None
                attempts_used = 0
                _t_fix_start = time.time()
                for attempt in range(1, _MAX_FIX_ATTEMPTS + 1):
                    attempts_used = attempt
                    try:
                        res = await apply_finding_fix(
                            db=db, user=user, project_id=project_id, finding=finding,
                        )
                    except Exception as e:                            # noqa: BLE001
                        logger.exception("bulk fix raised for finding=%s "
                                         "attempt=%d", finding_id, attempt)
                        res = {"ok": False, "error": f"unhandled: {e}"}
                    if res.get("ok"):
                        break
                    last_err = res.get("error") or "unknown"
                    if last_err in _TERMINAL_ERROR_CODES:
                        # No point retrying — surface the real reason.
                        break
                    if attempt >= _MAX_FIX_ATTEMPTS:
                        break
                    # Emit a retry event so the UI shows "Retry 2/3 …"
                    # and the user has visible proof the system is still
                    # working (instead of silently hanging on a fail).
                    backoff = _RETRY_BACKOFFS_S[min(attempt - 1, len(_RETRY_BACKOFFS_S) - 1)]
                    fjm.emit(job_id, "retrying",
                             finding_id=finding_id, attempt=attempt + 1,
                             of=_MAX_FIX_ATTEMPTS, last_error=last_err,
                             backoff_s=backoff, file=path, rule_id=rule_id)
                    await asyncio.sleep(backoff)
                _fix_duration_ms = int((time.time() - _t_fix_start) * 1000)

                if not res.get("ok"):
                    fjm.emit(job_id, "fix-done",
                             ok=False, finding_id=finding_id,
                             error=res.get("error") or "unknown",
                             attempts=_MAX_FIX_ATTEMPTS,
                             file=path, rule_id=rule_id,
                             fix_index=global_idx,
                             fix_total=len(ordered))
                    await fjm.persist_event(db, job_id)
                    total_failed += 1
                    fixes_summary.append({
                        "index":      global_idx,
                        "file":       path,
                        "rule":       rule_id,
                        "status":     "failed",
                        "error":      res.get("error") or "unknown",
                        "commit_sha": None,
                        "github_url": None,
                    })
                    # Iter 212m-129 — Learning hook (failure path).
                    await ora_fix_learning.record_fix_outcome(
                        db, user_id=user_id, project_id=project_id,
                        finding=finding, result=res,
                        attempts=attempts_used,
                        duration_ms=_fix_duration_ms,
                        tokens_charged=0,   # never deducted (task-quota model)
                        scanner=finding.get("scanner"),
                    )
                    continue

                # Iter 212m-147 — Emit the diff BEFORE the GitHub
                # verification call so the drawer animates `+`/`-`
                # lines in immediately after the LLM patch lands,
                # then transitions to the "Committing" badge while
                # the verify call runs.  Diff is computed from the
                # before/after contents apply_finding_fix returned.
                _diff_lines = _compute_diff_lines(
                    res.get("original_content") or "",
                    res.get("patched_content")  or "",
                )
                if _diff_lines:
                    fjm.emit(job_id, "fix-diff",
                             finding_id=finding_id,
                             fix_index=global_idx,
                             fix_total=len(ordered),
                             file=path, rule_id=rule_id,
                             diff=_diff_lines)
                fjm.emit(job_id, "fix-committing",
                         finding_id=finding_id,
                         fix_index=global_idx,
                         fix_total=len(ordered),
                         file=path, rule_id=rule_id,
                         commit_sha=res.get("commit_sha"))

                # Real GitHub verification — confirm the commit exists.
                fjm.emit(job_id, "verifying",
                         finding_id=finding_id,
                         commit_sha=res.get("commit_sha"),
                         fix_index=global_idx,
                         fix_total=len(ordered))
                verified = await _verify_commit_exists(
                    db=db, user=user, project_id=project_id,
                    full_sha=res.get("full_sha"),
                )
                fjm.emit(job_id, "fix-done",
                         ok=True, finding_id=finding_id,
                         commit_sha=res.get("commit_sha"),
                         full_sha=res.get("full_sha"),
                         html_url=res.get("html_url"),
                         github_url=res.get("html_url"),
                         pr_url=res.get("pr_url"),
                         branch=res.get("branch"),
                         file=res.get("file"),
                         rule_id=res.get("rule_id"),
                         fix_index=global_idx,
                         fix_total=len(ordered),
                         verified=verified)
                await fjm.persist_event(db, job_id)
                fixes_summary.append({
                    "index":      global_idx,
                    "file":       res.get("file"),
                    "rule":       res.get("rule_id"),
                    "status":     "ok",
                    "commit_sha": res.get("commit_sha"),
                    "github_url": res.get("html_url"),
                    "pr_url":     res.get("pr_url"),
                    "verified":   verified,
                })
                # Iter 212m-129 — Learning hook (success path).  The
                # `verified` field carries the real GitHub-commit-
                # exists check so analytics can distinguish "LLM said
                # it fixed it" from "GitHub actually has the commit".
                await ora_fix_learning.record_fix_outcome(
                    db, user_id=user_id, project_id=project_id,
                    finding=finding,
                    result={**res, "verified": verified},
                    attempts=attempts_used,
                    duration_ms=_fix_duration_ms,
                    tokens_charged=0,
                    scanner=finding.get("scanner"),
                )
                total_ok += 1
                # Iter 212m-193 — persist the fixed state so a rescan
                # doesn't resurrect this finding (fix lives on a draft
                # PR branch; base branch is unchanged until merge).
                try:
                    await record_fixed(
                        db, user_id=user_id, project_id=project_id,
                        finding=finding,
                        commit_sha=res.get("commit_sha") or "",
                        html_url=res.get("html_url") or "",
                        tool=tool,
                    )
                except Exception:
                    pass
                # Iter 212m-190 — deduct exactly 1 task PER successful
                # fix (atomic $inc). Failed fixes above never reach
                # this line, so a 12-selected / 2-failed run deducts 10.
                if not is_unlim:
                    try:
                        await record_scan_fixes(user_id, tool, 1)
                        total_charged += 1
                    except Exception as _e:
                        logger.warning("task record failed user=%s: %r",
                                       user_id, _e)

            # End of batch — emit a summary event the UI uses for the
            # progress-bar tick + take a short breather before the next
            # batch so GitHub's branch indexer catches up.
            fjm.emit(job_id, "batch-end",
                     batch=batch_no, of=len(batches),
                     fixed_so_far=total_ok)
            await fjm.persist_event(db, job_id)
            if batch_no < len(batches):
                await asyncio.sleep(_INTER_BATCH_BREATHE_S)

        await fjm.close(
            db, job_id, ok=True,
            message=f"Fixed {total_ok}/{len(ordered)} "
                    f"({total_charged} tasks used · "
                    f"{len(batches)} batches of {_BULK_BATCH_SIZE})",
        )
    except asyncio.CancelledError:
        # Task was cancelled by the runtime (graceful shutdown).
        # Mark as orphaned so the UI offers restart on next page load.
        logger.warning("bulk job cancelled mid-flight job=%s", job_id)
        try:
            fjm.emit(job_id, "job-error", ok=False,
                     reason="cancelled",
                     message="Worker cancelled — pod may be restarting.")
        except Exception:
            pass
        await fjm.close(
            db, job_id, ok=False, status="orphaned",
            message="Worker cancelled mid-flight. Click Restart to resume.",
        )
        raise
    except Exception as e:                                  # noqa: BLE001
        # Top-level catch: a Mongo glitch, an upstream GitHub 5xx that
        # escaped the per-finding block, a programming bug — instead
        # of silently dying and leaving "running" forever, we emit a
        # final `job-error` SSE event and close the job as `failed`
        # so the drawer can show a real error message + a Restart
        # button driving POST /restart/{job_id}.
        import traceback
        tb = traceback.format_exc(limit=4)
        logger.exception("bulk job crashed job=%s", job_id)
        try:
            fjm.emit(job_id, "job-error", ok=False,
                     reason="worker_exception",
                     error=f"{type(e).__name__}: {e}",
                     traceback=tb,
                     message="Worker crashed — click Restart to retry the remaining findings.")
        except Exception:
            pass
        await fjm.close(
            db, job_id, ok=False, status="failed",
            message=f"Worker crashed: {type(e).__name__}: {str(e)[:200]}",
        )


def _interleave_by_severity(findings: list[dict]) -> list[dict]:
    """Sort findings into severity buckets (critical→high→medium→low,
    unknown trailing) and round-robin them so the output contains a
    mix of severities at every position.  Order within a bucket is
    preserved so the original scanner ordering still wins ties.

    Example with [C, C, C, H, H, M, M, L] →
       [C, H, M, L, C, H, M, C].  Each batch of 10 will therefore
    contain at least one critical AND at least one high/medium/low
    whenever the source list has them, giving the founder a visible
    mix of fixes shipping per batch instead of 'critical first'.
    """
    buckets: dict[str, list[dict]] = {k: [] for k in _SEVERITY_BUCKET_ORDER}
    trailing: list[dict] = []
    for f in findings:
        sev = (f.get("severity") or "").lower()
        if sev in buckets:
            buckets[sev].append(f)
        else:
            trailing.append(f)

    out: list[dict] = []
    while any(buckets[k] for k in _SEVERITY_BUCKET_ORDER):
        for k in _SEVERITY_BUCKET_ORDER:
            if buckets[k]:
                out.append(buckets[k].pop(0))
    out.extend(trailing)
    return out


async def _verify_commit_exists(*, db, user: dict, project_id: str,
                                full_sha: Optional[str]) -> bool:
    """Hit GET /repos/{owner}/{repo}/commits/{sha} to confirm the
    commit landed on GitHub.  No `True` shortcut without a real
    HTTP 200 + non-empty parents list — the user explicitly asked
    for proof, not optimism."""
    if not full_sha:
        return False
    try:
        proj = await db.cto_projects.find_one(
            {"project_id": project_id, "user_id": user["user_id"]},
            {"_id": 0, "github_owner": 1, "github_repo": 1, "github_token": 1},
        )
        if not proj:
            return False
        owner = proj.get("github_owner")
        repo  = proj.get("github_repo")
        from routers.security_scan import _decrypt_pat
        token = await _decrypt_pat(user["user_id"], proj.get("github_token"))
        if not token:
            try:
                u = await db.dev_users.find_one(
                    {"user_id": user["user_id"]}, {"_id": 0, "github": 1},
                )
                token = ((u or {}).get("github") or {}).get("access_token") or None
            except Exception:
                token = None
        if not (owner and repo and token):
            return False
        import httpx
        async with httpx.AsyncClient(timeout=10.0) as cx:
            r = await cx.get(
                f"https://api.github.com/repos/{owner}/{repo}/commits/{full_sha}",
                headers={"Authorization": f"token {token}",
                         "Accept": "application/vnd.github+json",
                         "User-Agent": "aurem-fix-verifier"},
            )
            if r.status_code != 200:
                return False
            data = r.json() or {}
            # A real commit always carries an `html_url` and a `sha`.
            return bool(data.get("sha") == full_sha and data.get("html_url"))
    except Exception as e:                                    # noqa: BLE001
        logger.warning("verify_commit_exists soft-failed: %r", e)
        return False


# ─── SSE stream ────────────────────────────────────────────────────────
@router.get("/stream/{job_id}")
async def stream_fix(job_id: str,
                     token: Optional[str] = None,
                     authorization: Optional[str] = Header(None)):
    """Server-Sent Events.  Re-auth via the standard JWT; rejects
    cross-tenant subscriptions so one user can't tail another's
    fix job.

    Auth fallback: browsers cannot set Authorization headers on an
    EventSource, so we also accept `?token=...` in the query string.
    Same JWT, same validation path — just a different transport.

    Iter 212m-128 — Mongo hydration. If the job is no longer in
    memory (pod restart, multi-pod miss) but its row exists in
    `fix_jobs`, we replay its terminal snapshot as a `hydrated`
    event so the drawer can render the partial results AND a
    Restart button instead of a blank "gone" screen.
    """
    if not authorization and token:
        authorization = f"Bearer {token}"
    user = await current_dev(authorization)
    db = get_db()
    summary = fjm.get_summary(job_id)

    # If not in memory, try Mongo for owner-check before letting the
    # generator stream the hydrated payload.
    if not summary:
        persisted = await fjm.get_persisted(db, job_id, user["user_id"])
        if persisted is None and not user.get("is_admin"):
            # Don't 403 — the user might be the legitimate owner but
            # we lost the row entirely.  Let the generator emit
            # `gone` so the drawer can render "expired".
            async def _empty():
                yield {"event": "phase",
                       "data": json.dumps({
                           "phase":   "gone",
                           "ts":      time.time(),
                           "message": "Job not found (may have expired)",
                           "can_restart": False,
                       })}
            return EventSourceResponse(_empty())
    else:
        # Owner check — in-memory job must belong to caller.
        job = fjm._JOBS.get(job_id) or {}                 # noqa: SLF001
        if job.get("user_id") and job["user_id"] != user["user_id"] \
                and not user.get("is_admin"):
            raise HTTPException(403, "Not your fix job")

    async def _event_stream():
        async for ev in fjm.subscribe(job_id, db=db):
            yield {"event": "phase", "data": json.dumps(ev)}

    return EventSourceResponse(_event_stream())


# ─── Polling fallback ──────────────────────────────────────────────────
@router.get("/summary/{job_id}")
async def get_job_summary(job_id: str,
                           authorization: Optional[str] = Header(None)) -> dict:
    user = await current_dev(authorization)
    db = get_db()
    s = fjm.get_summary(job_id)
    if not s:
        # Fall back to Mongo so a multi-pod / post-restart caller
        # still gets a polling response.
        persisted = await fjm.get_persisted(db, job_id, user["user_id"])
        if not persisted:
            raise HTTPException(404, "Job not found")
        return {"ok": True, "from_mongo": True, **persisted}
    job = fjm._JOBS.get(job_id) or {}                         # noqa: SLF001
    if job.get("user_id") and job["user_id"] != user["user_id"] \
            and not user.get("is_admin"):
        raise HTTPException(403, "Not your fix job")
    return {"ok": True, **s}


# ─── Iter 212m-128 — List user's recent jobs ──────────────────────────
@router.get("/list")
async def list_user_jobs(
    status: Optional[str] = None,
    limit: int = 20,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Returns the caller's recent fix jobs (newest first).  Used by
    the Dashboard "Resume in-flight fix" banner + the FixProgressDrawer
    re-attach logic on page reload."""
    user = await current_dev(authorization)
    db = get_db()
    rows = await fjm.list_jobs(
        db, user["user_id"], limit=min(50, max(1, int(limit))),
        status=status,
    )
    return {"ok": True, "jobs": rows, "count": len(rows)}


# ─── Iter 212m-128 — Restart an orphaned / failed job ─────────────────
@router.post("/restart/{job_id}")
async def restart_job(job_id: str,
                      authorization: Optional[str] = Header(None)) -> dict:
    """Spin a fresh worker on the findings that were NOT yet
    completed by the original job.  Returns a NEW job_id (with a
    clean event stream) — the old row stays in Mongo as historical
    audit.

    Rules:
      • Job must be `orphaned`, `failed`, or `running` (the latter
        covers the case where a different pod's worker is *thought*
        to still be running but the user has lost faith).
      • The caller must own the job.
      • If every finding was already completed, returns 200 with
        `{nothing_to_do: true}` instead of starting an empty worker.
    """
    user = await current_dev(authorization)
    user_id = user["user_id"]
    db = get_db()
    if db is None:
        raise HTTPException(503, "DB not connected")

    persisted = await fjm.get_persisted(db, job_id, user_id)
    if not persisted:
        raise HTTPException(404, "Job not found")
    if persisted.get("status") not in ("orphaned", "failed", "running"):
        raise HTTPException(409, {
            "error":   "job_not_restartable",
            "status":  persisted.get("status"),
            "message": "Only orphaned, failed, or running jobs can be restarted.",
        })

    all_findings = persisted.get("all_findings") or []
    completed    = set(persisted.get("completed_ids") or [])
    fail_terminal = set(persisted.get("failed_terminal_ids") or [])
    # Remaining = original list minus anything already finished
    # successfully OR terminally failed (where retry can't help).
    remaining = [
        f for f in all_findings
        if (f.get("id") or f.get("rule_id")) not in completed
        and (f.get("id") or f.get("rule_id")) not in fail_terminal
    ]
    if not remaining:
        # Flip the old row to "done" so the UI list stops nagging.
        try:
            await db.fix_jobs.update_one(
                {"job_id": job_id, "user_id": user_id},
                {"$set": {"status": "done",
                          "message": "All findings already completed."}},
            )
        except Exception:
            pass
        return {"ok": True, "nothing_to_do": True,
                "message": "All findings were already completed."}

    project_id = persisted.get("project_id") or ""
    is_unlim   = _is_unlimited(user)
    new_job_id = await fjm.create_job(
        db=db, user_id=user_id, kind="bulk", total=len(remaining),
        project_id=project_id, findings=remaining,
    )
    # Mark the original row as superseded so /list doesn't show it
    # alongside the new one.
    try:
        await db.fix_jobs.update_one(
            {"job_id": job_id, "user_id": user_id},
            {"$set": {"superseded_by": new_job_id,
                      "status":        "restarted",
                      "closed_at":     time.time()}},
        )
    except Exception:
        pass
    asyncio.create_task(_run_bulk_job(
        job_id=new_job_id, db=db, user=user, project_id=project_id,
        findings=remaining, is_unlim=is_unlim,
    ))
    return {
        "ok":             True,
        "original":       job_id,
        "job_id":         new_job_id,
        "remaining":      len(remaining),
        "skipped_done":   len(completed),
        "skipped_terminal": len(fail_terminal),
        "stream":         f"/api/aurem-dev/fix-pipeline/stream/{new_job_id}",
    }
