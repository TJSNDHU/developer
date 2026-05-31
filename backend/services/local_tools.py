"""
services/local_tools.py — First-party tools for AUREM CTO orchestrator.

Iter 35 — Major upgrade to close the Emergent capability gap:

NEW TOOLS ADDED:
  read_repo_file      → read single file (existed)
  read_repo_files     → read UP TO 6 files IN PARALLEL (new — asyncio.gather)
  list_repo_files     → list repo tree, filterable by path/extension (new)
  search_repo         → grep pattern across all files in repo (new)
  read_multiple_lines → read specific line ranges from multiple files (new)

These 4 new tools bring AUREM CTO from 1 local tool to 5, closing the
biggest practical gap vs Emergent (which can read 20 files at once via
mcp_view_bulk + mcp_glob_files pattern).

With parallel reads: a 6-file security fix that previously needed 6 tool
call iterations (6 × ~30s = 3 min wait) now takes 1 iteration (~30s).
"""
from __future__ import annotations

import asyncio
import fnmatch
import logging
from typing import Optional

from cto_services.db import get_db
from .repo_context import _fetch_file as _gh_fetch_file

logger = logging.getLogger(__name__)

MAX_FILE_CHARS = 12_000   # per file
MAX_FILES_BULK = 6        # max files in one read_repo_files call


# ── Helper: resolve project from DB ──────────────────────────────────────────

async def _resolve_project(user_id: str, project_id: str) -> dict | None:
    """Return project doc or None if not found."""
    if not user_id or not project_id or project_id == "home":
        return None
    db = get_db()
    if db is None:
        return None
    return await db.cto_projects.find_one(
        {"project_id": project_id, "user_id": user_id}
    )


def _slice_content(content: str, lines: list | None, max_chars: int) -> tuple[str, bool]:
    """Apply optional line-range slice, then hard-truncate. Returns (content, truncated)."""
    if isinstance(lines, list) and len(lines) == 2:
        try:
            start = max(int(lines[0]), 1)
            end   = max(int(lines[1]), start)
            content = "\n".join(content.splitlines()[start - 1:end])
        except Exception:
            pass
    truncated = len(content) > max_chars
    if truncated:
        content = content[:max_chars] + "\n... [truncated — use lines=[start,end] to read a specific range]"
    return content, truncated


# ── TOOL 1: read_repo_file (single file) ─────────────────────────────────────

async def read_repo_file(ctx: dict, args: dict) -> dict:
    """Fetch one file from the connected repo.
    args: {path: str, lines?: [start, end]}
    """
    user_id    = ctx.get("user_id")
    project_id = ctx.get("project_id")
    path       = (args or {}).get("path")

    if not path or not isinstance(path, str):
        return {"ok": False, "error": "Missing required arg `path`"}
    if path.startswith("/") or ".." in path.split("/"):
        return {"ok": False, "error": "Invalid path — no absolute paths or traversal"}

    proj = await _resolve_project(user_id, project_id)
    if not proj:
        return {"ok": False, "error": "No project connected or project not found"}

    owner  = proj.get("github_owner")
    repo   = proj.get("github_repo")
    branch = proj.get("branch") or "main"
    token  = proj.get("github_token") or None

    if not owner or not repo:
        return {"ok": False, "error": "Project has no resolved github_owner/repo"}

    content = await _gh_fetch_file(owner, repo, path, branch, token)
    if content is None:
        return {
            "ok":    False,
            "error": (
                f"Could not fetch `{path}` from {owner}/{repo}@{branch}. "
                "File may not exist or PAT lacks Contents:Read permission."
            ),
        }

    content, truncated = _slice_content(content, (args or {}).get("lines"), MAX_FILE_CHARS)
    return {"ok": True, "path": path, "branch": branch, "truncated": truncated, "content": content}


# ── TOOL 2: read_repo_files (parallel multi-file) ────────────────────────────

