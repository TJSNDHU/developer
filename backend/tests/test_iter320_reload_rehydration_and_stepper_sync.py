"""
test_iter320_reload_rehydration_and_stepper_sync.py — Iter 320

Bug 4 (rehydration) + Loop stepper sync bug.

# ═════════════════════════════════════════════════════════════════════
# Bug 4 — Reload rehydration for paused_for_user + ship_pending
# ═════════════════════════════════════════════════════════════════════
#
# Symptom (live-confirmed): reload of /dashboard during a
# state=paused_for_user, phase=ship, ship_pending=set loop wipes the
# chat panel, ship-ready card, commit message, file list from the UI.
# Backend still has the record — /loop/active returns it — but the
# UI treats it as if there is no active loop.
#
# Verified root cause (source-inspection of ChatPanel.jsx lines
# 499-510): the ship-hydrate branch sets `loopId` + `shipPending`
# but MISSES two calls that the sibling awaiting_confirmation
# branch (Iter 316 Fix B) added:
#   1. setLoopPhase("paused_for_user") — without this, the phase
#      state pins to its initial value and the loop card gate
#      never opens.
#   2. openLoopStream(loop_id) — without this, no SSE frames are
#      bound; any subsequent state transitions (confirm-ship,
#      integrity_guard_rejected) can't reach the tab.
#
# Fix: mirror Iter 316 Fix B in the ship-hydrate branch.

# ═════════════════════════════════════════════════════════════════════
# Loop stepper sync bug
# ═════════════════════════════════════════════════════════════════════
#
# The top LoopStepBar (PLAN → EXECUTE → VERIFY → SCAN → SHIP) showed
# EXECUTE-orange while the ChatPanel bubble on the SAME screen said
# "Step 5/5 ready to ship". Two separate state sources. Wire the
# stepper to the same phase field ChatPanel/ShipPendingCard use.
"""
from __future__ import annotations

import re
from pathlib import Path

_CHATPANEL_SRC = Path(
    "/app/frontend/src/components/ChatPanel.jsx",
).read_text()
_STEPBAR_SRC = Path(
    "/app/frontend/src/components/LoopStepBar.jsx",
).read_text()


def test_ship_hydrate_branch_sets_loop_phase():
    """Iter 320 Bug 4: the ship-hydrate branch inside the mount-time
    /loop/active hydrate must call setLoopPhase — otherwise the loop
    card never opens on reload and the UI reverts to the welcome
    message despite ship_pending being fully staged server-side."""
    # Isolate the ship-hydrate `if` block (lines ~499-510).
    m = re.search(
        r'active\.state === "paused_for_user" && active\.phase === "ship"'
        r'(.*?)(?=\}\s*else if\s*\(\s*active\.state === "awaiting_confirmation")',
        _CHATPANEL_SRC, re.DOTALL,
    )
    assert m, (
        "Iter 320: ship-hydrate branch (`paused_for_user + phase='ship'"
        "') not found in ChatPanel.jsx mount-effect. Was the branch "
        "removed or the pattern refactored?"
    )
    branch = m.group(1)
    assert "setLoopPhase" in branch, (
        "Iter 320 Bug 4: ship-hydrate branch must call setLoopPhase "
        "(matching Iter 316 Fix B for the awaiting_confirmation "
        "branch). Without it, phase pins to its initial value and "
        "the ship card gate never opens on reload."
    )
    assert "openLoopStream" in branch, (
        "Iter 320 Bug 4: ship-hydrate branch must call "
        "openLoopStream(active.loop_id) so subsequent SSE frames "
        "(confirm-ship result, integrity_guard_rejected, etc.) reach "
        "the reloaded tab."
    )


def test_stepper_uses_same_phase_source_as_chatpanel():
    """LoopStepBar must read the same `loopPhase` prop the ChatPanel
    drives via setLoopPhase — no independent state store. Live
    incident: stepper showed EXECUTE while ChatPanel showed
    ship_pending on the same screen."""
    # LoopStepBar consumes a `phase` (or `loopPhase` or `state`)
    # prop passed by its parent. What we really need to check is
    # that ChatPanel passes its OWN loopPhase / shipPending signal
    # into LoopStepBar — not a separate hook.
    assert "LoopStepBar" in _CHATPANEL_SRC, (
        "sanity: ChatPanel must render <LoopStepBar/>"
    )
    # Extract the props ChatPanel passes to LoopStepBar (multiline JSX).
    m = re.search(
        r"<LoopStepBar([\s\S]*?)/>", _CHATPANEL_SRC,
    )
    assert m, "LoopStepBar props not extractable from ChatPanel"
    props = m.group(1)
    # Must be driven by loopPhase / phase / step — the same values
    # ShipPendingCard sees. NOT by a private eventBus / hook.
    assert (
        "loopPhase" in props or "phase" in props or "step" in props
    ), (
        "Iter 320 stepper sync: LoopStepBar must be driven by the "
        "same phase source that ShipPendingCard consumes — not a "
        "separate stepper-only state hook."
    )
