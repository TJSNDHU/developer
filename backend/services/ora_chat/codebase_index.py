"""
services/ora_chat/codebase_index.py — Iter 212m-246

Read-only codebase awareness layer for ORA Chat.

The index gives ORA baseline knowledge of the AUREM repo so questions
like "have we implemented X?", "where is Y defined?", "does Z exist?"
can be answered without a follow-up trip to the founder's editor.

Design rules:
  - READ-ONLY. Never writes to the source tree.
  - Bounded: only indexes /app/backend and /app/frontend/src (the two
    trees that actually contain product code). node_modules, .git,
    __pycache__, build outputs, and dot-directories are skipped.
  - Cheap: files are opened for a max of 200 lines each and truncated
    to 8 KB; the full manifest is a compact dict, not raw source.
  - Cached in-memory with a 15-minute TTL. First call warms; every
    call within the window is O(1).
  - Non-destructive to the LLM's context budget: `compact_tree()` is
    the ONLY function that emits >2 KB of text, and callers must
    explicitly pass `max_files=N` to stay within their token budget.

Public API:
  - `build_index(force=False)` — walk the trees, populate cache.
  - `compact_tree(max_files=120)` — top-level file tree for system prompt.
  - `find_files(pattern, limit=25)` — glob against paths.
  - `read_file(path, max_lines=200)` — safe read with a hard cap.
  - `search_defs(name, limit=15)` — locate a function/class name.
  - `bm25_relevant_files(query, top_k=3)` — cheap term-frequency
     retrieval for the NEEDS_CODEBASE branch of deep_research.
  - `index_stats()` — for `/repo-stats` slash-command.
"""
from __future__ import annotations

import asyncio
import fnmatch
import logging
import os
import re
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ─── Config ──────────────────────────────────────────────────────
_REPO_ROOT = Path(os.getenv("ORA_REPO_ROOT", "/app"))
_SCAN_DIRS = [
    _REPO_ROOT / "backend",
    _REPO_ROOT / "frontend" / "src",
]
_SKIP_DIR_NAMES = {
    "node_modules", ".git", "__pycache__", ".pytest_cache",
    "build", "dist", ".next", ".venv", "venv", ".cache",
    "coverage", ".turbo", "test_reports",
}
_ALLOWED_EXT = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
    ".css", ".scss", ".html", ".json", ".yaml", ".yml",
    ".md", ".env.example", ".toml",
}
_MAX_BYTES_PER_FILE_READ = 8 * 1024   # 8 KB per file when index building
_MAX_LINES_PER_FILE_READ = 200
_MAX_INDEXED_FILES        = 1500
_INDEX_TTL_S              = 15 * 60

_PY_DEF_RE = re.compile(r"^\s*(?:async\s+)?(?:def|class)\s+([A-Za-z_][A-Za-z0-9_]*)", re.M)
_JS_DEF_RE = re.compile(
    r"^\s*(?:export\s+)?(?:async\s+)?"
    r"(?:function\s+([A-Za-z_$][\w$]*)"
    r"|(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\("
    r"|class\s+([A-Za-z_$][\w$]*))",
    re.M,
)


# ─── In-memory cache ─────────────────────────────────────────────
_CACHE: dict = {
    "built_at":     0.0,
    "files":        [],       # list of {path, size, lang, defs, head}
    "by_path":      {},       # path → file dict (fast lookup)
    "def_to_paths": {},       # symbol → list[str]
    "total_bytes":  0,
    "lock":         asyncio.Lock(),
}


def _rel(p: Path) -> str:
    """Return the repo-relative path (e.g. `backend/services/foo.py`)."""
    try:
        return str(p.relative_to(_REPO_ROOT))
    except ValueError:
        return str(p)


def _lang_of(p: Path) -> str:
    return {
        ".py": "python", ".js": "javascript", ".jsx": "javascript",
        ".ts": "typescript", ".tsx": "typescript", ".mjs": "javascript",
        ".cjs": "javascript", ".css": "css", ".scss": "scss",
        ".html": "html", ".json": "json", ".yaml": "yaml", ".yml": "yaml",
        ".md": "markdown", ".toml": "toml",
    }.get(p.suffix, "text")


def _extract_defs(text: str, lang: str) -> list[str]:
    """Cheap def-name extraction — regex, not AST. Bounded to 25 defs
    per file (huge auto-generated files won't dominate the index)."""
    names: list[str] = []
    if lang == "python":
        names = _PY_DEF_RE.findall(text)
    elif lang in ("javascript", "typescript"):
        for m in _JS_DEF_RE.finditer(text):
            for g in m.groups():
                if g:
                    names.append(g)
                    break
    # De-dupe while preserving order.
    seen: set[str] = set()
    out: list[str] = []
    for n in names:
        if n not in seen and not n.startswith("_"):
            seen.add(n)
            out.append(n)
            if len(out) >= 25:
                break
    return out


