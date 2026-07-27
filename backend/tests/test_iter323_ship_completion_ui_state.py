"""
test_iter323_ship_completion_ui_state.py — Iter 323

Two live-verified bugs after commit 7bb304d shipped to
TJSNDHU/Aurem@main via loop_7d4f8ee67cfd44:

BUG A — LoopStepBar SHIP stays orange even after successful ship.
  DOM evidence: PLAN/EXECUTE/VERIFY/SCAN all render
  color rgb(34,197,94) with a checkmark. SHIP is rgb(255,102,8)
  (COL.amber / "active" ECG variant) forever, even though the
  "Shipped · Pushed to GitHub · 7bb304d" success card renders
  directly below.

  Root cause (traced through the code):
    • Backend `_do_ship` emits `state=COMPLETED` FIRST (line 2836)
      then the `step="ship", tone="success"` narration (line 2849).
      SSE streams typically close on the terminal frame, so the
      trailing narration frame can be lost.
    • Frontend `ecgVariant()` checks stepTones BEFORE the phase-
      based legacy fallback. So `stepTones.ship === "pending"`
      stays sticky and returns "active" (orange) forever, beating
      the `isDone` phase-based fallback that would have painted
      it green.

  Fix pillars:
    1. Backend: reorder — narrate ship success BEFORE the terminal
       COMPLETED emit so the frame is guaranteed on the wire before
       any stream close.
    2. Frontend: when `phase === "completed"` or `phase === "done"`,
       force step 5 to "success" — override any stale pending
       narration. Also add `"shipped"` alias to PHASE_TO_STEP and
       isDone recognition.

BUG B — LoopStatusChip vanishes from the DOM once ship completes.
  DOM evidence: chip element (`data-testid=loop-status-chip`) is
  entirely gone post-ship; no "Shipped" terminal state ever shows.

  Root cause: `LoopStatusChip.jsx` line 189 —
    `if (!active && !err) return null;`
  `getActiveLoop()` returns `active: null` once the backend marks the
  loop COMPLETED (terminal states are filtered out). The chip has no
  grace window / last-known snapshot, so it disappears the moment
  the poll returns null.

  Fix: keep the last-known non-terminal snapshot in a ref/state and
  render a "SHIPPED" pill for a grace window (~30s) after transition
  active → null.

Test-first: assertions below fail against the current tree; fix
lands them green. Runtime DOM/Playwright validation follows via
bug_testing_agent (frontend-only pass).
"""
from __future__ import annotations

import re
from pathlib import Path


_ENGINE_SRC = Path("/app/backend/services/loop_engine.py").read_text()
_STEPBAR_SRC = Path(
    "/app/frontend/src/components/LoopStepBar.jsx",
).read_text()
_CHIP_SRC = Path(
    "/app/frontend/src/components/LoopStatusChip.jsx",
).read_text()


# ═══════════════════════════════════════════════════════════════════
# BUG A · Backend ordering — narration precedes terminal emit
# ═══════════════════════════════════════════════════════════════════

def test_do_ship_narrates_success_before_terminal_completed_emit():
    """The ship-success narration must be emitted BEFORE the
    terminal `state=COMPLETED` emit — SSE streams close on the
    terminal frame, so any narration after it can be lost. The
    trailing narration is what flatlines the SHIP ECG green; if
    it's lost, `stepTones.ship` stays "pending" forever.

    Note: the actual ship commit is done inside `confirm_ship`
    (not `_do_ship`, which stages ship_pending and pauses for
    user approval). We search `confirm_ship`."""
    m = re.search(
        r"async def confirm_ship\(.*?(?=\n    async def |\n    def )",
        _ENGINE_SRC, re.DOTALL,
    )
    assert m, "confirm_ship not found"
    body = m.group(0)

    narr_idx = body.find('step="ship", tone="success"')
    emit_idx = body.find("await self._emit(LoopState.COMPLETED, \"ship\"")
    assert narr_idx > 0, "ship success narration not found in confirm_ship"
    assert emit_idx > 0, "terminal COMPLETED emit not found in confirm_ship"
    assert narr_idx < emit_idx, (
        "Iter 323 Bug A backend: ship-success narration must be "
        "emitted BEFORE the terminal state=COMPLETED emit. Current "
        "order lets SSE close on the terminal frame and swallow the "
        "narration, keeping stepTones.ship='pending' forever."
    )


# ═══════════════════════════════════════════════════════════════════
# BUG A · Frontend override — terminal phase beats stale narration
# ═══════════════════════════════════════════════════════════════════

