"""
services/local_tools.py — First-party tools that AUREM CTO can invoke
directly inside the orchestrator (no HTTP roundtrip, no upstream
dependency).

Tools registered here get added to the catalog the LLM sees, and are
short-circuited inside the orchestrator's `invoke_tool` path.
"""
from __future__ import annotations

import fnmatch
import logging
from typing import Optional

from cto_services.db import get_db
from .repo_context import _fetch_file as _gh_fetch_file
from .repo_context import _fetch_tree as _gh_fetch_tree

logger = logging.getLogger(__name__)

# Max bytes returned per file → keeps the LLM context budget sane.
MAX_FILE_CHARS = 12000
MAX_LIST_HITS = 80


async def _load_project(ctx: dict) -> tuple[Optional[dict], Optional[dict]]:
    """Helper — returns (project_dict, error_dict). Exactly one is None."""
    user_id = ctx.get("user_id")
    project_id = ctx.get("project_id")
    if not user_id or not project_id or project_id == "home":
        return None, {"ok": False, "error": "No project is currently connected"}
    db = get_db()
    if db is None:
        return None, {"ok": False, "error": "Database unavailable"}
    proj = await db.cto_projects.find_one(
        {"project_id": project_id, "user_id": user_id}
    )
    if not proj:
        return None, {"ok": False, "error": "Project not found for this user"}
    if not proj.get("github_owner") or not proj.get("github_repo"):
        return None, {"ok": False,
                      "error": "Project has no resolved github_owner/repo"}
    return proj, None


async def read_repo_file(ctx: dict, args: dict) -> dict:
    """Fetch a single file from the user's connected project repo by path.
    `ctx` is {user_id, project_id}; `args` is {path: str, lines?: [start,end]}.
    Returns {ok, path, content, truncated, project_id} or {ok:False, error}.
    """
    path = (args or {}).get("path")
    if not path or not isinstance(path, str):
        return {"ok": False, "error": "Missing required arg `path`"}
    # Reject obvious traversal / absolute paths
    if path.startswith("/") or ".." in path.split("/"):
        return {"ok": False, "error": "Invalid path"}

    proj, err = await _load_project(ctx)
    if err:
        return err

    owner = proj["github_owner"]
    repo = proj["github_repo"]
    branch = proj.get("branch") or "main"
    token = proj.get("github_token") or None

    content = await _gh_fetch_file(owner, repo, path, branch, token)
    if content is None:
        return {
            "ok": False,
            "error": (
                f"Could not fetch `{path}` from {owner}/{repo}@{branch}. "
                "File may not exist on this branch, or the project PAT lacks "
                "Contents:Read permission. Tip: call `list_repo_files` with "
                "a glob to discover the real path."
            ),
        }

    # Optional line-range slice so the LLM can ask for a specific chunk
    lines = (args or {}).get("lines")
    if isinstance(lines, list) and len(lines) == 2:
        try:
            start = max(int(lines[0]), 1)
            end = max(int(lines[1]), start)
            split = content.splitlines()
            content = "\n".join(split[start - 1:end])
        except Exception:
            pass

    truncated = False
    if len(content) > MAX_FILE_CHARS:
        content = content[:MAX_FILE_CHARS] + "\n... [truncated by server]"
        truncated = True

    return {
        "ok": True,
        "path": path,
        "branch": branch,
        "truncated": truncated,
        "content": content,
    }


async def list_repo_files(ctx: dict, args: dict) -> dict:
    """Glob the connected repo's file tree to find paths matching a pattern.

    args.pattern (str, required) — fnmatch glob like `pillars/*`, `**/auth*.py`,
                                   `backend/routers/*.py`. Empty/'*' returns
                                   every blob path.
    args.limit   (int, optional) — cap on returned paths, default 80, max 200.

    Returns:
      { ok: True, count, total_blobs, hits: [{path, size}], truncated, branch }
    """
    proj, err = await _load_project(ctx)
    if err:
        return err
    pattern = ((args or {}).get("pattern") or "").strip() or "*"
    try:
        limit = min(max(int((args or {}).get("limit") or MAX_LIST_HITS), 1), 200)
    except (TypeError, ValueError):
        limit = MAX_LIST_HITS

    owner = proj["github_owner"]
    repo = proj["github_repo"]
    branch = proj.get("branch") or "main"
    token = proj.get("github_token") or None

    try:
        tree = await _gh_fetch_tree(owner, repo, branch, token)
    except Exception as e:
        return {"ok": False, "error": f"GitHub tree fetch failed: {e!r}"}

    # fnmatch's `*` already matches across `/`, so we collapse `**` to `*`.
    raw = pattern.replace("**", "*")
    hits: list[dict] = []
    total_blobs = 0
    for node in tree:
        if node.get("type") != "blob":
            continue
        p = node.get("path") or ""
        if not p:
            continue
        total_blobs += 1
        if fnmatch.fnmatch(p, raw):
            if len(hits) < limit:
                hits.append({"path": p, "size": node.get("size") or 0})

    return {
        "ok": True,
        "branch": branch,
        "pattern": pattern,
        "count": len(hits),
        "total_blobs": total_blobs,
        "truncated": len(hits) >= limit,
        "hits": hits,
    }


# ── Catalog the LLM sees ─────────────────────────────────────────────
TOOL_SPECS: list[dict] = [
    {
        "name": "read_repo_file",
        "description": (
            "Fetch the FULL TEXT of any file in the user's connected GitHub "
            "repo by path. Use this whenever you need to verify a bug claim, "
            "read code you haven't seen, or quote actual lines. "
            "Strongly preferred over asking the user to paste."
        ),
        "args_spec": {
            "path": "string — repo-relative path, e.g. 'routers/auth.py'",
            "lines": "optional [start,end] line range, 1-indexed inclusive",
        },
    },
    {
        "name": "list_repo_files",
        "description": (
            "Glob the connected repo's file tree to find paths matching a "
            "pattern. Use this FIRST whenever the user mentions a folder, "
            "feature, or pattern you don't already see in the inlined tree "
            "(e.g. 'pillar 4' → list_repo_files(pattern='**/pillar*') "
            "or 'pillars/**'). Then call read_repo_file in parallel for "
            "every relevant hit. NEVER tell the user a folder doesn't "
            "exist without calling this tool first."
        ),
        "args_spec": {
            "pattern": "string — fnmatch glob like 'pillars/*', '**/auth*.py', "
                       "'backend/routers/*.py'. Use '**' as recursive wildcard.",
            "limit":   "optional int (default 80, max 200) — cap on returned paths",
        },
    },
]


# ── Dispatch table ───────────────────────────────────────────────────
LOCAL_TOOLS: dict[str, callable] = {
    "read_repo_file":  read_repo_file,
    "list_repo_files": list_repo_files,
}


async def invoke_local_tool(name: str, args: dict, ctx: dict) -> Optional[dict]:
    """Run a local tool. Returns None if `name` isn't a local tool."""
    fn = LOCAL_TOOLS.get(name)
    if not fn:
        return None
    try:
        return await fn(ctx, args or {})
    except Exception as e:
        logger.exception(f"local tool {name} crashed")
        return {"ok": False, "error": str(e)}
