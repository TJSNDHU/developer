"""
services/github_api_writer.py — Push commits to GitHub via REST API only,
no `git` binary required. This is the production-friendly fallback used
when the container doesn't have git installed (Iter 21).

Public surface:
    commit_files(...)  → push a multi-file commit atomically
    revert_commit(...) → push a revert commit (no force-push)
    fetch_file(...)    → read a file from a specific ref

All operations preserve full git history. We use the Git Data API
(blobs / trees / commits / refs) so a single multi-file change lands
as ONE atomic commit, just like local git would do it.

Iter 22 — every multi-file step (blob upload, file fetch) runs in
parallel via asyncio.gather, so a 10-file commit takes ~1-2s instead
of 10s.
"""
from __future__ import annotations

import asyncio
import base64
import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"
# httpx connection pool — bump limits so parallel calls actually parallel
_LIMITS = httpx.Limits(max_connections=20, max_keepalive_connections=20)


def _headers(token: str) -> dict:
    h = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


async def fetch_file(client: httpx.AsyncClient, owner: str, repo: str,
                      path: str, ref: str, token: str) -> Optional[str]:
    """Return file text at `ref` or None if missing."""
    url = f"{GITHUB_API}/repos/{owner}/{repo}/contents/{path}?ref={ref}"
    try:
        r = await client.get(url, headers=_headers(token))
        if r.status_code == 404:
            return None
        r.raise_for_status()
        data = r.json()
        if data.get("encoding") != "base64":
            return None
        return base64.b64decode(data.get("content", "")).decode(
            "utf-8", errors="replace"
        )
    except Exception as e:
        logger.debug(f"fetch_file {path}@{ref} failed: {e!r}")
        return None


async def _get_branch_head(client: httpx.AsyncClient, owner: str, repo: str,
                            branch: str, token: str) -> dict:
    """Return {sha, tree_sha} for branch head commit."""
    r = await client.get(
        f"{GITHUB_API}/repos/{owner}/{repo}/git/ref/heads/{branch}",
        headers=_headers(token),
    )
    r.raise_for_status()
    head_sha = r.json()["object"]["sha"]
    r2 = await client.get(
        f"{GITHUB_API}/repos/{owner}/{repo}/git/commits/{head_sha}",
        headers=_headers(token),
    )
    r2.raise_for_status()
    return {"sha": head_sha, "tree_sha": r2.json()["tree"]["sha"]}


async def commit_files(
    owner: str, repo: str, branch: str, token: str,
    files: dict[str, str], commit_message: str,
    author_email: str,
    author_name: str,
    progress=None,
) -> dict:
    """Atomically commit `files = {path: content}` to `branch`.
    Returns {ok, sha, html_url} on success or raises.

    `progress(step, status)` is called for each phase so callers can
    stream UI updates (same shape as _log in cto_projects.py).

    Iter 212m-218 — `author_name` and `author_email` are REQUIRED
    keyword arguments (no defaults).  Every caller MUST resolve the
    real developer identity via `services.git_identity.resolve_git_identity`
    before invoking this writer.  Hardcoded `AUREM CTO <cto@auremcto.com>`
    defaults were removed so a lazy caller can't accidentally push
    bot-attributed commits again.
    """
    if not author_name or not author_email:
        raise ValueError(
            "commit_files requires non-empty author_name and author_email — "
            "use services.git_identity.resolve_git_identity() to fetch them"
        )

    async def _p(step: str, status: str = "info"):
        if progress is not None:
            await progress(step, status)

    async with httpx.AsyncClient(timeout=60.0, limits=_LIMITS) as client:
        await _p(f"📡 Reading branch head ({branch})…")
        head = await _get_branch_head(client, owner, repo, branch, token)
        await _p(f"✅ HEAD @ {head['sha'][:7]}", "success")

        # 1. Upload every file as a blob IN PARALLEL → returns sha each
        await _p(f"📦 Uploading {len(files)} blob(s) in parallel…")

        async def _upload(path: str, content: str) -> dict:
            r = await client.post(
                f"{GITHUB_API}/repos/{owner}/{repo}/git/blobs",
                headers=_headers(token),
                json={
                    "content": base64.b64encode(
                        content.encode("utf-8")
                    ).decode("ascii"),
                    "encoding": "base64",
                },
            )
            r.raise_for_status()
            return {
                "path": path,
                "mode": "100644",
                "type": "blob",
                "sha": r.json()["sha"],
            }

        blob_specs = await asyncio.gather(*[
            _upload(p, c) for p, c in files.items()
        ])
        for spec in blob_specs:
            await _p(f"   blob {spec['path']} → {spec['sha'][:7]}")

        # 2. Build a new tree based on the previous head's tree
        await _p("🌳 Building tree…")
        r = await client.post(
            f"{GITHUB_API}/repos/{owner}/{repo}/git/trees",
            headers=_headers(token),
            json={
                "base_tree": head["tree_sha"],
                "tree": blob_specs,
            },
        )
        r.raise_for_status()
        new_tree_sha = r.json()["sha"]
        await _p(f"   tree → {new_tree_sha[:7]}", "success")

        # 3. Create the commit object pointing at the new tree
        await _p("🧾 Creating commit…")
        r = await client.post(
            f"{GITHUB_API}/repos/{owner}/{repo}/git/commits",
            headers=_headers(token),
            json={
                "message": commit_message,
                "tree": new_tree_sha,
                "parents": [head["sha"]],
                "author": {"name": author_name, "email": author_email},
            },
        )
        r.raise_for_status()
        new_commit_sha = r.json()["sha"]

        # 4. Advance the branch ref to the new commit (NOT a force-push;
        # we set it to a descendant of HEAD)
        await _p("🚀 Pushing ref…")
        r = await client.patch(
            f"{GITHUB_API}/repos/{owner}/{repo}/git/refs/heads/{branch}",
            headers=_headers(token),
            json={"sha": new_commit_sha, "force": False},
        )
        r.raise_for_status()

        html_url = (
            f"https://github.com/{owner}/{repo}/commit/{new_commit_sha}"
        )
        await _p(f"✅ {new_commit_sha[:7]}", "success")
        return {"ok": True, "sha": new_commit_sha[:7],
                "full_sha": new_commit_sha, "html_url": html_url}


