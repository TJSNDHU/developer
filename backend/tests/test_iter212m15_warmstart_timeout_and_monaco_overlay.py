"""Iter 212m-15 — Regression pins for two UI/UX bugs reported in the
testing-agent run (test_reports/iteration_10.json):

  1. Warm-start progress bar stuck at 80% — the LLM-driven graph agent
     could exceed the polling window. Every warm-start agent must now
     be bounded by `asyncio.wait_for(timeout=12.0)` and `_mark_done`
     must use `$addToSet` (not `$push`) so a re-mark from the bounded
     wrapper can't push the progress past 100%.

  2. Monaco editor inside chat bubbles overlapping the chat composer —
     CodeBlock.jsx must scope its stacking context (isolation: isolate
     + contain: paint), wrap the editor with a tabIndex=-1 div, and
     the .glass-composer must have its own z-index + isolation so it
     always wins against any inline code-block overlay.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


# ── Warm-start backend pins ────────────────────────────────────────


def test_warmstart_uses_addtoset_not_push():
    src = (ROOT / "routers" / "cto_projects.py").read_text(encoding="utf-8")
    # The mark-done helper inside _run_warm_agents must be idempotent.
    assert '"$addToSet": {"agents_done"' in src, (
        "warm-start _mark_done must $addToSet so a re-mark after timeout "
        "doesn't push the progress past 100%"
    )
    # And the old buggy $push for agents_done must be gone.
    assert '"$push": {"agents_done"' not in src, (
        "Legacy $push found — duplicate marks will inflate progress > 1.0"
    )


def test_warmstart_bounds_each_agent_with_wait_for():
    src = (ROOT / "routers" / "cto_projects.py").read_text(encoding="utf-8")
    # The `_bounded` wrapper must exist inside _run_warm_agents.
    assert "async def _bounded(" in src
    assert "asyncio.wait_for(coro, timeout=12.0)" in src
    # Every agent goes through `_bounded(...)` so none can hang the bar.
    for label in ("brain", "recent", "structure", "stack", "graph"):
        assert f'_bounded(agent_{label}()' in src, (
            f"agent_{label} must be wrapped with _bounded(...) so it can't "
            "leave the warm-start progress bar stuck below 100%"
        )


def test_warmstart_timeout_marks_done():
    src = (ROOT / "routers" / "cto_projects.py").read_text(encoding="utf-8")
    # On TimeoutError the bounded wrapper must still mark the agent
    # done so progress reaches 100%.
    # Find the _bounded helper body and check its except TimeoutError branch.
    idx = src.find("async def _bounded(")
    end = src.find("await asyncio.gather(", idx)
    bounded_body = src[idx:end]
    assert "except asyncio.TimeoutError" in bounded_body
    assert 'await _mark_done(label)' in bounded_body, (
        "TimeoutError handler must still _mark_done so the warm-start "
        "progress bar can reach 100%"
    )


# ── Warm-start frontend pins ───────────────────────────────────────


def test_useWarmStart_force_progress_one_before_ready():
    src = (
        ROOT.parent / "frontend" / "src" / "hooks" / "useWarmStart.js"
    ).read_text(encoding="utf-8")
    # When ready arrives we must explicitly set progress(1) before
    # transitioning status so the bar visually fills before unmounting.
    assert "setProgress(1)" in src, (
        "useWarmStart must call setProgress(1) inside the ready branch "
        "so the bar smoothly hits 100% before unmounting"
    )
    # Status transition should be deferred via setTimeout so React can
    # paint the 100% frame.
    assert 'setTimeout(() => setStatus("ready")' in src


# ── Monaco overlay pins ────────────────────────────────────────────


def test_codeblock_isolates_stacking_context():
    src = (
        ROOT.parent / "frontend" / "src" / "components" / "CodeBlock.jsx"
    ).read_text(encoding="utf-8")
    # Outer container must isolate so Monaco overlays don't bleed.
    assert 'isolation: "isolate"' in src
    assert 'contain: "layout paint style"' in src
    # Wrapper around Monaco must be present so we can target it via CSS
    # and remove the focus-stealing aria-container from the tab order.
    assert 'className="aurem-monaco-wrap"' in src
    assert 'tabIndex={-1}' in src


def test_composer_has_higher_zindex_than_messages():
    src = (
        ROOT.parent / "frontend" / "src" / "index.css"
    ).read_text(encoding="utf-8")
    # The composer must explicitly create a stacking context above the
    # message list so a long Monaco code block can't intercept clicks
    # on the textarea.
    assert ".glass-composer" in src
    # Look for the composer rule block specifically.
    block_start = src.find(".glass-composer {")
    block_end = src.find("}", block_start)
    block = src[block_start:block_end]
    assert "position: relative" in block
    assert "z-index: 4" in block
    assert "isolation: isolate" in block


def test_monaco_aria_container_pointer_events_disabled():
    src = (
        ROOT.parent / "frontend" / "src" / "index.css"
    ).read_text(encoding="utf-8")
    # The hidden announce containers Monaco creates must not be hit-
    # testable, otherwise Playwright (and screen readers' synthetic
    # clicks) treat them as the front-most element on the page.
    assert ".aurem-monaco-wrap .monaco-aria-container" in src
    assert "pointer-events: none" in src
