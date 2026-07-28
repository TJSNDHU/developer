"""Iter 331 · Bug 1 — source-level lock.

The engine must emit exactly one step="plan" success narration when
EXECUTE starts so LoopStepBar.stepTones.plan resolves and PLAN can
never render gray during a live loop. Emitting it in confirm() is
FORBIDDEN — the frame would carry state=awaiting_confirmation and
ChatPanel would flip back to plan_pending (PlanApprovalCard regression).
"""
from pathlib import Path

SRC = Path("/app/backend/services/loop_engine.py").read_text(encoding="utf-8")


def test_plan_success_narration_present_exactly_once():
    assert SRC.count('self._narrate("plan", "success"') == 1


def test_plan_narration_lives_in_do_execute_not_confirm():
    execute_seg = SRC.split("async def _do_execute")[1].split("async def ")[0]
    assert '_narrate("plan", "success"' in execute_seg

    confirm_seg = SRC.split("async def confirm(")[1].split("async def ")[0]
    assert '_narrate("plan"' not in confirm_seg


def test_plan_narration_after_executing_state_set():
    execute_seg = SRC.split("async def _do_execute")[1].split("async def ")[0]
    state_idx = execute_seg.index("self.state = LoopState.EXECUTING")
    narrate_idx = execute_seg.index('_narrate("plan", "success"')
    assert narrate_idx > state_idx