async def revert_commit(
    owner: str, repo: str, branch: str, token: str,
    commit_sha: str, commit_message: Optional[str] = None,
    author_email: str = "",
    author_name: str = "",
    progress=None,
) -> dict:
    """Push a non-destructive revert of `commit_sha` to `branch`.
    Strategy: for every file in that commit's tree, restore the version
    from the commit's PARENT (or delete it if it didn't exist before).
    Then push that as a NEW commit on top of HEAD.

    Iter 212m-218 — `author_name` / `author_email` should be passed
    by callers via `services.git_identity.resolve_git_identity`.  For
    backward-compat we still accept empty strings (some callers
    revert on a background timer and don't have a user context) and
    fall back to a synthetic identity in that path.
    """
    if not author_name:
        author_name = "AUREM Auto-Revert"
    if not author_email:
        author_email = "aurem-revert@users.noreply.github.com"

    async def _p(step: str, status: str = "info"):
        if progress is not None:
            await progress(step, status)

    async with httpx.AsyncClient(timeout=60.0, limits=_LIMITS) as client:
        await _p(f"📡 Loading commit {commit_sha[:7]}…")
        r = await client.get(
            f"{GITHUB_API}/repos/{owner}/{repo}/commits/{commit_sha}",
            headers=_headers(token),
        )
        r.raise_for_status()
        commit = r.json()
        if not commit.get("parents"):
            raise RuntimeError("Cannot revert a root commit (no parent)")
        parent_sha = commit["parents"][0]["sha"]
        await _p(f"   parent @ {parent_sha[:7]}", "success")

        # PARALLEL: for every changed file, fetch the parent's version
        # (or mark for deletion if file was added in the reverted commit).
        changed = [
            (f.get("filename"), f.get("status"))
            for f in (commit.get("files") or [])
            if f.get("filename")
        ]

        async def _restore_spec(path: str, status: str):
            if status == "added":
                # File didn't exist before → deletion (sha=None in tree)
                return path, None
            body = await fetch_file(client, owner, repo, path, parent_sha, token)
            return path, body

        restored = await asyncio.gather(*[
            _restore_spec(p, s) for p, s in changed
        ])
        files_to_restore: dict[str, Optional[str]] = dict(restored)
        await _p(
            f"   {len(files_to_restore)} file(s) to restore (parallel)",
            "success",
        )

        # Get current HEAD to commit on top of (not the commit being reverted)
        head = await _get_branch_head(client, owner, repo, branch, token)

        # PARALLEL: build blobs for non-delete restorations
        async def _build_spec(path: str, content: Optional[str]) -> dict:
            if content is None:
                return {"path": path, "mode": "100644",
                        "type": "blob", "sha": None}
            r = await client.post(
                f"{GITHUB_API}/repos/{owner}/{repo}/git/blobs",
                headers=_headers(token),
                json={
                    "content": base64.b64encode(
                        content.encode("utf-8")
                    ).decode("ascii"),
                    "encoding": "base64",
                },
            )
            r.raise_for_status()
            return {"path": path, "mode": "100644",
                    "type": "blob", "sha": r.json()["sha"]}

        blob_specs = await asyncio.gather(*[
            _build_spec(p, c) for p, c in files_to_restore.items()
        ])

        # Build the revert tree based on current HEAD
        r = await client.post(
            f"{GITHUB_API}/repos/{owner}/{repo}/git/trees",
            headers=_headers(token),
            json={
                "base_tree": head["tree_sha"],
                "tree": blob_specs,
            },
        )
        r.raise_for_status()
        new_tree_sha = r.json()["sha"]

        msg = commit_message or (
            f'chore: revert "{commit_sha[:7]}" [via ORA]\n\n'
            f"Automated revert triggered by ORA safety pipeline.\n\n"
            f"Co-authored-by: ORA by Aurem CTO <cto@auremcto.com>"
        )
        r = await client.post(
            f"{GITHUB_API}/repos/{owner}/{repo}/git/commits",
            headers=_headers(token),
            json={
                "message": msg,
                "tree": new_tree_sha,
                "parents": [head["sha"]],
                "author": {"name": author_name, "email": author_email},
            },
        )
        r.raise_for_status()
        new_commit_sha = r.json()["sha"]

        r = await client.patch(
            f"{GITHUB_API}/repos/{owner}/{repo}/git/refs/heads/{branch}",
            headers=_headers(token),
            json={"sha": new_commit_sha, "force": False},
        )
        r.raise_for_status()

        await _p(f"✅ revert {new_commit_sha[:7]}", "success")
        return {"ok": True, "sha": new_commit_sha[:7],
                "full_sha": new_commit_sha}
