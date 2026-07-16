"""
services/github_org_client.py — Iter 212m-232 — Phase 2

GitHub API client scoped to the AUREM-owned organisation (`aurem-apps`
or whatever the founder configures via env). Used by Personal Track's
`materialize` step to create fresh private repos on AUREM's behalf
without ever touching the end-user's GitHub account.

WHY a separate client (vs. re-using the per-user PAT clients):
  • Different auth identity — a platform-scoped GitHub App token,
    NOT a personal PAT (founder's or user's).
  • Different rate limit bucket — org-scoped requests count against
    the org's 5,000/h, not the founder's 5,000/h.
  • Different audit trail — every commit shows `aurem-apps[bot]` in
    the GitHub UI, not a human name.

Configuration
=============
    backend/.env:
        AUREM_ORG_NAME              = "aurem-apps"
        AUREM_ORG_GITHUB_APP_TOKEN  = "ghs_...  or  github_pat_..."
        AUREM_ORG_DEFAULT_BRANCH    = "main"       (optional, default main)

If either name or token is missing, every function returns a structured
`{"ok": False, "reason": "aurem_org_not_configured", ...}` — the router
turns that into a clean HTTP 503 with setup instructions rather than a
raw stack trace.

Public API
==========
    is_configured() -> bool
    create_org_repo(name, description, private=True) -> dict
    push_file(repo_name, path, content, message) -> dict
    push_files_bulk(repo_name, files, commit_message) -> dict
    delete_org_repo(repo_name) -> dict     # cleanup for aborted materializations
"""
# arch: allow-http — GitHub API is this module's entire purpose (iter 212m-232)
from __future__ import annotations

import base64
import logging
import os
import re
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_API_ROOT = "https://api.github.com"
_TIMEOUT = 20.0


# ── Config helpers ─────────────────────────────────────────────────
def _org_name() -> str:
    return (os.environ.get("AUREM_ORG_NAME") or "").strip()


def _org_token() -> str:
    return (os.environ.get("AUREM_ORG_GITHUB_APP_TOKEN") or "").strip()


def _default_branch() -> str:
    return (os.environ.get("AUREM_ORG_DEFAULT_BRANCH") or "main").strip() or "main"


def is_configured() -> bool:
    """True when both env vars are set. Router should short-circuit
    to a 503 with setup instructions when this is False."""
    return bool(_org_name() and _org_token())


def _headers() -> dict:
    return {
        "Authorization":        f"Bearer {_org_token()}",
        "Accept":               "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent":           "AUREM-CTO/personal-track",
    }


# ── Repo naming: enforce GitHub's rules AUP-safe ──────────────────
_SLUG_RX = re.compile(r"[^a-zA-Z0-9._-]+")


def sanitize_repo_name(raw: str) -> str:
    """Convert a free-form name to a valid GitHub repo slug.
    - lowercase
    - alphanumeric + `.-_` only
    - collapse runs of non-allowed chars into a single `-`
    - trim leading/trailing separators
    - cap at 90 chars (GitHub allows 100, we leave room for suffixes)"""
    s = _SLUG_RX.sub("-", (raw or "").lower()).strip("-._")
    return (s or "personal-track-app")[:90]


# ── Core operations ────────────────────────────────────────────────
async def create_org_repo(
    name: str,
    description: str = "",
    private: bool = True,
) -> dict:
    """Create a new private repo under the AUREM org.

    Returns:
        {"ok": True, "full_name", "html_url", "default_branch", "clone_url", "id"}
        or {"ok": False, "reason", "detail"}.

    Rate-limit / collision handling: if the repo already exists (422),
    the caller should retry with a suffix — not automatic here so the
    router can log which draft ran into a collision.
    """
    if not is_configured():
        return {
            "ok": False, "reason": "aurem_org_not_configured",
            "detail": "Set AUREM_ORG_NAME and AUREM_ORG_GITHUB_APP_TOKEN "
                      "in backend/.env then restart the backend.",
        }

    slug = sanitize_repo_name(name)
    payload = {
        "name":            slug,
        "description":     (description or "")[:350],
        "private":         bool(private),
        "auto_init":       True,          # gives us an initial commit + main branch
        "has_issues":      False,
        "has_projects":    False,
        "has_wiki":        False,
    }

    async with httpx.AsyncClient(timeout=_TIMEOUT) as cli:
        r = await cli.post(
            f"{_API_ROOT}/orgs/{_org_name()}/repos",
            headers=_headers(), json=payload,
        )
    if r.status_code in (201, 200):
        d = r.json()
        logger.info("[org-repo] created: %s", d.get("full_name"))
        return {
            "ok":             True,
            "full_name":      d.get("full_name"),
            "name":           d.get("name"),
            "html_url":       d.get("html_url"),
            "clone_url":      d.get("clone_url"),
            "default_branch": d.get("default_branch") or _default_branch(),
            "id":             d.get("id"),
        }
    return {
        "ok":       False,
        "reason":   f"github_{r.status_code}",
        "detail":   r.text[:400],
        "slug":     slug,
    }


