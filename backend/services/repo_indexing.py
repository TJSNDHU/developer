"""
services/repo_indexing.py — Deterministic, zero-LLM static analysis of a
connected GitHub repo. Generates a CODEBASE.md file the user can use as
an at-a-glance map of their own project; everything is computed from a
single recursive `git/trees` call plus an optional README fetch.

NO LLM calls. NO local filesystem. NO `git` binary. Pure REST + Python.

Output schema (also persisted to MongoDB `repo_index`):
  {
    "project_id":            str,
    "user_id":               str,
    "owner":                 str,
    "repo":                  str,
    "branch":                str,
    "dominant_language":     str,           # e.g. "Python"
    "language_breakdown":    {ext: count},  # {"py": 47, "tsx": 12}
    "entry_points":          [str, ...],    # ["main.py", "frontend/src/App.tsx"]
    "service_folders":       [str, ...],    # ["backend/services", "backend/routers"]
    "dependency_files":      [str, ...],    # ["requirements.txt", "package.json"]
    "has_tests":             bool,
    "file_count":            int,
    "readme_title":          str | None,
    "readme_summary":        str | None,
    "codebase_md":           str,           # the rendered file
    "commit_sha":            str | None,    # set after commit_files() returns
    "commit_url":            str | None,
    "indexed_at":            ISO-string,
  }
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from collections import Counter
from datetime import datetime, timezone
from typing import Optional

import httpx

from cto_services.db import get_db
from services.repo_context import _fetch_tree, _fetch_file
from services.github_api_writer import commit_files

logger = logging.getLogger(__name__)


# ── Heuristics ───────────────────────────────────────────────────────
# Mapping of common file extensions → human-friendly language name.
# Anything not in this table is treated as "other" for the breakdown.
_LANG_BY_EXT: dict[str, str] = {
    "py":    "Python",
    "js":    "JavaScript",
    "jsx":   "JavaScript (React)",
    "ts":    "TypeScript",
    "tsx":   "TypeScript (React)",
    "go":    "Go",
    "rs":    "Rust",
    "java":  "Java",
    "kt":    "Kotlin",
    "swift": "Swift",
    "rb":    "Ruby",
    "php":   "PHP",
    "cs":    "C#",
    "cpp":   "C++",
    "c":     "C",
    "scala": "Scala",
    "vue":   "Vue",
    "svelte": "Svelte",
    "dart":  "Dart",
}

# Files at the repo root (or one level deep) that signify the server's
# main entry point. Order matters: first match wins per layer.
_ENTRY_CANDIDATES: tuple[str, ...] = (
    "main.py", "app.py", "server.py", "manage.py",
    "wsgi.py", "asgi.py", "run.py",
    "index.js", "index.ts", "server.js", "server.ts",
    "src/index.js", "src/index.ts",
    "src/main.js", "src/main.ts",
    "src/main.jsx", "src/main.tsx",
    "src/App.jsx", "src/App.tsx",
    "backend/main.py", "backend/server.py", "backend/app.py",
    "frontend/src/App.jsx", "frontend/src/App.tsx",
    "frontend/src/main.jsx", "frontend/src/main.tsx",
    "pages/_app.tsx", "pages/_app.js",
    "app/page.tsx", "app/page.jsx",
)

# Top-level folder names that almost always indicate a "service" layer
# in a backend codebase. We surface these in CODEBASE.md so the user
# knows where their business logic lives without grep'ing manually.
_SERVICE_FOLDER_NAMES: frozenset[str] = frozenset({
    "api", "apis", "routers", "routes",
    "services", "service",
    "models", "model", "schemas", "schema",
    "db", "database", "data",
    "utils", "lib", "helpers", "core",
    "controllers", "handlers",
    "components", "pages", "views",
    "middleware", "middlewares",
})

# Dependency manifest filenames we report on. Order = display order.
_DEP_FILES: tuple[str, ...] = (
    "requirements.txt", "pyproject.toml", "Pipfile", "setup.py",
    "package.json", "yarn.lock", "pnpm-lock.yaml",
    "go.mod", "go.sum", "Cargo.toml", "Cargo.lock",
    "Gemfile", "composer.json", "build.gradle",
    "pom.xml", "Dockerfile", "docker-compose.yml",
)


# ── Public API ───────────────────────────────────────────────────────
async def build_repo_index(
    *,
    user_id: str,
    project_id: str,
    commit: bool = True,
) -> dict:
    """End-to-end repo index for one project.

    1. Verifies ownership in `cto_projects`.
    2. Fetches the full recursive tree via the existing `_fetch_tree`
       (which already wraps caching + secondary rate-limit handling).
    3. Computes the deterministic metrics described in the module docstring.
    4. Optionally fetches README.md to extract the first H1 + paragraph.
    5. Renders `CODEBASE.md`.
    6. Persists every metric into `repo_index` (upsert on project_id).
    7. If `commit=True`, pushes `CODEBASE.md` to the repo root via the
       existing `commit_files()` writer (single atomic commit).

    Never raises — errors are surfaced via the returned dict's `errors`
    list so the caller (background task / route) can still respond.
    """
    out: dict = {
        "ok":         False,
        "project_id": project_id,
        "errors":     [],
        "started_at": datetime.now(timezone.utc).isoformat(),
    }

    db = get_db()
    if db is None:
        out["errors"].append("database unavailable")
        return out

    proj = await db.cto_projects.find_one(
        {"project_id": project_id, "user_id": user_id},
    )
    if not proj:
        out["errors"].append("project not found or not owned by caller")
        return out

    owner  = (proj.get("github_owner") or "").strip()
    repo   = (proj.get("github_repo")  or "").strip()
    branch = (proj.get("branch")       or "main").strip()
    token  = proj.get("github_token")  or None
    if not (owner and repo):
        out["errors"].append("project has no github_owner/github_repo")
        return out

    # Decrypt PAT if it's stored encrypted (v1:-prefixed). The existing
    # repo_context helpers already pass the token straight through, but
    # commit_files needs a real plaintext PAT to upload blobs.
    plaintext_token = await _maybe_decrypt(user_id, token)

    # ── Step 1: Tree fetch ───────────────────────────────────────
    try:
        tree, _truncated = await _fetch_tree(
            owner, repo, branch, plaintext_token,
        )
    except httpx.HTTPStatusError as e:
        out["errors"].append(
            f"github tree fetch failed (HTTP {e.response.status_code})"
        )
        return out
    except Exception as e:
        out["errors"].append(f"github tree fetch failed: {e!r}")
        return out

    # ── Step 2: Static analysis ──────────────────────────────────
    metrics = _analyse_tree(tree)

    # ── Step 3: README ───────────────────────────────────────────
    readme_title, readme_summary = await _readme_summary(
        owner, repo, branch, plaintext_token,
    )

    # ── Step 4: Render CODEBASE.md ───────────────────────────────
    codebase_md = _render_codebase_md(
        owner=owner, repo=repo, branch=branch,
        metrics=metrics,
        readme_title=readme_title,
        readme_summary=readme_summary,
    )

    record = {
        "project_id":         project_id,
        "user_id":            user_id,
        "owner":              owner,
        "repo":               repo,
        "branch":             branch,
        "dominant_language":  metrics["dominant_language"],
        "language_breakdown": metrics["language_breakdown"],
        "entry_points":       metrics["entry_points"],
        "service_folders":    metrics["service_folders"],
        "dependency_files":   metrics["dependency_files"],
        "has_tests":          metrics["has_tests"],
        "file_count":         metrics["file_count"],
        "readme_title":       readme_title,
        "readme_summary":     readme_summary,
        "codebase_md":        codebase_md,
        "indexed_at":         datetime.now(timezone.utc).isoformat(),
    }

    # ── Step 5: Optional commit ──────────────────────────────────
    if commit and plaintext_token:
        try:
            commit_info = await commit_files(
                owner=owner, repo=repo, branch=branch,
                token=plaintext_token,
                files={"CODEBASE.md": codebase_md},
                commit_message="chore(aurem): regenerate CODEBASE.md",
            )
            record["commit_sha"] = commit_info.get("sha")
            record["commit_url"] = commit_info.get("html_url")
        except Exception as e:
            # Index data is still useful even if the commit failed
            # (e.g. PAT lacks Contents:write). Surface it but don't bail.
            out["errors"].append(f"commit failed: {e!r}")
            record["commit_sha"] = None
            record["commit_url"] = None
    else:
        record["commit_sha"] = None
        record["commit_url"] = None

    # ── Step 6: Persist ──────────────────────────────────────────
    try:
        await db.repo_index.update_one(
            {"project_id": project_id},
            {"$set": record},
            upsert=True,
        )
        # Make sure the index on project_id exists. Idempotent.
        await db.repo_index.create_index("project_id", unique=True)
    except Exception as e:
        out["errors"].append(f"persist failed: {e!r}")

    out.update(record)
    out["ok"] = True
    out["finished_at"] = datetime.now(timezone.utc).isoformat()
    return out


# ── Internals ────────────────────────────────────────────────────────
async def _maybe_decrypt(user_id: str, token: Optional[str]) -> Optional[str]:
    """Pass-through if `token` is None or already plaintext; otherwise
    use the vault to decrypt. Never raises — returns None on failure."""
    if not token:
        return None
    if not token.startswith("v1:"):
        return token
    try:
        from services.vault import decrypt
        return await decrypt(user_id, token, kind="github_token")
    except Exception as e:
        logger.warning("repo_indexing: PAT decrypt failed: %r", e)
        return None


def _analyse_tree(tree: list[dict]) -> dict:
    """Deterministic per-tree metrics. Pure function — no I/O."""
    paths = [n.get("path") or "" for n in tree if n.get("type") == "blob"]
    dir_paths = [n.get("path") or "" for n in tree if n.get("type") == "tree"]
    # Also infer directories from blob paths so the static analysis works
    # even on tree responses that only contain blobs (some unit-test
    # fixtures + truncated trees).
    inferred_dirs: set[str] = set()
    for p in paths:
        parts = p.split("/")
        for i in range(1, len(parts)):
            inferred_dirs.add("/".join(parts[:i]))
    dir_paths = sorted(set(dir_paths) | inferred_dirs)
    file_count = len(paths)

    # ── Language breakdown ────────────────────────────────────────
    ext_counter: Counter[str] = Counter()
    for p in paths:
        ext = p.rsplit(".", 1)[-1].lower() if "." in p.rsplit("/", 1)[-1] else ""
        if ext and ext in _LANG_BY_EXT:
            ext_counter[ext] += 1
    language_breakdown = dict(ext_counter)
    if ext_counter:
        top_ext, _ = ext_counter.most_common(1)[0]
        dominant_language = _LANG_BY_EXT.get(top_ext, top_ext.upper())
    else:
        dominant_language = "unknown"

    # ── Entry points ──────────────────────────────────────────────
    path_set = set(paths)
    entry_points = [c for c in _ENTRY_CANDIDATES if c in path_set]

    # ── Top-level service folders ─────────────────────────────────
    service_folders_set: set[str] = set()
    for dp in dir_paths:
        # We care about depth ≤ 2: e.g. "backend/services", "src/components"
        segs = dp.split("/")
        if not segs:
            continue
        for i in range(min(2, len(segs))):
            seg = segs[i]
            if seg in _SERVICE_FOLDER_NAMES:
                # Reconstruct the path up to and including this segment
                service_folders_set.add("/".join(segs[: i + 1]))
                break
    service_folders = sorted(service_folders_set)

    # ── Dependency manifests ──────────────────────────────────────
    dep_present: list[str] = [d for d in _DEP_FILES if d in path_set]
    # Also pick up nested manifests like backend/requirements.txt
    for p in paths:
        base = p.rsplit("/", 1)[-1]
        if base in _DEP_FILES and p not in dep_present:
            dep_present.append(p)

    # ── Tests folder presence ─────────────────────────────────────
    has_tests = any(
        seg in ("tests", "test", "__tests__", "spec")
        for dp in dir_paths
        for seg in dp.split("/")
    )

    return {
        "dominant_language":  dominant_language,
        "language_breakdown": language_breakdown,
        "entry_points":       entry_points,
        "service_folders":    service_folders,
        "dependency_files":   dep_present,
        "has_tests":          has_tests,
        "file_count":         file_count,
    }


_README_H1_RE = re.compile(r"^\s*#\s+(.+?)\s*$", re.MULTILINE)


async def _readme_summary(
    owner: str, repo: str, branch: str, token: Optional[str],
) -> tuple[Optional[str], Optional[str]]:
    """Fetch README.md (or fallback variants) and return
    `(first_h1_title, first_paragraph)`. Both may be None when no
    README exists or it has no H1 / no body."""
    for candidate in ("README.md", "readme.md", "Readme.md", "README"):
        try:
            body = await _fetch_file(owner, repo, candidate, branch, token)
        except Exception:
            body = None
        if not body:
            continue

        title: Optional[str] = None
        m = _README_H1_RE.search(body)
        if m:
            title = m.group(1).strip()

        # First non-empty, non-heading paragraph after the first H1
        # (or from the very top if no H1).
        body_after = body[m.end():] if m else body
        paragraphs = re.split(r"\n\s*\n", body_after.strip())
        summary: Optional[str] = None
        for para in paragraphs:
            line = para.strip()
            if not line:
                continue
            if line.startswith("#"):
                continue
            # Strip simple markdown noise so the persisted summary is
            # readable as plain text.
            line = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", line)  # images
            line = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", line)  # links
            line = re.sub(r"`([^`]+)`", r"\1", line)
            line = re.sub(r"\s+", " ", line).strip()
            if line:
                summary = line[:400]
                break
        return title, summary
    return None, None


def _render_codebase_md(
    *,
    owner: str, repo: str, branch: str,
    metrics: dict,
    readme_title: Optional[str],
    readme_summary: Optional[str],
) -> str:
    """Pure function — renders the CODEBASE.md committed back to the repo.

    Stable, idempotent layout so the diff between two runs is minimal
    (mostly the date stamp + file counts when nothing else changed)."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines: list[str] = []
    lines.append(f"# {readme_title or repo} — Codebase Map")
    lines.append("")
    lines.append(
        f"_Auto-generated by Aurem CTO on **{now}** from "
        f"`{owner}/{repo}@{branch}` — do not edit by hand; this file "
        "is overwritten on each `repo index` run._"
    )
    lines.append("")

    if readme_summary:
        lines.append("## Summary")
        lines.append("")
        lines.append(readme_summary)
        lines.append("")

    lines.append("## At a glance")
    lines.append("")
    lines.append(f"- **Dominant language:** {metrics['dominant_language']}")
    lines.append(f"- **Source files indexed:** {metrics['file_count']}")
    lines.append(f"- **Has tests folder:** {'yes' if metrics['has_tests'] else 'no'}")
    lines.append("")

    if metrics["language_breakdown"]:
        lines.append("## Languages (by file count)")
        lines.append("")
        lines.append("| Extension | Files | Language |")
        lines.append("|---|---:|---|")
        for ext, count in sorted(
            metrics["language_breakdown"].items(),
            key=lambda kv: kv[1], reverse=True,
        ):
            lines.append(f"| `.{ext}` | {count} | {_LANG_BY_EXT.get(ext, ext)} |")
        lines.append("")

    if metrics["entry_points"]:
        lines.append("## Entry points")
        lines.append("")
        for p in metrics["entry_points"]:
            lines.append(f"- `{p}`")
        lines.append("")

    if metrics["service_folders"]:
        lines.append("## Service / layer folders")
        lines.append("")
        for f in metrics["service_folders"]:
            lines.append(f"- `{f}/`")
        lines.append("")

    if metrics["dependency_files"]:
        lines.append("## Dependency manifests")
        lines.append("")
        for d in metrics["dependency_files"]:
            lines.append(f"- `{d}`")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(
        "_Generated by [Aurem CTO](https://auremcto.com) — your AI "
        "engineering co-pilot._"
    )
    return "\n".join(lines).rstrip() + "\n"
