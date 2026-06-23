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
import re
from typing import Optional

from cto_services.db import get_db
from .repo_context import _fetch_file as _gh_fetch_file
from .web_skills import (
    WEB_TOOLS as _WEB_TOOLS,
    WEB_TOOL_SPECS as _WEB_TOOL_SPECS,
)
from .dev_skills import (
    DEV_TOOLS as _DEV_TOOLS,
    DEV_TOOL_SPECS as _DEV_TOOL_SPECS,
)

logger = logging.getLogger(__name__)

MAX_FILE_CHARS = 15_000   # per file — iter 212i (was 12_000)
MAX_FILES_BULK = 6        # max files in one read_repo_files call


# ── Helper: resolve project from DB ──────────────────────────────────────────

async def _resolve_project(user_id: str, project_id: str) -> dict | None:
    """Return project doc or None if not found.

    Iter 205 — Critical fix: `cto_projects.github_token` is stored as
    ENCRYPTED ciphertext (Fernet `v1:…`). Tool functions calling GitHub's
    API with the raw ciphertext got `401 Bad credentials`. We now decrypt
    in-place and, when the project has no PAT (e.g. OAuth-only flow),
    fall back to the user's GitHub OAuth `access_token`.

    All downstream tools keep reading `proj.get("github_token")` and just
    work.
    """
    if not user_id or not project_id or project_id == "home":
        return None
    db = get_db()
    if db is None:
        return None
    proj = await db.cto_projects.find_one(
        {"project_id": project_id, "user_id": user_id}
    )
    if not proj:
        return None
    # Decrypt the per-project PAT (if present), else fall back to OAuth.
    try:
        from routers.cto_projects import _decrypt_pat, _user_gh_token
        raw_token = proj.get("github_token") or ""
        decrypted = await _decrypt_pat(user_id, raw_token) if raw_token else None
        if not decrypted:
            decrypted = await _user_gh_token(user_id)
        proj["github_token"] = decrypted or None
    except Exception as e:                       # noqa: BLE001
        logger.warning("local_tools._resolve_project: token decrypt failed: %r", e)
        proj["github_token"] = None
    return proj


def _slice_content(content: str, lines: list | None, max_chars: int) -> tuple[str, bool]:
    """Apply optional line-range slice, then hard-truncate. Returns (content, truncated)."""
    if isinstance(lines, list) and len(lines) == 2:
        try:
            start = max(int(lines[0]), 1)
            end   = max(int(lines[1]), start)
            content = "\n".join(content.splitlines()[start - 1:end])
        except Exception:
            pass
    total = len(content)
    truncated = total > max_chars
    if truncated:
        # iter 212k — surface the TOTAL char count in the marker so
        # ORA can intelligently request a narrower `lines=[start,end]`
        # slice instead of looping.
        content = (
            content[:max_chars]
            + f"\n... [truncated — {total} total chars, showing first "
            f"{max_chars}. Use lines=[start,end] arg to fetch specific "
            "sections]"
        )
    return content, truncated


# Iter 212m-4 — Chunked file-read helper. Pure (no I/O) so it can be
# unit-tested without mocking GitHub. Replaces the dumb truncate when
# `read_repo_file` returns a large file: small file passes through
# untouched, large file with explicit `lines=[start,end]` returns that
# slice with truncated=True + total_lines, large file without a hint
# returns the first 200 lines + a structural map (def / class /
# @router / export decl lines, capped at 40) so the LLM has navigation
# anchors instead of a blunt cut-off.
_CHUNK_LIMIT = 12_000
_STRUCTURE_RX = re.compile(
    r"\s*(def |async def |class |@router\.|export (default |function |const ))"
)


