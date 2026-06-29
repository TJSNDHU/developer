"""
Iter 212m-133 — Red repo dot in sidebar must be actionable.

Founder reported "dogfood repo showing red in founder account".
Production check confirmed:
  • polarisbuiltinc-wq/auremdev returns HTTP 404 from GitHub —
    the repo was deleted or renamed.
  • The other project TJSNDHU/Aurem returns 200, so the OAuth
    token is healthy — this is per-repo, not a global auth issue.
  • The sidebar just showed a red dot with no path to recovery.

This iter:
  • Surfaces the disconnected `error` reason (e.g. `repo_not_found`)
    through `liveStatus` in SidebarBound.jsx.
  • Renders a human-readable reason below the repo name in red,
    + a Settings (⚙) icon button next to the row that deep-links
    to `/projects?edit=<project_id>`.
  • Right-clicking a red row also opens the edit deep-link
    (power-user shortcut).
  • Projects.jsx now reads `?edit=<id>` and opens the Edit Project
    modal directly so the user can re-link to a different repo or
    delete the project in two clicks.

Source-pattern contract tests (no DOM bootstrap needed) — keep
the regressions tight without needing playwright.
"""
from __future__ import annotations


def _read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def test_sidebar_tracks_disconnect_error_reason():
    """SidebarBound must store the `error` reason from the backend
    in `liveStatus[id]` so the UI can show why the dot is red."""
    src = _read("/app/frontend/src/components/dashboard/v2/SidebarBound.jsx")
    # The mapped object must capture status + error + http_code.
    assert "error: s.error || null" in src
    assert "http_code: s.http_code || null" in src
    # liveError() helper exists.
    assert "function liveError" in src


def test_sidebar_renders_red_reason_and_fix_button():
    """A red repo must show (a) the reason text in red below the
    branch line and (b) an inline Settings icon that deep-links
    to /projects?edit=<id>."""
    src = _read("/app/frontend/src/components/dashboard/v2/SidebarBound.jsx")
    # Fix-button test id pattern follows the existing repo slug naming.
    assert "ds2-sidebar-repo-${slug}-fix" in src
    # Settings icon imported + used.
    assert "<Settings className=\"size-3\"" in src
    # Deep-link to /projects with edit=
    assert "/projects?edit=" in src
    # Right-click also opens the fix flow.
    assert "onContextMenu={isRed ? goFix : undefined}" in src


def test_sidebar_reason_label_covers_known_codes():
    """liveReasonLabel must translate the backend's machine codes
    to short human strings — pin the critical ones so a refactor
    can't silently regress to 'Disconnected' for every reason."""
    src = _read("/app/frontend/src/components/dashboard/v2/SidebarBound.jsx")
    for code in [
        "repo_not_found",
        "invalid_token",
        "missing_scope",
        "github_unauthorized",
        "github_rate_limited",
    ]:
        assert f'"{code}"' in src, f"missing reason mapping for {code}"


def test_projects_page_reads_edit_query_param():
    """Projects.jsx must read `?edit=<id>` and open the Edit Project
    modal so the deep-link from the sidebar lands the user where
    they can fix the broken project."""
    src = _read("/app/frontend/src/pages/Projects.jsx")
    assert 'params.get("edit")' in src
    # setEditingProject is the existing state setter we hook into.
    assert "setEditingProject(p)" in src
    # window.history.replaceState clears the query so refreshing
    # doesn't re-open the modal.
    assert 'window.history.replaceState({}, "", "/projects")' in src


def test_data_attributes_for_e2e_pin():
    """Pin the data-attributes the future testing-agent will use to
    target the disconnected-repo flow."""
    src = _read("/app/frontend/src/components/dashboard/v2/SidebarBound.jsx")
    # data-status surfaces the dot tone (green/red/yellow/gray).
    assert 'data-status={dot}' in src
    # data-error surfaces the backend reason string.
    assert 'data-error={err || ""}' in src
