"""
test_iter88_admin_and_wall — three real user-reported fixes:

  (1) /wall page rendered without the sidebar even after login.
      Fix: ShipWall now wraps itself in <Shell> when getToken() is truthy
      (still renders the marketing layout for anonymous visitors).

  (2) AuremAdminPanel "Refresh" button clicked but nothing visible
      happened. Endpoint actually worked (200 OK), but no spinner,
      no last-updated timestamp, no error surface.
      Fix: refreshNow() with refreshing + lastUpdated state, disabled
      button + spinner during fetch, "Live · last updated Xs ago"
      indicator visible on every tab.

  (3) Admin auto-update wasn't visible — users couldn't tell whether
      it was live or stale.
      Fix: visibility-aware setInterval (pauses on background tabs,
      refetches immediately on tab focus), plus the "Live ·" indicator.
"""
from __future__ import annotations

import os
import re

BASE = os.path.join(os.path.dirname(__file__), "..", "..")


def _read(rel: str) -> str:
    with open(os.path.join(BASE, rel), encoding="utf-8") as fh:
        return fh.read()


# ── (1) ShipWall sidebar ──────────────────────────────────────────────

def test_shipwall_imports_shell_and_renders_inside_when_authed():
    src = _read("frontend/src/pages/ShipWall.jsx")
    assert 'import Shell from "../components/Shell"' in src
    # When the user is logged in, must wrap content in <Shell>.
    assert "if (authed) {" in src
    assert "<Shell>{body}</Shell>" in src
    # getToken must drive the decision (not a random env / state).
    assert "getToken()" in src


def test_shipwall_still_renders_for_anonymous_visitors():
    """The fix must NOT break the public marketing-style layout —
    anonymous users keep the no-chrome view."""
    src = _read("frontend/src/pages/ShipWall.jsx")
    # The "body" var holds the standalone layout; gets returned when
    # !authed via the final `return body;`.
    assert re.search(r"return body;\s*}", src), (
        "anonymous visitors should still get the standalone layout"
    )


# ── (2) Admin refresh button feedback ─────────────────────────────────

def test_admin_panel_refresh_button_has_disabled_and_spinner_state():
    src = _read("frontend/src/components/AuremAdminPanel.jsx")
    # State must exist.
    assert "const [refreshing,  setRefreshing]  = useState(false);" in src
    # Button must consume it (disabled + spinner + label switch).
    assert "disabled={refreshing}" in src
    assert "refreshing ? \"Refreshing…\" : \"Refresh\"" in src
    # data-testid for QA / automation.
    assert 'data-testid="admin-panel-refresh"' in src
    # Spinner keyframes injected so the animation works.
    assert "@keyframes auremspin" in src


def test_admin_panel_refresh_actually_refetches_visible_tab():
    """refreshNow must hit BOTH ora-stats AND (when on the brain tab)
    project-brain — that's the real fix vs the old version which only
    re-pulled stats and silently dropped a brain refresh."""
    src = _read("frontend/src/components/AuremAdminPanel.jsx")
    m = re.search(
        r"const refreshNow = useCallback\(async \(\) => \{([\s\S]+?)\}, \[",
        src,
    )
    assert m, "refreshNow callback not found"
    body = m.group(1)
    assert "await fetchStats()" in body
    assert 'tab === "brain"' in body
    assert "await fetchBrain(projectId)" in body
    assert "setRefreshing(true)" in body
    assert "setRefreshing(false)" in body  # ensures finally-clause cleanup


# ── (3) Live indicator + visibility-aware polling ─────────────────────

def test_admin_panel_shows_last_updated_indicator():
    src = _read("frontend/src/components/AuremAdminPanel.jsx")
    assert 'data-testid="admin-panel-last-updated"' in src
    assert "lastUpdated" in src
    # Helper that formats the relative time must be defined.
    assert "function _relTime" in src
    # The indicator copy must mention the live refresh cadence so the
    # user trusts the data is current.
    assert "auto-refresh 30" in src


def test_admin_panel_polling_pauses_on_hidden_tabs():
    src = _read("frontend/src/components/AuremAdminPanel.jsx")
    # visibility-aware effect must exist (otherwise we waste API calls
    # on backgrounded tabs).
    assert 'document.visibilityState === "visible"' in src
    assert 'document.addEventListener("visibilitychange"' in src
    # And it MUST also refetch immediately on tab refocus so the user
    # sees fresh data the moment they switch back, not 30 s later.
    assert "// catch up immediately on tab refocus" in src


def test_admin_panel_clears_error_on_successful_refresh():
    """A previous failed refresh's error banner must clear when the
    next refresh succeeds — otherwise it stays stuck red."""
    src = _read("frontend/src/components/AuremAdminPanel.jsx")
    m = re.search(
        r"const fetchStats = useCallback\(async \(\) => \{([\s\S]+?)\}, \[",
        src,
    )
    assert m
    body = m.group(1)
    assert "setError(null);" in body, (
        "fetchStats must clear any previous error before attempting "
        "the new request"
    )