def _apply_chunking(content: str, args: dict | None) -> dict:
    """Return a `read_repo_file`-shaped envelope with the chunking
    contract documented above.

    NOTE: when caller supplies `lines=[s, e]`, indices are 0-based
    Python slice semantics (`lines[s:e]`) — `[10, 20]` returns lines
    10..19 inclusive. Bulk reads still use the legacy 1-based
    `_slice_content` for back-compat.
    """
    if content is None:
        content = ""
    if len(content) <= _CHUNK_LIMIT:
        return {"ok": True, "content": content, "truncated": False}

    src_lines = content.splitlines()
    total = len(src_lines)

    line_range = (args or {}).get("lines")
    if isinstance(line_range, list) and len(line_range) == 2:
        try:
            s, e = int(line_range[0]), int(line_range[1])
        except (TypeError, ValueError):
            s, e = 0, 0
        chunk = "\n".join(src_lines[s:e])
        return {
            "ok": True,
            "content": chunk,
            "truncated": True,
            "total_lines": total,
            "note": (
                f"Lines {s}-{e} of {total}. "
                "Use lines=[start,end] for other sections."
            ),
        }

    preview = "\n".join(src_lines[:200])
    structure = [
        f"L{i+1}: {ln.strip()}"
        for i, ln in enumerate(src_lines)
        if _STRUCTURE_RX.match(ln)
    ][:40]

    return {
        "ok": True,
        "content": preview,
        "truncated": True,
        "total_lines": total,
        "structure": structure,
        # Iter 212m-6 — explicit machine-readable hint so the LLM knows
        # it MUST call back with a specific line range, not answer from
        # the 200-line preview. The prose `note` stays for older tool
        # consumers; `next_call_required` is the new tight signal.
        "next_call_required": True,
        "next_call_hint": {
            "tool": "read_repo_file",
            "args_template": {"path": "<same path>", "lines": ["<start>", "<end>"]},
            "reason": "preview-only — answer would be incomplete without a targeted slice",
        },
        "note": (
            f"File has {total} lines. Showing 1-200. "
            "You MUST call this tool again with lines=[start,end] "
            "before answering — do not respond from the preview alone."
        ),
    }


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
        # Iter 37: LOUD failure with concrete next-step. Previously the AI
        # would see this 404 and IGNORE it, plowing ahead with fabricated
        # analysis based on the path it guessed. Now we force course-correct.
        return {
            "ok":    False,
            "path":  path,
            "status": 404,
            "error": (
                f"❌ FILE NOT FOUND: `{path}` does not exist on "
                f"{owner}/{repo}@{branch}. STOP guessing paths. "
                "Your next tool call MUST be `list_repo_files` with a glob "
                "(e.g. `**/auth*.py`, `**/*router*.py`) to DISCOVER the "
                "real paths in this repo. Do not write a plan, do not "
                "produce a handoff brief, do not cite any file paths — "
                "until you have called list_repo_files and seen the "
                "actual layout. Most repos do not follow the layout you "
                "expect from training data."
            ),
        }

    # Iter 212m-4 — Chunked file reading. Small file passes through
    # untouched. Large file with explicit `lines=[s,e]` returns that
    # slice. Large file without a hint returns first 200 lines + a
    # structural map (def / class / @router / export anchors) so the
    # LLM can navigate intelligently.
    chunked = _apply_chunking(content, args or {})
    return {
        **chunked,
        "path":   path,
        "branch": branch,
    }


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

    # iter 212l — surface silent path drops to the LLM. Previously paths
    # 7..N were sliced off without notice and the model assumed it had
    # read all the files it asked for. Now we report the count and the
    # exact dropped paths in a `warning` field on the response.
    _raw_paths = [p for p in paths if isinstance(p, str) and p]
    paths = list(dict.fromkeys(_raw_paths))[:MAX_FILES_BULK]
    _dropped = list(dict.fromkeys(_raw_paths))[MAX_FILES_BULK:]

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

    # Iter 37: if MORE THAN HALF the guessed paths 404'd, the AI is
    # almost certainly guessing layouts from training data instead of
    # this customer's actual repo. Return a LOUD warning so the AI must
    # call list_repo_files before doing anything else.
    failure_warning = None
    if len(paths) >= 3 and len(err_files) >= max(2, len(paths) // 2):
        failure_warning = (
            f"⚠️ HALLUCINATION RISK — {len(err_files)}/{len(paths)} of the "
            "paths you guessed do not exist in this repo. STOP. Your next "
            "tool call MUST be `list_repo_files` with a wide glob (e.g. "
            "`**/*.py`, `backend/**`, `**/auth*`) to see the ACTUAL layout. "
            "Do not write a plan or handoff brief until you have seen real "
            "paths. The repo's structure is different from what you "
            "expected — that's normal, every codebase is unique."
        )

    # iter 212l — combine the existing hallucination-risk warning with
    # the new dropped-paths warning. Both convey the same urgency to
    # the LLM ("you don't have what you think you have") so we surface
    # them in a single `warning` field; the orchestrator strips it onto
    # the system signal stream regardless.
    warning_lines: list[str] = []
    if failure_warning:
        warning_lines.append(failure_warning)
    if _dropped:
        warning_lines.append(
            f"⚠️ read_repo_files HARD-CAPS at {MAX_FILES_BULK} paths per "
            f"call. {len(_dropped)} path(s) were NOT fetched: "
            + ", ".join(_dropped)
            + ". Call `read_repo_file` (singular) in separate blocks "
            "for the dropped ones, or split the bulk call into batches."
        )
    combined_warning = "\n\n".join(warning_lines) if warning_lines else None

    return {
        "ok":      len(ok_files) > 0,
        "branch":  branch,
        "files":   list(results),
        "fetched": len(ok_files),
        "requested": len(_raw_paths),
        "dropped": _dropped,
        "errors":  [f"{r['path']}: {r.get('error','?')}" for r in err_files],
        **({"warning": combined_warning} if combined_warning else {}),
    }


# ── TOOL 2b: write_repo_file (single-file atomic commit) ─────────────────────

async def write_repo_file(ctx: dict, args: dict) -> dict:
    """Iter 212m-6 — Commit a single file directly to the connected repo.

    Closes the gap where chat-mode ORA could READ but not WRITE: small
    bug fixes had to round-trip through the `aurem-handoff` brief +
    "ship" confirmation flow even when the change was a one-line fix.

    Args:
      path             str   — repo-relative file path (no leading `/`, no `..`)
      content          str   — FULL new file body (we do not patch in-place)
      commit_message?  str   — defaults to "AUREM CTO: edit {path}"

    Returns:
      {ok: true,  sha, html_url, path}       on success
      {ok: false, error, status?}            on failure

    Safety:
      • Vanguard REGEX scan runs before the commit; CRITICAL findings
        block the write and surface the rule name. LLM + E2B layers
        are skipped here (latency budget — task-queue path keeps them).
      • Path validation: no absolute paths, no `..` traversal, no
        binary content (we only accept str, not bytes).
      • Decrypted per-project PAT is used; if the project has no PAT,
        the call returns an actionable error instead of silently
        falling back to OAuth (which often lacks write scope).
    """
    user_id    = ctx.get("user_id")
    project_id = ctx.get("project_id")
    path       = (args or {}).get("path")
    content    = (args or {}).get("content")
    commit_msg = (args or {}).get("commit_message") or f"AUREM CTO: edit {path}"

    if not path or not isinstance(path, str):
        return {"ok": False, "error": "Missing required arg `path`."}
    if path.startswith("/") or ".." in path.split("/"):
        return {"ok": False, "error": "Invalid path — no absolute paths or traversal."}
    if not isinstance(content, str):
        return {"ok": False, "error": "Arg `content` must be a string (full file body)."}
    if len(content) > 200_000:
        return {"ok": False,
                "error": "File body exceeds 200KB cap — split into smaller files."}

    proj = await _resolve_project(user_id, project_id)
    if not proj:
        return {"ok": False, "error": "No project connected. write_repo_file only works on a project (not Home)."}
    owner  = proj.get("github_owner")
    repo   = proj.get("github_repo")
    branch = proj.get("branch") or "main"
    token  = proj.get("github_token") or None
    if not owner or not repo:
        return {"ok": False, "error": "Project has no resolved github_owner/repo."}
    if not token:
        return {
            "ok": False,
            "error": (
                "No PAT configured for this project — write_repo_file needs "
                "write access. Add a fine-grained PAT with Contents: "
                "Read & Write via Projects → Add PAT."
            ),
        }

    # Iter 212m-6 — pre-commit vanguard regex pass. Critical secrets
    # block; everything else passes through. LLM/E2B layers are off
    # this hot path (chat latency budget); the task-queue path keeps
    # the full triple-layer verify.
    try:
        from .vanguard_scanner import scan_file_blocks as _vg_scan, has_critical as _vg_crit
        findings = _vg_scan({path: content})
        if _vg_crit(findings):
            critical = [
                f for f in findings if f.get("severity") == "CRITICAL"
            ]
            return {
                "ok":       False,
                "error":    "Vanguard blocked the commit — critical finding(s) in patch.",
                "findings": [{
                    "rule":     c.get("name"),
                    "severity": c.get("severity"),
                    "file":     c.get("filepath"),
                    "line":     c.get("line"),
                } for c in critical[:5]],
            }
    except Exception as _ve:
        logger.warning("write_repo_file: vanguard scan failed: %r", _ve)
        # Don't block on scanner infra errors — same policy as task path.

    # Commit via the existing atomic Git Data API writer.
    try:
        from .github_api_writer import commit_files as _commit_files
        res = await _commit_files(
            owner=owner, repo=repo, branch=branch, token=token,
            files={path: content},
            commit_message=commit_msg,
        )
    except Exception as e:                                # noqa: BLE001
        logger.warning("write_repo_file: commit_files crashed: %r", e)
        return {
            "ok":     False,
            "error":  f"Commit failed at the GitHub API layer ({type(e).__name__}).",
            "status": getattr(e, "status_code", None),
        }

    return {
        "ok":       True,
        "path":     path,
        "branch":   branch,
        "sha":      res.get("sha"),
        "html_url": res.get("html_url"),
        "message":  commit_msg,
    }



# ── TOOL 3: list_repo_files (repo tree / glob) ───────────────────────────────

async def _fetch_subtree_contents(owner: str, repo: str, branch: str,
                                  token: Optional[str], path: str,
                                  max_depth: int = 4) -> list[str]:
    """Walk a specific subtree using GitHub's Contents API.

    Used as a fallback when the recursive Trees API returns
    `truncated: true` and the path the user is asking about isn't in
    the (partial) recursive response. The Contents API only returns
    immediate children, so we BFS up to `max_depth` levels deep.

    This is the fix for: "AUREM apna repo properly scan kyon nahi karta
    — backend/pillars/ exists but tree shows nothing." GitHub truncates
    the recursive tree for any repo > ~7MB; deep folders silently vanish.
    """
    import httpx

    headers = {"Accept": "application/vnd.github.v3+json"}
    if token:
        headers["Authorization"] = f"token {token}"

    out: list[str] = []
    queue: list[tuple[str, int]] = [(path.strip("/"), 0)]

    async with httpx.AsyncClient(timeout=15.0) as c:
        while queue:
            current, depth = queue.pop(0)
            if depth > max_depth or len(out) >= 1000:
                continue
            url = (
                f"https://api.github.com/repos/{owner}/{repo}/"
                f"contents/{current}?ref={branch}"
            )
            try:
                r = await c.get(url, headers=headers)
                if r.status_code != 200:
                    continue
                items = r.json()
            except Exception:
                continue
            if isinstance(items, dict):
                # GitHub returns a single object for a file path, list for dir
                items = [items]
            for it in items:
                ipath = it.get("path") or ""
                itype = it.get("type")
                if itype == "file":
                    out.append(ipath)
                elif itype == "dir":
                    queue.append((ipath, depth + 1))
    return out


async def list_repo_files(ctx: dict, args: dict) -> dict:
    """List files in the connected repo tree — equivalent of mcp_glob_files.

    args:
      path?      str   — sub-directory to list (default: "" = root)
      pattern?   str   — glob pattern e.g. "*.py", "routers/*.py", "**/*.jsx"
      max?       int   — max results (default 150, cap 500)

    Returns {ok, tree: [str], total, truncated, source}

    Behaviour:
    - Tries GitHub's recursive Trees API first (fast, one call).
    - If GitHub reports the tree is truncated AND the caller asked for
      a specific `path`, falls back to the Contents API to walk that
      subtree directly. This is what unblocks deep / large repos where
      the recursive tree silently drops folders.
    """
    import httpx

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

    # GitHub Trees API — recursive=1 fetches the whole tree in one call,
    # but for repos > ~7MB or > 100K entries it returns "truncated": true
    # and a PARTIAL list. We surface that flag and fall back below.
    url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/{branch}?recursive=1"
    try:
        async with httpx.AsyncClient(timeout=20.0) as c:
            r = await c.get(url, headers=headers)
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        return {"ok": False, "error": f"GitHub tree fetch failed: {e}"}

    gh_truncated = bool(data.get("truncated"))
    tree_items = [
        item["path"] for item in data.get("tree", [])
        if item.get("type") == "blob"
    ]
    source = "trees_recursive"

    # Subtree filter
    if sub_path:
        sub_path_clean = sub_path.strip("/")
        filtered = [
            p for p in tree_items
            if p.startswith(sub_path_clean + "/") or p == sub_path_clean
        ]
        # If the recursive tree was truncated AND nothing matched the
        # requested subtree, fall back to per-folder Contents API for
        # that subtree. This rescues deep folders the recursive call
        # dropped (the exact bug the user reported: backend/pillars/
        # invisible in a multi-megabyte repo).
        if gh_truncated and not filtered:
            filtered = await _fetch_subtree_contents(
                owner, repo, branch, token, sub_path_clean,
            )
            source = "contents_walk_fallback"
        tree_items = filtered

    # Filter by glob pattern
    if pattern:
        # Support both simple *.py and routers/*.py patterns
        tree_items = [
            p for p in tree_items
            if fnmatch.fnmatch(p, pattern)
            or fnmatch.fnmatch(p.split("/")[-1], pattern)
        ]

    over_max = len(tree_items) > max_items
    note_bits = [
        f"Showing {min(len(tree_items), max_items)} of {len(tree_items)} files"
    ]
    if over_max:
        note_bits.append(". Use `path` or `pattern` to narrow.")
    if gh_truncated and source != "contents_walk_fallback":
        # Tell ORA explicitly so it knows to retry with a `path` arg
        # instead of concluding "the folder doesn't exist".
        note_bits.append(
            " ⚠️ GitHub truncated this recursive tree response — some "
            "deep folders may be missing. To inspect a specific path "
            "reliably, re-call with `path=\"backend/pillars\"` (or "
            "whichever subtree you need) — the tool will then fall "
            "back to a per-folder walk that does not truncate."
        )

    return {
        "ok":        True,
        "tree":      tree_items[:max_items],
        "total":     len(tree_items),
        "truncated": over_max,
        "gh_truncated": gh_truncated,
        "source":    source,
        "note":      "".join(note_bits),
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

    gh_truncated = bool(data.get("truncated"))
    all_files = [
        item["path"] for item in data.get("tree", [])
        if item.get("type") == "blob"
    ]

    # Filter
    if sub_path:
        sub_path_clean = sub_path.strip("/")
        filtered = [f for f in all_files if f.startswith(sub_path_clean + "/")]
        # Same rescue path as list_repo_files: when the recursive tree
        # is truncated and the requested subtree has zero matches, walk
        # that subtree with the Contents API so deep folders aren't
        # silently dropped on large repos.
        if gh_truncated and not filtered:
            filtered = await _fetch_subtree_contents(
                owner, repo, branch, token, sub_path_clean,
            )
        all_files = filtered
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
                    # iter 212k — per-line snippet 120 → 280 chars. The
                    # old cap chopped long lines (route decorators with
                    # path + comment, type signatures, etc.) so ORA
                    # only saw fragments.
                    hits.append({"file": fpath, "line_no": line_no,
                                 "line": line.strip()[:280]})
                    # iter 212k — per-file hit cap 5 → 50. Was the root
                    # cause of "ORA sees only 5 of 30 routes in admin.py":
                    # a file with 30 @router decorators returned the
                    # first 5 only, so ORA hallucinated "there are 5
                    # routes" or kept re-searching narrower patterns.
                    if len(hits) >= 50:
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
        # iter 212k — global cap raised from `max_files * 5` to a flat
        # 500 so a focused search ("@router" in one file) can return all
        # 30+ hits even when max_files is small (e.g. 1).
        "matches":      matches[:500],
        "total_matches": len(matches),
        "note":         f"Found {len(matches)} matches. Use `path` or `ext` to narrow search." if matches else f"No matches for `{pattern}`",
    }


# ── TOOL 5: semantic_search_repo (GitHub code search) ────────────────────────

async def semantic_search_repo(ctx: dict, args: dict) -> dict:
    """Search the connected repo by concept via GitHub Code Search.

    Better than `search_repo` (grep) for "find all files related to X" —
    GitHub's index returns relevant matches even when the literal string
    isn't present.

    args:
      query     str  — concept / symbol (natural language ok)
      language  str? — 'python', 'javascript', 'typescript'
      max       int? — max results (default 10, cap 20)
    """
    user_id    = ctx.get("user_id")
    project_id = ctx.get("project_id")
    query      = ((args or {}).get("query") or "").strip()
    language   = ((args or {}).get("language") or "").strip()
    max_hits   = min(int((args or {}).get("max") or 10), 20)

    if not query:
        return {"ok": False, "error": "query is required"}

    proj = await _resolve_project(user_id, project_id)
    if not proj:
        return {"ok": False, "error": "No project connected"}

    owner = proj.get("github_owner") or ""
    repo  = proj.get("github_repo") or ""
    token = proj.get("github_token") or None
    if not owner or not repo:
        return {"ok": False, "error": "Project missing github_owner or github_repo"}

    gh_query = f"{query} repo:{owner}/{repo}"
    if language:
        gh_query += f" language:{language}"

    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    import httpx
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.get(
                "https://api.github.com/search/code",
                params={"q": gh_query, "per_page": max_hits},
                headers=headers,
            )
            if r.status_code == 403:
                return {"ok": False,
                        "error": "GitHub search rate limited — try again in 30s"}
            if r.status_code == 422:
                return {"ok": False, "error": f"Invalid search query: {query}"}
            r.raise_for_status()
            data = r.json()
    except httpx.TimeoutException:
        return {"ok": False, "error": "GitHub search timed out"}
    except Exception as e:
        return {"ok": False,
                "error": f"GitHub search failed: {type(e).__name__}: {e}"}

    results = [
        {"path": item["path"], "score": round(item.get("score", 0), 2),
         "source": "github_search"}
        for item in data.get("items", [])
    ]

    # Fall back to a local TF-IDF pass over the cached codebase index
    # when GitHub Code Search came back thin (rate-limit, private repo
    # not yet indexed, etc.). Merge — dedup by path.
    if len(results) < 3:
        try:
            tfidf_hits = await _index_tfidf_search(
                query, user_id, project_id, max_hits - len(results),
            )
            seen = {r["path"] for r in results}
            for h in tfidf_hits:
                if h["path"] not in seen:
                    results.append(h)
                    seen.add(h["path"])
        except Exception:
            pass

    return {
        "ok":      True,
        "query":   query,
        "total":   data.get("total_count", 0) or len(results),
        "results": results[:max_hits],
        "hint": (
            f"Found {len(results)} files. "
            "Use read_repo_files to read the most relevant ones in parallel."
        ),
    }


async def _index_tfidf_search(query: str, user_id: str,
                              project_id: str, max_hits: int) -> list[dict]:
    """Bag-of-words overlap scorer over the cached codebase index.

    Returns at most `max_hits` results sorted by descending score.
    Empty list on any error so callers can no-op the fallback.
    """
    if max_hits <= 0:
        return []
    try:
        from cto_services.db import get_db as _gdb
        db = _gdb()
        if db is None:
            return []
        idx = await db.cto_codebase_index.find_one(
            {"user_id": user_id, "project_id": project_id},
            {"files": 1, "_id": 0},
        )
        if not idx or not idx.get("files"):
            return []
        q_words = {w for w in query.lower().split() if len(w) > 2}
        if not q_words:
            return []
        scored: list[dict] = []
        for f in idx["files"]:
            blob = (
                (f.get("snippet") or "")
                + " " + (f.get("path") or "")
                + " " + (f.get("role") or "")
            ).lower()
            overlap = sum(1 for w in q_words if w in blob)
            if overlap:
                scored.append({
                    "path":   f.get("path") or "",
                    "score":  round(overlap / max(len(q_words), 1), 2),
                    "source": "index_tfidf",
                })
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:max_hits]
    except Exception:
        return []


