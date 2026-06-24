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
# Mix of frontend (React/Vue/Next), backend (FastAPI/Flask/Django/Express),
# and config/manifest paths so we get SOMETHING for any repo layout.
_PRIORITY_FILES = [
    "README.md", "README.rst", "README", "readme.md",
    "package.json", "requirements.txt", "pyproject.toml",
    "Cargo.toml", "go.mod", "Gemfile", "composer.json",
    ".env.example", "env.example",
    # FastAPI / Flask / Django entry points (root level)
    "main.py", "app.py", "server.py", "manage.py", "wsgi.py", "asgi.py",
    # Common nested entry points
    "backend/main.py", "backend/server.py", "backend/app.py",
    "backend/server/main.py", "api/main.py", "src/main.py",
    # Routers / common backend module names (Iter 37 — TJSNDHU/Aurem style)
    "backend/routers/__init__.py", "backend/routes/__init__.py",
    "backend/services/__init__.py",
    # Frontend entry points
    "index.html", "index.js", "index.ts",
    "src/index.js", "src/index.ts", "src/main.js", "src/main.ts",
    "src/main.jsx", "src/main.tsx", "src/App.jsx", "src/App.tsx",
    "frontend/src/App.jsx", "frontend/src/App.tsx",
    "frontend/src/main.jsx", "frontend/src/main.tsx",
    "pages/_app.tsx", "pages/_app.js", "pages/index.tsx", "pages/index.js",
    "app/layout.tsx", "app/layout.jsx", "app/page.tsx", "app/page.jsx",
    "src/app.py", "src/server.py",
    # Build / tooling configs
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
                       token: Optional[str]) -> tuple[list[dict], bool]:
    """Return (tree, gh_truncated).

    `gh_truncated` mirrors GitHub's own `truncated` flag on the
    recursive Trees API response. When True, parts of the tree are
    missing from this single call and `_build_file_tree` needs to
    rescue the missing top-level folders via a Contents-API walk so
    deep dirs like `backend/pillars/` aren't silently invisible.

    Iter 212m-13 — short-TTL in-memory cache. Reuses results across
    every `read_repo_file` / `list_repo_files` call inside a single
    chat turn so the LLM's planning round-trips don't each hit
    GitHub freshly.
    """
    from .github_cache import tree_key, get_tree, set_tree
    ck = tree_key(owner, repo, branch, token)
    cached = get_tree(ck)
    if cached is not None:
        return cached

    url = (
        f"https://api.github.com/repos/{owner}/{repo}/git/trees/"
        f"{branch}?recursive=1"
    )
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        r = await client.get(url, headers=_gh_headers(token))
        r.raise_for_status()
        data = r.json()
        result = ((data.get("tree") or []), bool(data.get("truncated")))
        set_tree(ck, result)
        return result


async def _fetch_file(owner: str, repo: str, path: str, branch: str,
                       token: Optional[str]) -> Optional[str]:
    """Return the decoded text content of a file, or None on any failure.

    Iter 212m-13 — short-TTL in-memory cache. The LLM frequently
    re-reads the same file across tool-call iterations within a
    single chat turn (scope → patch → verify). Caching for 90 s
    eliminates 60-90% of the duplicate GitHub round-trips that were
    inflating turn latency on production.
    """
    from .github_cache import file_key, get_file, set_file
    ck = file_key(owner, repo, path, branch, token)
    cached = get_file(ck)
    if cached is not None:
        return cached

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
            decoded = raw.decode("utf-8", errors="replace")
            set_file(ck, decoded)
            return decoded
    except Exception as e:
        logger.debug(f"fetch_file failed for {path}: {e!r}")
        return None


