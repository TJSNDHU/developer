"""
test_iter312_loop_start_async_repro.py — Iter 312

RCA REPRO TEST (written FIRST, before Class 1 fix).

Reproduces the 2026-07-27 loop-start desync bug found via loop_4473f240:
`/loop/start` synchronously blocks through the entire plan phase
(`async for _ev in engine.start(): pass` at loop.py:162-163). Any plan
taking >60s hits the client-side axios timeout in `frontend/src/lib/api.js:15`
(`timeout: 60000`). Backend keeps running, session doc exists,
chip's /loop/active poll sees truth — but ChatPanel renders "Loop
failed to start" from the raw axios timeout error.

TEST DISCIPLINE:
  1. `test_repro_start_blocks_until_plan_complete` — asserts today's
     start_loop() sync consumer waits through the full engine.start()
     generator. This is the code shape that CAUSES the client timeout.
     MUST FAIL after Class 1 (start_loop returns immediately, doesn't
     block through plan phase).
  2. Regression (a): fast plan still works — start_loop returns with
     usable loop_id + initial state, session doc exists.
  3. Regression (b) & (c): Class 3 recovery — timeout with/without
     active session (frontend-side logic; tested where the recovery
     lives).
  4. Regression (d): CRITICAL — acquire_loop_lock must fire
     synchronously, BEFORE the response is prepared, so a second
     concurrent /loop/start call sees the lock and 409s. NOT a race.
"""
from __future__ import annotations
import asyncio
import inspect
import re
from pathlib import Path

import pytest


_ROUTER_SRC = Path("/app/backend/routers/loop.py").read_text()


def _strip_py_comments(src: str) -> str:
    """
    Strip Python `#`-comments (but not `#` inside string literals) so
    the static-regex checks below don't false-match on the exact
    blocking pattern mentioned in a documentation comment. Simple
    line-by-line scanner — good enough for router source (no exotic
    raw strings with embedded #).
    """
    out_lines = []
    for line in src.splitlines():
        in_str = None
        i = 0
        while i < len(line):
            ch = line[i]
            if in_str:
                if ch == "\\":
                    i += 2
                    continue
                if ch == in_str:
                    in_str = None
                i += 1
                continue
            if ch in ("'", '"'):
                # Handle triple-quotes minimally
                if line[i:i+3] in ("'''", '"""'):
                    # Skip triple-quoted spans on the same line if any
                    end = line.find(line[i:i+3], i+3)
                    if end == -1:
                        i = len(line)
                    else:
                        i = end + 3
                    continue
                in_str = ch
                i += 1
                continue
            if ch == "#":
                line = line[:i]
                break
            i += 1
        out_lines.append(line)
    return "\n".join(out_lines)


_ROUTER_SRC_NOCOMMENT = _strip_py_comments(_ROUTER_SRC)


# ── 1. REPRO — start_loop currently blocks through engine.start() ───
def test_repro_start_loop_blocks_through_plan_phase():
    """
    The `async for _ev in engine.start(): pass` pattern at
    loop.py:162-163 (approx) consumes the entire generator before
    returning the HTTP response. For plan-phase-heavy tasks this
    exceeds the 60s axios timeout.

    MUST FAIL against current code (proves the blocking pattern
    exists). MUST PASS after Class 1 (blocking consumer removed,
    replaced by asyncio.create_task).
    """
    # Locate the start_loop function source (comment-stripped so a
    # documentation comment mentioning the old pattern does not
    # false-fail the invariant).
    start_fn_match = re.search(
        r"async def start_loop\([^)]*\).*?(?=\n(?:@router|async def |def ))",
        _ROUTER_SRC_NOCOMMENT, re.DOTALL,
    )
    assert start_fn_match, "start_loop function not found in loop.py"
    start_fn_src = start_fn_match.group(0)

    # The blocking consumer pattern that causes the bug:
    #   async for _ev in engine.start(): pass
    # (any variant: `async for ... in engine.start(): pass` or the
    # equivalent that fully drains the generator inside the handler)
    blocking_pattern = re.compile(
        r"async for \s*\w+\s+in\s+engine\.start\(\)\s*:\s*(pass|continue|\.\.\.)",
    )
    has_blocking = bool(blocking_pattern.search(start_fn_src))

    assert not has_blocking, (
        "REPRO: start_loop still contains the synchronous "
        "`async for _ev in engine.start(): pass` consumer that "
        "blocks the HTTP response until plan phase completes. "
        "Class 1 must replace this with asyncio.create_task(...)."
    )


