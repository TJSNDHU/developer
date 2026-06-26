"""
Iter 212m-34 — Founder card layout move (below composer) + homepage pill.

Source pins covering the two visual contracts the user signed off on:
  1. `FounderOfferCard` is mounted AFTER `</form>` in ChatPanel.jsx
     (footer position, matching the Cursor/Cline reference) and the
     previous above-form mount is gone.
  2. Landing.jsx imports + renders `<FounderOfferPill />` in the hero
     so the homepage shows the live counter.
"""


def test_founder_card_is_attached_to_top_of_chat_form_in_jsx():
    src = open("/app/frontend/src/components/ChatPanel.jsx").read()

    # Iter 212m-35 — the card mount must come BEFORE the <form open tag
    # (banner sits attached to the top of the composer per the user's
    # marked screenshot).
    # The form JSX tag has both data-testid="chat-form" + the
    # glass-composer className. The querySelector string also contains
    # data-testid="chat-form", so we anchor on the JSX-only pair.
    form_open_idx = src.find('className="glass-composer"')
    card_idx = src.find("<FounderOfferCard projectId=")
    assert form_open_idx > 0, "chat-form not found"
    assert card_idx > 0, "<FounderOfferCard not found"
    assert card_idx < form_open_idx, (
        "FounderOfferCard must mount BEFORE the <form> so it sits "
        "attached to the TOP of the composer per the user-locked layout."
    )


def test_founder_card_styling_has_rounded_top_only():
    """Iter 212m-37 — the banner is now edge-to-edge (no side margins),
    has amber borders on left/right/top, rounded TOP corners, and a
    flat bottom that visually fuses with the composer beneath."""
    src = open("/app/frontend/src/components/FounderOfferCard.jsx").read()
    # Rounded TOP corners only.
    assert "borderTopLeftRadius: 12" in src
    assert "borderTopRightRadius: 12" in src
    assert "borderBottomLeftRadius: 0" in src
    assert "borderBottomRightRadius: 0" in src
    # Edge-to-edge (no side margins).
    assert "margin: 0," in src
    # Amber borders on 3 sides (top + both sides), none on bottom so
    # it fuses with the composer.
    assert 'borderTop: "1px solid rgba(234,179,8,0.45)"' in src
    assert 'borderLeft: "1px solid rgba(234,179,8,0.45)"' in src
    assert 'borderRight: "1px solid rgba(234,179,8,0.45)"' in src
    assert 'borderBottom: "none"' in src
    # Old footer-strip + boxed-with-side-margins styling must be gone.
    assert 'background: "transparent"' not in src
    assert 'margin: "8px 12px 0"' not in src
    # Brighter, readable text.
    assert '#fde68a' in src or 'color: "#fde68a"' in src
    # Headline copy still locked.
    assert "Free SEO fix from the founder" in src


def test_chat_composer_has_matching_amber_side_borders():
    """The form's `.glass-composer` is given amber side + bottom
    borders + rounded bottom corners so the offer + composer read as
    one unified container per the user-marked screenshot."""
    src = open("/app/frontend/src/components/ChatPanel.jsx").read()
    # The matching borders on the form's inline style.
    assert 'borderLeft: "1px solid rgba(234,179,8,0.45)"' in src
    assert 'borderRight: "1px solid rgba(234,179,8,0.45)"' in src
    assert 'borderBottom: "1px solid rgba(234,179,8,0.45)"' in src
    assert "borderBottomLeftRadius: 12" in src
    assert "borderBottomRightRadius: 12" in src


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