async def read_repo_files(ctx: dict, args: dict) -> dict:
    """Fetch UP TO 6 files from the connected repo IN PARALLEL.
    This is the Emergent-equivalent of mcp_view_bulk.

    args: {paths: [str, ...], lines?: [start, end]}  — lines applied to all

    Returns {ok, files: [{path, content, ok, error?}], errors: [...]}
    """
    user_id    = ctx.get("user_id")
    project_id = ctx.get("project_id")
    paths      = (args or {}).get("paths") or []
    line_range = (args or {}).get("lines")

    if not isinstance(paths, list) or not paths:
        return {"ok": False, "error": "Missing required arg `paths` (list of strings)"}

    # Deduplicate, cap at MAX_FILES_BULK
    paths = list(dict.fromkeys(p for p in paths if isinstance(p, str) and p))[:MAX_FILES_BULK]

    proj = await _resolve_project(user_id, project_id)
    if not proj:
        return {"ok": False, "error": "No project connected or project not found"}

    owner  = proj.get("github_owner")
    repo   = proj.get("github_repo")
    branch = proj.get("branch") or "main"
    token  = proj.get("github_token") or None

    if not owner or not repo:
        return {"ok": False, "error": "Project has no resolved github_owner/repo"}

    # Fetch all files concurrently
    async def _fetch_one(path: str) -> dict:
        if path.startswith("/") or ".." in path.split("/"):
            return {"ok": False, "path": path, "error": "Invalid path"}
        try:
            content = await _gh_fetch_file(owner, repo, path, branch, token)
            if content is None:
                return {"ok": False, "path": path, "error": f"`{path}` not found on {branch}"}
            content, truncated = _slice_content(content, line_range, MAX_FILE_CHARS)
            return {"ok": True, "path": path, "content": content, "truncated": truncated}
        except Exception as e:
            return {"ok": False, "path": path, "error": str(e)}

    results = await asyncio.gather(*[_fetch_one(p) for p in paths])

    ok_files  = [r for r in results if r.get("ok")]
    err_files = [r for r in results if not r.get("ok")]

    return {
        "ok":     len(ok_files) > 0,
        "branch": branch,
        "files":  list(results),
        "fetched": len(ok_files),
        "errors": [f"{r['path']}: {r.get('error','?')}" for r in err_files],
    }


# ── TOOL 3: list_repo_files (repo tree / glob) ───────────────────────────────

async def list_repo_files(ctx: dict, args: dict) -> dict:
    """List files in the connected repo tree — equivalent of mcp_glob_files.

    args:
      path?      str   — sub-directory to list (default: "" = root)
      pattern?   str   — glob pattern e.g. "*.py", "routers/*.py", "**/*.jsx"
      max?       int   — max results (default 150, cap 500)

    Returns {ok, tree: [str], total, truncated}
    """
    # iter 33: read_repo_files & search_repo use plain `import` at module top
    import httpx
    import base64
    import json as _json
    import re as _re

    user_id    = ctx.get("user_id")
    project_id = ctx.get("project_id")
    sub_path   = (args or {}).get("path") or ""
    pattern    = (args or {}).get("pattern") or ""
    max_items  = min(int((args or {}).get("max") or 150), 500)

    proj = await _resolve_project(user_id, project_id)
    if not proj:
        return {"ok": False, "error": "No project connected or project not found"}

    owner  = proj.get("github_owner")
    repo   = proj.get("github_repo")
    branch = proj.get("branch") or "main"
    token  = proj.get("github_token") or None

    if not owner or not repo:
        return {"ok": False, "error": "Project has no resolved github_owner/repo"}

    headers = {"Accept": "application/vnd.github.v3+json"}
    if token:
        headers["Authorization"] = f"token {token}"

    # GitHub Trees API — recursive=1 gets the ENTIRE tree in one call
    url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/{branch}?recursive=1"
    try:
        async with httpx.AsyncClient(timeout=20.0) as c:
            r = await c.get(url, headers=headers)
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        return {"ok": False, "error": f"GitHub tree fetch failed: {e}"}

    tree_items = [
        item["path"] for item in data.get("tree", [])
        if item.get("type") == "blob"
    ]

    # Filter by sub_path
    if sub_path:
        sub_path = sub_path.strip("/")
        tree_items = [p for p in tree_items if p.startswith(sub_path + "/") or p == sub_path]

    # Filter by glob pattern
    if pattern:
        # Support both simple *.py and routers/*.py patterns
        tree_items = [p for p in tree_items if fnmatch.fnmatch(p, pattern) or fnmatch.fnmatch(p.split("/")[-1], pattern)]

    truncated = len(tree_items) > max_items
    return {
        "ok":       True,
        "tree":     tree_items[:max_items],
        "total":    len(tree_items),
        "truncated": truncated,
        "note":     f"Showing {min(len(tree_items), max_items)} of {len(tree_items)} files" + (". Use `path` or `pattern` to narrow." if truncated else "."),
    }


