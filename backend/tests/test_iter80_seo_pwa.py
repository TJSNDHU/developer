"""
test_iter80_seo_pwa.py — SEO / GEO / PWA wiring lock.

Iter 80 closed three gaps:
  1. PWA — added /sw.js service worker with offline shell + cache strategies,
     enriched site.webmanifest (id, shortcuts, screenshots, display_override),
     registered the SW in src/main.jsx.
  2. SEO — sitemap.xml now lists /wall + /wrapped, lastmod refreshed,
     index.html JSON-LD pricing corrected (Free / Starter $9 / Pro $19 /
     Team $35) and feature list updated to current capabilities.
  3. GEO — robots.txt opens for additional LLM crawlers (YouBot,
     Meta-ExternalAgent, Amazonbot), llms.txt rewritten with the actual
     June 2026 pricing + capability list.

This test locks all three so a refactor can't quietly delete them.
"""
from __future__ import annotations

import json
import os
import re

BASE = os.path.join(os.path.dirname(__file__), "..", "..")
PUBLIC = os.path.join(BASE, "frontend", "public")


def _read(rel: str) -> str:
    with open(os.path.join(BASE, rel), encoding="utf-8") as fh:
        return fh.read()


# ── PWA ────────────────────────────────────────────────────────────────

def test_service_worker_exists_and_has_versioned_cache():
    src = _read("frontend/public/sw.js")
    assert "CACHE_VERSION" in src
    # Three lifecycle handlers are required.
    for evt in ("install", "activate", "fetch"):
        assert f'addEventListener("{evt}"' in src, f"sw.js missing {evt}"
    # API calls must NEVER be cached (chat is live).
    assert "/api/" in src and "return;" in src
    # SSE must bypass cache.
    assert "text/event-stream" in src


def test_main_jsx_registers_service_worker():
    src = _read("frontend/src/main.jsx")
    assert "serviceWorker" in src
    assert 'navigator.serviceWorker' in src
    assert '/sw.js' in src


def test_manifest_is_valid_pwa_with_shortcuts():
    src = _read("frontend/public/site.webmanifest")
    m = json.loads(src)
    # Required PWA fields
    assert m["start_url"]
    assert m["display"] in ("standalone", "fullscreen", "minimal-ui")
    assert m["theme_color"].startswith("#")
    assert m["background_color"].startswith("#")
    # PWA installability essentials
    assert m.get("id"), "manifest missing `id` (required for new Chrome PWAs)"
    icons = m["icons"]
    sizes = {icon["sizes"] for icon in icons}
    assert "192x192" in sizes
    assert "512x512" in sizes
    has_maskable = any("maskable" in icon.get("purpose", "") for icon in icons)
    assert has_maskable, "manifest missing maskable icon"
    # Shortcuts hook ORA's main surfaces.
    shortcuts = m.get("shortcuts") or []
    short_urls = {s["url"].split("?")[0] for s in shortcuts}
    assert "/dashboard" in short_urls
    assert "/projects" in short_urls


def test_manifest_linked_in_index_html():
    src = _read("frontend/index.html")
    assert 'rel="manifest"' in src
    assert '/site.webmanifest' in src


# ── SEO ────────────────────────────────────────────────────────────────

def test_sitemap_lists_all_public_pages():
    src = _read("frontend/public/sitemap.xml")
    for path in ("/", "/signup", "/login", "/wall", "/wrapped"):
        assert f"<loc>https://auremcto.com{path}</loc>" in src, \
            f"sitemap missing public route {path}"
    # Admin-gated surfaces must NOT be in the sitemap.
    for path in ("/admin", "/dashboard", "/settings", "/automations"):
        assert f"<loc>https://auremcto.com{path}</loc>" not in src, \
            f"sitemap leaks gated route {path}"


def test_json_ld_pricing_is_current():
    """Offers must match the live four-tier model (Free / Starter / Pro /
    Team) at the right price points."""
    src = _read("frontend/index.html")
    # Extract the first JSON-LD block
    m = re.search(
        r'<script type="application/ld\+json">\s*(\{.*?\})\s*</script>',
        src, re.DOTALL,
    )
    assert m, "JSON-LD block not found"
    blob = json.loads(m.group(1))
    # Find the SoftwareApplication node inside @graph
    app = next((n for n in blob["@graph"]
                if n.get("@type") == "SoftwareApplication"), None)
    assert app, "no SoftwareApplication node in JSON-LD"
    offers = {o["name"]: o["price"] for o in app["offers"]}
    assert offers == {"Free": "0", "Starter": "9", "Pro": "19", "Team": "35"}, \
        f"unexpected pricing in JSON-LD: {offers}"


def test_json_ld_feature_list_covers_current_capabilities():
    src = _read("frontend/index.html")
    for must_mention in (
        "Direct GitHub commit",
        "Project Brain",
        "Vanguard",
        "Maxx mode",
        "Automations",
        "Live preview",
        "F12",
    ):
        assert must_mention in src, f"JSON-LD missing feature: {must_mention}"


def test_index_title_and_description_reflect_aurem_cto():
    src = _read("frontend/index.html")
    assert "AUREM CTO" in src   # not the old "AUREM Dev"
    # Twitter + OG must say the same thing.
    assert 'name="twitter:title" content="AUREM CTO' in src
    assert 'property="og:title" content="AUREM CTO' in src


# ── GEO (LLM-engine optimization) ──────────────────────────────────────

def test_robots_allows_modern_ai_crawlers():
    src = _read("frontend/public/robots.txt")
    for bot in ("GPTBot", "PerplexityBot", "ClaudeBot", "Google-Extended",
                "OAI-SearchBot", "Bytespider", "CCBot",
                "YouBot", "Meta-ExternalAgent", "Amazonbot"):
        assert re.search(rf"User-agent:\s*{re.escape(bot)}\s*\nAllow:\s*/", src), \
            f"robots.txt missing Allow for {bot}"
    # Public surfaces opened up
    assert "Allow: /wall" in src
    assert "Allow: /wrapped" in src


def test_llms_txt_has_current_pricing_and_capabilities():
    src = _read("frontend/public/llms.txt")
    # Pricing
    for tier in ("Free", "Starter", "$9/mo", "Pro", "$19/mo",
                 "Team", "$35"):
        assert tier in src, f"llms.txt missing pricing fragment: {tier}"
    # Capabilities the model must be able to cite
    for cap in ("Project Brain", "Vanguard", "Maxx mode",
                "Automations", "Tavily", "Firecrawl", "VS Code"):
        assert cap in src, f"llms.txt missing capability: {cap}"
    # No stale wording from the previous llms.txt
    assert "1,000 tokens" not in src
    assert "50k tokens" not in src
    assert "100k tokens" not in src
    assert "$49" not in src