def _format_tree(tree: list[dict]) -> str:
    """One path per line. Trim binary / huge stuff, mark directories with /.

    Iter 32: ensure every TOP-LEVEL directory is included in the summary
    even on huge monorepos (was: silently truncated at 400 entries, so
    folders like `pillars/`, `legion/`, `camofox/` vanished and the AI
    told the user 'pillars/ doesn't exist'). Strategy: split into
    (dirs+root-files) shown first, then deeper paths up to the cap.
    """
    top_dirs: list[str] = []
    top_files: list[str] = []
    deep: list[str] = []
    seen_top_dirs: set[str] = set()
    for node in tree:
        t = node.get("type")
        p = node.get("path", "")
        if not p:
            continue
        if "/" not in p:
            if t == "tree":
                top_dirs.append(f"{p}/")
                seen_top_dirs.add(p)
            else:
                top_files.append(f"{p}  ({node.get('size') or 0}b)")
        else:
            # Also record any new second-level dirs we discover
            first_seg = p.split("/", 1)[0]
            seen_top_dirs.add(first_seg)
            if t == "tree":
                deep.append(f"{p}/")
            else:
                deep.append(f"{p}  ({node.get('size') or 0}b)")

    # Always preserve full top-level visibility
    rows: list[str] = []
    rows.append("# Top-level folders:")
    rows.extend(sorted(set(top_dirs)) or ["(none)"])
    if top_files:
        rows.append("# Top-level files:")
        rows.extend(sorted(top_files))
    if deep:
        rows.append("# Deeper paths (capped):")
        cap = max(0, MAX_TREE_ENTRIES - len(rows))
        rows.extend(deep[:cap])
        if len(deep) > cap:
            rows.append(f"... +{len(deep) - cap} more entries — "
                        f"call `list_repo_files` with a glob to see them")
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
        tree, gh_truncated = await _fetch_tree(owner, repo, branch, token)
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

    # Rescue truncated trees. GitHub silently drops deep folders for
    # repos > ~7MB or > 100K entries (the `truncated: true` flag on the
    # response). Without this rescue, ORA was telling users "there's no
    # backend/pillars/ folder" when the folder existed but had been
    # dropped by GitHub's API. We walk every top-level dir we DID see
    # via the Contents API and merge anything missing from deeper
    # subtrees so `_format_tree` shows them.
    truncation_note = ""
    if gh_truncated:
        existing_paths = {n.get("path") for n in tree if n.get("path")}
        top_level_dirs = sorted({
            (n.get("path") or "").split("/", 1)[0]
            for n in tree
            if n.get("type") == "tree" and "/" not in (n.get("path") or "")
        })
        rescued: list[dict] = []
        for top in top_level_dirs:
            if not top:
                continue
            try:
                sub_paths = await _fetch_subtree_contents(
                    owner, repo, branch, token, top, max_depth=4,
                )
            except Exception:
                sub_paths = []
            for sp in sub_paths:
                if sp and sp not in existing_paths:
                    rescued.append({"path": sp, "type": "blob"})
                    existing_paths.add(sp)
        if rescued:
            tree = tree + rescued
            truncation_note = (
                f"(GitHub truncated the recursive tree — auto-rescued "
                f"{len(rescued)} additional file paths via per-folder "
                f"walk so deep folders are visible.)"
            )

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

    return _wrap(owner, repo, branch, tree_text, inlined_text, truncation_note)


async def _fetch_subtree_contents(owner: str, repo: str, branch: str,
                                  token: Optional[str], path: str,
                                  max_depth: int = 4) -> list[str]:
    """BFS-walk a subtree via GitHub's Contents API.

    Mirrors `local_tools._fetch_subtree_contents` so the initial repo
    context can rescue folders dropped by a truncated Trees response.
    Kept local (not imported from local_tools) to avoid a circular
    import — local_tools imports `_fetch_file` from this module.
    """
    headers = _gh_headers(token)
    out: list[str] = []
    queue: list[tuple[str, int]] = [(path.strip("/"), 0)]
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        while queue:
            current, depth = queue.pop(0)
            if depth > max_depth or len(out) >= 1000:
                continue
            url = (
                f"https://api.github.com/repos/{owner}/{repo}/"
                f"contents/{current}?ref={branch}"
            )
            try:
                r = await client.get(url, headers=headers)
                if r.status_code != 200:
                    continue
                items = r.json()
            except Exception:
                continue
            if isinstance(items, dict):
                items = [items]
            for it in items or []:
                ipath = it.get("path") or ""
                itype = it.get("type")
                if itype == "file":
                    out.append(ipath)
                elif itype == "dir":
                    queue.append((ipath, depth + 1))
    return out


def _wrap(owner: str, repo: str, branch: str,
           tree_text: str, inlined_text: str, note: str) -> str:
    """Compose the final system-prompt block."""
    parts = [
        "=== CONNECTED REPO CONTEXT ===",
        f"You are scoped to: {owner}/{repo}@{branch}",
        # NOTE: previous wording said "Answer using ONLY this real data"
        # which trained the model to refuse repo questions whenever the
        # answer wasn't in the inlined snippet (the "README mein nahin
        # hai" bug). Reworded to make tool-use mandatory whenever the
        # inlined slice isn't enough.
        "You DO have read access to this user's repo via GitHub's API. "
        "Below is the current file tree and the contents of a few key "
        "files (README, package.json, entry points, etc.) inlined for "
        "speed.",
        "",
        "MANDATORY BEHAVIOUR when the user asks about ANY file, "
        "function, route, or behaviour of this repo:",
        "  1. If the answer is in the inlined files above — answer from "
        "them and cite the path.",
        "  2. If the answer is NOT in the inlined files BUT the path "
        "exists in the file tree — call the `read_repo_file` tool (or "
        "`read_repo_files` for multiple paths) to fetch the real source "
        "BEFORE replying. Never say \"it's not in the README\" or \"I "
        "don't have access\" — you do have access, use the tool.",
        "  3. If the file tree shows the path but the user asked about "
        "a directory — call `list_repo_files` with a glob first.",
        "  4. Only after you have read the actual source, write the "
        "answer. Never guess. Never extrapolate from filename alone.",
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
        parts.append("--- key file contents (inlined) ---")
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
