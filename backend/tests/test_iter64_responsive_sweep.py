"""
test_iter64_responsive_sweep.py — Iter 64 responsive overhaul.

Source-level smoke tests covering:
  • Global CSS safety net in index.css
  • Shell.jsx mobile drawer state + menu button
  • Admin.jsx table wrapped in scrollable container
  • Architecture endpoint expanded with new integrations
"""
from __future__ import annotations

import os
import re


def _read(rel):
    base = os.path.join(os.path.dirname(__file__), "..", "..")
    with open(os.path.join(base, rel), encoding="utf-8") as fh:
        return fh.read()


# ── 1. Global CSS safety net ──────────────────────────────────────────

def test_global_css_caps_viewport_overflow():
    css = _read("frontend/src/index.css")
    # Body never scrolls horizontally
    assert "overflow-x: hidden" in css
    assert "max-width: 100vw" in css
    # Media never breaks out
    for tag in ("img", "video", "iframe"):
        assert tag in css
    # Tables wrap helper exists
    assert ".aurem-table-wrap" in css
    assert "overflow-x: auto" in css
    # App shell + mobile drawer rules
    assert ".aurem-app-shell" in css
    assert "@media (max-width: 900px)" in css
    assert "aurem-mobile-menu-btn" in css
    assert "aurem-mobile-backdrop" in css
    # Mobile main padding override
    assert ".aurem-main-padded" in css


def test_global_css_long_words_wrap():
    css = _read("frontend/src/index.css")
    assert "overflow-wrap: anywhere" in css
    # Code blocks wrap or scroll, never overflow
    assert "pre" in css and "code" in css


# ── 2. Shell.jsx mobile drawer wiring ─────────────────────────────────

def test_shell_renders_mobile_menu_button():
    src = _read("frontend/src/components/Shell.jsx")
    assert 'data-testid="mobile-menu-btn"' in src
    assert "aurem-mobile-menu-btn" in src
    # Backdrop closes drawer
    assert 'data-testid="mobile-backdrop"' in src
    # State + media query
    assert "drawerOpen" in src
    assert 'matchMedia("(max-width: 900px)")' in src
    # Grid class — owned by CSS now
    assert 'className="aurem-app-shell"' in src
    # Drawer closes on route change
    assert "setDrawerOpen(false)" in src
    # Main uses padded class
    assert "aurem-main-padded" in src


def test_shell_no_longer_uses_fixed_grid_pixel_template_in_jsx():
    """The old `gridTemplateColumns: \\`${collapsed ? 64 : 260}px 1fr\\``
    in the JSX must be gone — that string broke mobile. The CSS file
    now owns the grid template via `.aurem-app-shell`."""
    src = _read("frontend/src/components/Shell.jsx")
    # JS-side hard pixel template removed
    assert "${collapsed ? 64 : 260}px 1fr" not in src


# ── 3. Admin.jsx table is wrapped ─────────────────────────────────────

def test_admin_table_wrapped_for_horizontal_scroll():
    src = _read("frontend/src/pages/Admin.jsx")
    assert 'className="aurem-table-wrap"' in src
    # Dashboard metric grid is responsive
    assert "repeat(auto-fit, minmax(150px, 1fr))" in src
    # Architecture services grid is responsive
    assert "repeat(auto-fit, minmax(180px, 1fr))" in src


# ── 4. Backend architecture endpoint expanded ─────────────────────────

def test_architecture_endpoint_probes_new_services():
    src = _read("backend/routers/admin.py")
    for svc in ("Cloudflare API", "Vercel API", "Anthropic API",
                "Sentry ingest", "Stripe API"):
        assert svc in src, f"Architecture must probe {svc}"


def test_architecture_endpoint_tracks_new_integrations():
    src = _read("backend/routers/admin.py")
    for key in ("anthropic (claude maxx)", "cloudflare_purge",
                "vercel_deploy_hook", "sentry_dsn", "resend (email)"):
        assert key in src, f"Architecture must track {key} integration"


def test_architecture_returns_human_note_field():
    src = _read("backend/routers/admin.py")
    # The "X/Y integrations configured" summary must be produced
    assert "integrations configured" in src
    assert '"note": note' in src


# ── 5. Recurring issues memory file ───────────────────────────────────

def test_recurring_issues_memory_file_exists():
    path = os.path.join(os.path.dirname(__file__), "..", "..",
                        "memory", "RECURRING_ISSUES.md")
    assert os.path.exists(path), (
        "RECURRING_ISSUES.md must exist so future agents read it before "
        "touching ORA / Vanguard / Mode-D"
    )
    with open(path, encoding="utf-8") as fh:
        body = fh.read()
    # Each recurring pattern must be present
    for pat in ("empty file body", "90s timeout", "insufficient_signal",
                "Wrong-mode classification", "Multi-pillar"):
        assert pat.lower() in body.lower(), (
            f"RECURRING_ISSUES.md must document '{pat}' pattern"
        )
    # Standing rules section
    assert "Standing rules" in body