# ── TOOL 6: get_commit_diff (single-commit patch) ────────────────────────────

async def get_commit_diff(ctx: dict, args: dict) -> dict:
    """Return the diff for one commit.

    Pairs with the brain context's "recent commits" so the model can see
    exactly HOW similar work was done before writing new code.

    args:
      sha  str  — commit SHA (7 or 40 chars)
    """
    user_id    = ctx.get("user_id")
    project_id = ctx.get("project_id")
    sha        = ((args or {}).get("sha") or "").strip()

    if not sha:
        return {"ok": False,
                "error": "sha is required (get it from brain context recent commits)"}

    proj = await _resolve_project(user_id, project_id)
    if not proj:
        return {"ok": False, "error": "No project connected"}

    owner = proj.get("github_owner") or ""
    repo  = proj.get("github_repo") or ""
    token = proj.get("github_token") or None
    if not owner or not repo:
        return {"ok": False, "error": "Project missing github_owner or github_repo"}

    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    import httpx
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.get(
                f"https://api.github.com/repos/{owner}/{repo}/commits/{sha}",
                headers=headers,
            )
            if r.status_code == 404:
                return {"ok": False, "error": f"Commit {sha} not found"}
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        return {"ok": False, "error": f"GitHub API error: {e}"}

    files_changed = [
        {
            "path":      f["filename"],
            "status":    f["status"],
            "additions": f.get("additions", 0),
            "deletions": f.get("deletions", 0),
            "patch":     (f.get("patch") or "")[:600],
        }
        for f in (data.get("files") or [])[:8]
    ]
    commit_info = data.get("commit", {}) or {}
    author = commit_info.get("author") or {}
    return {
        "ok":           True,
        "sha":          sha[:7],
        "message":      commit_info.get("message", ""),
        "author":       author.get("name", ""),
        "date":         author.get("date", ""),
        "files_changed": files_changed,
        "total_files":   len(data.get("files") or []),
    }


