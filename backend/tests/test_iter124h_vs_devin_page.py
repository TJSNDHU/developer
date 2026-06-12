"""
Iter 124h — /vs/devin SEO/GEO page integrity guard.

Mirrors the iter 123g test pattern. Pure file-content checks; no LLM,
no browser, no network. Catches:
  • stale pricing claims (Devin ACU rates, AUREM tier prices)
  • silently-deleted comparison sections
  • missing footer link / pricing CTA / sitemap entry
  • dead /vs/cursor link regressing
"""
from __future__ import annotations
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VS_DEVIN = ROOT / "frontend" / "src" / "pages" / "VsDevin.jsx"
APP_JSX = ROOT / "frontend" / "src" / "App.jsx"
LANDING = ROOT / "frontend" / "src" / "pages" / "Landing.jsx"
SITEMAP = ROOT / "frontend" / "public" / "sitemap.xml"
LLMS = ROOT / "frontend" / "public" / "llms.txt"
LLMS_FULL = ROOT / "frontend" / "public" / "llms-full.txt"


def _src(p: Path) -> str:
    assert p.exists(), f"missing required file: {p}"
    return p.read_text(encoding="utf-8")


# ── Page content (from handoff notes #5) ────────────────────────────

def test_vs_devin_mentions_both_delivery_modes():
    src = _src(VS_DEVIN)
    assert "Pull Request" in src
    assert "Direct commit" in src or "direct commit" in src


def test_vs_devin_has_current_devin_acu_rate():
    """Devin Core is $20/mo + $2.25/ACU per devin.ai/pricing as of June 2026."""
    src = _src(VS_DEVIN)
    assert "$2.25" in src, "Devin per-ACU rate ($2.25) missing — fact-check needed"
    assert "$20" in src, "Devin Core base ($20/mo) missing"


def test_vs_devin_has_no_stale_prices():
    src = _src(VS_DEVIN)
    # Old AUREM Pro tier was never $35; if it appears, someone copy-pasted stale data
    assert '"price": "35"' not in src
    assert '"$35/mo"' not in src


def test_vs_devin_has_faq_jsonld():
    src = _src(VS_DEVIN)
    # The schema.org FAQPage block is what feeds Google rich results.
    assert "application/ld+json" in src
    assert "FAQPage" in src
    assert "BreadcrumbList" in src


def test_vs_devin_credits_where_devin_wins():
    """Honesty policy — the page must acknowledge Devin's strengths."""
    src = _src(VS_DEVIN)
    # Phrase used in handoff doc & the actual section
    low = src.lower()
    assert "where devin wins" in low or "devin is a strong fit" in low or \
           "vpc" in low and "hours" in low


# ── Route wiring ────────────────────────────────────────────────────

def test_app_jsx_has_vs_devin_route():
    src = _src(APP_JSX)
    assert 'path="/vs/devin"' in src
    assert 'lazy(() => import("./pages/VsDevin"))' in src


def test_app_jsx_redirects_vs_cursor_to_vs_devin():
    """Old footer link kept alive — handoff note #3."""
    src = _src(APP_JSX)
    # Looks for the Navigate redirect block specifically on /vs/cursor
    assert re.search(
        r'path="/vs/cursor".*Navigate to="/vs/devin"',
        src,
        re.DOTALL,
    ) is not None


# ── Landing internal links ──────────────────────────────────────────

def test_landing_has_footer_vs_devin_link():
    src = _src(LANDING)
    assert 'to="/vs/devin"' in src
    assert 'data-testid="footer-vs-devin"' in src


def test_landing_has_pricing_vs_devin_cta():
    src = _src(LANDING)
    assert 'data-testid="pricing-vs-devin"' in src
    assert "How we compare to Devin" in src


# ── SEO files ───────────────────────────────────────────────────────

def test_sitemap_has_vs_devin_entry():
    src = _src(SITEMAP)
    assert "https://auremcto.com/vs/devin" in src


def test_llms_txt_links_vs_devin():
    src = _src(LLMS)
    assert "/vs/devin" in src


def test_llms_full_links_vs_devin():
    src = _src(LLMS_FULL)
    assert "/vs/devin" in src
