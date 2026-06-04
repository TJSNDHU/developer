"""Iter 76 — live preview pane (split-pane chat ↔ iframe blob)."""
import os
import re


def _read(rel: str) -> str:
    base = os.path.join(os.path.dirname(__file__), "..", "..")
    with open(os.path.join(base, rel), encoding="utf-8") as fh:
        return fh.read()


def test_preview_pane_component_exists():
    js = _read("frontend/src/components/PreviewPane.jsx")
    assert 'data-testid="preview-pane"' in js
    assert "buildBlobUrl" in js
    # Sandbox iframe — no privilege escalation
    assert 'sandbox="allow-scripts allow-same-origin allow-forms"' in js
    # Polls the task endpoint
    assert "/cto/tasks/" in js
    # Both modes available
    assert "preview-tab-blob" in js
    assert "preview-tab-live" in js


def test_dashboard_renders_split_pane():
    js = _read("frontend/src/pages/Dashboard.jsx")
    assert "PreviewPane" in js
    assert "split-handle" in js
    assert "preview-toggle" in js
    assert "aurem_preview_open" in js
    # Persist user pref so refresh keeps their choice
    assert "localStorage" in js


def test_chat_panel_fires_aurem_shipped_event():
    js = _read("frontend/src/components/ChatPanel.jsx")
    assert 'CustomEvent("aurem:shipped"' in js
    # carries task_id payload
    assert "task_id: p.task_id" in js


def test_backend_persists_frontend_subset_on_done():
    """When a task ships, only render-safe files (HTML/CSS/JS/TS) are
    persisted to the cto_tasks doc so the preview pane can render them
    without a repo round-trip."""
    src = _read("backend/routers/cto_projects.py")
    assert "def _frontend_subset(" in src
    # Done-status writes pass the trimmed dict
    assert "edits=_frontend_subset(edits)" in src


def test_frontend_subset_filters_correctly():
    """Smoke-test the actual helper — only renderable types, capped at 10."""
    import importlib
    m = importlib.import_module("routers.cto_projects")
    out = m._frontend_subset({
        "ok.html":             "<html></html>",
        "ok.css":              "body{}",
        "ok.js":               "x;",
        "ok.tsx":              "export const A = () => null;",
        "drop.py":              "print(1)",
        "drop.md":              "# hi",
        "drop_big.js":          "x" * 40_000,           # > 32 KB cap
        "drop_bytes.html":      b"\x00\x01",            # non-string
    })
    assert set(out.keys()) == {"ok.html", "ok.css", "ok.js", "ok.tsx"}
    # Cap test
    cap_in = {f"f{i}.js": "x;" for i in range(15)}
    cap_out = m._frontend_subset(cap_in)
    assert len(cap_out) == 10


def test_spin_keyframe_in_index_css():
    css = _read("frontend/src/index.css")
    assert "@keyframes aurem-spin" in css
