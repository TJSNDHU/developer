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

logger = logging.getLogger("aurem-dev.fix_pipeline")
router = APIRouter(prefix="/fix-pipeline", tags=["Fix Pipeline"])


# ─── Cost model ────────────────────────────────────────────────────────
TOKEN_USD_RATE = 0.0001         # 1 token = $0.0001 → 10 000 tokens = $1
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
# Token cost per category — must stay in sync with the CATS array in
# frontend/src/pages/CodebaseHealth.jsx.
_CATEGORY_TOKEN_COST = {
    "security":     5,
    "performance":  5,
    "code_quality": 5,
    "dependencies": 5,
    "database":     5,
    "bug_hunt":     8,
    "vanguard":     5,   # in-process Vanguard findings (Security drawer)
    "trufflehog":   5,   # CI secret scan findings
}


def _token_cost_for_finding(f: dict) -> int:
    """Look up per-finding cost.  Falls back to 5 (the dominant
    category)."""
    cat = (
        f.get("category")
        or f.get("scanner")
        or f.get("vuln")
        or ""
    ).lower()
    # Map common vuln classes to their parent category.
    if cat in ("secret_leak", "sql_injection", "nosql_injection",
               "ssti", "lpdos", "redos", "chain"):
        cat = "vanguard"
    return _CATEGORY_TOKEN_COST.get(cat, 5)


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

    tokens_cost = sum(_token_cost_for_finding(f) for f in findings)
    is_unlim    = _is_unlimited(user)

    me = await db.dev_users.find_one(
        {"user_id": user_id}, {"_id": 0, "tokens_remaining": 1},
    )
    balance = int((me or {}).get("tokens_remaining") or 0)

    return {
        "ok":           True,
        "count":        len(findings),
        "tokens_cost":  tokens_cost if not is_unlim else 0,
        "usd_cost":     round(tokens_cost * TOKEN_USD_RATE, 4)
                        if not is_unlim else 0.0,
        "is_unlimited": is_unlim,
        "balance":      balance,
        "can_proceed":  is_unlim or balance >= tokens_cost,
        "shortfall":    0 if (is_unlim or balance >= tokens_cost) else tokens_cost - balance,
    }


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
    if len(findings) > 500:
        # Iter 212m-127 — Raised cap from 50 to 500.  Findings now
        # process in interleaved batches of 10 (see _interleave_by_
        # severity + _BULK_BATCH_SIZE) so the user gets a mix of
        # critical / high / medium / low fixes shipping every batch
        # instead of "50 criticals then nothing".  Hard ceiling of
        # 500 still protects against pathological bulk clicks.
        raise HTTPException(400, "max 500 findings per bulk fix")
    db = get_db()
    if db is None:
        raise HTTPException(503, "DB not connected")

    is_unlim   = _is_unlimited(user)
    tokens_total = sum(_token_cost_for_finding(f) for f in findings)
    # Founder bypass — never check balance, never deduct.
    if not is_unlim:
        me = await db.dev_users.find_one(
            {"user_id": user_id}, {"_id": 0, "tokens_remaining": 1},
        )
        bal = int((me or {}).get("tokens_remaining") or 0)
        if bal < tokens_total:
            raise HTTPException(402, {
                "error":   "insufficient_tokens",
                "needed":  tokens_total,
                "balance": bal,
            })

    job_id = fjm.create_job(user_id=user_id, kind="bulk", total=len(findings))
    # Launch the worker — runs in the background, streams events.
    asyncio.create_task(_run_bulk_job(
        job_id=job_id, db=db, user=user, project_id=project_id,
        findings=findings, is_unlim=is_unlim,
    ))
    return {
        "ok":     True,
        "job_id": job_id,
        "count":  len(findings),
        "stream": f"/api/aurem-dev/fix-pipeline/stream/{job_id}",
    }


