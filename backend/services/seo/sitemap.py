"""
services/seo/sitemap.py — Extract routes from a repo tree and render
a valid sitemap.xml.

We support three router styles:
  - Next.js pages/  router (legacy `pages/*.tsx`)
  - Next.js app/    router (`app/<route>/page.tsx`)
  - Plain static `.html` files (anywhere in repo or under public/)

The caller (orchestrator) hands us a tree from GitHub's recursive
trees API + an optional `public_files_present` flag. We never read
file bytes — pure path-list math.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional

from .meta_tags import SeoPatch


_DYNAMIC = re.compile(r"\[.*?\]")          # Next.js [slug] etc.
_EXT = re.compile(r"\.(tsx|jsx|js|mjs|ts)$")


def extract_routes(paths: list[str]) -> list[str]:
    """Pure function. Given a flat list of repo paths, return the
    routes a search engine should index. Deduped, sorted, no
    dynamic params (we can't enumerate values for `[slug]` from a
    static scan), no API paths."""
    routes: set[str] = set()

    has_pages = any(p.startswith("pages/") for p in paths)
    has_app   = any(p.startswith("app/") for p in paths)

    for p in paths:
        if _DYNAMIC.search(p):
            continue
        # Next.js pages router
        if has_pages and p.startswith("pages/"):
            if "/api/" in p or p.startswith("pages/api/"):
                continue
            if not _EXT.search(p):
                continue
            inner = p[len("pages/"):]
            inner = _EXT.sub("", inner)
            if inner.split("/")[-1].startswith("_"):
                continue
            if inner.endswith("/index"):
                inner = inner[: -len("/index")]
            if inner == "index" or inner == "":
                routes.add("/")
            else:
                routes.add("/" + inner)
            continue
        # Next.js app router
        if has_app and p.startswith("app/"):
            if not p.endswith(("page.tsx", "page.jsx", "page.js")):
                continue
            inner = p[len("app/"):]
            # Drop the trailing /page.ext segment.
            inner = inner.rsplit("/", 1)[0] if "/" in inner else ""
            # Drop route-group segments like (marketing)/...
            inner = "/".join(
                seg for seg in inner.split("/")
                if not (seg.startswith("(") and seg.endswith(")"))
            )
            routes.add("/" + inner if inner else "/")
            continue
        # Plain HTML files
        if p.endswith(".html"):
            # Strip a leading public/ if present so the route maps
            # to a clean URL.
            rel = p[len("public/"):] if p.startswith("public/") else p
            rel = rel[:-len(".html")]
            if rel.endswith("/index") or rel == "index":
                routes.add("/" if rel == "index" else "/" + rel[:-len("/index")])
            else:
                routes.add("/" + rel)
            continue

    # Always include the root.
    routes.add("/")
    # Dedup + sort, drop the literal "/" duplicates.
    return sorted(routes)


def render_sitemap_xml(
    routes: list[str], site_url: str,
    *, lastmod: Optional[str] = None,
) -> str:
    base = (site_url or "https://yourdomain.com").rstrip("/")
    date = lastmod or datetime.now(timezone.utc).date().isoformat()
    parts = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for r in routes:
        priority = "1.0" if r == "/" else "0.8"
        loc = base + ("" if r == "/" else r)
        parts.append("  <url>")
        parts.append(f"    <loc>{loc}</loc>")
        parts.append(f"    <lastmod>{date}</lastmod>")
        parts.append("    <changefreq>weekly</changefreq>")
        parts.append(f"    <priority>{priority}</priority>")
        parts.append("  </url>")
    parts.append("</urlset>")
    parts.append("")
    return "\n".join(parts)


def patch_sitemap(
    *,
    paths: list[str],
    site_url: str,
    has_public_dir: bool,
    existing_public_sitemap: Optional[str] = None,
    existing_root_sitemap: Optional[str] = None,
    lastmod: Optional[str] = None,
) -> Optional[SeoPatch]:
    routes = extract_routes(paths)
    target = "public/sitemap.xml" if has_public_dir else "sitemap.xml"
    existing = (
        existing_public_sitemap if has_public_dir else existing_root_sitemap
    )
    body = render_sitemap_xml(routes, site_url, lastmod=lastmod)
    if (existing or "").strip() == body.strip():
        return None
    return SeoPatch(
        path=target,
        before=existing,
        after=body,
        action="modify" if existing is not None else "create",
        reason=f"generated sitemap.xml ({len(routes)} routes)",
    )
