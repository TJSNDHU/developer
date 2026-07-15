"""
services/repo_heal.py — Iter 212m-126

Auto-heal pipeline kicked off the moment `routers/repo_status` flags
a project as `disconnected`.  Runs entirely in the backend — the
sidebar dots will simply turn green on the next 30 s poll if the
heal succeeded.

Heal strategies by error class (cheapest first, expensive last):

  network: TimeoutException / NetworkError
      → Retry 3× with exponential backoff (0.5 s, 1 s, 2 s).
        Most transient failures resolve in <2 s.

  no_token
      → Patch the project row to attach the user's OAuth
        access_token if available.  PAT slot stays empty so the user
        can still set one explicitly later.

  github_rejected (401 / 403)
      → If the project was using a PAT, retry with the user's OAuth
        token instead.  If that also fails, mark the project's PAT
        as `revoked_at: now` so the user gets a banner to re-auth.

  repo_not_found (404)
      → Hit `GET /repositories?since=` style?  Easier: fetch the
        user's repo list and look for `owner/repo` with a *case-
        insensitive* match — covers the "user renamed the repo" case
        because GitHub serves the new name in the list but the old
        URL 404s without a Location redirect for unauthenticated
        clients.  When we find it, update `github_owner` /
        `github_repo` on the project row to the canonical names.

  repo_not_set
      → Skip — needs user input.

Concurrency / rate-limiting:
  • Per-project cooldown: a project that just healed will NOT be
    re-attempted for 5 minutes (`_HEAL_COOLDOWN_S`).  Prevents heal
    storms when the same status check fires every 30 s.
  • In-flight lock per project: while a heal is running for project
    P, a second concurrent trigger is a no-op.
  • All heals are fire-and-forget `asyncio.create_task` — the caller
    never awaits them.  Outcomes write directly to Mongo +
    `repo_status._CACHE`.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

import httpx

from cto_services.crypto import decrypt

logger = logging.getLogger("aurem-dev.repo_heal")


_HEAL_COOLDOWN_S      = 5 * 60
# Iter 212m-127 — Permanent-failure cooldown. When the heal pipeline
# returns a reason that won't be cured by a 30 s re-poll (e.g. the
# GitHub repo was deleted, the user hasn't connected OAuth yet, or
# the project simply needs user input), we extend the cooldown to
# 30 minutes so we stop hammering GitHub + filling the logs with the
# same "success=False" line every 30 s.  Real recovery (user
# reconnects PAT / re-creates repo) clears the entry via
# `clear_cooldown()` from the project-edit endpoints.
_HEAL_PERMANENT_COOLDOWN_S = 30 * 60
_PERMANENT_FAIL_REASONS = {
    "repo_gone_or_no_access",   # 404 lookup exhausted — repo deleted/private
    "no_oauth_to_attach",       # User never connected GitHub OAuth
    "no_token_for_retry",       # No PAT and no OAuth — needs user input
    "no_token_for_lookup",      # Same as above for 404 branch
    "needs_user_input",         # repo_not_set
    "not_owned",                # Project row doesn't belong to user
    "all_tokens_failed",        # Substring match handled below
}
_RETRY_BACKOFFS       = (0.5, 1.0, 2.0)
_GH_TIMEOUT_S         = 6.0
_last_heal_at: dict[str, float] = {}      # keyed by project_id
_cooldown_until: dict[str, float] = {}    # keyed by project_id (permanent-block)
_inflight:     set[str]         = set()    # project_ids currently healing


def _is_permanent_failure(reason: str) -> bool:
    """Return True if the heal failure won't self-cure by re-polling."""
    if not reason:
        return False
    if reason in _PERMANENT_FAIL_REASONS:
        return True
    # Some reasons are prefixed (`all_tokens_failed (tried: oauth,pat)`).
    for prefix in _PERMANENT_FAIL_REASONS:
        if reason.startswith(prefix):
            return True
    return False


def clear_cooldown(project_id: str) -> None:
    """Called by the project-edit endpoints (token refresh, repo
    re-link) to immediately unblock heal attempts for a project the
    user has just touched."""
    _last_heal_at.pop(project_id, None)
    _cooldown_until.pop(project_id, None)


def _allowed(project_id: str) -> bool:
    """Cooldown + in-flight gate.  Returns True if heal can run now."""
    if project_id in _inflight:
        return False
    # Permanent-failure block takes precedence over the normal cooldown.
    until = _cooldown_until.get(project_id, 0.0)
    if until and time.time() < until:
        return False
    last = _last_heal_at.get(project_id, 0.0)
    return (time.time() - last) >= _HEAL_COOLDOWN_S


async def _decrypt_pat(user_id: str, ct: Optional[str]) -> Optional[str]:
    if not ct:
        return None
    try:
        return await decrypt(user_id, ct, kind="github_token")
    except Exception:
        return None


async def _gh_get(client: httpx.AsyncClient, url: str, token: str) -> httpx.Response:
    return await client.get(
        url, headers={
            "Authorization": f"token {token}",
            "Accept":        "application/vnd.github+json",
            "User-Agent":    "aurem-repo-heal",
        },
    )


