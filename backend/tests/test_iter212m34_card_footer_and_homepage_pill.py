"""
Iter 212m-34 — Founder card layout move (below composer) + homepage pill.

Source pins covering the two visual contracts the user signed off on:
  1. `FounderOfferCard` is mounted AFTER `</form>` in ChatPanel.jsx
     (footer position, matching the Cursor/Cline reference) and the
     previous above-form mount is gone.
  2. Landing.jsx imports + renders `<FounderOfferPill />` in the hero
     so the homepage shows the live counter.
"""


def test_founder_card_is_below_chat_form_in_jsx():
    src = open("/app/frontend/src/components/ChatPanel.jsx").read()

    # New (below form) — the mount must come AFTER the </form> tag.
    form_close_idx = src.find("</form>")
    card_idx = src.find("<FounderOfferCard")
    assert form_close_idx > 0, "</form> not found in ChatPanel.jsx"
    assert card_idx > 0, "<FounderOfferCard not found in ChatPanel.jsx"
    assert card_idx > form_close_idx, (
        "FounderOfferCard must be mounted AFTER </form> "
        "(below the composer) per the user-locked layout."
    )

    # The pre-Iter-212m-34 above-form mount must be gone. We grep for
    # the unique comment that marked it; if it's still present the
    # card will render twice.
    assert "PR-2 — Founder Offer (free SEO fix). Card auto-hides" not in src


def test_founder_card_styling_matches_footer_strip():
    """The new design is a footer strip, not a heavy card. Catch
    regressions to the old gradient-background bordered card."""
    src = open("/app/frontend/src/components/FounderOfferCard.jsx").read()
    # Footer cues:
    assert "borderTop: \"1px solid rgba(234,179,8,0.18)\"" in src
    assert "background: \"transparent\"" in src
    # Old card cues that MUST be gone:
    assert ("background: \"linear-gradient(135deg, "
            "rgba(234,179,8,0.10)") not in src
    assert "boxShadow: \"0 1px 3px rgba(0,0,0,0.04)\"" not in src
    # Headline copy still locked.
    assert "Free SEO fix from the founder" in src
    # Counter still uses the mono font.
    assert "JetBrains Mono" in src


def test_homepage_renders_founder_offer_pill():
    src = open("/app/frontend/src/pages/Landing.jsx").read()
    assert ("import FounderOfferPill from "
            "\"../components/FounderOfferPill\"") in src
    assert "<FounderOfferPill />" in src


def test_founder_offer_pill_unchanged_endpoint_and_link():
    """Pill is reused on Landing + Projects, so its contract must
    stay stable across iters."""
    src = open("/app/frontend/src/components/FounderOfferPill.jsx").read()
    assert '"/founder-offer/status"' in src
    assert "/dashboard?action=connect-repo" in src
