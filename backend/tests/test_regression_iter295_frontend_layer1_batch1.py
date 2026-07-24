"""
Iter 295 — Frontend Layer 1 Batch 1 completion.

# static-grep-ok: this file locks the extraction of AgentStatusBar
# + the two new frontend test files' shapes. Behavioural coverage
# of the components themselves lives in the .test.jsx files.

Locks:
  1. AgentStatusBar.jsx exists as a standalone component, imported
     by ChatPanel — the inlined-JSX era is over.
  2. All 3 Batch-1 test files exist and pass the classifier as
     3× BEHAVIOURAL each.
  3. Original AgentStatusBar behaviour preserved: `data-testid=
     "agent-status-bar"` and the `form.glass-composer[data-agent-
     running="true"]` amber border-color CSS rule.
"""
from __future__ import annotations

import os


AGENT_BAR    = "/app/frontend/src/components/AgentStatusBar.jsx"
CHATPANEL    = "/app/frontend/src/components/ChatPanel.jsx"
BATCH1_TESTS = [
    "/app/frontend/src/components/__tests__/LoopStepBar.test.jsx",
    "/app/frontend/src/components/__tests__/AgentStatusBar.test.jsx",
    "/app/frontend/src/components/__tests__/LoopLiveFeed.test.jsx",
]


def _read(p: str) -> str:
    with open(p, "r", encoding="utf-8") as f:
        return f.read()


def test_agent_status_bar_extracted_as_standalone_component():
    assert os.path.isfile(AGENT_BAR)
    src = _read(AGENT_BAR)
    assert "export default function AgentStatusBar" in src
    # Prop contract locked.
    assert "busy" in src and "queuedCount" in src
    # Returns null when !busy — the exact iter288 invariant.
    assert "if (!busy) return null" in src
    # Original data-testids preserved.
    assert 'data-testid="agent-status-bar"' in src
    assert 'data-testid="agent-status-shell"' in src
    assert 'data-testid="queued-chip"' in src


def test_agent_status_bar_amber_border_css_preserved():
    """iter284's amber composer border must survive the extraction —
    this was the visual pairing between the running-agent bar and
    the composer. Regression against a silent CSS drop."""
    src = _read(AGENT_BAR)
    assert 'form.glass-composer[data-agent-running="true"]' in src
    assert "border-color: rgba(255,102,8,0.35) !important" in src


def test_chatpanel_uses_agent_status_bar_component_not_inline_jsx():
    src = _read(CHATPANEL)
    assert 'import AgentStatusBar from "./AgentStatusBar"' in src
    assert "<AgentStatusBar busy={busy} queuedCount={queuedCount}" in src
    # Old inline JSX MUST be gone — the extraction is not "half done".
    assert '{busy && (\n        <div className="chat-inline-card" data-testid="agent-status-shell"' \
        not in src


def test_batch_1_test_files_all_exist():
    for p in BATCH1_TESTS:
        assert os.path.isfile(p), f"missing {p}"


def test_batch_1_all_nine_tests_classified_behavioural():
    """iter295 discipline check — every one of the 9 Batch-1 tests
    MUST classify BEHAVIOURAL by iter290's classifier. If any comes
    back STATIC_GREP/HYBRID, the frontend Layer-1 pattern has
    regressed and needs correction BEFORE Batch 2 is written."""
    import sys
    sys.path.insert(0, "/app/backend")
    from services.test_style_analyzer import analyze_file
    for path in BATCH1_TESTS:
        r = analyze_file(path)
        assert r["ok"] is True
        assert len(r["tests"]) == 3, (
            f"{path} expected 3 tests, got {len(r['tests'])}"
        )
        kinds = [t["kind"] for t in r["tests"]]
        assert all(k == "BEHAVIOURAL" for k in kinds), (
            f"{path} classified as {kinds} — Batch 1 pattern REGRESSED"
        )


def test_vitest_setup_file_loads_jest_dom_matchers():
    """`toBeInTheDocument`, `toBeNull` — matchers required by the
    Batch 1 tests — come from @testing-library/jest-dom/vitest. If
    setup.js drops the import, the whole Layer 1 suite breaks."""
    setup = _read("/app/frontend/src/__tests__/setup.js")
    assert "@testing-library/jest-dom/vitest" in setup
