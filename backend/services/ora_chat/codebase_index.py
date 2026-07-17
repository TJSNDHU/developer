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
    # English
    "the", "is", "in", "at", "of", "and", "or", "to", "for", "on",
    "a", "an", "how", "does", "do", "our", "we", "have", "has", "with",
    "this", "that", "what", "which", "who", "when", "where", "why",
    "are", "was", "were", "be", "been", "being", "will", "would",
    "should", "could", "can", "may", "might", "must", "shall",
    "not", "no", "yes", "any", "some", "all", "each", "every",
    "system", "systems", "build", "builds", "gap", "gaps", "code",
    "codebase", "app", "apps", "file", "files", "part", "parts",
    "best", "worst", "good", "bad", "great", "better", "worse",
    "kind", "type", "types", "thing", "things", "here", "there",
    "get", "got", "give", "given", "make", "made", "take", "taken",
    "want", "need", "know", "think", "see", "seen", "look", "looking",
    "abhi", "still", "yet", "now", "then", "soon", "later",
    # Hindi / Hinglish
    "kya", "hai", "mein", "aur", "yaha", "wo", "ye", "yah", "yeh",
    "kaise", "kaunsa", "kaunsi", "kar", "karo", "karta", "karti",
    "kiya", "hoga", "hogi", "hoge", "bhai", "hain", "ho", "hum",
    "hmara", "hmari", "mera", "meri", "mere", "apna", "apni", "apne",
    "main", "mainn", "tum", "tera", "teri", "tere", "aap", "aapka",
    "bhi", "toh", "to", "na", "nahi", "nahin", "haan", "ji", "sir",
    "koi", "kuch", "sab", "sabb", "sabhi", "log", "logon", "banaya",
    "banana", "banate", "banai", "banate", "diya", "diye", "de", "do",
    "achha", "accha", "kaisa", "kaisi", "acha", "bura", "buri",
    "chal", "chalo", "chalte", "hoti", "hota", "hote", "wala", "wali",
    "wale", "abhi", "phir", "fir", "waise", "vaise",
}


def _tokenize(text: str) -> list[str]:
    return [t for t in re.findall(r"[A-Za-z_][A-Za-z0-9_]+", text.lower())
             if t not in _STOPWORDS and len(t) > 2]


async def bm25_relevant_files(query: str, top_k: int = 3,
                                min_tokens: int = 2,
                                min_score: float = 3.5) -> list[dict]:
    """Naïve term-frequency ranking. Not a full BM25 — just enough
    signal to pick the most-relevant files when NEEDS_CODEBASE
    fires. Returns [{path, score, head_excerpt}, ...] sorted by score.

    Guardrails to prevent noisy meta-queries (e.g. "what's the best
    build in our system?") from surfacing random files:
      - `min_tokens`: require at least 2 substantive tokens after
        stopword strip. Meta-questions typically have 0-1 real content
        tokens and shouldn't trigger retrieval at all.
      - `min_score`: reject results whose top score is below the
        threshold. Generic tokens ("system", "app", "code") get
        stopworded away, but if the residual signal is still weak,
        we return [] and let the model answer from its baseline
        AUREM_CONTEXT + system-highlights block instead.
    """
    await _ensure_fresh()
    q_tokens = set(_tokenize(query))
    if len(q_tokens) < min_tokens:
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
        if score >= min_score:
            scores.append((score, f))
    scores.sort(key=lambda x: -x[0])
    return [{"path": f["path"], "score": round(s, 2),
              "head_excerpt": f["head"][:1200]}
             for s, f in scores[:top_k]]


async def system_highlights() -> str:
    """Curated ground-truth summary of AUREM's crown-jewel subsystems.

    This block is auto-injected into every system prompt AFTER the
    compact tree. Unlike the raw file tree, it names the actual
    major features so ORA can answer "kya best build hai hmara
    system mein" / "what does AUREM actually do" without depending
    on BM25 retrieval (which is noisy for generic meta-questions).

    Counts are computed live from the index so the block stays
    honest as the codebase grows.
    """
    await _ensure_fresh()
    counts = {"routers": 0, "services": 0, "pages": 0,
               "components": 0, "tests": 0}
    for f in _CACHE["files"]:
        p = f["path"]
        if p.startswith("backend/routers/") and p.endswith(".py"):     counts["routers"] += 1
        elif p.startswith("backend/services/") and p.endswith(".py"):  counts["services"] += 1
        elif p.startswith("frontend/src/pages/") and p.endswith((".jsx", ".tsx")): counts["pages"] += 1
        elif p.startswith("frontend/src/components/") and p.endswith((".jsx", ".tsx")): counts["components"] += 1
        elif p.startswith("backend/tests/") and p.endswith(".py"):     counts["tests"] += 1

    lines = [
        "AUREM system highlights (curated ground truth — cite these "
        "when asked 'what does AUREM have' / 'best builds' / 'kya "
        "banaya hai'; use /read for exact code):",
        "",
        f"  Repo scale: {counts['routers']} backend routers · "
        f"{counts['services']} services · {counts['pages']} pages · "
        f"{counts['components']} components · {counts['tests']} test files",
        "",
        "  Core subsystems (in rough order of engineering weight):",
        "    1. Council + Loop Engine — multi-agent orchestration for code review",
        "       backend/routers/loop.py, backend/services/loop_engine.py,",
        "       frontend/src/pages/admin/AdminParliamentLive.jsx",
        "    2. Personal Track (T0-T4) — non-technical user app-generation flow",
        "       backend/routers/personal_track.py, frontend/src/pages/personal/",
        "    3. Feature Window + Security Gate — spec-driven guarded feature builds",
        "       backend/routers/feature_window.py, frontend/src/pages/FeatureWindow.jsx",
        "    4. ORA Chat (founder-only) — multi-model routing + deep-research + PIN + codebase awareness",
        "       backend/routers/ora_chat.py, backend/services/ora_chat/*",
        "    5. Stripe Billing — subscriptions + credits + gates + reconciliation cron",
        "       backend/services/stripe_client.py, backend/services/billing_cron.py, backend/routers/payments.py",
        "    6. Ask Advisor — GLM/Claude explain-my-code assistant",
        "       backend/routers/chat.py (Ask Advisor branch), frontend/src/pages/Dashboard.jsx",
        "    7. Codebase Health / Bug Hunt / Findings pipeline — static scanners + fix jobs",
        "       backend/routers/codebase_health.py, backend/services/full_scan_orchestrator.py,",
        "       backend/routers/findings.py, backend/services/fix_pipeline.py",
        "    8. GitHub + Vercel + Supabase integrations — PAT/OAuth flows + deploy pipeline",
        "       backend/routers/github_oauth.py, backend/routers/deploy.py, backend/routers/managed_db.py",
        "    9. Admin panel — feature flags, analytics, LLM credits, integrations, vanguard controls",
        "       backend/routers/admin*.py, frontend/src/pages/Admin*.jsx",
        "   10. Codebase Indexer (this layer!) — Ora's own repo awareness",
        "       backend/services/ora_chat/codebase_index.py, backend/services/codebase_indexer.py",
        "",
        "  IMPORTANT for the model reading this: If the question is a "
        "META question about the system as a WHOLE (best/worst/gaps/"
        "overview/'kya hai overall'), answer from THIS block, not "
        "from a BM25 codebase snippet — BM25 will pick random files "
        "for meta-queries. If the question names a SPECIFIC subsystem "
        "or file, THEN use /read or /find.",
    ]
    return "\n".join(lines)


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
