"""
services/repo_context.py — Fetch a lightweight "what's in this repo" briefing
from GitHub so the chat LLM can answer questions about the connected project
without us having to clone the entire repo.

Strategy:
  1. GET /repos/{owner}/{repo}/git/trees/{branch}?recursive=1 — full file tree
  2. Filter to a set of "high-signal" filenames (README, package.json, entry
     points, config files) and fetch their contents one by one
  3. Cap at MAX_FILES files / MAX_CHARS total / MAX_FILE_CHARS per-file
  4. Return one human-readable text blob ready to splice into the LLM
     system prompt
  5. Cache the blob per project_id in MongoDB for CACHE_TTL_SECONDS so the
     same chat session doesn't re-fetch on every turn

If the GitHub call fails (bad PAT, private repo, network), we return a
short note instead of crashing the chat.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

import httpx

from cto_services.db import get_db

logger = logging.getLogger(__name__)

# ── Tunables ─────────────────────────────────────────────────────────────
CACHE_TTL_SECONDS = 30 * 60       # 30 min — refetch if older
MAX_FILES = 10                    # at most 10 file-contents inlined
MAX_FILE_CHARS = 3000             # truncate each file at 3KB
MAX_TOTAL_CHARS = 15000           # total budget across all inlined files
MAX_TREE_ENTRIES = 400            # how many paths to list in the tree

# Files we want to inline if present — checked in this priority order.
_PRIORITY_FILES = [
    "README.md", "README.rst", "README", "readme.md",
    "package.json", "requirements.txt", "pyproject.toml",
    "Cargo.toml", "go.mod", "Gemfile", "composer.json",
    ".env.example", "env.example",
    "main.py", "app.py", "server.py", "manage.py",
    "index.html", "index.js", "index.ts",
    "src/index.js", "src/index.ts", "src/main.js", "src/main.ts",
    "src/main.jsx", "src/main.tsx", "src/App.jsx", "src/App.tsx",
    "src/app.py", "src/server.py",
    "next.config.js", "vite.config.js", "vite.config.ts",
    "tailwind.config.js", "tsconfig.json",
    "Dockerfile", "docker-compose.yml", "docker-compose.yaml",
    "Makefile",
]


def _gh_headers(token: Optional[str]) -> dict:
    h = {"Accept": "application/vnd.github+json",
         "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


async def _fetch_tree(owner: str, repo: str, branch: str,
                       token: Optional[str]) -> list[dict]:
    """Return the flat recursive tree (list of {path, type, size, sha})."""
    url = (
        f"https://api.github.com/repos/{owner}/{repo}/git/trees/"
        f"{branch}?recursive=1"
    )
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        r = await client.get(url, headers=_gh_headers(token))
        r.raise_for_status()
        data = r.json()
        return data.get("tree") or []


async def _fetch_file(owner: str, repo: str, path: str, branch: str,
                       token: Optional[str]) -> Optional[str]:
    """Return the decoded text content of a file, or None on any failure."""
    url = (
        f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"
        f"?ref={branch}"
    )
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            r = await client.get(url, headers=_gh_headers(token))
            r.raise_for_status()
            data = r.json()
            # GitHub returns content base64-encoded
            if data.get("encoding") != "base64":
                return None
            import base64
            raw = base64.b64decode(data.get("content", "") or "")
            return raw.decode("utf-8", errors="replace")
    except Exception as e:
        logger.debug(f"fetch_file failed for {path}: {e!r}")
        return None


def _format_tree(tree: list[dict]) -> str:
    """One path per line. Trim binary / huge stuff, mark directories with /."""
    rows = []
    for node in tree[:MAX_TREE_ENTRIES]:
        t = node.get("type")
        p = node.get("path", "")
        if not p:
            continue
        if t == "tree":
            rows.append(f"{p}/")
        else:
            sz = node.get("size") or 0
            rows.append(f"{p}  ({sz}b)")
    if len(tree) > MAX_TREE_ENTRIES:
        rows.append(f"... +{len(tree) - MAX_TREE_ENTRIES} more entries")
    return "\n".join(rows)


def _pick_files_to_inline(tree: list[dict]) -> list[str]:
    """Pick up to MAX_FILES paths to inline, based on _PRIORITY_FILES."""
    present = {n["path"] for n in tree if n.get("type") == "blob"}
    picks: list[str] = []
    for cand in _PRIORITY_FILES:
        if cand in present and cand not in picks:
            picks.append(cand)
        if len(picks) >= MAX_FILES:
            break
    return picks


async def _build_blob(project: dict) -> str:
    """Build the full repo briefing text from scratch (no cache lookup)."""
    owner = project.get("github_owner") or ""
    repo = project.get("github_repo") or ""
    branch = project.get("branch") or "main"
    token = project.get("github_token") or None

    try:
        tree = await _fetch_tree(owner, repo, branch, token)
    except httpx.HTTPStatusError as e:
        status = e.response.status_code
        if status == 404:
            note = (
                f"(could not load repo tree — 404. "
                f"Check that branch `{branch}` exists on {owner}/{repo} and "
                f"that the saved PAT has access.)"
            )
        elif status == 401:
            note = (
                "(GitHub rejected the project's PAT — 401 Unauthorized. "
                "Open Projects → Edit and paste a fresh fine-grained PAT "
                "with `Contents: Read` access for this repo.)"
            )
        else:
            note = f"(GitHub error {status} fetching repo tree.)"
        return _wrap(owner, repo, branch, "", "", note)
    except Exception as e:
        logger.warning(f"build_repo_context tree fetch failed: {e!r}")
        return _wrap(owner, repo, branch, "", "",
                     "(repo tree unavailable — proceed with limited context.)")

    tree_text = _format_tree(tree)

    # Inline a few high-signal files
    picks = _pick_files_to_inline(tree)
    inlined: list[tuple[str, str]] = []
    used = 0
    for path in picks:
        if used >= MAX_TOTAL_CHARS:
            break
        body = await _fetch_file(owner, repo, path, branch, token)
        if body is None:
            continue
        if len(body) > MAX_FILE_CHARS:
            body = body[:MAX_FILE_CHARS] + "\n... [truncated]"
        used += len(body)
        inlined.append((path, body))

    inlined_text = "\n\n".join(
        f"--- {p} ---\n{b}" for p, b in inlined
    ) if inlined else "(no priority files inlined)"

    return _wrap(owner, repo, branch, tree_text, inlined_text, "")


def _wrap(owner: str, repo: str, branch: str,
           tree_text: str, inlined_text: str, note: str) -> str:
    """Compose the final system-prompt block."""
    parts = [
        "=== CONNECTED REPO CONTEXT ===",
        f"You are scoped to: {owner}/{repo}@{branch}",
        "You DO have read access to this user's repo via GitHub's API. "
        "Below is the actual file tree and the contents of key files. "
        "Answer the user's questions about this repo using ONLY this real "
        "data — never tell them you can't access their repo.",
        "",
    ]
    if note:
        parts.append(note)
        parts.append("")
    if tree_text:
        parts.append("--- file tree ---")
        parts.append(tree_text)
        parts.append("")
    if inlined_text:
        parts.append("--- key file contents ---")
        parts.append(inlined_text)
    parts.append("=== END REPO CONTEXT ===")
    return "\n".join(parts)


# ── Cached entry point used by chat router ───────────────────────────────
async def get_repo_context(user_id: str, project_id: str) -> str:
    """Return cached or freshly-built repo context blob for a project.
    Returns empty string if the project doesn't exist for this user."""
    db = get_db()
    if db is None or not project_id or project_id == "home":
        return ""
    proj = await db.cto_projects.find_one(
        {"project_id": project_id, "user_id": user_id}
    )
    if not proj:
        return ""

    # Check cache first
    cache = await db.repo_contexts.find_one({"project_id": project_id})
    now = time.time()
    if cache and (now - (cache.get("ts") or 0)) < CACHE_TTL_SECONDS:
        return cache.get("blob") or ""

    blob = await _build_blob(proj)
    try:
        await db.repo_contexts.update_one(
            {"project_id": project_id},
            {"$set": {"project_id": project_id, "blob": blob, "ts": now}},
            upsert=True,
        )
    except Exception as e:
        logger.warning(f"repo_context cache save failed: {e!r}")
    return blob


async def invalidate_repo_context(project_id: str) -> None:
    """Drop the cached blob — call after PATCH (PAT/branch changed)."""
    db = get_db()
    if db is None or not project_id:
        return
    try:
        await db.repo_contexts.delete_one({"project_id": project_id})
    except Exception as e:
        logger.debug(f"invalidate_repo_context failed: {e!r}")
