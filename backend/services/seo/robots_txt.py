"""
services/seo/robots_txt.py — Create or update robots.txt at
public/robots.txt (preferred) or robots.txt (fallback).
"""
from __future__ import annotations

from typing import Optional

from .meta_tags import SeoPatch


DEFAULT_DISALLOW = ("/admin", "/api", "/private", "/.env")


def render_robots_txt(
    *, site_url: str, disallow_paths: tuple[str, ...] = DEFAULT_DISALLOW,
) -> str:
    """Pure function — render the canonical robots.txt body."""
    lines = ["User-agent: *", "Allow: /"]
    for p in disallow_paths:
        lines.append(f"Disallow: {p}")
    lines.append("")
    lines.append(
        f"Sitemap: {(site_url or 'https://yourdomain.com').rstrip('/')}/sitemap.xml"
    )
    return "\n".join(lines) + "\n"


def patch_robots_txt(
    *,
    existing_public_robots: Optional[str],
    existing_root_robots: Optional[str],
    site_url: str,
    has_public_dir: bool,
    disallow_paths: tuple[str, ...] = DEFAULT_DISALLOW,
) -> Optional[SeoPatch]:
    """Decide WHERE to write (public/ or root) and whether to write
    at all (byte-identical content → skip).

    Inputs are pre-fetched by the caller (orchestrator) so this stays
    a pure function and is trivially unit-testable.
    """
    target_path = (
        "public/robots.txt" if has_public_dir else "robots.txt"
    )
    existing = (
        existing_public_robots if has_public_dir else existing_root_robots
    )
    new_body = render_robots_txt(
        site_url=site_url, disallow_paths=disallow_paths,
    )
    if (existing or "").strip() == new_body.strip():
        return None
    return SeoPatch(
        path=target_path,
        before=existing,
        after=new_body,
        action="modify" if existing is not None else "create",
        reason=(
            "created robots.txt"
            if existing is None
            else "updated robots.txt with sitemap + disallows"
        ),
    )