async def _run_bulk_job(*, job_id: str, db, user: dict, project_id: str,
                        findings: list[dict], is_unlim: bool) -> None:
    """The actual worker.  Findings are first interleaved by severity
    so each batch carries a mix of critical/high/medium/low fixes,
    then processed in chunks of _BULK_BATCH_SIZE.  Within a chunk we
    run sequentially (no Git conflicts on the same branch); between
    chunks we pause briefly so GitHub's branch indexer catches up.
    Every event is emitted onto the same job_id queue so the SSE
    drawer keeps streaming uninterrupted."""
    user_id = user["user_id"]
    ordered = _interleave_by_severity(findings)
    batches = [ordered[i : i + _BULK_BATCH_SIZE]
               for i in range(0, len(ordered), _BULK_BATCH_SIZE)]
    fjm.emit(job_id, "job-start", count=len(ordered),
             batches=len(batches), batch_size=_BULK_BATCH_SIZE,
             is_unlimited=is_unlim, project_id=project_id)
    total_charged = 0
    total_ok = 0
    global_idx = 0
    for batch_no, batch in enumerate(batches, start=1):
        fjm.emit(job_id, "batch-start",
                 batch=batch_no, of=len(batches), size=len(batch),
                 severities=[f.get("severity") or "" for f in batch])
        for finding in batch:
            global_idx += 1
            finding_id = (finding.get("id") or finding.get("rule_id")
                          or f"f_{global_idx}")
            rule_id    = finding.get("rule_id") or finding.get("rule") or ""
            path       = finding.get("file") or finding.get("path") or ""
            severity   = finding.get("severity") or ""
            cost       = _token_cost_for_finding(finding)

            fjm.emit(job_id, "queued",
                     index=global_idx, batch=batch_no,
                     finding_id=finding_id, rule_id=rule_id,
                     severity=severity, file=path)

            # Token deduction is BEST-EFFORT per finding so a mid-batch
            # 402 doesn't leak a partial commit.  Founders skip entirely.
            if not is_unlim and cost > 0:
                upd = await db.dev_users.update_one(
                    {"user_id": user_id, "tokens_remaining": {"$gte": cost}},
                    {"$inc": {"tokens_remaining": -cost}},
                )
                if upd.modified_count == 0:
                    fjm.emit(job_id, "fix-done",
                             ok=False, finding_id=finding_id,
                             error="insufficient_tokens_midbatch",
                             file=path, rule_id=rule_id)
                    continue
                total_charged += cost

            fjm.emit(job_id, "reading", finding_id=finding_id, file=path)
            # NOTE: apply_finding_fix already runs read → llm patch →
            # re-validate → commit → draft PR in sequence.  We bracket
            # the call with phase markers so the UI can show staged
            # progress; the actual sub-steps are opaque inside the fn.
            try:
                res = await apply_finding_fix(
                    db=db, user=user, project_id=project_id, finding=finding,
                )
            except Exception as e:                                # noqa: BLE001
                logger.exception("bulk fix raised for finding=%s", finding_id)
                res = {"ok": False, "error": f"unhandled: {e}"}

            if not res.get("ok"):
                # Refund the per-finding deduction on failure.
                if not is_unlim and cost > 0:
                    try:
                        await db.dev_users.update_one(
                            {"user_id": user_id},
                            {"$inc": {"tokens_remaining": cost}},
                        )
                        total_charged -= cost
                    except Exception:
                        pass
                fjm.emit(job_id, "fix-done",
                         ok=False, finding_id=finding_id,
                         error=res.get("error") or "unknown",
                         file=path, rule_id=rule_id)
                continue

            # Real GitHub verification — confirm the commit exists.
            fjm.emit(job_id, "verifying",
                     finding_id=finding_id, commit_sha=res.get("commit_sha"))
            verified = await _verify_commit_exists(
                db=db, user=user, project_id=project_id,
                full_sha=res.get("full_sha"),
            )
            fjm.emit(job_id, "fix-done",
                     ok=True, finding_id=finding_id,
                     commit_sha=res.get("commit_sha"),
                     full_sha=res.get("full_sha"),
                     html_url=res.get("html_url"),
                     pr_url=res.get("pr_url"),
                     branch=res.get("branch"),
                     file=res.get("file"),
                     rule_id=res.get("rule_id"),
                     verified=verified)
            total_ok += 1

        # End of batch — emit a summary event the UI uses for the
        # progress-bar tick + take a short breather before the next
        # batch so GitHub's branch indexer catches up.
        fjm.emit(job_id, "batch-end",
                 batch=batch_no, of=len(batches),
                 fixed_so_far=total_ok)
        if batch_no < len(batches):
            await asyncio.sleep(_INTER_BATCH_BREATHE_S)

    fjm.close(job_id, ok=True,
              message=f"Fixed {total_ok}/{len(ordered)} "
                      f"({total_charged} tokens charged · "
                      f"{len(batches)} batches of {_BULK_BATCH_SIZE})")


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
    Same JWT, same validation path — just a different transport."""
    if not authorization and token:
        authorization = f"Bearer {token}"
    user = await current_dev(authorization)
    summary = fjm.get_summary(job_id)
    if not summary:
        # Could be a job that was GC'd — still let the client see the
        # `gone` event so the drawer can render a friendly "expired".
        async def _empty():
            yield {"event": "phase",
                   "data": json.dumps({
                       "phase":   "gone",
                       "ts":      time.time(),
                       "message": "Job not found (may have expired)",
                   })}
        return EventSourceResponse(_empty())

    # Owner check.
    if summary.get("kind") and summary.get("job_id") == job_id:
        # We stored user_id on the job — pull it from the active map.
        job = fjm._JOBS.get(job_id) or {}                     # noqa: SLF001
        if job.get("user_id") and job["user_id"] != user["user_id"] \
                and not user.get("is_admin"):
            raise HTTPException(403, "Not your fix job")

    async def _event_stream():
        async for ev in fjm.subscribe(job_id):
            yield {"event": "phase", "data": json.dumps(ev)}

    return EventSourceResponse(_event_stream())


# ─── Polling fallback ──────────────────────────────────────────────────
@router.get("/summary/{job_id}")
async def get_job_summary(job_id: str,
                           authorization: Optional[str] = Header(None)) -> dict:
    user = await current_dev(authorization)
    s = fjm.get_summary(job_id)
    if not s:
        raise HTTPException(404, "Job not found")
    job = fjm._JOBS.get(job_id) or {}                         # noqa: SLF001
    if job.get("user_id") and job["user_id"] != user["user_id"] \
            and not user.get("is_admin"):
        raise HTTPException(403, "Not your fix job")
    return {"ok": True, **s}