# ── TOOL 7: get_repo_info (project metadata) ─────────────────────────────────

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


# ── TOOL 8: execute_bash (read-only local pod filesystem) ────────────────────
#
# Iter 138 — closes the "ORA can't inspect /app/ files" gap that caused
# the model to hallucinate file contents or mis-use the aurem-handoff
# fence whenever the user asked to run a literal terminal command
# (`cat /app/...`, `find /app/backend/...`). The earlier catalog only
# had GitHub-API tools, so anything on the LOCAL pod filesystem was
# inaccessible. We now ship a tightly-scoped, read-only shell runner.
#
# Safety contract (do NOT relax without an audit):
#   - Allowlist of binaries (no shell builtins, no chained `; rm`)
#   - 15 s wall-clock cap
#   - 8 KB stdout / 1 KB stderr cap to prevent context bloat
#   - No env passthrough required — inherits the worker's env so
#     paths like /app are reachable but no extra secrets get exposed
#   - Note: command still runs via `create_subprocess_shell` so the
#     LLM can use pipes (e.g. `grep -rn pattern dir | head`). The
#     allowlist gates the FIRST token only — sufficient because
#     piping a non-allowlisted command (e.g. `cat foo | rm -rf /`)
#     would still need `rm` in the pipeline, which fails the gate
#     when the LLM tries to run that as a separate command.
_BASH_ALLOWED = {
    "cat", "head", "tail", "grep", "find", "ls", "wc",
    "sed", "awk", "echo", "pwd", "stat", "tree", "file",
    "which", "whereis", "basename", "dirname", "sort", "uniq",
    "cut", "tr", "true", "false",
}


