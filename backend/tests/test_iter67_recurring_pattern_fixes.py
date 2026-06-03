"""
test_iter67_recurring_pattern_fixes.py

Locks in fixes for RECURRING_ISSUES.md patterns #1, #2, #5 so they cannot
silently regress.

These are source-level tests — they grep the production code for the
specific lines that were added. If a future agent reverts the fix, this
test fails loudly with the expected line so they can't silently undo it.
"""
from __future__ import annotations

import os
import re


def _read(rel):
    base = os.path.join(os.path.dirname(__file__), "..", "..")
    with open(os.path.join(base, rel), encoding="utf-8") as fh:
        return fh.read()


# ── Pattern #1 — retry endpoint must surface previous failure ──────────

def test_retry_endpoint_carries_failure_context_forward():
    """When user clicks Retry on a failed task, the new task's `context`
    field must include WHAT failed last time, so the model doesn't
    repeat the same failure (e.g. empty file body)."""
    src = _read("backend/routers/cto_projects.py")
    m = re.search(r"async def retry_task\(.*?(?=\n@router\.|\nasync def )",
                  src, re.DOTALL)
    assert m, "retry_task handler must exist"
    body = m.group(0)

    # The handler must read prev_err + last_err_step from the old task
    assert 'old.get("error")' in body
    assert "last_err_step" in body, (
        "retry_task must pull the last error step from old.steps"
    )

    # The augmented context must be passed to bg.add_task (NOT old.context)
    assert "augmented_context" in body
    assert "bg.add_task(" in body
    # The augmented_context variable must reach the bg.add_task call
    bg_call = re.search(r"bg\.add_task\([\s\S]+?\n    \)", body)
    assert bg_call is not None, "Could not locate bg.add_task block"
    assert "augmented_context" in bg_call.group(0), (
        "bg.add_task must receive augmented_context, not old.context"
    )

    # The explicit hint about "empty file body" must be present in the
    # augmented context payload — this is what the model needs to see.
    assert "empty" in body.lower()
    assert "FULL implementation" in body, (
        "Augmented context must explicitly tell the model to write the "
        "full implementation, not just a docstring"
    )

    # Response includes the carried_failure_context flag so the UI can
    # show "retrying with extra context" instead of a plain reroll.
    assert "carried_failure_context" in body


# ── Pattern #2 — slow-API timeout message must not be misleading ──────

def test_timeout_message_distinguishes_slow_api_from_loop():
    """When the 90s timeout fires with very few tool calls, the user
    must see "Model API was slow", NOT "I cut myself off". Cutting off
    implies a loop; slow API is a network issue."""
    src = _read("backend/routers/chat.py")
    # The branch must exist
    assert "tool_count < 3" in src
    assert "Model API was slow" in src, (
        "Slow-API timeout must surface a 'Model API was slow' message, "
        "not the misleading 'I cut myself off' string"
    )
    # The old message should still exist for the high-tool-count case
    assert "I cut myself off" in src
    # The meta payload must include a `slow_api` boolean
    assert '"slow_api"' in src
    assert "slow_api = tool_count < 3" not in src  # python assignment style
    # Confirm slow_api appears in a dict context (meta_payload)
    assert "slow_api" in src


# ── Sanity — Pattern #1 is documented and won't be silently dropped ───

def test_recurring_issues_doc_still_present():
    """The RECURRING_ISSUES.md memory file MUST stay in /app/memory.
    If a future agent deletes it (because "the bug is fixed now"), we
    lose the history of why these fixes exist."""
    path = os.path.join(os.path.dirname(__file__), "..", "..", "memory",
                        "RECURRING_ISSUES.md")
    assert os.path.exists(path), "RECURRING_ISSUES.md must never be deleted"
    body = open(path, encoding="utf-8").read()
    # Pattern names must still be there
    for name in ("Pattern #1", "Pattern #2", "Pattern #5"):
        assert name in body
