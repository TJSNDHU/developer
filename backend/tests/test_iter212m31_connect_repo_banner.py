"""
Iter 212m-31 — Persistent empty-state CTA banner.

Source pins guarding the contract the user signed off on:
  • Banner copy is locked to the exact phrasing requested.
  • Banner mounts in Dashboard.jsx only when projectCount === 0.
  • PAT-page deeplink points at fine-grained tokens, not classic ones.
  • Counter polls /founder-offer/status (the existing PR-2 route).
"""


def test_banner_copy_matches_signed_off_text():
    src = open("/app/frontend/src/components/ConnectRepoBanner.jsx").read()
    assert "Connect a repo to unlock your free SEO fix" in src
    assert "of 500 founder spots remaining" in src
    assert "Connect repo →" in src


def test_banner_three_step_pat_guide_present():
    src = open("/app/frontend/src/components/ConnectRepoBanner.jsx").read()
    # User-locked copy for each of the 3 steps.
    assert "github.com/settings/tokens" in src
    assert "Fine-grained tokens" in src
    assert "Contents" in src and ("Read & Write" in src or "Read &amp; Write" in src)
    # Deeplink must target fine-grained (?type=beta), not classic PATs.
    assert "github.com/settings/tokens?type=beta" in src


def test_banner_polls_founder_offer_status_endpoint():
    src = open("/app/frontend/src/components/ConnectRepoBanner.jsx").read()
    assert '"/founder-offer/status"' in src
    # Default-collapsed handling persisted to localStorage.
    assert "aurem_connect_banner_collapsed" in src


def test_dashboard_mounts_banner_only_when_zero_projects():
    src = open("/app/frontend/src/pages/Dashboard.jsx").read()
    assert "import ConnectRepoBanner from" in src
    assert "{projectCount === 0 && (" in src
    assert "<ConnectRepoBanner" in src
    # Wizard kept (per user W1 — banner persists alongside wizard).
    assert "NewUserWizard" in src
    assert "openWizardFromBanner" in src


def test_banner_hides_when_offer_sold_out():
    """Visibility rule: counter <= 0 hides the banner so the founder
    SEO incentive doesn't dangle once spots are gone."""
    src = open("/app/frontend/src/components/ConnectRepoBanner.jsx").read()
    assert "remaining ?? 0) <= 0) return null" in src
