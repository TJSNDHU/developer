"""
Iter 212m-158 — Backend require_admin decorator + /tools preview page.

Two-part landing:

PART 1 — Backend admin gate
  • New shared helper `cto_services.auth.require_admin(authorization)`
    that is the single source of truth for admin-only routes.
  • Applied to /security-scan/run, /security-scan/fix, and all four
    /codebase-health/* endpoints (cache-stats, scan, last, fix).
  • Returns HTTP 403 "Admin access required" for non-admins.
  • CI-side endpoints (/vanguard/ci-findings etc.) still use the
    shared-token auth — they are NOT user-auth gated, so we leave
    them alone.

PART 2 — /tools preview page
  • New ToolsPage.jsx with the four Coming-Soon cards (Bug Hunt,
    Vanguard, Security Scan, Health Scan).
  • /tools route registered (visible to ALL users — no admin gate).
  • Sidebar TOOLS array has a new "Developer tools" entry without
    `adminOnly`.
  • Notify-me form POSTs to /notify-interest which persists into
    the `tool_notify_interest` Mongo collection (anon-friendly,
    rate-limited 20/min per IP).
  • Page does NOT link to the real /codebase-health, /admin/vanguard,
    /bug-hunt routes — the cards are display-only previews.
"""
from pathlib import Path

import pytest

_BACKEND  = Path(__file__).resolve().parent.parent
_FRONTEND = Path(__file__).resolve().parent.parent.parent / "frontend" / "src"


# ─── Part 1 — backend require_admin ─────────────────────────────────

def test_require_admin_is_exposed_from_auth_module():
    src = (_BACKEND / "cto_services" / "auth.py").read_text()
    assert "async def require_admin" in src
    # Falls through to 403 when neither the JWT claim nor the live
    # DB row mark the user as admin/founder.
    assert 'HTTPException(403, "Admin access required")' in src


def test_security_scan_run_uses_require_admin():
    text = (_BACKEND / "routers" / "security_scan.py").read_text()
    assert "from cto_services.auth import current_dev, require_admin" in text
    # The two routes both gate through require_admin now.
    run_idx = text.index("async def run_security_scan")
    run_body = text[run_idx: run_idx + 1500]
    assert "await require_admin(authorization)" in run_body
    fix_idx = text.index("async def apply_security_fix")
    fix_body = text[fix_idx: fix_idx + 1500]
    assert "await require_admin(authorization)" in fix_body


def test_codebase_health_all_four_routes_use_require_admin():
    text = (_BACKEND / "routers" / "codebase_health.py").read_text()
    assert "from cto_services.auth import current_dev, require_admin" in text
    # Each of the 4 handlers (cache-stats, scan, last, fix) calls the
    # shared helper.  We verify by checking the count appears at
    # least 4 times in the file.
    assert text.count("await require_admin(authorization)") >= 4
    # And the legacy inline "Admin only" 403 raise is gone.
    assert 'raise HTTPException(403, "Admin only")' not in text


def test_vanguard_ci_routes_left_alone():
    """vanguard_ci.py uses CI-shared-token auth, not user JWT.  We
    must NOT add require_admin there — that would break trufflehog
    CI ingestion."""
    text = (_BACKEND / "routers" / "vanguard_ci.py").read_text()
    assert "require_admin" not in text
    # Still uses its own bespoke token check.
    assert "_verify_ci_auth" in text


# ─── Part 2 — /tools preview page ───────────────────────────────────

def test_tools_page_exists_and_has_all_four_cards():
    p = _FRONTEND / "pages" / "ToolsPage.jsx"
    assert p.exists(), "ToolsPage.jsx missing"
    src = p.read_text()
    for tool_id in ("bug-hunt", "vanguard", "security-scan", "health-scan"):
        assert f'id: "{tool_id}"' in src, f"missing tool entry: {tool_id}"
    # testids are built via template literal `tools-card-${tool.id}` —
    # the pattern lives in the JSX once and the suffix is per-tool at
    # render time.
    assert 'data-testid={`tools-card-${tool.id}`}' in src
    # No links to the protected routes — per spec "DO NOT link this
    # page to actual tool routes".
    for route in ("/codebase-health", "/admin/vanguard"):
        assert route not in src, f"unexpected link to {route}"


def test_tools_page_uses_real_repos_hook():
    src = (_FRONTEND / "pages" / "ToolsPage.jsx").read_text()
    # The mock useRepos() with hard-coded "your-org/*" repos is gone.
    assert '"your-org/frontend"' not in src
    # And the real /cto/projects/list fetch is wired.
    assert '/cto/projects/list' in src
    # Hook name preserved per founder spec.
    assert "function useRepos" in src


def test_tools_page_notify_form_posts_to_notify_interest():
    src = (_FRONTEND / "pages" / "ToolsPage.jsx").read_text()
    # The notify-me form fires the POST that the new backend handles.
    assert 'api.post("/notify-interest"' in src
    # Payload shape matches the contract.
    assert "{ tool: tool.id, email, repo:" in src


def test_tools_route_registered_in_app_jsx():
    app = (_FRONTEND / "App.jsx").read_text()
    assert "ToolsPage" in app
    assert '<Route path="/tools"' in app


def test_sidebar_has_developer_tools_link_no_admin_gate():
    src = (_FRONTEND / "components" / "dashboard" / "v2" / "SidebarBound.jsx").read_text()
    # New TOOLS entry, no adminOnly flag.
    assert 'id: "tools",' in src
    assert "Developer tools" in src
    # The new id is NOT gated.  We approximate this by checking that
    # the `tools` id line does NOT contain adminOnly anywhere on it.
    tools_line = next(
        (ln for ln in src.splitlines() if 'id: "tools",' in ln),
        "",
    )
    assert "adminOnly" not in tools_line


def test_dashboard_routes_tools_id_to_slash_tools():
    src = (_FRONTEND / "pages" / "Dashboard.jsx").read_text()
    # onToolClick → /tools navigation handler is present.
    assert '_go("/tools")' in src


# ─── /notify-interest backend endpoint ──────────────────────────────

def test_notify_interest_router_registered():
    main = (_BACKEND / "main.py").read_text()
    assert "from routers.notify_interest import router" in main
    assert "notify_interest_router" in main


def test_notify_interest_validates_input():
    src = (_BACKEND / "routers" / "notify_interest.py").read_text()
    # Allowed tools whitelist must match the four cards on /tools.
    for tool_id in ("bug-hunt", "vanguard", "security-scan", "health-scan"):
        assert f'"{tool_id}"' in src
    # Email regex + length cap.
    assert "_EMAIL_RX" in src
    # Per-IP rate limit.
    assert "_rate_check" in src
    # Persists into tool_notify_interest collection.
    assert "tool_notify_interest" in src