# ── TOOL 4: search_repo (grep across repo) ───────────────────────────────────

async def search_repo(ctx: dict, args: dict) -> dict:
    """Search for a pattern across files in the connected repo.
    Equivalent of Emergent's mcp_execute_bash grep.

    args:
      pattern   str   — text or regex to search for
      path?     str   — limit search to this directory
      ext?      str   — limit to files with this extension e.g. ".py"
      max?      int   — max matching files to return (default 20)

    Returns {ok, matches: [{file, line_no, line}], total_matches}
    """
    user_id    = ctx.get("user_id")
    project_id = ctx.get("project_id")
    pattern    = (args or {}).get("pattern") or ""
    sub_path   = (args or {}).get("path") or ""
    ext        = (args or {}).get("ext") or ""
    max_files  = min(int((args or {}).get("max") or 20), 50)

    if not pattern:
        return {"ok": False, "error": "Missing required arg `pattern`"}

    proj = await _resolve_project(user_id, project_id)
    if not proj:
        return {"ok": False, "error": "No project connected or project not found"}

    owner  = proj.get("github_owner")
    repo   = proj.get("github_repo")
    branch = proj.get("branch") or "main"
    token  = proj.get("github_token") or None

    # First get the tree
    import httpx
    import re as _re

    headers = {"Accept": "application/vnd.github.v3+json"}
    if token:
        headers["Authorization"] = f"token {token}"

    url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/{branch}?recursive=1"
    try:
        async with httpx.AsyncClient(timeout=20.0) as c:
            r = await c.get(url, headers=headers)
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        return {"ok": False, "error": f"GitHub tree fetch failed: {e}"}

    all_files = [
        item["path"] for item in data.get("tree", [])
        if item.get("type") == "blob"
    ]

    # Filter
    if sub_path:
        all_files = [f for f in all_files if f.startswith(sub_path.strip("/") + "/")]
    if ext:
        ext = ext if ext.startswith(".") else "." + ext
        all_files = [f for f in all_files if f.endswith(ext)]

    # Compile pattern (treat as regex, fallback to literal)
    try:
        compiled = _re.compile(pattern, _re.IGNORECASE)
    except _re.error:
        compiled = _re.compile(_re.escape(pattern), _re.IGNORECASE)

    # Search files — cap at max_files matches, fetch in parallel batches of 10
    matches = []
    searched = 0
    batch_size = 10

    for i in range(0, len(all_files), batch_size):
        if len(matches) >= max_files:
            break
        batch = all_files[i:i + batch_size]

        async def _search_file(fpath: str) -> list[dict]:
            content = await _gh_fetch_file(owner, repo, fpath, branch, token)
            if content is None:
                return []
            hits = []
            for line_no, line in enumerate(content.splitlines(), 1):
                if compiled.search(line):
                    hits.append({"file": fpath, "line_no": line_no, "line": line.strip()[:120]})
                    if len(hits) >= 5:   # max 5 hits per file
                        break
            return hits

        batch_results = await asyncio.gather(*[_search_file(f) for f in batch])
        for file_hits in batch_results:
            matches.extend(file_hits)
            if file_hits:
                searched += 1
        if searched >= max_files:
            break

    return {
        "ok":           True,
        "pattern":      pattern,
        "matches":      matches[:max_files * 5],
        "total_matches": len(matches),
        "note":         f"Found {len(matches)} matches. Use `path` or `ext` to narrow search." if matches else f"No matches for `{pattern}`",
    }


