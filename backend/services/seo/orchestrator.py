"""
services/seo/orchestrator.py — End-to-end run for one project.

Flow:
  1. Load project doc (validates ownership + carries github token).
  2. Fetch the recursive repo tree via GitHub REST.
  3. For each feature enabled by the user's plan tier:
       - Read the files we need via parallel GH fetch (asyncio.gather)
       - Build a SeoPatch via the relevant fixer module
  4. Atomically commit ALL patches in a single commit using the
     existing services.github_api_writer.commit_files().
  5. Return a result dict with per-feature patch summary +
     commit metadata (sha, html_url).

`dry_run=True` SKIPS step 4 and returns the patches as-is — useful
for tests, admin spot-check, and a "preview the diff" UI later.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import httpx

from cto_services.db import get_db
from services.repo_context import _fetch_tree, _fetch_file
from services.github_api_writer import commit_files

from .meta_tags     import SeoPatch, patch_meta_tags
from .schema_markup import patch_schema_markup
from .robots_txt    import patch_robots_txt
from .sitemap       import patch_sitemap
from .image_alts    import patch_image_alts

logger = logging.getLogger(__name__)

# ── Plan tier matrix ─────────────────────────────────────────────────
# Category A only for now; B/C deferred per user (PR-1 scope).
PLAN_FEATURES: dict[str, frozenset[str]] = {
    "swift": frozenset({"meta", "schema", "robots", "sitemap", "alts"}),
    "pro":   frozenset({"meta", "schema", "robots", "sitemap", "alts"}),
    "maxx":  frozenset({"meta", "schema", "robots", "sitemap", "alts"}),
}

# How many HTML files we'll touch in one run. Above this we bail with
# a clear message so we don't write 500 files in one mega-commit and
# blow GitHub's blob limit.
_MAX_HTML_FILES = 50
# Parallel GitHub reads (rate-limit safe).
_FETCH_CONCURRENCY = 6


@dataclass
class SeoOptions:
    """Per-run knobs passed by the caller (chat tool / admin endpoint /
    background worker).  All optional — sensible defaults below."""
    plan:            str = "swift"
    site_url:        str = ""
    title:           str = ""
    description:     str = ""
    og_image:        str = ""
    author:          str = ""
    commit_message:  str = "chore(seo): aurem auto-fix"
    dry_run:         bool = False
    # Inject during tests so the LLM alt-text generator is replaced.
    alt_provider:    Optional[object] = field(default=None, repr=False)


async def _fetch_optional(
    owner: str, repo: str, path: str, branch: str, token: Optional[str],
) -> Optional[str]:
    """GET a file; return None on 404 / any failure."""
    try:
        return await _fetch_file(owner, repo, path, branch, token)
    except Exception:
        return None


def _is_html_path(p: str) -> bool:
    return p.endswith((".html", ".htm"))


async def run_seo_fixes(
    *,
    user_id: str,
    project_id: str,
    options: SeoOptions,
) -> dict:
    """Run every plan-enabled SEO fix on `project_id` for `user_id`.

    Returns a structured dict the caller can return verbatim from a
    FastAPI route, OR pass to the chat composer for a tool-result
    block. Never raises — every recoverable failure ends up in the
    returned dict's `errors` list so the UI can show partial success.
    """
    out: dict = {
        "ok":         False,
        "plan":       options.plan,
        "project_id": project_id,
        "dry_run":    options.dry_run,
        "patches":    [],
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

    features = PLAN_FEATURES.get(options.plan, PLAN_FEATURES["swift"])
    out["features_enabled"] = sorted(features)

    # ── Fetch tree ────────────────────────────────────────────────
    try:
        tree, _truncated = await _fetch_tree(owner, repo, branch, token)
    except httpx.HTTPStatusError as e:
        out["errors"].append(
            f"github tree fetch failed (HTTP {e.response.status_code})"
        )
        return out
    except Exception as e:
        out["errors"].append(f"github tree fetch failed: {e!r}")
        return out

    all_paths = [n.get("path", "") for n in tree if n.get("path")]
    has_public_dir = any(p == "public" or p.startswith("public/") for p in all_paths)
    html_paths = [p for p in all_paths if _is_html_path(p)][:_MAX_HTML_FILES]

    # Pre-fetch every HTML file once (in parallel) so the fixers
    # don't have to re-fetch.
    sem = asyncio.Semaphore(_FETCH_CONCURRENCY)

    async def _bounded(p: str) -> tuple[str, Optional[str]]:
        async with sem:
            return p, await _fetch_optional(owner, repo, p, branch, token)

    html_pairs = await asyncio.gather(*(_bounded(p) for p in html_paths))
    html_files: dict[str, str] = {
        p: (body or "")
        for p, body in html_pairs
        if body is not None
    }

    patches: list[SeoPatch] = []

    # ── Fix: meta tags + schema + image alts (per HTML file) ─────
    if {"meta", "schema", "alts"} & features:
        for path, html in html_files.items():
            if "meta" in features:
                p = patch_meta_tags(
                    path=path, html=html,
                    title=options.title,
                    description=options.description,
                    og_image=options.og_image,
                    url=options.site_url,
                )
                if p:
                    patches.append(p)
                    html = p["after"]   # subsequent fixers see the latest

            if "schema" in features:
                p = patch_schema_markup(
                    path=path, html=html,
                    title=options.title,
                    description=options.description,
                    url=options.site_url,
                    author=options.author,
                    image=options.og_image,
                )
                if p:
                    patches.append(p)
                    html = p["after"]

            if "alts" in features:
                p = await patch_image_alts(
                    path=path, html=html,
                    page_context=options.description,
                    alt_provider=options.alt_provider,
                )
                if p:
                    patches.append(p)
                    html = p["after"]

    # ── Fix: robots.txt + sitemap.xml (one-shot) ─────────────────
    if "robots" in features:
        existing_pub_robots  = await _fetch_optional(owner, repo, "public/robots.txt", branch, token)
        existing_root_robots = await _fetch_optional(owner, repo, "robots.txt",        branch, token)
        rp = patch_robots_txt(
            existing_public_robots=existing_pub_robots,
            existing_root_robots=existing_root_robots,
            site_url=options.site_url,
            has_public_dir=has_public_dir,
        )
        if rp:
            patches.append(rp)

    if "sitemap" in features:
        existing_pub_sm  = await _fetch_optional(owner, repo, "public/sitemap.xml", branch, token)
        existing_root_sm = await _fetch_optional(owner, repo, "sitemap.xml",        branch, token)
        sp = patch_sitemap(
            paths=all_paths,
            site_url=options.site_url,
            has_public_dir=has_public_dir,
            existing_public_sitemap=existing_pub_sm,
            existing_root_sitemap=existing_root_sm,
        )
        if sp:
            patches.append(sp)

    # ── Coalesce: same path may have been patched by multiple
    # fixers — keep only the FINAL after-state for each path so the
    # GitHub commit doesn't get the same file twice. ─────────────
    final_by_path: dict[str, SeoPatch] = {}
    reasons_by_path: dict[str, list[str]] = {}
    for p in patches:
        final_by_path[p["path"]] = p
        reasons_by_path.setdefault(p["path"], []).append(p["reason"])
    coalesced: list[SeoPatch] = []
    for path, p in final_by_path.items():
        merged_reason = "; ".join(reasons_by_path[path])
        coalesced.append(SeoPatch(
            path=path, before=p["before"], after=p["after"],
            action=p["action"], reason=merged_reason,
        ))

    out["patches"] = [
        {
            "path":   p["path"],
            "action": p["action"],
            "reason": p["reason"],
            "before_len": len(p["before"] or ""),
            "after_len":  len(p["after"]),
        }
        for p in coalesced
    ]
    out["patch_count"] = len(coalesced)

    if not coalesced:
        out["ok"] = True
        out["committed"] = False
        out["note"] = "nothing to fix — site already SEO-clean"
        return out

    # ── Commit ────────────────────────────────────────────────────
    if options.dry_run:
        out["ok"] = True
        out["committed"] = False
        out["note"] = "dry_run=True — no commit was made"
        return out

    files_to_commit = {p["path"]: p["after"] for p in coalesced}
    try:
        # Iter 212m-218 — resolve real developer identity + attach
        # Co-authored-by trailer via git_identity helper.
        from services.git_identity import (
            resolve_git_identity, build_commit_message,
        )
        _author_name, _author_email = await resolve_git_identity(db, user_id)
        _final_msg = build_commit_message(
            user_message=options.commit_message,
            summary=options.commit_message,
        )
        commit = await commit_files(
            owner=owner, repo=repo, branch=branch, token=token or "",
            files=files_to_commit,
            commit_message=_final_msg,
            author_name=_author_name, author_email=_author_email,
        )
        out["ok"] = bool(commit.get("ok"))
        out["committed"] = True
        out["commit_sha"] = commit.get("sha")
        out["commit_url"] = commit.get("html_url")
    except Exception as e:
        out["errors"].append(f"commit failed: {e!r}")
        out["committed"] = False
        return out

    out["finished_at"] = datetime.now(timezone.utc).isoformat()
    return out