async def _try_with_retries(
    fn, *, tries: tuple[float, ...] = _RETRY_BACKOFFS,
) -> tuple[bool, Optional[httpx.Response], Optional[str]]:
    """Run `fn()` (returns httpx.Response) with exponential-backoff
    retries on Timeout/NetworkError.  Returns (ok, response, error)."""
    last_err = None
    for i, backoff in enumerate((0.0, *tries)):
        if backoff:
            await asyncio.sleep(backoff)
        try:
            r = await fn()
            return True, r, None
        except (httpx.TimeoutException, httpx.NetworkError) as e:
            last_err = f"{type(e).__name__}: {e}"
            logger.debug("repo_heal retry %d/%d failed: %s",
                         i + 1, len(tries) + 1, last_err)
    return False, None, last_err


async def heal_project(*, db, user_id: str, project_id: str,
                       prior_status: dict) -> dict:
    """Single-pass heal attempt.  `prior_status` is the dict from
    `_check_one()` that flagged disconnected (provides `error` and
    `auth`).  Returns a result dict describing what was tried."""
    if not _allowed(project_id):
        return {"project_id": project_id, "heal_attempted": False,
                "reason": "cooldown"}

    _inflight.add(project_id)
    try:
        _last_heal_at[project_id] = time.time()
        proj = await db.cto_projects.find_one(
            {"project_id": project_id, "user_id": user_id},
            {"_id": 0, "github_owner": 1, "github_repo": 1,
             "github_token": 1, "name": 1},
        )
        if not proj:
            return {"project_id": project_id, "heal_attempted": False,
                    "reason": "not_owned"}

        owner = (proj.get("github_owner") or "").strip()
        repo  = (proj.get("github_repo")  or "").strip()
        err   = (prior_status or {}).get("error") or ""

        me = await db.dev_users.find_one(
            {"user_id": user_id}, {"_id": 0, "github": 1},
        )
        oauth = ((me or {}).get("github") or {}).get("access_token") or None
        pat   = await _decrypt_pat(user_id, proj.get("github_token"))

        # ── Strategy router ──────────────────────────────────────────
        if err == "repo_not_set":
            return {"project_id": project_id, "heal_attempted": False,
                    "reason": "needs_user_input"}

        async with httpx.AsyncClient(timeout=_GH_TIMEOUT_S) as client:

            # 1) Network errors — pure retry.
            if err.startswith("network:"):
                token = pat or oauth
                if not token:
                    return _finalise(db, project_id, success=False,
                                     reason="no_token_for_retry")
                ok, r, e = await _try_with_retries(
                    lambda: _gh_get(
                        client, f"https://api.github.com/repos/{owner}/{repo}", token,
                    ),
                )
                if ok and r is not None and 200 <= r.status_code < 300:
                    return await _finalise(db, project_id, success=True,
                                           reason="network_retry_recovered")
                return await _finalise(db, project_id, success=False,
                                       reason=f"network_retry_exhausted: {e or (r and r.status_code)}")

            # 2) No token at all — attach OAuth fallback if we have one.
            if err == "no_token":
                if not oauth:
                    return await _finalise(db, project_id, success=False,
                                           reason="no_oauth_to_attach")
                # Verify the OAuth token actually authorises the repo
                # BEFORE we mark the project as healed — don't trade
                # one broken state for another.
                r = await _gh_get(
                    client, f"https://api.github.com/repos/{owner}/{repo}", oauth,
                )
                if 200 <= r.status_code < 300:
                    # Project row already has user OAuth implicitly;
                    # nothing to mutate, just succeed.
                    return await _finalise(db, project_id, success=True,
                                           reason="oauth_fallback_works")
                return await _finalise(db, project_id, success=False,
                                       reason=f"oauth_also_failed: {r.status_code}")

            # 3) Token rejected — swap PAT → OAuth or vice versa.
            if err == "github_rejected":
                # Try the OTHER token first.
                tried = []
                for label, tok in (("oauth", oauth), ("pat", pat)):
                    if not tok:
                        continue
                    if (prior_status or {}).get("auth") == label:
                        continue  # already failed with this one
                    tried.append(label)
                    r = await _gh_get(
                        client, f"https://api.github.com/repos/{owner}/{repo}", tok,
                    )
                    if 200 <= r.status_code < 300:
                        # Mark the OLD token as revoked so the
                        # connection-status endpoint picks the working
                        # one on the next poll.
                        if (prior_status or {}).get("auth") == "pat":
                            await db.cto_projects.update_one(
                                {"project_id": project_id, "user_id": user_id},
                                {"$set": {"github_token": None,
                                          "github_token_revoked_at": time.time()}},
                            )
                        return await _finalise(db, project_id, success=True,
                                               reason=f"{label}_token_works")
                return await _finalise(db, project_id, success=False,
                                       reason=f"all_tokens_failed (tried: {','.join(tried) or 'none'})")

            # 4) 404 — rename / transfer detection.
            if err == "repo_not_found":
                token = pat or oauth
                if not token:
                    return await _finalise(db, project_id, success=False,
                                           reason="no_token_for_lookup")
                # Search the user's accessible repo list for a case-
                # insensitive name match.  The new full_name in the
                # response tells us where the repo moved to.
                target_owner = owner.lower()
                target_repo  = repo.lower()
                page = 1
                found = None
                while page <= 5:           # cap at 5 pages × 100 = 500 repos
                    r = await _gh_get(
                        client,
                        f"https://api.github.com/user/repos?per_page=100&page={page}",
                        token,
                    )
                    if r.status_code != 200:
                        break
                    rows = r.json() or []
                    for row in rows:
                        full = (row.get("full_name") or "").lower()
                        if full == f"{target_owner}/{target_repo}":
                            found = row
                            break
                        # Match just by repo name (handles ownership
                        # transfer where only the owner changed).
                        name = (row.get("name") or "").lower()
                        if name == target_repo:
                            found = row
                            break
                    if found or len(rows) < 100:
                        break
                    page += 1
                if not found:
                    return await _finalise(db, project_id, success=False,
                                           reason="repo_gone_or_no_access")
                new_full = found.get("full_name") or ""
                if "/" in new_full:
                    new_owner, new_repo = new_full.split("/", 1)
                    if (new_owner, new_repo) != (owner, repo):
                        await db.cto_projects.update_one(
                            {"project_id": project_id, "user_id": user_id},
                            {"$set": {"github_owner": new_owner,
                                      "github_repo":  new_repo,
                                      "renamed_from": f"{owner}/{repo}",
                                      "renamed_at":   time.time()}},
                        )
                        return await _finalise(
                            db, project_id, success=True,
                            reason=f"repo_renamed_to:{new_owner}/{new_repo}",
                        )
                # Same name, somehow accessible now — must have been a
                # transient 404 from GitHub.
                return await _finalise(db, project_id, success=True,
                                       reason="repo_accessible_now")

            # Unknown error class — fall back to a single retry.
            token = pat or oauth
            if not token:
                return await _finalise(db, project_id, success=False,
                                       reason=f"unknown_error:{err}")
            r = await _gh_get(
                client, f"https://api.github.com/repos/{owner}/{repo}", token,
            )
            if 200 <= r.status_code < 300:
                return await _finalise(db, project_id, success=True,
                                       reason="single_retry_recovered")
            return await _finalise(db, project_id, success=False,
                                   reason=f"single_retry_failed:{r.status_code}")

    except Exception as e:                                # noqa: BLE001
        logger.exception("heal_project crashed for %s", project_id)
        return {"project_id": project_id, "heal_attempted": True,
                "ok": False, "reason": f"crash: {e}"}
    finally:
        _inflight.discard(project_id)


