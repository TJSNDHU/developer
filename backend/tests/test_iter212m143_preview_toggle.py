"""
Iter 212m-143 — Topbar Preview tab toggle behaviour.

Founder spec: clicking the topbar "Preview" tab should TOGGLE the
preview window — first click opens, second click closes. Previously
every click dispatched `aurem:toggle-preview { open: true }`, so a
second click on Preview was a no-op (the panel stayed open and the
user had to click the Hide button inside the panel itself).

Source-pattern contract tests pin the fix so a future refactor
can't silently bring the bug back.
"""
from __future__ import annotations

from pathlib import Path

import pytest

DASHBOARD = Path("/app/frontend/src/pages/Dashboard.jsx")


@pytest.fixture(scope="module")
def src() -> str:
    return DASHBOARD.read_text(encoding="utf-8")


def test_dashboard_tracks_preview_open_state(src: str) -> None:
    """Dashboard must own a `previewOpen` state mirror so the topbar
    toggle has a definitive `is-it-open-right-now?` to flip against."""
    assert "useState(false)" in src
    assert "const [previewOpen" in src or "previewOpen, setPreviewOpen" in src


def test_toggle_preview_callback_flips_state(src: str) -> None:
    """`handleTogglePreview` must call `setPreviewOpen((cur) => !cur)`
    (or equivalent) — NOT a hard-coded `open: true` payload."""
    # Anchor on the comment marker for the new behaviour.
    assert "Iter 212m-143" in src
    # The new callback must contain the flip and the dispatch must
    # use the computed `next` value, not a literal true.
    assert "setPreviewOpen((cur)" in src
    assert 'detail: { open: next }' in src
    # The old hard-coded payload must be gone.
    assert (
        "detail: { open: true }\n    }));\n  }, []);"
        not in src
    ), "Old hard-coded `open: true` handleTogglePreview must be removed."


def test_dashboard_listens_to_preview_state_changed(src: str) -> None:
    """ChatPanel broadcasts `aurem:preview-state-changed` on every
    state flip (incl. auto-open when a code reply lands). Dashboard
    must mirror that so the topbar's effective state never drifts."""
    assert 'addEventListener("aurem:preview-state-changed"' in src
    assert 'removeEventListener("aurem:preview-state-changed"' in src


def test_clicking_preview_tab_toggles_when_open(src: str) -> None:
    """The TopBar `onTabChange` handler must funnel `Preview` clicks
    through `handleTogglePreview` so the toggle behaviour applies to
    the tab AND the (separate) Preview button consistently."""
    # The handler should invoke handleTogglePreview when the next
    # tab is "Preview" — and NOT before checking the current state.
    assert 'if (next === "Preview")' in src
    assert "handleTogglePreview()" in src


def test_chatpanel_already_supports_event_toggle() -> None:
    """ChatPanel's existing event listener already supports `open:
    false` to close — sanity-check that contract is unchanged."""
    cp = Path("/app/frontend/src/components/ChatPanel.jsx").read_text(
        encoding="utf-8",
    )
    assert 'addEventListener("aurem:toggle-preview"' in cp
    # The listener already handles boolean OR fallback-toggle — we
    # don't need to change it; just pin that contract.
    assert 'const desired = e?.detail?.open;' in cp
    assert 'typeof desired === "boolean" ? desired : !cur' in cp