async def push_file(
    repo_name: str,
    path: str,
    content: str,
    message: str,
    branch: Optional[str] = None,
) -> dict:
    """Create OR update a single file via the Contents API.

    Idempotent by design: if the path already exists on the branch,
    GitHub returns 422 without a `sha`; we fetch the current sha and
    retry with it (update instead of create). This lets Phase 2 push
    the same draft twice (e.g. after a regenerate → materialize race)
    without a 422 permanently poisoning the flow.
    """
    if not is_configured():
        return {"ok": False, "reason": "aurem_org_not_configured"}
    br = branch or _default_branch()
    url = f"{_API_ROOT}/repos/{_org_name()}/{repo_name}/contents/{path.lstrip('/')}"
    b64 = base64.b64encode((content or "").encode("utf-8")).decode()

    async def _try(with_sha: Optional[str]) -> dict:
        body = {"message": message[:120] or "add file", "content": b64, "branch": br}
        if with_sha:
            body["sha"] = with_sha
        async with httpx.AsyncClient(timeout=_TIMEOUT) as cli:
            r = await cli.put(url, headers=_headers(), json=body)
        return {"status": r.status_code, "json": r.json() if r.text else {}}

    res = await _try(None)
    if res["status"] in (200, 201):
        return {"ok": True, "path": path,
                "commit": (res["json"].get("commit") or {}).get("sha")}

    # Retry with sha if the file exists (create → conflict → update).
    if res["status"] == 422:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as cli:
            g = await cli.get(url + f"?ref={br}", headers=_headers())
        if g.status_code == 200 and g.json().get("sha"):
            res2 = await _try(g.json()["sha"])
            if res2["status"] in (200, 201):
                return {"ok": True, "path": path, "updated": True,
                        "commit": (res2["json"].get("commit") or {}).get("sha")}
            return {"ok": False, "path": path, "reason": f"update_failed_{res2['status']}",
                    "detail": str(res2["json"])[:300]}

    return {"ok": False, "path": path, "reason": f"github_{res['status']}",
            "detail": str(res["json"])[:300]}


async def push_files_bulk(
    repo_name: str,
    files: list[dict],
    commit_message: str = "Initial scaffold from AUREM CTO",
    branch: Optional[str] = None,
) -> dict:
    """Push many files to a repo. Sequential (respects GitHub's 5,000/h
    limit, which is plenty for a <20-file draft).

    Returns per-file results + a rollup `all_ok`.

    NOTE: Does NOT do a single atomic tree/commit for now — that
    optimisation ships in Phase 2.5 when we start scaffolding
    50+ file projects. For a 6-file react-fastapi draft, sequential
    PUTs finish in ~2-3 seconds and are far simpler to reason about.
    """
    if not is_configured():
        return {"ok": False, "reason": "aurem_org_not_configured",
                "results": []}

    results = []
    for f in files or []:
        r = await push_file(
            repo_name=repo_name,
            path=f.get("path", ""),
            content=f.get("content", ""),
            message=commit_message,
            branch=branch,
        )
        results.append(r)

    all_ok = all(r.get("ok") for r in results)
    return {
        "ok":       all_ok,
        "results":  results,
        "pushed":   sum(1 for r in results if r.get("ok")),
        "failed":   sum(1 for r in results if not r.get("ok")),
    }


async def delete_org_repo(repo_name: str) -> dict:
    """Cleanup helper — used when a materialization partially fails and
    we need to unwind. Idempotent (404 is treated as success)."""
    if not is_configured():
        return {"ok": False, "reason": "aurem_org_not_configured"}
    async with httpx.AsyncClient(timeout=_TIMEOUT) as cli:
        r = await cli.delete(
            f"{_API_ROOT}/repos/{_org_name()}/{repo_name}",
            headers=_headers(),
        )
    if r.status_code in (204, 404):
        return {"ok": True, "deleted": r.status_code == 204}
    return {"ok": False, "reason": f"github_{r.status_code}", "detail": r.text[:200]}


__all__ = [
    "is_configured", "sanitize_repo_name",
    "create_org_repo", "push_file", "push_files_bulk", "delete_org_repo",
]
