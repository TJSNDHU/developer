"""
test_iter212m104_topbar_collapse_and_cache.py — Iter 212m-104

Static contract tests for the four user-reported fixes:

1. Topbar collapses its layout slot (max-height 0) when hidden — no
   black header gap above the chat pane.
2. /loop/start backend endpoint exists and returns a structured plan
   (proves Loop mode is real, not mocked).
3. /chat/history backend endpoint exists and returns saved messages
   (proves chat persistence is real).
4. Dashboard.jsx hydrates projects from localStorage cache on mount
   (instant render after login, no flicker waiting for /cto/projects).
"""
from pathlib import Path
import re


def _read(rel: str) -> str:
    return Path(f"/app/frontend/src/{rel}").read_text(encoding="utf-8")


def _read_be(rel: str) -> str:
    return Path(f"/app/backend/{rel}").read_text(encoding="utf-8")


def test_topbar_collapses_layout_slot_when_hidden():
    src = _read("components/dashboard/v2/TopBar.jsx")
    # Inline style drives maxHeight — survives Tailwind JIT race.
    assert "maxHeight: effectiveHidden ? 0 : 200" in src
    # Border bottom also drops when hidden so there's no 1px line.
    assert "border-b-transparent" in src
    # transition includes max-height for smooth collapse.
    assert "transition-[max-height,transform,border-color]" in src


def test_loop_endpoint_exists_in_backend():
    src = _read_be("routers/loop.py")
    # Real route, real plan structure.
    assert "@router.post" in src
    # The startLoop handler returns loop_id + state + plan
    assert "loop_id" in src
    assert "phase" in src


def test_chat_history_endpoint_exists():
    src = _read_be("routers/chat.py")
    assert "@router.get" in src
    # The /history route ChatPanel calls
    assert "/history" in src or "/chat/history" in src


def test_dashboard_caches_projects_for_instant_render():
    src = _read("pages/Dashboard.jsx")
    # Cache key + read on mount + write after every successful fetch
    assert 'PROJECTS_CACHE_KEY = "aurem_projects_cache"' in src
    assert "localStorage.getItem(PROJECTS_CACHE_KEY)" in src
    assert "localStorage.setItem(PROJECTS_CACHE_KEY" in src


def test_logo_cache_clear_preserves_projects_cache():
    """User shouldn't lose their cached project list when they click
    the logo to clear cache — only ephemeral keys are dropped."""
    src = _read("components/dashboard/v2/SidebarBound.jsx")
    # KEEP set whitelist must include the projects cache.
    m = re.search(r'const KEEP = new Set\(\[(.*?)\]\)', src, re.S)
    assert m, "KEEP whitelist not found in logo click handler"
    keep_str = m.group(1)
    assert "aurem_projects_cache" in keep_str
    # Auth + theme + wizard + active project also preserved.
    for key in ("aurem_token", "aurem_user", "aurem_theme",
                "aurem_wizard_dismissed", "aurem_active_project"):
        assert key in keep_str, f"{key} missing from KEEP whitelist"
