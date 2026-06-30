"""
test_iter212m103_composer_redesign.py — Iter 212m-103

Static guards on the LoopModeToggle / LoopStepBar / StreamHealthPill
redesign. We don't render React here (Python-only test), but we lock
the source for the contract surface so a future refactor can't
silently drop the testids or the wiring patterns.
"""
from pathlib import Path


def _read(rel: str) -> str:
    return Path(f"/app/frontend/src/{rel}").read_text(encoding="utf-8")


def test_loop_mode_toggle_v0_pill_present():
    src = _read("components/LoopModeToggle.jsx")
    # Pill button shape, testid, both states
    assert 'data-testid="loop-mode-toggle"' in src
    assert "Loop on" in src and "Loop off" in src
    # data-loop-active is the contract attr ChatPanel uses to flip
    # the placeholder + Send-button text.
    assert "data-loop-active" in src
    # Brand color #FF6608 (orange) for the active pill
    assert "#FF6608" in src
    # Persisted via existing key — backward compat with older bundles.
    assert "ora_execution_mode" in src


def test_loop_step_bar_5_phase_v0_layout():
    src = _read("components/LoopStepBar.jsx")
    assert 'data-testid="loop-step-bar"' in src
    # All 5 phases present
    for label in ("PLAN", "EXECUTE", "VERIFY", "SCAN", "SHIP"):
        assert label in src, f"Phase label '{label}' missing from LoopStepBar"
    # Per-step testids the test harness uses to drive Loop UX assertions.
    # The JSX uses a template literal `loop-step-${s.key}` so we just
    # assert the prefix + STEPS keys are present.
    assert "loop-step-${s.key}" in src
    for key in ("plan", "execute", "verify", "security", "ship"):
        assert f'key: "{key}"' in src or f'"{key}"' in src
    # Step states (done/active/future/error) drive icon + color swap
    for state in ("done", "active", "future", "error"):
        assert state in src


def test_stream_health_pill_retry_now_wiring():
    src = _read("components/ChatPanel.jsx")
    # StreamHealthPill now exposes onRetry (Iter 212m-103) and
    # ChatPanel passes a handler that aborts the in-flight controller.
    assert "Retry now" in src
    assert 'data-testid="chat-stream-retry-now"' in src
    assert "abortRef.current?.abort()" in src
    # `Slow response` is the banner headline in the screenshot
    assert "Slow response" in src
    # `Reconnecting` is the variant for the active retry hop
    assert "Reconnecting" in src


def test_send_button_is_circular_pill():
    src = _read("components/ChatPanel.jsx")
    # The send button should be a 38x38 circular icon button per
    # Iter 212m-103 redesign. Stop button shares the same geometry.
    assert 'data-testid="chat-send"' in src
    assert 'data-testid="chat-stop"' in src
    # Width/height 38 + borderRadius 50% are the v0 spec.
    assert 'width: 38, height: 38' in src
    assert 'borderRadius: "50%"' in src


def test_loop_mode_toggle_lives_inside_toolbar():
    src = _read("components/ChatPanel.jsx")
    # Iter 212m-103 → 212m-163 — the toggle must render inside the
    # composer toolbar block (not above the composer like the legacy
    # implementation).  We assert the source ordering by finding the
    # toolbar open + the LoopModeToggle tag below it.
    #
    # Iter 212m-163 reinstated the toggle (founder/admin only) after
    # Iter 212m-149 had temporarily replaced it with the
    # IntentTierIndicator.  Accept either single-line or multi-line
    # JSX shape.
    import re
    toolbar_idx = src.find('<div className="composer-toolbar">')
    m = re.search(r"<LoopModeToggle\b", src)
    toggle_idx = m.start() if m else -1
    send_idx   = src.find('data-testid="chat-send"')
    assert toolbar_idx != -1, "composer-toolbar opener not found"
    assert toggle_idx != -1, "LoopModeToggle not mounted"
    assert send_idx != -1,   "chat-send button not found"
    assert toolbar_idx < toggle_idx < send_idx, (
        "LoopModeToggle must render inside composer-toolbar between the "
        "icon group and the Send button (Iter 212m-103 layout contract)."
    )
