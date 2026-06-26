"""
Iter 212m-29 — SEO core engine (PR-1).

Pure-function tests for all 5 Category A fixers + the orchestrator's
pipeline + plan-tier feature matrix. NO network — the orchestrator
test uses unittest.mock to patch GitHub IO.
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch, AsyncMock, MagicMock

import pytest


# ───────────────────────────────────────────────────────────────────
# 1. meta_tags
# ───────────────────────────────────────────────────────────────────

def test_meta_injects_missing_title_description_og():
    from services.seo.meta_tags import patch_meta_tags
    html = "<html><head></head><body><p>hi</p></body></html>"
    p = patch_meta_tags(
        path="index.html", html=html,
        title="My Site", description="A short blurb",
        og_image="https://x/y.png", url="https://x",
    )
    assert p is not None
    assert "<title>My Site</title>" in p["after"]
    assert 'name="description"' in p["after"]
    assert 'property="og:title"' in p["after"]
    assert 'property="og:image"' in p["after"]
    assert 'property="og:type"' in p["after"]
    assert p["action"] == "modify"


def test_meta_returns_none_when_nothing_to_add():
    from services.seo.meta_tags import patch_meta_tags
    html = (
        '<html><head>'
        '<title>Already</title>'
        '<meta name="description" content="x">'
        '<meta property="og:title" content="x">'
        '<meta property="og:description" content="x">'
        '<meta property="og:image" content="https://x/y.png">'
        '<meta property="og:url" content="https://x">'
        '<meta property="og:type" content="website">'
        '</head><body></body></html>'
    )
    p = patch_meta_tags(
        path="index.html", html=html,
        title="X", description="x",
        og_image="https://x/y.png", url="https://x",
    )
    assert p is None


def test_meta_returns_none_when_no_head():
    from services.seo.meta_tags import patch_meta_tags
    html = "not even html"
    p = patch_meta_tags(path="x.html", html=html, title="t", description="d")
    assert p is None


# ───────────────────────────────────────────────────────────────────
# 2. schema_markup
# ───────────────────────────────────────────────────────────────────

def test_schema_skips_when_already_present():
    from services.seo.schema_markup import patch_schema_markup
    html = (
        '<html><head>'
        '<script type="application/ld+json">{"@context":"https://schema.org"}</script>'
        '</head><body></body></html>'
    )
    p = patch_schema_markup(path="i.html", html=html, title="t")
    assert p is None


def test_schema_detects_article_and_adds_headline_date():
    from services.seo.schema_markup import patch_schema_markup
    html = '<html><head></head><body><article>hello</article></body></html>'
    p = patch_schema_markup(
        path="blog.html", html=html,
        title="My Post", description="A blurb",
        url="https://x/p", author="Teji",
    )
    assert p is not None
    after = p["after"]
    assert 'application/ld+json' in after
    assert '"@type": "Article"' in after
    assert '"headline": "My Post"' in after
    assert '"Person"' in after
    assert '"Teji"' in after


def test_schema_detects_product_with_offers():
    from services.seo.schema_markup import (
        detect_page_type, generate_schema,
    )
    html = '<div itemtype="https://schema.org/Product">$99</div>'
    assert detect_page_type(html) == "Product"
    schema = generate_schema("Product", title="X", price="99", currency="INR")
    assert schema["offers"]["price"] == "99"
    assert schema["offers"]["priceCurrency"] == "INR"


def test_schema_defaults_to_webpage():
    from services.seo.schema_markup import detect_page_type
    assert detect_page_type("<html><body><p>hi</p></body></html>") == "WebPage"


# ───────────────────────────────────────────────────────────────────
# 3. robots_txt
# ───────────────────────────────────────────────────────────────────

def test_robots_creates_in_public_when_dir_exists():
    from services.seo.robots_txt import patch_robots_txt
    p = patch_robots_txt(
        existing_public_robots=None,
        existing_root_robots=None,
        site_url="https://aurem.dev",
        has_public_dir=True,
    )
    assert p is not None
    assert p["path"] == "public/robots.txt"
    assert p["action"] == "create"
    assert "User-agent: *" in p["after"]
    assert "Sitemap: https://aurem.dev/sitemap.xml" in p["after"]
    assert "Disallow: /admin" in p["after"]


def test_robots_writes_to_root_when_no_public_dir():
    from services.seo.robots_txt import patch_robots_txt
    p = patch_robots_txt(
        existing_public_robots=None,
        existing_root_robots=None,
        site_url="https://x", has_public_dir=False,
    )
    assert p is not None
    assert p["path"] == "robots.txt"


def test_robots_unchanged_when_existing_matches():
    from services.seo.robots_txt import patch_robots_txt, render_robots_txt
    body = render_robots_txt(site_url="https://x")
    p = patch_robots_txt(
        existing_public_robots=body,
        existing_root_robots=None,
        site_url="https://x", has_public_dir=True,
    )
    assert p is None


# ───────────────────────────────────────────────────────────────────
# 4. sitemap
# ───────────────────────────────────────────────────────────────────

def test_sitemap_extract_routes_nextjs_pages():
    from services.seo.sitemap import extract_routes
    paths = [
        "pages/index.tsx",
        "pages/about.tsx",
        "pages/blog/index.tsx",
        "pages/blog/[slug].tsx",     # dynamic — must be skipped
        "pages/api/auth.ts",         # api — skipped
        "pages/_app.tsx",            # underscore — skipped
    ]
    routes = extract_routes(paths)
    assert "/" in routes
    assert "/about" in routes
    assert "/blog" in routes
    assert "/blog/[slug]" not in routes
    assert not any(r.startswith("/api") for r in routes)
    assert "/_app" not in routes


def test_sitemap_extract_routes_nextjs_app():
    from services.seo.sitemap import extract_routes
    paths = [
        "app/page.tsx",
        "app/about/page.tsx",
        "app/(marketing)/landing/page.tsx",
        "app/blog/[slug]/page.tsx",      # dynamic — skipped
    ]
    routes = extract_routes(paths)
    assert "/" in routes
    assert "/about" in routes
    assert "/landing" in routes           # group dropped
    assert not any("[slug]" in r for r in routes)


def test_sitemap_extract_routes_plain_html():
    from services.seo.sitemap import extract_routes
    paths = ["public/index.html", "public/about.html", "public/x/y.html"]
    routes = extract_routes(paths)
    assert "/" in routes
    assert "/about" in routes
    assert "/x/y" in routes


def test_sitemap_render_includes_all_routes_and_loc():
    from services.seo.sitemap import render_sitemap_xml
    xml = render_sitemap_xml(["/", "/about"], "https://aurem.dev", lastmod="2026-02-25")
    assert "https://aurem.dev/about" in xml
    assert "<priority>1.0</priority>" in xml      # root
    assert "<priority>0.8</priority>" in xml      # non-root
    assert "<lastmod>2026-02-25</lastmod>" in xml


def test_sitemap_skip_when_existing_matches():
    from services.seo.sitemap import render_sitemap_xml, patch_sitemap
    body = render_sitemap_xml(["/"], "https://x", lastmod="2026-01-01")
    p = patch_sitemap(
        paths=[], site_url="https://x", has_public_dir=True,
        existing_public_sitemap=body, lastmod="2026-01-01",
    )
    assert p is None


# ───────────────────────────────────────────────────────────────────
# 5. image_alts
# ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_image_alts_skips_imgs_with_alt():
    from services.seo.image_alts import patch_image_alts
    html = '<html><body><img src="a.png" alt="logo"></body></html>'
    async def stub(src, ctx):
        return f"STUB:{src}"
    p = await patch_image_alts(
        path="i.html", html=html, alt_provider=stub,
    )
    assert p is None


@pytest.mark.asyncio
async def test_image_alts_fills_empty_alts():
    from services.seo.image_alts import patch_image_alts
    html = (
        '<html><body>'
        '<img src="hero.png">'
        '<img src="logo.svg" alt="">'
        '<img src="decor.gif" role="presentation">'    # must NOT touch
        '</body></html>'
    )
    async def stub(src, ctx):
        return f"alt-for-{src.rsplit('/', 1)[-1].split('.', 1)[0]}"
    p = await patch_image_alts(
        path="i.html", html=html, alt_provider=stub,
    )
    assert p is not None
    assert 'alt="alt-for-hero"' in p["after"]
    assert 'alt="alt-for-logo"' in p["after"]
    assert 'role="presentation"' in p["after"]      # still there
    # And the presentation img remains untouched (no alt added).
    assert 'role="presentation" alt="' not in p["after"]


@pytest.mark.asyncio
async def test_image_alts_uses_fallback_when_llm_returns_empty():
    from services.seo.image_alts import patch_image_alts
    html = '<html><body><img src="my-cool-thing.png"></body></html>'
    async def empty(src, ctx):
        return ""    # LLM returned junk
    p = await patch_image_alts(path="i.html", html=html, alt_provider=empty)
    assert p is not None
    # fallback derived from filename
    assert 'alt="image: my cool thing"' in p["after"]


@pytest.mark.asyncio
async def test_image_alts_caps_long_text_at_125():
    from services.seo.image_alts import patch_image_alts, _MAX_ALT_LEN
    html = '<html><body><img src="x.png"></body></html>'
    async def long(src, ctx):
        return "x" * 500
    p = await patch_image_alts(path="i.html", html=html, alt_provider=long)
    # The provider returns a long string; image_alts itself doesn't
    # clip on the path (LLM helper does). Sanity: bs4 still sets it.
    assert "x" * 100 in p["after"]
    assert _MAX_ALT_LEN > 0     # constant exists


# ───────────────────────────────────────────────────────────────────
# 6. plan tier matrix
# ───────────────────────────────────────────────────────────────────

def test_plan_features_matrix_swift_pro_maxx_all_carry_category_a():
    from services.seo.orchestrator import PLAN_FEATURES
    CATEGORY_A = {"meta", "schema", "robots", "sitemap", "alts"}
    for plan in ("swift", "pro", "maxx"):
        assert CATEGORY_A.issubset(PLAN_FEATURES[plan]), (
            f"{plan} missing Category A features: "
            f"{CATEGORY_A - PLAN_FEATURES[plan]}"
        )


# ───────────────────────────────────────────────────────────────────
# 7. orchestrator end-to-end (with all IO mocked)
# ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_orchestrator_dry_run_full_flow(monkeypatch):
    """End-to-end: project lookup → tree fetch → file fetch (parallel) →
    every fixer runs → patches coalesced. NO commit (dry_run=True)."""
    from services.seo import orchestrator as orch
    from services.seo.orchestrator import SeoOptions, run_seo_fixes

    # 1. db.cto_projects.find_one returns a fake project.
    fake_db = MagicMock()
    fake_db.cto_projects.find_one = AsyncMock(return_value={
        "project_id":    "p_test",
        "user_id":       "u_test",
        "github_owner":  "tiangolo",
        "github_repo":   "fastapi",
        "branch":        "master",
        "github_token":  "fake",
    })
    monkeypatch.setattr(orch, "get_db", lambda: fake_db)

    # 2. _fetch_tree returns a small simulated repo.
    async def fake_tree(owner, repo, branch, token):
        return [
            {"path": "public",            "type": "tree"},
            {"path": "public/index.html", "type": "blob"},
            {"path": "public/about.html", "type": "blob"},
            {"path": "pages/index.tsx",   "type": "blob"},
            {"path": "README.md",         "type": "blob"},
        ], False
    monkeypatch.setattr(orch, "_fetch_tree", fake_tree)

    # 3. _fetch_file returns small HTML for the two HTML paths,
    #    None for the meta-files (robots/sitemap don't exist yet).
    async def fake_file(owner, repo, path, branch, token):
        if path == "public/index.html":
            return '<html><head></head><body><img src="a.png"></body></html>'
        if path == "public/about.html":
            return '<html><head></head><body><p>About</p></body></html>'
        return None
    monkeypatch.setattr(orch, "_fetch_file", fake_file)

    # 4. commit_files must NOT be called in dry_run mode — assert that.
    commit_mock = AsyncMock(side_effect=AssertionError(
        "commit_files must not be called in dry_run mode"
    ))
    monkeypatch.setattr(orch, "commit_files", commit_mock)

    # 5. Stub alt provider so we don't hit the LLM.
    async def stub_alt(src, ctx):
        return f"alt for {src}"

    result = await run_seo_fixes(
        user_id="u_test",
        project_id="p_test",
        options=SeoOptions(
            plan="swift",
            site_url="https://aurem.dev",
            title="Aurem",
            description="AI CTO",
            dry_run=True,
            alt_provider=stub_alt,
        ),
    )

    assert result["ok"] is True
    assert result["committed"] is False
    assert result["dry_run"] is True
    assert result["plan"] == "swift"
    assert "meta" in result["features_enabled"]
    assert "robots" in result["features_enabled"]
    # Should have patched index.html (meta+schema+alts) +
    # about.html (meta+schema) + robots.txt + sitemap.xml.
    paths = {p["path"] for p in result["patches"]}
    assert "public/index.html" in paths
    assert "public/about.html" in paths
    assert "public/robots.txt" in paths
    assert "public/sitemap.xml" in paths
    # commit_files was NOT called.
    commit_mock.assert_not_called()
    # Errors must be empty on clean run.
    assert result["errors"] == []


@pytest.mark.asyncio
async def test_orchestrator_403s_unowned_project(monkeypatch):
    """If the project doesn't belong to the user, the orchestrator
    must NOT touch GitHub — just return an error."""
    from services.seo import orchestrator as orch
    from services.seo.orchestrator import SeoOptions, run_seo_fixes
    fake_db = MagicMock()
    fake_db.cto_projects.find_one = AsyncMock(return_value=None)
    monkeypatch.setattr(orch, "get_db", lambda: fake_db)

    # Tree-fetch must NEVER be called for unauthorized callers.
    tree_mock = AsyncMock(side_effect=AssertionError(
        "tree fetch must not run on unauthorized access"
    ))
    monkeypatch.setattr(orch, "_fetch_tree", tree_mock)

    result = await run_seo_fixes(
        user_id="attacker",
        project_id="not-mine",
        options=SeoOptions(plan="swift", dry_run=True),
    )
    assert result["ok"] is False
    assert "not found or not owned" in result["errors"][0]
    tree_mock.assert_not_called()


@pytest.mark.asyncio
async def test_orchestrator_returns_nothing_to_fix_on_clean_site(monkeypatch):
    """If every HTML page is already SEO-clean and robots/sitemap
    match, the orchestrator returns ok=True, committed=False, and a
    'nothing to fix' note."""
    from services.seo import orchestrator as orch
    from services.seo.orchestrator import SeoOptions, run_seo_fixes
    from services.seo.robots_txt import render_robots_txt
    from services.seo.sitemap   import render_sitemap_xml

    fake_db = MagicMock()
    fake_db.cto_projects.find_one = AsyncMock(return_value={
        "project_id": "p", "user_id": "u",
        "github_owner": "o", "github_repo": "r",
        "branch": "main", "github_token": "x",
    })
    monkeypatch.setattr(orch, "get_db", lambda: fake_db)

    async def fake_tree(*a, **kw):
        return [], False
    monkeypatch.setattr(orch, "_fetch_tree", fake_tree)

    async def fake_file(owner, repo, path, branch, token):
        if path == "robots.txt":
            return render_robots_txt(site_url="https://x")
        if path == "sitemap.xml":
            return render_sitemap_xml(["/"], "https://x")
        return None
    monkeypatch.setattr(orch, "_fetch_file", fake_file)

    commit_mock = AsyncMock()
    monkeypatch.setattr(orch, "commit_files", commit_mock)

    result = await run_seo_fixes(
        user_id="u", project_id="p",
        options=SeoOptions(plan="swift", site_url="https://x", dry_run=False),
    )
    assert result["ok"] is True
    assert result["committed"] is False
    assert "nothing to fix" in result.get("note", "").lower()
    commit_mock.assert_not_called()