def _read_head(p: Path) -> str:
    """Read the first 200 lines / 8 KB (whichever is smaller). Silently
    returns "" on any I/O error so the walker never crashes."""
    try:
        with p.open("r", encoding="utf-8", errors="replace") as f:
            data = f.read(_MAX_BYTES_PER_FILE_READ)
        lines = data.splitlines()[:_MAX_LINES_PER_FILE_READ]
        return "\n".join(lines)
    except (OSError, UnicodeDecodeError):
        return ""


def _should_index(p: Path) -> bool:
    if p.suffix not in _ALLOWED_EXT and p.name != ".env.example":
        return False
    parts = p.parts
    for name in _SKIP_DIR_NAMES:
        if name in parts:
            return False
    if p.name.startswith("."):
        return False
    return True


def _walk_scan_dirs() -> list[Path]:
    """Enumerate every candidate file across the scan roots. Deterministic
    order so index_stats are stable across calls."""
    out: list[Path] = []
    for root in _SCAN_DIRS:
        if not root.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            # Prune skipped dirs in-place (os.walk honors this).
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIR_NAMES
                            and not d.startswith(".")]
            for name in filenames:
                p = Path(dirpath) / name
                if _should_index(p):
                    out.append(p)
                    if len(out) >= _MAX_INDEXED_FILES:
                        return out
    return out


async def build_index(force: bool = False) -> dict:
    """Populate `_CACHE`. Cheap enough to call from startup + refresh
    on a 15-min TTL. Concurrent callers coalesce via the async lock."""
    async with _CACHE["lock"]:
        if not force and _CACHE["files"] \
                and (time.time() - _CACHE["built_at"] < _INDEX_TTL_S):
            return {"ok": True, "cached": True,
                     "files": len(_CACHE["files"])}
        t0 = time.time()

        def _worker() -> tuple[list, dict, dict, int]:
            files: list[dict] = []
            by_path: dict = {}
            def_to_paths: dict[str, list[str]] = {}
            total_bytes = 0
            for p in _walk_scan_dirs():
                try:
                    size = p.stat().st_size
                except OSError:
                    continue
                head = _read_head(p)
                lang = _lang_of(p)
                defs = _extract_defs(head, lang)
                rel = _rel(p)
                doc = {"path": rel, "size": size, "lang": lang,
                        "defs": defs, "head": head[:2000]}
                files.append(doc)
                by_path[rel] = doc
                total_bytes += size
                for d in defs:
                    def_to_paths.setdefault(d, []).append(rel)
            return files, by_path, def_to_paths, total_bytes

        files, by_path, def_to_paths, total_bytes = \
            await asyncio.to_thread(_worker)

        _CACHE["files"] = files
        _CACHE["by_path"] = by_path
        _CACHE["def_to_paths"] = def_to_paths
        _CACHE["total_bytes"] = total_bytes
        _CACHE["built_at"] = time.time()
        logger.info("codebase_index built: %d files, %.1f KB, %.2fs",
                    len(files), total_bytes / 1024.0, time.time() - t0)
        return {"ok": True, "cached": False,
                 "files": len(files),
                 "bytes": total_bytes,
                 "elapsed_s": round(time.time() - t0, 3)}


async def _ensure_fresh() -> None:
    """Cheap TTL check — rebuilds when stale, otherwise no-op."""
    if not _CACHE["files"] \
            or (time.time() - _CACHE["built_at"] > _INDEX_TTL_S):
        await build_index(force=True)


# ─── Public helpers ──────────────────────────────────────────────
async def compact_tree(max_files: int = 120) -> str:
    """Return a compact top-level directory listing for the system prompt.
    Groups files by directory, caps at `max_files` overall so the block
    stays under ~4 KB.
    """
    await _ensure_fresh()
    files = _CACHE["files"]
    # Group by first two path components.
    groups: dict[str, list[str]] = {}
    for f in files:
        parts = f["path"].split("/")
        key = "/".join(parts[:2]) if len(parts) > 2 else parts[0]
        groups.setdefault(key, []).append("/".join(parts[2:]) if len(parts) > 2 else parts[-1])
    # Cap group members so no single dir dominates.
    lines: list[str] = ["AUREM repo tree (compact — auto-generated, always fresh):"]
    shown = 0
    for key in sorted(groups):
        members = groups[key][:8]
        lines.append(f"  {key}/  ({len(groups[key])} files)")
        for m in members:
            lines.append(f"    {m}")
            shown += 1
            if shown >= max_files:
                break
        if shown >= max_files:
            lines.append("    ... (truncated, use /find or /read for more)")
            break
    return "\n".join(lines)


async def find_files(pattern: str, limit: int = 25) -> list[str]:
    """Glob match against repo-relative paths.
    Example: `services/ora_chat/*.py` or `*Drawer.jsx`.
    """
    await _ensure_fresh()
    if not pattern:
        return []
    pat = pattern.strip()
    if "*" not in pat and "?" not in pat and "[" not in pat:
        pat = f"*{pat}*"
    matches = [f["path"] for f in _CACHE["files"]
                if fnmatch.fnmatch(f["path"], pat)]
    return matches[:limit]


