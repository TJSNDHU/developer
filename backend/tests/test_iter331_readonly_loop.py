"""Iter 331 · read-only loop fix — source-level locks.

Founder-reported on prod (loop 6de15d4c): "list my repo files" produced
a valid plan with zero files_to_change and the loop FAILED at Execute
("Plan has no files_to_change — refine the plan and retry"). Query/
report-type loops must terminate COMPLETED (read-only report), never
FAILED — and must never progress to Verify/Scan/Ship (iter212m-131
bug #6: fake "Ship complete").
"""
from pathlib import Path

SRC = Path("/app/backend/services/loop_engine.py").read_text(encoding="utf-8")


def _empty_files_block() -> str:
    seg = SRC.split("async def _do_execute")[1].split("async def ")[0]
    start = seg.index("if not files:")
    return seg[start:start + 2200]


def test_empty_plan_completes_not_fails():
    block = _empty_files_block()
    assert 'self._fail(' not in block.split("return")[0], (
        "empty files_to_change must NOT fail the loop"
    )
    assert "LoopState.COMPLETED" in block
    assert '"read_only": True' in block


def test_empty_plan_terminates_pipeline_before_verify():
    block = _empty_files_block()
    # Terminal state set BEFORE the emit + early return, so
    # _should_stop() halts the pipeline (no fake Ship complete).
    assert block.index("self.state = LoopState.COMPLETED") < block.index("await self._emit")
    assert "return" in block


def test_empty_plan_releases_loop_lock():
    assert "release_loop_lock" in _empty_files_block()


def test_pipeline_still_guards_between_phases():
    seg = SRC.split("async def _run_pipeline")[1].split("async def ")[0]
    assert seg.count("_should_stop()") >= 3
