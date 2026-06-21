"""
test_iter99_policies_and_signup_consent.py — locks in:

  • 3 policy markdown files exist at frontend/public/policies/
  • Each policy uses ora@aurem.live as the contact (no stale
    privacy@/support@/abuse@auremcto.com leaks)
  • App.jsx wires /privacy, /terms, /acceptable-use routes
  • Landing footer has links to all 3 policies + Contact
  • Signup.jsx requires the ToS/Privacy checkbox before submit
  • README mentions ora@aurem.live as support email
"""
from __future__ import annotations

from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[2]
PUBLIC = REPO / "frontend" / "public"
SRC = REPO / "frontend" / "src"


@pytest.mark.parametrize("filename", [
    "privacy-policy.md",
    "terms-of-service.md",
    "acceptable-use-policy.md",
])
def test_policy_files_exist(filename):
    p = PUBLIC / "policies" / filename
    assert p.exists(), f"{p} missing"
    text = p.read_text()
    assert len(text) > 500, f"{filename} suspiciously small ({len(text)} bytes)"
    # Stale support emails must be wiped — Iter 201 unified everything to polarisbuiltinc@gmail.com.
    for stale in ("privacy@auremcto.com", "support@auremcto.com", "abuse@auremcto.com"):
        assert stale not in text, f"{filename} still references {stale}"
    # New canonical address must be present.
    assert "polarisbuiltinc@gmail.com" in text, f"{filename} missing polarisbuiltinc@gmail.com"


def test_app_jsx_wires_policy_routes():
    app = (SRC / "App.jsx").read_text()
    for route in ("/privacy", "/terms", "/acceptable-use"):
        assert f'path="{route}"' in app, f"{route} route missing from App.jsx"
    assert "PolicyPage" in app, "PolicyPage import missing"


def test_landing_footer_has_policy_links():
    landing = (SRC / "pages" / "Landing.jsx").read_text()
    assert 'data-testid="footer-privacy"' in landing, "footer Privacy link missing"
    assert 'data-testid="footer-terms"' in landing, "footer Terms link missing"
    assert 'data-testid="footer-aup"' in landing, "footer Acceptable Use link missing"
    assert 'data-testid="footer-support"' in landing, "footer Contact link missing"
    assert "polarisbuiltinc@gmail.com" in landing


def test_signup_requires_terms_checkbox():
    signup = (SRC / "pages" / "Signup.jsx").read_text()
    # State-flag for agreement
    assert "agreed" in signup, "Signup must track an `agreed` state"
    # Hard gate at submit time
    assert "Please agree to the Terms" in signup, (
        "Signup submit must block until ToS is accepted with a clear message"
    )
    # Button disabled when unchecked
    assert "disabled={busy || !agreed}" in signup, (
        "Submit button must be disabled while ToS unchecked"
    )
    # Checkbox + both policy links
    assert 'data-testid="signup-terms-checkbox"' in signup
    assert 'data-testid="signup-terms-link"' in signup
    assert 'data-testid="signup-privacy-link"' in signup


def test_policy_page_component_renders_markdown():
    """PolicyPage.jsx must fetch the .md file via /policies/ static
    path AND use `marked` to render — regression-guard the rendering
    pipeline so a future refactor doesn't silently break it."""
    pp = (SRC / "pages" / "PolicyPage.jsx").read_text()
    assert 'from "marked"' in pp, "PolicyPage must import marked"
    assert "/policies/" in pp, "PolicyPage must fetch from /policies/ static path"
    assert "marked.parse" in pp or "marked(" in pp, "Must invoke marked parser"


def test_readme_uses_canonical_support_email():
    readme = (REPO / "README.md").read_text()
    assert "polarisbuiltinc@gmail.com" in readme, "README must reference polarisbuiltinc@gmail.com"
    # Stale CAD price must be gone (covered in iter94 too but belt+braces).
    assert "$35 / user / mo" not in readme, "stale $35 CAD price still in README"