async def read_file(path: str, max_lines: int = 200) -> dict:
    """Read a repo-relative file, capped at max_lines / 40 KB.
    Path is validated to live under _REPO_ROOT — no `..` escapes.
    """
    await _ensure_fresh()
    # Normalise + validate
    rel = path.strip().lstrip("/")
    if ".." in rel.split("/"):
        return {"ok": False, "error": "path_traversal_blocked"}
    p = (_REPO_ROOT / rel).resolve()
    try:
        p.relative_to(_REPO_ROOT.resolve())
    except ValueError:
        return {"ok": False, "error": "outside_repo_root"}
    if not p.exists() or not p.is_file():
        return {"ok": False, "error": "not_found", "path": rel}
    if p.suffix not in _ALLOWED_EXT and p.name != ".env.example":
        return {"ok": False, "error": "extension_not_allowed",
                 "path": rel}
    try:
        with p.open("r", encoding="utf-8", errors="replace") as f:
            data = f.read(40 * 1024)
    except OSError as e:
        return {"ok": False, "error": f"read_fail:{type(e).__name__}"}
    lines = data.splitlines()
    truncated = len(lines) > max_lines
    body = "\n".join(lines[:max_lines])
    return {"ok": True, "path": rel, "lang": _lang_of(p),
             "lines_shown": min(len(lines), max_lines),
             "total_lines": len(lines),
             "truncated": truncated,
             "content": body}


async def search_defs(name: str, limit: int = 15) -> list[dict]:
    """Where is `name` defined? Returns [{name, path, lang}, ...]."""
    await _ensure_fresh()
    if not name:
        return []
    target = name.strip()
    results: list[dict] = []
    # Exact match on the def_to_paths map first.
    paths = _CACHE["def_to_paths"].get(target, [])
    for p in paths[:limit]:
        f = _CACHE["by_path"].get(p)
        if f:
            results.append({"name": target, "path": p, "lang": f["lang"]})
    if results:
        return results
    # Fallback: fuzzy contains (case-insensitive) over def names.
    target_lo = target.lower()
    for sym, paths in _CACHE["def_to_paths"].items():
        if target_lo in sym.lower():
            for p in paths[:2]:
                f = _CACHE["by_path"].get(p)
                if f:
                    results.append({"name": sym, "path": p, "lang": f["lang"]})
                    if len(results) >= limit:
                        return results
    return results


# ─── BM25-lite retrieval for the NEEDS_CODEBASE branch ───────────
_STOPWORDS = {
    "the", "is", "in", "at", "of", "and", "or", "to", "for", "on",
    "a", "an", "how", "does", "do", "our", "we", "have", "has", "with",
    "kya", "hai", "mein", "aur", "yaha", "wo", "ye", "kaise", "kaunsa",
    "kar", "karo", "karta", "karti", "kiya", "hoga", "bhai",
}


def _tokenize(text: str) -> list[str]:
    return [t for t in re.findall(r"[A-Za-z_][A-Za-z0-9_]+", text.lower())
             if t not in _STOPWORDS and len(t) > 2]


async def bm25_relevant_files(query: str, top_k: int = 3) -> list[dict]:
    """Naïve term-frequency ranking. Not a full BM25 — just enough
    signal to pick the 3 most-relevant files when NEEDS_CODEBASE
    fires. Returns [{path, score, head_excerpt}, ...] sorted by score.
    """
    await _ensure_fresh()
    q_tokens = set(_tokenize(query))
    if not q_tokens:
        return []
    scores: list[tuple[float, dict]] = []
    for f in _CACHE["files"]:
        haystack = (f["path"] + " " + " ".join(f["defs"]) + " " + f["head"]).lower()
        score = 0.0
        for t in q_tokens:
            # Path & def hits weighted higher (they name the concept).
            if t in f["path"].lower():
                score += 3.0
            if any(t == d.lower() or t in d.lower() for d in f["defs"]):
                score += 2.0
            score += haystack.count(t) * 0.5
        if score > 0:
            scores.append((score, f))
    scores.sort(key=lambda x: -x[0])
    return [{"path": f["path"], "score": round(s, 2),
              "head_excerpt": f["head"][:1200]}
             for s, f in scores[:top_k]]


async def index_stats() -> dict:
    await _ensure_fresh()
    langs: dict[str, int] = {}
    for f in _CACHE["files"]:
        langs[f["lang"]] = langs.get(f["lang"], 0) + 1
    return {
        "ok": True,
        "files":        len(_CACHE["files"]),
        "total_bytes":  _CACHE["total_bytes"],
        "total_kb":     round(_CACHE["total_bytes"] / 1024.0, 1),
        "by_language":  dict(sorted(langs.items(), key=lambda x: -x[1])),
        "def_count":    len(_CACHE["def_to_paths"]),
        "built_at":     _CACHE["built_at"],
        "age_s":        round(time.time() - _CACHE["built_at"], 1),
        "scan_roots":   [str(r) for r in _SCAN_DIRS],
    }
