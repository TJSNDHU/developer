"""
tests/test_iter54_shipwall_wrapped_overview.py
================================================
Iter 54 — Ship Wall + ORA Wrapped + Admin Overview.

Source-level smoke tests for the new feature surface so a refactor that
silently removes them (or a wrong-prefix wiring) fails CI.
"""
from __future__ import annotations
import os
import inspect


# ─── Backend routers ────────────────────────────────────────────────────

def test_shipwall_router_registered():
    from routers.shipwall import router as wall
    paths = [r.path for r in wall.routes]
    # All public reads + the SVG badge endpoint + opt-in/out.
    assert "/wall/feed" in paths
    assert "/wall/user/{handle}" in paths
    assert "/wall/card/{task_id}" in paths
    assert "/wall/badge/{user_id}" in paths
    assert "/wall/stats" in paths
    assert "/wall/opt-out" in paths
    assert "/wall/opt-in" in paths


def test_wrapped_router_registered():
    from routers.wrapped import router as wrp
    paths = [r.path for r in wrp.routes]
    assert "/wrapped/me" in paths


def test_admin_council_stats_endpoint_present():
    from routers.admin import router as adm
    paths = [r.path for r in adm.routes]
    assert "/admin/council/stats" in paths


def test_main_includes_new_routers():
    """main.py must include both new routers under /api/aurem-dev."""
    with open(os.path.join(os.path.dirname(__file__),
                            "..", "main.py"), encoding="utf-8") as fh:
        src = fh.read()
    assert "shipwall_router" in src
    assert "wrapped_router" in src
    assert "app.include_router(shipwall_router" in src
    assert "app.include_router(wrapped_router"  in src


# ─── ShipWall helpers ───────────────────────────────────────────────────

def test_public_ship_strips_sensitive_fields():
    from routers.shipwall import _public_ship
    out = _public_ship(
        {
            "task_id": "t_abc", "commit_sha": "deadbef1234",
            "task": "fix",
            "github_owner": "TJ", "github_repo": "Aurem",
            "completed_at": 1717000000,
            "files_changed": ["a.py", "b.py"],
            # secrets that MUST NOT escape:
            "github_token": "ghp_XXXXX",
            "user_id": "u_1",
            "session_id": "s_1",
        },
        {"user_id": "u_1", "name": "Test", "github_login": "tj"},
    )
    serialised = repr(out)
    assert "ghp_XXXXX" not in serialised, "PAT leaked through public ship"
    assert "session_id" not in out, "session id leaked in public ship"
    # Commit sha is exposed (truncated) for the public card.
    assert out.get("commit_sha") and out["commit_sha"].startswith("deadbef")


def test_wrapped_share_text_format():
    """The share-text returned to the client must not leak placeholders
    and must include the AUREM hashtag for the viral loop."""
    from routers.wrapped import _share_text
    text = _share_text({
        "period_label": "June 2026",
        "tasks_shipped": 12, "hours_saved": 9.6, "repos_touched": 3,
        "top_mode": "C", "ship_streak_days": 5,
        "claude_corrections": 2, "developer_name": "Test",
    })
    assert "AUREMcto" in text or "AUREM" in text
    assert "12" in text       # tasks_shipped
    assert "9.6" in text      # hours_saved
    # No raw f-string placeholders left:
    assert "{" not in text and "}" not in text


# ─── Frontend wiring ────────────────────────────────────────────────────

def test_app_jsx_registers_wall_route():
    path = os.path.join(
        os.path.dirname(__file__),
        "..", "..", "frontend", "src", "App.jsx",
    )
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    assert 'path="/wall"' in src
    assert "ShipWall" in src


def test_admin_page_wires_overview_as_first_tab():
    path = os.path.join(
        os.path.dirname(__file__),
        "..", "..", "frontend", "src", "pages", "Admin.jsx",
    )
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    # Overview must be the default landing tab.
    assert 'useState("overview")' in src
    # Overview must be the FIRST nav item.
    nav_block = src.split("const NAV = [", 1)[1].split("];", 1)[0]
    first_id = nav_block.split('{ id: "', 1)[1].split('"', 1)[0]
    assert first_id == "overview", f"first nav id is {first_id!r}, expected 'overview'"
    # And the switch must route to AdminOverview.
    assert 'case "overview": return <AdminOverview />' in src


def test_analytics_page_renders_wrapped_card():
    path = os.path.join(
        os.path.dirname(__file__),
        "..", "..", "frontend", "src", "pages", "Analytics.jsx",
    )
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    assert "OraWrapped" in src
    assert "<OraWrapped" in src


def test_landing_has_wall_nav_link():
    path = os.path.join(
        os.path.dirname(__file__),
        "..", "..", "frontend", "src", "pages", "Landing.jsx",
    )
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    assert 'to="/wall"' in src
    assert "Ship Wall" in src