# ── 2. Regression (d) CRITICAL — lock write must be synchronous ─────
def test_regression_d_lock_write_synchronous_before_response():
    """
    Class 1 must NOT accidentally move `acquire_loop_lock` into an
    async background task. The lock write MUST happen inside the
    request-response cycle, BEFORE any return statement, so a
    concurrent second /loop/start call sees the lock and correctly
    409s with `loop_already_running`.

    Race condition to prevent:
       client A → POST /loop/start → create_task(engine) → return 200
       client A → POST /loop/start (again, immediately)
                → acquire_loop_lock ran? if async: RACE → both succeed

    Enforcement: `acquire_loop_lock` call site must appear in source
    BEFORE the first `return` inside start_loop, AND must not be
    wrapped in `asyncio.create_task(...)` / `asyncio.ensure_future(...)` /
    background task scheduling.
    """
    m = re.search(
        r"async def start_loop\([^)]*\).*?(?=\n(?:@router|async def |def ))",
        _ROUTER_SRC, re.DOTALL,
    )
    assert m, "start_loop function not found"
    src = m.group(0)

    # Find first `return` statement in start_loop
    return_idx = src.find("\n    return ")
    if return_idx == -1:
        return_idx = src.find("\nreturn ")
    assert return_idx > 0, "start_loop must have a return statement"
    src_before_return = src[:return_idx]

    # Assert acquire_loop_lock is called BEFORE the return
    assert "acquire_loop_lock(" in src_before_return, (
        "Regression (d): acquire_loop_lock MUST be called before the "
        "first return statement in start_loop. Moving it after the "
        "return (or into a create_task) would break the "
        "loop_already_running 409 guarantee."
    )

    # Assert the acquire_loop_lock call is NOT wrapped in a background
    # task primitive (asyncio.create_task / ensure_future / gather).
    lock_call_line_match = re.search(
        r"([^\n]*acquire_loop_lock\([^)]*\)[^\n]*)", src_before_return,
    )
    assert lock_call_line_match, "lock call line not extracted"
    lock_line = lock_call_line_match.group(1)
    for background in ("create_task", "ensure_future", "gather("):
        assert background not in lock_line, (
            f"Regression (d): acquire_loop_lock line contains "
            f"`{background}` — that makes lock write async/deferred "
            f"and re-opens the concurrent-start race."
        )


# ── 3. Regression (a) — fast plan happy-path still returns cleanly ──
def test_regression_a_start_response_shape_preserved():
    """
    After Class 1, start_loop still returns a dict with:
      loop_id, state, phase (initial values — 'planning' / 'plan').
    Plan blob is no longer required in the response body (moves to
    SSE stream), so its absence from the return dict is EXPECTED.
    Frontend adapts to receive plan via SSE.

    This test asserts the response-shape contract survives Class 1 —
    downstream consumers keying off loop_id/state/phase don't break.
    """
    m = re.search(
        r"async def start_loop\([^)]*\).*?(?=\n(?:@router|async def |def ))",
        _ROUTER_SRC, re.DOTALL,
    )
    assert m, "start_loop function not found"
    src = m.group(0)

    # Look for a return statement including loop_id, state, phase
    # (the minimum contract).
    return_block = re.search(
        r"return\s*\{[^}]*\}", src, re.DOTALL,
    )
    assert return_block, "start_loop must return a dict"
    rb = return_block.group(0)
    for required_key in ('"loop_id"', '"state"', '"phase"'):
        assert required_key in rb, (
            f"Regression (a): start_loop return dict is missing "
            f"required key {required_key}. Frontend contract broken."
        )


# ── 4. Feature-flag rollback safety ─────────────────────────────────
def test_feature_flag_rollback_safety_present():
    """
    Class 1 ships behind `LOOP_START_ASYNC` feature flag (default on)
    per founder's approval. This allows one-flip rollback if a
    downstream flow (e.g., a test harness or an SDK consumer) still
    expects the old sync-plan-in-response behavior.

    Enforcement: the source must reference LOOP_START_ASYNC. This test
    documents the invariant so a future refactor that drops the flag
    prematurely (before founder approval to remove it) fails loudly.
    """
    assert "LOOP_START_ASYNC" in _ROUTER_SRC, (
        "Feature flag LOOP_START_ASYNC not found in loop.py. Class 1 "
        "must gate the fire-and-forget behavior behind this flag "
        "(default True) for one-flip rollback safety."
    )
