"""Iter 212m-46 — Mode D auto-ship is dead; the diagnosis bubble itself
now carries an aurem-handoff fence so the manual Ship button is the
ONLY commit path.

These tests are AST-grade source assertions (no LLM, no DB) so they
run in <50 ms and catch any future regression that re-introduces the
auto-enqueue path on the fix-confirmation fast-path.
"""
from __future__ import annotations

import os
import re

CHAT_PY      = os.path.join(os.path.dirname(__file__), "..", "routers", "chat.py")
MODE_D_PY    = os.path.join(os.path.dirname(__file__), "..", "services", "mode_d_debugger.py")


def test_mode_d_fast_path_does_not_enqueue() -> None:
    """The is_fix_confirmation fast path MUST NOT call
    `_enqueue_cto_task` — that bypassed the user's Ship click."""
    src = open(CHAT_PY).read()
    # Find the fast-path block (everything between the iter 212m-46
    # marker and the matching `return` that closes it).
    marker = "Iter 212m-46 — KILL auto-ship on Mode D fix-confirm"
    assert marker in src, f"Iter 212m-46 marker missing in chat.py"
    block_start = src.index(marker)
    # Look at the next ~3 KB of source — the fast path itself.
    block = src[block_start:block_start + 3000]
    # Strip Python comments so we don't false-match on doc text like
    # "NO _enqueue_cto_task call here".
    non_comment = "\n".join(
        ln for ln in block.splitlines() if not ln.lstrip().startswith("#")
    )
    assert "_enqueue_cto_task" not in non_comment, (
        "Mode D fix-confirm fast path must not call _enqueue_cto_task. "
        "Re-introduce the manual Ship-button path instead."
    )
    # Provider tag should be the new redirect, not the legacy handoff.
    assert "mode-d-redirect" in block
    assert "mode-d-handoff" not in block


def test_mode_d_diagnosis_emits_handoff_fence() -> None:
    """When `can_auto_fix` is True, the Mode D reply MUST embed an
    aurem-handoff fence so MessageBubble can render the Ship button on
    the diagnosis bubble itself."""
    src = open(MODE_D_PY).read()
    assert "```aurem-handoff" in src, (
        "Mode D debugger must embed an aurem-handoff fence in the "
        "diagnosis reply when can_auto_fix is True."
    )
    # The Ship-button CTA copy must be present.
    assert re.search(r"Ship via CTO.*button", src), (
        "Confirm line must direct the user to click the Ship via CTO "
        "button — not auto-fire on 'yes'."
    )


def test_old_question_prompt_is_dead() -> None:
    """The legacy 'Want me to ship the fix?' question (which trained the
    user to type 'yes' and auto-fire a commit) is gone."""
    src = open(MODE_D_PY).read()
    assert "Want me to ship the fix" not in src
