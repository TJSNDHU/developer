"""
test_iter101_2_frontend_referral_annual_ui.py — locks in the frontend
side of iter 101: annual toggle, hero save badge, referral capture on
landing, signup attribution, and the Settings share card.
"""
from __future__ import annotations

from pathlib import Path

import pytest


SRC = Path(__file__).resolve().parents[2] / "frontend" / "src"


@pytest.mark.parametrize("needle", [
    'data-testid="billing-toggle"',
    'data-testid="billing-monthly"',
    'data-testid="billing-annual"',
    'data-testid="annual-save-badge"',
    'SAVE 20%',
    "_annual",   # plan rewrite logic
])
def test_pricing_cards_has_annual_toggle(needle):
    src = (SRC / "components" / "PricingCards.jsx").read_text()
    assert needle in src, f"PricingCards.jsx missing {needle!r}"


def test_landing_hero_has_save_20_badge():
    src = (SRC / "pages" / "Landing.jsx").read_text()
    assert 'data-testid="hero-annual-badge"' in src
    assert "Save 20% with annual" in src


def test_app_jsx_captures_ref_param_on_landing():
    """Any visitor arriving at any URL with `?ref=…` must trigger:
       1. localStorage.setItem('aurem_ref', …)
       2. POST /referrals/track with the ref + path + UA
    so the referrer's click counter ticks and the new user gets
    attributed when they sign up."""
    src = (SRC / "App.jsx").read_text()
    assert "aurem_ref" in src, "App.jsx must stash ?ref into localStorage"
    assert "/referrals/track" in src, "App.jsx must hit /referrals/track"
    assert 'get("ref")' in src or 'get(\"ref\")' in src


def test_signup_attributes_referrer_after_account_creation():
    """After successful signup, if there's a ref in localStorage,
    Signup.jsx must call /referrals/attribute. The localStorage flag
    must be cleared after attribution to prevent stale state."""
    src = (SRC / "pages" / "Signup.jsx").read_text()
    assert "/referrals/attribute" in src, "Signup must call /referrals/attribute"
    assert "aurem_ref" in src
    assert "removeItem" in src, "must clear aurem_ref after use"
    # Self-referral guard at the frontend too.
    assert "r.data.user_id" in src and "ref !==" in src


def test_referral_share_component_wires_real_endpoint():
    src = (SRC / "components" / "ReferralShare.jsx").read_text()
    assert '/referrals/my' in src, "ReferralShare must fetch /referrals/my"
    for testid in ('referral-share-card', 'referral-link', 'copy-referral-link',
                   'share-twitter', 'share-linkedin', 'referral-stats'):
        assert f'data-testid="{testid}"' in src, f"missing data-testid {testid}"
    # No mocked stats — must render whatever the API returns.
    assert "data.clicks" in src
    assert "data.verified_signups" in src


def test_settings_renders_referral_share():
    src = (SRC / "pages" / "Settings.jsx").read_text()
    assert "import ReferralShare" in src
    assert "<ReferralShare" in src