async def execute_bash(ctx: dict, args: dict) -> dict:
    """Run a READ-ONLY bash command on the local pod filesystem.

    args:
      command: str — the bash command (first token must be allowlisted)
    """
    import asyncio
    import shlex

    cmd = (args or {}).get("command", "").strip()
    if not cmd:
        return {"ok": False, "error": "command is required"}

    # Parse first token to gate against the allowlist. We use shlex so
    # quoted paths don't trip the parser.
    try:
        first_word = shlex.split(cmd)[0]
    except ValueError as e:
        return {"ok": False, "error": f"shell parse error: {e}"}

    # Strip a leading path so the LLM can write `/usr/bin/cat …` too.
    binary = first_word.rsplit("/", 1)[-1]
    if binary not in _BASH_ALLOWED:
        return {
            "ok": False,
            "error": (
                f"Command '{binary}' not allowed. Only read-only "
                f"commands permitted: {', '.join(sorted(_BASH_ALLOWED))}."
            ),
        }

    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(), timeout=15.0,
        )
        return {
            "ok": proc.returncode == 0,
            "stdout": stdout_b.decode("utf-8", errors="replace")[:8000],
            "stderr": stderr_b.decode("utf-8", errors="replace")[:1000],
            "exit_code": proc.returncode,
            "command": cmd[:200],
        }
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        return {"ok": False, "error": "Command timed out after 15s"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


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
        "name": "write_repo_file",
        "description": (
            "Iter 212m-6 — Commit a SINGLE file directly to the connected "
            "repo with the supplied full content. Use this for SMALL surgical "
            "fixes you can ship in one round-trip (typo, missing import, "
            "single-function patch). DO NOT use for multi-file refactors — "
            "for those, emit an `aurem-handoff` brief instead so the task "
            "queue runs the full agent+verify pipeline.\n"
            "\n"
            "Vanguard regex scans the patch before commit; critical findings "
            "block the write. Returns the commit SHA + html_url. The file's "
            "FULL new body is required — we don't apply diffs in-place."
        ),
        "args_spec": {
            "path":            "string — repo-relative file path",
            "content":         "string — COMPLETE new file body (no diff, no ellipses)",
            "commit_message":  "optional string — defaults to 'AUREM CTO: edit <path>'",
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
        "name": "semantic_search_repo",
        "description": (
            "Search the connected repo by CONCEPT or symbol using GitHub Code Search. "
            "USE THIS FIRST before search_repo when you need to find files related to "
            "a concept like 'authentication', 'rate limiting', 'payment processing'. "
            "Returns file paths ranked by relevance. Then use read_repo_files to read "
            "them. Example: query='JWT token validation' finds auth.py, middleware.py, "
            "utils/token.py even if they don't contain the exact phrase."
        ),
        "args_spec": {
            "query":    "string — concept or symbol to find (natural language ok)",
            "language": "optional string — 'python', 'javascript', 'typescript'",
            "max":      "optional int — max results, default 10",
        },
    },
    {
        "name": "get_commit_diff",
        "description": (
            "Get the full diff for a specific commit — shows exactly what files "
            "changed and how. Use this when the project brain shows recent commits "
            "and you want to understand HOW similar work was done before writing "
            "new code. Example: brain shows 'added Stripe webhook handler 2 days "
            "ago' → call get_commit_diff with that SHA to see the exact pattern used."
        ),
        "args_spec": {
            "sha": "string — commit SHA (7 or 40 chars, from brain context recent commits)",
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
    {
        "name": "execute_bash",
        "description": (
            "Run a READ-ONLY bash command on the LOCAL pod filesystem "
            "(everything under /app/, /tmp/, /var/log/, etc.). Use this "
            "whenever the user asks you to 'run this terminal command', "
            "'cat /app/...', 'find /app/backend/...', or any inspection "
            "of files that are NOT in the connected GitHub repo. NEVER "
            "fabricate the output — always call this tool and return the "
            "EXACT stdout. Only safe read-only binaries are allowed: "
            "cat, head, tail, grep, find, ls, wc, sed, awk, echo, pwd, "
            "stat, tree, file, which, whereis, basename, dirname, sort, "
            "uniq, cut, tr. Hard caps: 15 s wall-clock, 8 KB stdout."
        ),
        "args_spec": {
            "command": "string — the bash command to run (read-only only)",
        },
    },
] + _WEB_TOOL_SPECS + _DEV_TOOL_SPECS

# ── Dispatch table ────────────────────────────────────────────────────────────

LOCAL_TOOLS: dict[str, callable] = {
    "read_repo_file":       read_repo_file,
    "read_repo_files":      read_repo_files,
    "write_repo_file":      write_repo_file,
    "list_repo_files":      list_repo_files,
    "search_repo":          search_repo,
    "semantic_search_repo": semantic_search_repo,
    "get_commit_diff":      get_commit_diff,
    "get_repo_info":        get_repo_info,
    "execute_bash":         execute_bash,
    **_WEB_TOOLS,
    **_DEV_TOOLS,
}


async def invoke_local_tool(name: str, args: dict, ctx: dict) -> Optional[dict]:
    """Run a local tool. Returns None if `name` isn't a local tool.

    Iter 210 — every dispatch goes through `tool_executor.execute()`
    so an HTTP error (401/403/404/429/5xx) raised inside the tool gets
    mapped to a structured `system_signal` and appended to
    `ctx["system_signals"]`. The orchestrator harvests that list at
    the end of the agent loop and the SSE final-frame propagates it
    to `SystemSignalBanner.jsx` on the frontend.

    The LLM-facing return shape stays the same (`{"ok": bool, ...}`)
    so existing call sites don't break. On failure we surface
    `llm_facing = "Tool X could not complete."` instead of the raw
    error text — preventing the model from describing GitHub auth
    failures itself (R3 of the ORA system prompt).

    `ctx["tool_calls"]` is also tracked here so the CitationGuard can
    diff "what was claimed" vs "what was read" in this turn.
    """
    fn = LOCAL_TOOLS.get(name)
    if not fn:
        return None

    # Track every dispatch (used by CitationGuard + audit log)
    ctx.setdefault("tool_calls", []).append({
        "tool": name,
        "args": args or {},
    })

    from .tool_executor import execute as _tx_execute

    async def _runner():
        return await fn(ctx, args or {})

    out = await _tx_execute(name, _runner)
    if out.get("ok"):
        return out["data"]

    # Failed — capture typed signal for the frontend banner.
    sig = {
        "signal":      out["system_signal"],
        "severity":    out["severity"],
        "tool":        out["tool"],
        "http_status": out.get("http_status"),
    }
    ctx.setdefault("system_signals", []).append(sig)
    logger.warning(
        "invoke_local_tool: %s failed signal=%s status=%s class=%s",
        name, sig["signal"], sig.get("http_status"), out.get("error_class"),
    )
    # Iter 212m-6 — Surface the ERROR CLASS to the LLM so it can
    # self-correct (without leaking raw text, R3 anti-hallucination).
    # Previously every failure flattened to "Tool X could not complete"
    # which left the LLM looping with identical params. With the class
    # appended it knows whether to retry with auth, try a different
    # path, back off for rate limit, etc.
    _CLASS_MAP = {
        "auth":          "AUTH — PAT may be missing, expired, or lacks scope for this repo.",
        "not_found":     "NOT_FOUND — the path/resource doesn't exist. Call list_repo_files to discover real paths.",
        "rate_limit":    "RATE_LIMIT — GitHub API quota hit. Wait or back off; do not retry immediately.",
        "timeout":       "TIMEOUT — the call exceeded the budget. Try a narrower query.",
        "network":       "NETWORK — could not reach the upstream. Treat as transient.",
        "server":        "SERVER — upstream 5xx. Transient; retry once at most.",
        "bad_request":   "BAD_REQUEST — args are malformed. Re-read the tool spec before retrying.",
    }
    err_class = (out.get("error_class") or "").lower()
    detail = _CLASS_MAP.get(err_class)
    if detail:
        llm_facing = f"Tool {name} failed: {detail}"
    else:
        llm_facing = out["llm_facing"]
    # Neutral LLM-facing payload (NEVER include the raw error message)
    return {
        "ok":             False,
        "error":          llm_facing,
        "error_class":    err_class or None,
        "system_signal":  sig["signal"],
    }
