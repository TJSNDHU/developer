"""
Iter 212m-157 — Admin-only gate on Bug Hunt, Vanguard, Security Scan,
Health Scan.

Founder spec: "Hide Bug Hunt, Vanguard Scan, Security Scan, and Health
Scan from the main sidebar nav for all users EXCEPT accounts flagged
as is_founder=true or is_admin=true in the DB. Routes stay alive. No
redirects. No new pages. Just conditional rendering on the nav links."

Follow-up tightening: "Add route guard on each of those 4 pages: if
user.is_admin !== true → redirect to /dashboard. Founder/admin
accounts bypass both guards."

This iter pins the contract:
  • Single source-of-truth helper `isAdminOrFounder()` in lib/api.js.
  • Three protected page routes redirect non-admins to /dashboard
    via a thin guard wrapper that keeps Rules of Hooks safe.
  • Sidebar Health Scanner link gated via `adminOnly: true` on the
    TOOLS array.
  • Inline composer Security Scan button gated by isAdminOrFounder().
  • Landing nav Bug Hunt link gated by isAdminOrFounder() (anon
    visitors still see it for marketing/SEO).
"""
from pathlib import Path

_FRONTEND = Path(__file__).resolve().parent.parent.parent / "frontend" / "src"


# ── Helper exposed and importable ───────────────────────────────────

def test_is_admin_or_founder_helper_exposed():
    src = (_FRONTEND / "lib" / "api.js").read_text()
    assert "export function isAdminOrFounder" in src
    # Both is_admin and is_founder are accepted, plus the legacy
    # tier==="founder" string.
    assert "is_admin" in src
    assert "is_founder" in src
    assert 'tier === "founder"' in src


# ── Route guards on the 3 protected pages ───────────────────────────

def test_bughunt_redirects_nonadmin_to_dashboard():
    src = (_FRONTEND / "pages" / "BugHunt.jsx").read_text()
    assert "isAdminOrFounder" in src
    # Non-admin authed → /dashboard (per founder spec).  Anonymous
    # visitors still see the marketing page.
    assert 'to="/dashboard"' in src
    assert "bh-nonadmin-redirect" in src
    # The old <Navigate to="/codebase-health"> push for ALL authed
    # users from iter 212m-154 is gone — admins now see the
    # marketing page just like anon visitors.  (A regular Link to
    # the Health Dashboard inside the marketing body is fine — that
    # is content, not a forced redirect.)
    assert '<Navigate to="/codebase-health"' not in src


def test_codebase_health_has_admin_guard_wrapper():
    src = (_FRONTEND / "pages" / "CodebaseHealth.jsx").read_text()
    assert "isAdminOrFounder" in src
    assert "health-nonadmin-redirect" in src
    # Hook-order safety: the inner component is split out so the
    # outer guard can early-return without violating Rules of Hooks.
    assert "function CodebaseHealthInner" in src


def test_admin_vanguard_has_admin_guard_wrapper():
    src = (_FRONTEND / "pages" / "AdminVanguard.jsx").read_text()
    assert "isAdminOrFounder" in src
    assert "vanguard-nonadmin-redirect" in src
    # Same Rules of Hooks split as CodebaseHealth.
    assert "function AdminVanguardInner" in src


# ── Nav link visibility (sidebar + landing + composer) ──────────────

def test_sidebar_health_link_is_admin_only():
    """Iter 212m-162 — Health Scanner is now FULLY REMOVED from the
    sidebar (stronger guarantee than the iter 212m-157 admin gate).
    It lives as a "Coming soon" card in /tools instead.  This test
    enforces the absence so the row never silently returns."""
    src = (_FRONTEND / "components" / "dashboard" / "v2" / "SidebarBound.jsx").read_text()
    # Slice to just the TOOLS list so historical comments don't false-positive.
    start = src.find("const TOOLS = [")
    end   = src.find("];", start)
    tools_block = src[start:end]
    # No literal id row, no label row, no HeartPulse icon reference.
    assert 'id: "health"' not in tools_block
    assert 'label: "Health Scanner"' not in tools_block
    assert "HeartPulse" not in tools_block


def test_landing_bughunt_link_is_admin_or_anon():
    src = (_FRONTEND / "pages" / "Landing.jsx").read_text()
    assert "isAdminOrFounder" in src
    # Conditional rendering wraps the bughunt link — anonymous (no
    # token) OR admin shows it; logged-in non-admin hides it.
    assert "!getToken() || isAdminOrFounder" in src


def test_chatpanel_security_scan_button_is_admin_only():
    """Iter 212m-162 — Security Scan composer button is now FULLY
    REMOVED from the chat composer (stronger guarantee than the iter
    212m-157 admin gate).  The scanner lives as a "Coming soon" card
    in /tools instead.  This test enforces the absence so the button
    never silently returns."""
    src = (_FRONTEND / "components" / "ChatPanel.jsx").read_text()
    assert 'testid="chat-security-scan-btn"' not in src
    assert 'chat-security-scan-badge' not in src
    assert 'chat-security-scan-auto-badge' not in src


# ── Routes stay alive (per founder spec — "Routes stay alive") ──────

def test_routes_for_protected_pages_still_registered():
    app = (_FRONTEND / "App.jsx").read_text()
    # Routes exist — the protection is at the page-component level,
    # not by removing routes from the registry.
    assert "/admin/vanguard" in app
    assert "/codebase-health" in app
    assert "/bug-hunt" in app
