"""
services/seo/meta_tags.py — Inject missing <title>, <meta description>,
and Open Graph tags into HTML files.

Returns a list of patches. Caller is responsible for committing them
via services.github_api_writer.commit_files().
"""
from __future__ import annotations

from typing import Optional, TypedDict

from bs4 import BeautifulSoup


class SeoPatch(TypedDict):
    path: str
    before: Optional[str]
    after: str
    action: str          # "modify" | "create"
    reason: str


def _esc(s: str) -> str:
    """Minimal HTML-attribute escape — the BeautifulSoup serializer
    handles the rest, but we apply this when injecting attr values
    so nothing ever lands as a raw `"` inside a `content="..."`."""
    return (s or "").replace('"', "&quot;")


def _has_meta(soup: BeautifulSoup, *, name: str = "", property_: str = "") -> bool:
    if name:
        return soup.find("meta", attrs={"name": name}) is not None
    if property_:
        return soup.find("meta", attrs={"property": property_}) is not None
    return False


def patch_meta_tags(
    *,
    path: str,
    html: str,
    title: str = "",
    description: str = "",
    og_title: str = "",
    og_description: str = "",
    og_image: str = "",
    url: str = "",
) -> Optional[SeoPatch]:
    """Return a patch for `html` or None if nothing needs to change."""
    soup = BeautifulSoup(html, "html.parser")
    head = soup.head
    if head is None:
        # No <head> — refuse to silently inject (would corrupt non-HTML
        # files that happen to have .html extension).
        return None

    changed = False

    # <title>
    if title and soup.title is None:
        t = soup.new_tag("title")
        t.string = title
        head.append(t)
        changed = True

    # <meta name="description">
    if description and not _has_meta(soup, name="description"):
        m = soup.new_tag(
            "meta", attrs={"name": "description", "content": description},
        )
        head.append(m)
        changed = True

    # OG tags
    og = [
        ("og:title",       og_title or title),
        ("og:description", og_description or description),
        ("og:image",       og_image),
        ("og:url",         url),
        ("og:type",        "website"),
    ]
    for prop, content in og:
        if not content:
            continue
        if _has_meta(soup, property_=prop):
            continue
        m = soup.new_tag(
            "meta", attrs={"property": prop, "content": content},
        )
        head.append(m)
        changed = True

    if not changed:
        return None
    return SeoPatch(
        path=path,
        before=html,
        after=str(soup),
        action="modify",
        reason="injected missing meta/og tags",
    )