# ── TOOL 5: get_repo_info (project metadata) ─────────────────────────────────

async def get_repo_info(ctx: dict, args: dict) -> dict:
    """Return connected project metadata: owner, repo, branch, tech_stack, last task."""
    user_id    = ctx.get("user_id")
    project_id = ctx.get("project_id")

    proj = await _resolve_project(user_id, project_id)
    if not proj:
        return {"ok": False, "error": "No project connected or project not found"}

    return {
        "ok":          True,
        "project_id":  proj.get("project_id"),
        "name":        proj.get("name"),
        "github_owner": proj.get("github_owner"),
        "github_repo": proj.get("github_repo"),
        "branch":      proj.get("branch", "main"),
        "tech_stack":  proj.get("tech_stack", "unknown"),
        "last_task":   proj.get("last_task"),
        "tasks_done":  proj.get("tasks_done", 0),
        "has_pat":     bool(proj.get("github_token")),
    }


# ── Catalog ───────────────────────────────────────────────────────────────────

TOOL_SPECS: list[dict] = [
    {
        "name": "read_repo_file",
        "description": (
            "Fetch the FULL TEXT of ONE file in the connected repo by path. "
            "Use whenever you need to verify a bug claim, read code, or quote actual lines. "
            "Strongly preferred over asking the user to paste. "
            "For multiple files, prefer read_repo_files (parallel, faster)."
        ),
        "args_spec": {
            "path":  "string — repo-relative path e.g. 'backend/routers/auth.py'",
            "lines": "optional [start,end] line range, 1-indexed inclusive",
        },
    },
    {
        "name": "read_repo_files",
        "description": (
            "Fetch UP TO 6 files from the connected repo IN PARALLEL — same as "
            "read_repo_file but faster for multi-file tasks. Use this when you "
            "need to read multiple files before planning a fix. "
            "Example: security fix touching 5 routers = 1 call vs 5 sequential calls."
        ),
        "args_spec": {
            "paths": "array of strings — up to 6 repo-relative file paths",
            "lines": "optional [start,end] applied to ALL files",
        },
    },
    {
        "name": "list_repo_files",
        "description": (
            "List files in the connected repo tree. Use to discover file structure "
            "before reading specific files. Supports glob patterns. "
            "Example: list all Python routers → path='backend/routers', ext='.py'"
        ),
        "args_spec": {
            "path":    "optional string — sub-directory to list (default: root)",
            "pattern": "optional glob pattern e.g. '*.py', 'routers/*.py', '**/*.jsx'",
            "max":     "optional int — max results (default 150)",
        },
    },
    {
        "name": "search_repo",
        "description": (
            "Search for a pattern across files in the connected repo. "
            "Use to find all occurrences of a bug pattern, import, or function. "
            "Example: find all verify_exp=False → pattern='verify_exp.*False', ext='.py'"
        ),
        "args_spec": {
            "pattern": "string — text or regex to search for",
            "path":    "optional string — limit to this directory",
            "ext":     "optional string — limit to this extension e.g. '.py'",
            "max":     "optional int — max matching files (default 20)",
        },
    },
    {
        "name": "get_repo_info",
        "description": (
            "Get connected project metadata: owner, repo, branch, tech stack, "
            "last task, tasks completed. Call this first if you're unsure what "
            "project is connected."
        ),
        "args_spec": {},
    },
]

# ── Dispatch table ────────────────────────────────────────────────────────────

LOCAL_TOOLS: dict[str, callable] = {
    "read_repo_file":  read_repo_file,
    "read_repo_files": read_repo_files,
    "list_repo_files": list_repo_files,
    "search_repo":     search_repo,
    "get_repo_info":   get_repo_info,
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
