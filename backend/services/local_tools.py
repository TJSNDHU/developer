"""
services/local_tools.py — First-party tools that AUREM CTO can invoke
directly inside the orchestrator (no HTTP roundtrip, no upstream
dependency).

Tools registered here get added to the catalog the LLM sees, and are
short-circuited inside the orchestrator's `invoke_tool` path.

Add a new tool by:
  1. Writing an async fn that takes a ctx dict + the LLM-provided args
  2. Listing it in TOOL_SPECS (catalog) + LOCAL_TOOLS (dispatch table)
"""
from __future__ import annotations

import logging
from typing import Optional

from cto_services.db import get_db
from .repo_context import _fetch_file as _gh_fetch_file

logger = logging.getLogger(__name__)

# Max bytes returned per file → keeps the LLM context budget sane.
MAX_FILE_CHARS = 12000


async def read_repo_file(ctx: dict, args: dict) -> dict:
    """Fetch a single file from the user's connected project repo by path.
    `ctx` is {user_id, project_id}; `args` is {path: str, lines?: [start,end]}.
    Returns {ok, path, content, truncated, project_id} or {ok:False, error}.
    """
    user_id = ctx.get("user_id")
    project_id = ctx.get("project_id")
    path = (args or {}).get("path")
    if not user_id or not project_id or project_id == "home":
        return {"ok": False, "error": "No project is currently connected"}
    if not path or not isinstance(path, str):
        return {"ok": False, "error": "Missing required arg `path`"}
    # Reject obvious traversal / absolute paths
    if path.startswith("/") or ".." in path.split("/"):
        return {"ok": False, "error": "Invalid path"}

    db = get_db()
    if db is None:
        return {"ok": False, "error": "Database unavailable"}
    proj = await db.cto_projects.find_one(
        {"project_id": project_id, "user_id": user_id}
    )
    if not proj:
        return {"ok": False, "error": "Project not found for this user"}

    owner = proj.get("github_owner")
    repo = proj.get("github_repo")
    branch = proj.get("branch") or "main"
    token = proj.get("github_token") or None
    if not owner or not repo:
        return {"ok": False, "error": "Project has no resolved github_owner/repo"}

    content = await _gh_fetch_file(owner, repo, path, branch, token)
    if content is None:
        return {
            "ok": False,
            "error": (
                f"Could not fetch `{path}` from {owner}/{repo}@{branch}. "
                "File may not exist on this branch, or the project PAT lacks "
                "Contents:Read permission."
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
]


# ── Dispatch table ───────────────────────────────────────────────────
LOCAL_TOOLS: dict[str, callable] = {
    "read_repo_file": read_repo_file,
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
