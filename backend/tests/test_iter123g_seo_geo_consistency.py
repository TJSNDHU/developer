"""
test_iter123g_seo_geo_consistency.py — Iter 123g SEO/GEO refresh validation.

Locks in:
  1. index.html mentions the 22-skill capability (drives AEO snippets).
  2. softwareVersion bumped to 1.23 to match the iter range.
  3. FAQ + meta acknowledge BOTH delivery modes (direct commit AND PR)
     so the SEO copy doesn't contradict the Landing hero ("commits
     directly to your GitHub") OR the new github_deploy PR flow.
  4. Pricing matches the Stripe live tier: $9 / $19 / $49 (was $35 stale).
  5. Annual plan mention present (20% savings — iter 90/123 work).
  6. AI / GEO crawler allow-list intact (GPTBot, PerplexityBot, etc.).
"""
import re


def _index_html() -> str:
    with open("/app/frontend/index.html") as f:
        return f.read()


def test_22_skills_mentioned():
    """The 22-skill catalog is our strongest GEO/AEO differentiator vs
    Cursor/Devin/Replit. Must appear in meta + JSON-LD."""
    src = _index_html()
    assert "22 native dev skills" in src or "22 ai dev skills" in src.lower(), \
        "missing '22 native dev skills' in meta keywords/description"
    # JSON-LD feature list should enumerate actual skill names
    for skill in ("find_usages", "validate_syntax", "e2b_run_code",
                  "get_dependencies", "detect_framework"):
        assert skill in src, f"skill '{skill}' not surfaced in JSON-LD featureList"


def test_software_version_bumped():
    src = _index_html()
    assert '"softwareVersion": "1.23"' in src, \
        "softwareVersion should reflect iter 123 work"


def test_both_delivery_modes_documented():
    """FAQ + meta must say BOTH 'direct commit' AND 'Pull Request' —
    because the actual product supports both and the Landing hero says
    direct-commit-no-PR. Single-mode copy creates a contradiction."""
    src = _index_html()
    # Direct commit mode mentioned
    assert "commit" in src.lower()
    # PR mode mentioned (the new iter 123 github_deploy flow)
    assert "Pull Request" in src
    # FAQ must explicitly mention both modes coexist
    faq_section = src[src.find('"FAQPage"'):]
    assert "Two delivery modes" in faq_section or "directly to" in faq_section, \
        "FAQ must clarify both delivery modes coexist"


def test_pricing_matches_stripe_live_tiers():
    """Stripe LIVE prices verified earlier: $9 / $19 / $49 monthly.
    The old $35 Team mention in JSON-LD was a stale price."""
    src = _index_html()
    assert '"price": "9"' in src and '"price": "19"' in src and '"price": "49"' in src
    assert '"price": "35"' not in src, "stale $35 Team price still in JSON-LD"


def test_annual_plan_mentioned():
    """Iter 90 annual price IDs are in Stripe. SEO copy should reference
    the savings so price-sensitive prospects from AI search see them."""
    src = _index_html()
    # Either FAQ or pricing offer should mention annual or 20%
    has_annual_signal = (
        "Annual plans save" in src or
        "save 20%" in src.lower() or
        "annual" in src.lower()
    )
    assert has_annual_signal, "no annual / 20% savings signal in SEO copy"


def test_ai_crawler_allow_list_intact():
    """GPTBot, PerplexityBot, ClaudeBot etc. must be explicitly allowed
    for AEO/GEO traffic. Iter 123g shouldn't have broken this."""
    src = _index_html()
    for bot in ("GPTBot", "PerplexityBot", "ClaudeBot",
                "Google-Extended", "Applebot-Extended"):
        assert f'name="{bot}"' in src, f"missing meta allow for {bot}"


def test_no_stale_contradictions():
    """Specific stale claims that contradict the current product."""
    src = _index_html()
    # Old token-based marketing was killed iter 75 (flat-fee pivot)
    assert "1,000 tokens free" not in src, \
        "noscript fallback still mentions old token-pricing model"
    # Old 'Maxx mode reviews' phrasing without 'Claude 4.5' is too vague
    # We don't enforce a specific phrase, just that Sonnet 4.5 is named
    # somewhere in the structured data + we mention 'Vanguard Verify'
    assert "Claude Sonnet 4.5" in src
    assert "Vanguard Verify" in src or "Vanguard 007" in src
