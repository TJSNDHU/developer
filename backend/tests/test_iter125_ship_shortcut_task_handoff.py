"""Iter 125 regression: the ship-shortcut SSE stream MUST emit a
`type:"task_handoff"` frame so the frontend LiveTaskPopup mounts.

Before the fix it only stuffed `task_id` into the `done` payload, which
`onDone` doesn't read — so the popup never appeared on the most common
Mode C trigger ("ship" / "go" / "do it" after an `aurem-handoff` fence).

This test reads the source and asserts the static SSE-shape we ship.
A full E2E would require a live worker + repo; the regression we keep
hitting is the missing frame, so a source-level guard is enough.
"""
from __future__ import annotations

import pathlib
import re


CHAT_ROUTER = pathlib.Path(__file__).resolve().parents[1] / "routers" / "chat.py"


def _src() -> str:
    return CHAT_ROUTER.read_text(encoding="utf-8")


def test_ship_shortcut_emits_task_handoff_frame() -> None:
    """The `_maybe_ship_shortcut._stream` block must yield a SSE frame
    whose JSON payload contains `"type": "task_handoff"` between the
    `task_id = res["task_id"]` line and the streaming of confirmation
    text. We pin the order so the popup mounts BEFORE tokens arrive."""
    src = _src()
    # Find the shortcut block specifically (avoid matching the Mode D→C
    # path, which is a different SSE frame inside `chat_stream`).
    block_match = re.search(
        r"async def _maybe_ship_shortcut\(.*?return _stream\(\)",
        src,
        re.DOTALL,
    )
    assert block_match, "could not isolate _maybe_ship_shortcut block"
    block = block_match.group(0)

    # 1) task_id is assigned from the enqueue result
    assert 'task_id = res["task_id"]' in block, (
        "ship-shortcut no longer extracts task_id from the enqueue result"
    )

    # 2) A `task_handoff` SSE frame follows that line
    task_id_pos = block.index('task_id = res["task_id"]')
    after = block[task_id_pos:]
    handoff_pos = after.find('"type": "task_handoff"')
    assert handoff_pos != -1, (
        "ship-shortcut path missing `task_handoff` SSE frame — the "
        "LiveTaskPopup will not mount on ship shortcuts. See Iter 125."
    )

    # 3) The frame includes the task_id we just got
    handoff_window = after[handoff_pos:handoff_pos + 400]
    assert '"task_id": task_id' in handoff_window, (
        "ship-shortcut `task_handoff` frame must include the live task_id"
    )

    # 4) The frame is emitted BEFORE the streaming-confirmation tokens.
    #    The first token-stream call is `yield f\"data: ...{'token':\"`.
    token_pos = after.find("'token':")
    assert token_pos > handoff_pos, (
        "task_handoff must be yielded BEFORE token streaming so the "
        "popup is mounted while the confirmation text streams in."
    )