def test_step_bar_forces_ship_success_on_terminal_completed_phase():
    """LoopStepBar's `ecgVariant()` must recognise a terminal-success
    phase (`"completed"`, `"done"`, `"shipped"`) and force step 5 to
    "success" — otherwise a stale `stepTones.ship === "pending"`
    beats the phase-based fallback and paints SHIP orange forever."""
    # `ecgVariant` body contains the narration-priority checks; the
    # fix must add a terminal-success override, either just above the
    # stepTones checks or explicitly for the ship step.
    assert (
        # Option A: explicit override branch keyed on isDone + step.id
        re.search(
            r"isDone\s*&&\s*step\.id\s*===\s*5",
            _STEPBAR_SRC,
        )
        # Option B: any comparable guard that forces terminal→success
        # for step 5 regardless of narration tone.
        or "terminalSuccess" in _STEPBAR_SRC
    ), (
        "Iter 323 Bug A frontend: LoopStepBar.ecgVariant must force "
        "step 5 to 'success' when the phase is terminal (completed/"
        "done/shipped). Without this, a lost ship-success narration "
        "keeps stepTones.ship='pending' and the ECG stays orange "
        "forever."
    )


def test_step_bar_recognises_shipped_alias_in_phase_map():
    """PHASE_TO_STEP must include a `shipped` alias mapping to 5, and
    `isDone` must recognise `phase === "shipped"` as a terminal-
    success. Older builds emit `shipped` in some SSE frames."""
    # Extract PHASE_TO_STEP block.
    m = re.search(r"const PHASE_TO_STEP\s*=\s*\{([^}]+)\}", _STEPBAR_SRC)
    assert m, "PHASE_TO_STEP not found"
    block = m.group(1)
    assert (
        re.search(r"\bshipped\b\s*:\s*5", block)
        or re.search(r'"shipped"\s*:\s*5', block)
    ), (
        "Iter 323 Bug A: PHASE_TO_STEP must include a `shipped: 5` "
        "alias so `active` computes correctly for SHIP frames."
    )
    # `isDone` check must recognise shipped too.
    isdone_m = re.search(
        r"const isDone\s*=\s*[^;]+;", _STEPBAR_SRC,
    )
    assert isdone_m, "isDone declaration not found"
    assert '"shipped"' in isdone_m.group(0), (
        "Iter 323 Bug A: `isDone` must include `phase === \"shipped\"` "
        "so the terminal-success override fires on the shipped alias "
        "too."
    )


# ═══════════════════════════════════════════════════════════════════
# BUG B · Chip persists a "SHIPPED" state after terminal
# ═══════════════════════════════════════════════════════════════════

def test_chip_holds_last_snapshot_after_terminal_null():
    """LoopStatusChip must retain the last-known active loop for a
    grace window when `getActiveLoop()` returns null. Otherwise the
    chip vanishes the instant the backend marks the loop COMPLETED
    (which filters it out of /loop/active), and the founder loses
    the top-of-panel terminal state indicator."""
    # The fix pattern is either a `lastActive` state/ref + a
    # `justShipped`/`terminalSnapshot` state that renders in place
    # of the raw `active` for a grace window.
    assert (
        "lastActive" in _CHIP_SRC
        or "terminalSnapshot" in _CHIP_SRC
        or "justShipped" in _CHIP_SRC
        or "shippedSnapshot" in _CHIP_SRC
    ), (
        "Iter 323 Bug B: LoopStatusChip must retain a last-known "
        "snapshot when getActiveLoop returns null so the chip can "
        "render a 'SHIPPED' terminal state for a grace window "
        "instead of vanishing from the DOM."
    )


def test_chip_renders_shipped_label_for_terminal_phases():
    """PHASE_LABEL must include `completed` / `done` / `shipped`
    mappings so the terminal-state pill reads 'SHIPPED' (or 'DONE')
    instead of the raw enum in caps."""
    m = re.search(
        r"const PHASE_LABEL\s*=\s*\{([^}]+)\}", _CHIP_SRC, re.DOTALL,
    )
    assert m, "PHASE_LABEL not found in LoopStatusChip"
    block = m.group(1)
    assert (
        re.search(r"\bcompleted\s*:\s*[\"']", block)
        or re.search(r"\bshipped\s*:\s*[\"']", block)
        or re.search(r"\bdone\s*:\s*[\"']", block)
    ), (
        "Iter 323 Bug B: PHASE_LABEL must include a terminal-state "
        "label (completed / shipped / done) so the chip renders a "
        "human-readable 'SHIPPED' pill instead of the enum in caps."
    )


def test_chip_unmount_condition_relaxed_for_terminal_grace():
    """The `if (!active && !err) return null;` guard must be
    relaxed — the chip must NOT unmount immediately when active
    goes null; it must fall back to the terminal snapshot for
    the grace window."""
    # Find the early-return guard.
    assert (
        "if (!active && !err && !lastActive" in _CHIP_SRC.replace(" ", " ")
        or "!active && !err && !terminalSnapshot" in _CHIP_SRC
        or "!active && !err && !shippedSnapshot" in _CHIP_SRC
    ), (
        "Iter 323 Bug B: the unmount guard `if (!active && !err) "
        "return null;` must include the terminal snapshot in its "
        "negation so the chip stays mounted through the grace "
        "window post-ship."
    )