async def _finalise(db, project_id: str, *, success: bool, reason: str) -> dict:
    """Common landing for every heal outcome — writes an audit row
    and bumps the connection-status cache so the next sidebar poll
    immediately reflects reality (no 30 s wait)."""
    now = time.time()
    try:
        await db.repo_heal_audit.insert_one({
            "project_id":  project_id,
            "success":     success,
            "reason":      reason,
            "healed_at":   now,
        })
    except Exception:
        # Audit collection may not exist on first run — ignore.
        pass
    if success:
        # Invalidate the cache so the next /connection-status call
        # re-fetches and turns the dot green right away.
        try:
            from routers import repo_status as rs  # arch: allow-router-import — auto-heal re-probes the sidebar status endpoint
            rs._CACHE.pop(project_id, None)
        except Exception:
            pass
        # Successful heal also clears any prior permanent-failure block.
        _cooldown_until.pop(project_id, None)
    else:
        # Iter 212m-127 — Stop the heal-spam loop for failures that
        # can't self-cure on the next 30 s poll (deleted repos, no
        # token at all, etc.). Normal 5 min cooldown stays in place
        # for transient / token-rejected paths.
        if _is_permanent_failure(reason):
            _cooldown_until[project_id] = now + _HEAL_PERMANENT_COOLDOWN_S
    logger.info("repo_heal project=%s success=%s reason=%s",
                project_id, success, reason)
    return {"project_id":     project_id,
            "heal_attempted": True,
            "ok":             success,
            "reason":         reason,
            "healed_at":      now}


def schedule_heal(*, db, user_id: str, project_id: str,
                  prior_status: dict) -> None:
    """Fire-and-forget wrapper. Caller never awaits."""
    if not _allowed(project_id):
        return
    # Iter 212m-127 — Mark "last heal" synchronously BEFORE handing
    # off to the event loop. Previously `_last_heal_at` was only set
    # inside `heal_project()` itself, so a second `schedule_heal()`
    # call that landed before the first task started running would
    # still see `_allowed() == True` and spawn a duplicate heal.
    # Setting it here closes that race; `_inflight` then guards the
    # actual run.
    _last_heal_at[project_id] = time.time()
    try:
        asyncio.create_task(
            heal_project(db=db, user_id=user_id,
                         project_id=project_id, prior_status=prior_status),
        )
    except RuntimeError:
        # No running event loop (e.g. test setup) — silently skip.
        pass
