"""
Iter 212m-148 — Persistent Fix Bar + global FixJob state contract tests.

Verifies the architecture promised by the founder spec:
"fix(fix-panel): persistent job state — SSE global, panel hide-only,
bar always visible until dismissed".
"""
from pathlib import Path

FRONTEND = Path(__file__).resolve().parent.parent.parent / "frontend" / "src"


def _read(rel):
    return (FRONTEND / rel).read_text()


def test_fix_job_context_exists_and_owns_sse():
    """The new global context lives at App root and owns the EventSource."""
    text = _read("components/FixJobContext.jsx")
    assert "export function FixJobProvider" in text
    assert "export function useFixJob" in text
    # The EventSource lives inside the provider — not the panel.
    assert "new EventSource(url" in text
    # The cleanup runs on jobId change, NOT on visibility change —
    # this is what makes the SSE survive panel hide/show.
    assert "}, [jobId])" in text or "}, [jobId]);" in text


def test_persistent_fix_bar_component_exists():
    """The 44 px persistent bar component is created."""
    text = _read("components/PersistentFixBar.jsx")
    assert "data-testid=\"persistent-fix-bar\"" in text
    # 44 px height per spec.
    assert "height: 44" in text
    # 2 px progress track.
    assert "data-testid=\"persistent-fix-bar-progress\"" in text
    # State-driven dot, label, badge.
    assert "data-testid=\"persistent-fix-bar-dot\"" in text
    assert "data-testid=\"persistent-fix-bar-label\"" in text
    assert "data-testid=\"persistent-fix-bar-badge\"" in text


def test_persistent_bar_click_toggles_panel_does_not_kill_sse():
    """Clicking the bar must call togglePanel (UI only), never cancel."""
    text = _read("components/PersistentFixBar.jsx")
    assert "togglePanel" in text
    # The bar must NOT import or call any cancel / close-stream
    # actions on its primary toggle path.
    assert "onClick={togglePanel}" in text


def test_persistent_bar_dismiss_only_in_terminal_states():
    """Dismiss button appears only when status is done/error."""
    text = _read("components/PersistentFixBar.jsx")
    assert "showDismiss" in text
    assert 'status === "done"' in text and 'status === "error"' in text


def test_drawer_uses_context_not_local_sse():
    """The drawer reads job state from FixJobContext — it does NOT
    own its own EventSource any more."""
    text = _read("components/FixProgressDrawer.jsx")
    assert "useFixJob" in text
    # No EventSource creation inside the drawer file.
    assert "new EventSource" not in text
    # Backdrop click + Escape → hidePanel(), NOT setOpen(false).
    assert "hidePanel" in text


def test_drawer_backdrop_click_hides_not_cancels():
    """Critical contract: clicking the scrim runs hidePanel (UI only)."""
    text = _read("components/FixProgressDrawer.jsx")
    # The scrim element calls hidePanel — not a close that nukes SSE.
    assert "data-testid=\"fix-progress-scrim\"" in text
    # Find the scrim onClick — must call hidePanel.
    idx = text.find('data-testid="fix-progress-scrim"')
    snippet = text[idx:idx + 400]
    assert "onClick={hidePanel}" in snippet, \
        f"Backdrop must invoke hidePanel — got: {snippet[:200]}"


def test_drawer_escape_key_hides_not_cancels():
    """Escape must call hidePanel, not cancel."""
    text = _read("components/FixProgressDrawer.jsx")
    assert 'e.key === "Escape"' in text
    assert "hidePanel()" in text


def test_drawer_uses_transform_not_unmount():
    """Drawer slides via CSS transform — never unmounts while job is
    running.  This is the foundation of "panel hide ≠ SSE kill"."""
    text = _read("components/FixProgressDrawer.jsx")
    assert "translateX(0)" in text or "translateY(0)" in text
    # The hidden state pushes off-screen — does NOT return null while
    # the job is in flight.
    assert "translateX(110%)" in text or "translateY(100%)" in text
    # Transition on transform property (not "all" — which breaks transforms).
    assert "transition: \"transform" in text


def test_app_jsx_mounts_provider_and_bar():
    """App root wires the provider + drawer + bar together."""
    text = _read("App.jsx")
    assert "FixJobProvider" in text
    assert "PersistentFixBar" in text
    assert "FixProgressDrawer" in text
    # Provider must wrap the drawer + bar so they share state.
    p_idx = text.find("<FixJobProvider>")
    d_idx = text.find("<FixProgressDrawer />")
    b_idx = text.find("<PersistentFixBar />")
    assert p_idx > 0 and d_idx > p_idx and b_idx > p_idx, \
        "FixJobProvider must wrap drawer + bar"


def test_context_dismiss_clears_localstorage_and_state():
    """Dismiss is the one path that ALSO closes the SSE — only
    available from terminal-state bar."""
    text = _read("components/FixJobContext.jsx")
    assert "const dismiss = useCallback" in text
    # It must clear the persisted in-flight job key.
    assert "localStorage.removeItem(LS_JOB_KEY)" in text
    # And actually close the SSE.
    assert "esRef.current.close()" in text


def test_context_open_event_hookup():
    """The global window event still works as the entry point —
    BulkFixConfirmModal etc don't need to change."""
    text = _read("components/FixJobContext.jsx")
    assert 'aurem:open-fix-progress' in text
    assert "startJob" in text


def test_context_localstorage_rehydration_does_not_auto_open():
    """Per founder spec: on page refresh the bar surfaces, NOT the
    panel.  The user explicitly clicks the bar to open the panel."""
    text = _read("components/FixJobContext.jsx")
    # The mount-rehydrate effect must set jobId WITHOUT setPanelVisible(true).
    rehydrate_idx = text.find("Re-attach silently")
    assert rehydrate_idx > 0
    snippet = text[rehydrate_idx:rehydrate_idx + 1200]
    assert "setPanelVisible(true)" not in snippet, \
        "Rehydrate path must not auto-open the panel"


def test_drawer_top_anchored_above_persistent_bar():
    """Drawer sits ABOVE the 44 px bar so the bar is always reachable."""
    text = _read("components/FixProgressDrawer.jsx")
    # bottom: 44 anchors the drawer's lower edge to the top of the bar.
    assert "bottom: 44" in text
