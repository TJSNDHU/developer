"""
services/loop_safety.py — Iter 212m-115

Five production-safety primitives shared by Loop Mode + Finding Fix:

  1. validate_github_token()      — PAT pre-flight (fail-fast at start)
  2. acquire_loop_lock()          — concurrent-loop refusal per project
  3. release_loop_lock()          — clean up when loop terminates
  4. record_loop_failure()        — circuit-breaker counter
  5. is_loop_circuit_open()       — refuse-start when 3+ fails in 15 min
  6. github_request_with_retry()  — rate-limit-aware HTTP wrapper

Per-collection compound indexes ensure cross-project isolation:
  loop_locks         { project_id, user_id, loop_id, acquired_at }
                     unique index on (project_id, user_id) — at most ONE
                     active loop per project per user.
  loop_failures      { project_id, user_id, occurred_at, reason }
                     window-scan index.

Branch-per-fix (#5 of the founder's safety asks) lives in
services/github_api_writer.commit_files() via the new `target_branch`
parameter + the new commit_to_aurem_branch() helper.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

import httpx

logger = logging.getLogger("aurem-dev.loop_safety")


# ─── 1 & 6. PAT pre-flight + rate-limit-aware GitHub HTTP ─────────────
async def validate_github_token(
    owner: str, repo: str, token: str,
) -> tuple[bool, Optional[str]]:
    """Hit GET /repos/{owner}/{repo} with the user's PAT. Returns
    (ok, error_code). Fails-fast in <2 s instead of letting the loop
    crash at SHIP after Plan+Execute+Verify+Scan have spent tokens."""
    if not (owner and repo and token):
        return False, "missing_args"
    headers = {
        "Authorization": f"token {token}",
        "Accept":        "application/vnd.github+json",
        "User-Agent":    "aurem-loop-preflight",
    }
    try:
        async with httpx.AsyncClient(timeout=8.0) as cx:
            r = await cx.get(f"https://api.github.com/repos/{owner}/{repo}",
                             headers=headers)
        if r.status_code == 200:
            return True, None
        if r.status_code == 401:
            return False, "pat_invalid_or_expired"
        if r.status_code == 403:
            # Could be rate-limited OR scope-missing.
            remaining = r.headers.get("x-ratelimit-remaining")
            if remaining == "0":
                return False, "github_rate_limited"
            return False, "pat_missing_scope"
        if r.status_code == 404:
            return False, "repo_not_found_or_no_access"
        return False, f"github_status_{r.status_code}"
    except (httpx.TimeoutException, httpx.NetworkError) as e:
        logger.warning("PAT preflight network err: %r", e)
        return False, "network_error"
    except Exception as e:                                # noqa: BLE001
        logger.exception("PAT preflight unexpected err")
        return False, f"unexpected: {e}"


async def github_request_with_retry(
    method: str, url: str, *, headers: dict, json: Optional[dict] = None,
    params: Optional[dict] = None, max_retries: int = 2, timeout: float = 15.0,
) -> httpx.Response:
    """Wraps an httpx request with rate-limit-aware retry. If GitHub
    responds with 403 + x-ratelimit-remaining=0, we sleep until the
    `x-ratelimit-reset` epoch (capped at 30 s) and try once more. On
    a 5xx we exponential-backoff 2 s then 4 s."""
    last_resp: Optional[httpx.Response] = None
    for attempt in range(max_retries + 1):
        async with httpx.AsyncClient(timeout=timeout) as cx:
            r = await cx.request(method, url, headers=headers,
                                 json=json, params=params)
        last_resp = r
        if r.status_code < 400:
            return r
        # Rate limit handling.
        if r.status_code == 403 and r.headers.get("x-ratelimit-remaining") == "0":
            if attempt >= max_retries:
                return r
            reset = int(r.headers.get("x-ratelimit-reset") or 0)
            wait = max(0, min(30, reset - int(time.time())))
            logger.warning("GH rate limited — sleeping %ds (attempt %d/%d)",
                           wait, attempt + 1, max_retries + 1)
            await asyncio.sleep(wait + 1)
            continue
        # 5xx — backoff retry.
        if 500 <= r.status_code < 600 and attempt < max_retries:
            backoff = 2 ** (attempt + 1)
            logger.warning("GH %d — backing off %ds (attempt %d/%d)",
                           r.status_code, backoff, attempt + 1, max_retries + 1)
            await asyncio.sleep(backoff)
            continue
        # Non-retryable.
        return r
    return last_resp  # type: ignore[return-value]


# ─── 2 & 3. Concurrent-loop lock ─────────────────────────────────────
async def acquire_loop_lock(
    db, project_id: str, user_id: str, loop_id: str,
) -> tuple[bool, Optional[dict]]:
    """Try to take the {project_id, user_id} lock for this loop_id.
    Returns (True, None) on success. Returns (False, existing_lock)
    if another loop_id already owns the lock.

    Stale locks (>15 min old) are forcibly released — covers the case
    where a backend process crashed without releasing.
    """
    if db is None:
        return True, None
    now = time.time()
    STALE_S = 15 * 60
    # Sweep stale locks first.
    try:
        await db.loop_locks.delete_many({
            "project_id": project_id,
            "user_id":    user_id,
            "acquired_at": {"$lt": now - STALE_S},
        })
    except Exception as e:                                # noqa: BLE001
        logger.debug("loop_lock stale sweep failed: %r", e)
    # Iter 212m-145 — also sweep locks whose `loop_id` points to a
    # loop that has ALREADY terminated in loop_sessions. This handles
    # the worker-crash / cancel-fallback scenario where the engine
    # never got a chance to call release_loop_lock — instead of making
    # the user wait 15 min for stale_s, we detect the ghost lock and
    # free the project immediately.
    try:
        existing = await db.loop_locks.find_one(
            {"project_id": project_id, "user_id": user_id},
            {"_id": 0},
        )
        if existing and existing.get("loop_id"):
            sess = await db.loop_sessions.find_one(
                {"loop_id": existing["loop_id"]},
                {"_id": 0, "state": 1},
            )
            if sess and (sess.get("state") or "") in (
                "aborted", "failed", "completed",
            ):
                await db.loop_locks.delete_one(
                    {"project_id": project_id, "user_id": user_id,
                     "loop_id": existing["loop_id"]},
                )
                logger.info(
                    "[loop_safety] swept ghost lock for terminated "
                    "loop %s (state=%s)",
                    existing["loop_id"], sess.get("state"),
                )
    except Exception as e:                                # noqa: BLE001
        logger.debug("loop_lock ghost sweep failed: %r", e)
    try:
        await db.loop_locks.insert_one({
            "project_id":  project_id,
            "user_id":     user_id,
            "loop_id":     loop_id,
            "acquired_at": now,
        })
        return True, None
    except Exception:
        existing = await db.loop_locks.find_one(
            {"project_id": project_id, "user_id": user_id},
            {"_id": 0},
        )
        return False, existing


async def release_loop_lock(
    db, project_id: str, user_id: str, loop_id: str,
) -> None:
    if db is None:
        return
    try:
        await db.loop_locks.delete_one({
            "project_id": project_id,
            "user_id":    user_id,
            "loop_id":    loop_id,
        })
    except Exception as e:                                # noqa: BLE001
        logger.warning("loop_lock release failed: %r", e)


async def ensure_loop_lock_index(db) -> None:
    """Create the unique compound index. Idempotent. Called once on
    backend boot."""
    if db is None:
        return
    try:
        await db.loop_locks.create_index(
            [("project_id", 1), ("user_id", 1)],
            unique=True, name="uq_active_loop_per_project_user",
        )
    except Exception as e:                                # noqa: BLE001
        logger.warning("loop_lock index create failed: %r", e)


# ─── 4 & 5. Circuit breaker (3 fails / 15 min) ────────────────────────
FAIL_WINDOW_S = 15 * 60
FAIL_THRESHOLD = 3


async def record_loop_failure(
    db, project_id: str, user_id: str, phase: str, reason: str,
) -> None:
    if db is None:
        return
    try:
        await db.loop_failures.insert_one({
            "project_id":  project_id,
            "user_id":     user_id,
            "phase":       phase,
            "reason":      (reason or "")[:500],
            "occurred_at": time.time(),
        })
    except Exception as e:                                # noqa: BLE001
        logger.warning("loop_failures insert failed: %r", e)


async def is_loop_circuit_open(
    db, project_id: str, user_id: str,
) -> tuple[bool, int, Optional[int]]:
    """Returns (circuit_open, recent_fail_count, retry_after_seconds).
    Circuit is OPEN (refuse new starts) when >=FAIL_THRESHOLD failures
    happened in the last FAIL_WINDOW_S seconds."""
    if db is None:
        return False, 0, None
    cutoff = time.time() - FAIL_WINDOW_S
    try:
        recent = await db.loop_failures.find({
            "project_id":  project_id,
            "user_id":     user_id,
            "occurred_at": {"$gte": cutoff},
        }, {"_id": 0, "occurred_at": 1}).to_list(length=FAIL_THRESHOLD + 5)
    except Exception as e:                                # noqa: BLE001
        logger.warning("loop_failures query failed: %r", e)
        return False, 0, None
    count = len(recent)
    if count >= FAIL_THRESHOLD:
        oldest = min(r["occurred_at"] for r in recent)
        retry_after = max(1, int(FAIL_WINDOW_S - (time.time() - oldest)))
        return True, count, retry_after
    return False, count, None


# ─── Branch-per-fix utilities ────────────────────────────────────────
def aurem_branch_name(kind: str, identifier: str) -> str:
    """Deterministic branch name for fixes/loops.
    Examples:
      aurem_branch_name("fix", "secret_aws") → "aurem/fix-secret_aws-<unix>"
      aurem_branch_name("loop", "abc12345")  → "aurem/loop-abc12345-<unix>"
    """
    import re
    safe = re.sub(r"[^a-zA-Z0-9_-]", "-", identifier or "x")[:40].strip("-") or "x"
    ts = int(time.time())
    return f"aurem/{kind}-{safe}-{ts}"


async def create_or_reuse_branch(
    *, owner: str, repo: str, base_branch: str, new_branch: str, token: str,
) -> tuple[bool, Optional[str]]:
    """Create `new_branch` off the tip of `base_branch`. Returns
    (ok, error). If the branch already exists (e.g. retry), returns
    (True, None) — caller can safely push to it."""
    headers = {
        "Authorization": f"token {token}",
        "Accept":        "application/vnd.github+json",
        "User-Agent":    "aurem-branch-helper",
    }
    base_url = f"https://api.github.com/repos/{owner}/{repo}"
    # Resolve base SHA.
    r = await github_request_with_retry(
        "GET", f"{base_url}/git/refs/heads/{base_branch}", headers=headers,
    )
    if r.status_code != 200:
        return False, f"base_ref_status_{r.status_code}"
    base_sha = (r.json() or {}).get("object", {}).get("sha")
    if not base_sha:
        return False, "base_sha_missing"
    # Create the new ref.
    r2 = await github_request_with_retry(
        "POST", f"{base_url}/git/refs", headers=headers,
        json={"ref": f"refs/heads/{new_branch}", "sha": base_sha},
    )
    if r2.status_code in (200, 201):
        return True, None
    if r2.status_code == 422:                              # already exists
        return True, None
    return False, f"create_branch_status_{r2.status_code}"


async def open_draft_pr(
    *, owner: str, repo: str, head_branch: str, base_branch: str,
    title: str, body: str, token: str,
) -> tuple[Optional[str], Optional[str]]:
    """Open a draft PR head → base. Returns (html_url, error)."""
    headers = {
        "Authorization": f"token {token}",
        "Accept":        "application/vnd.github+json",
        "User-Agent":    "aurem-pr-helper",
    }
    r = await github_request_with_retry(
        "POST", f"https://api.github.com/repos/{owner}/{repo}/pulls",
        headers=headers,
        json={"title": title[:255], "body": body[:65000],
              "head":  head_branch,  "base":  base_branch,
              "draft": True},
    )
    if r.status_code in (200, 201):
        return (r.json() or {}).get("html_url"), None
    return None, f"pr_status_{r.status_code}"
