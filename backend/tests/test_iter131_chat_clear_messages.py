"""Iter 131 regression: DELETE /chat/sessions/{id}/messages endpoint.

The chat-window "Clear chat" toolbar button calls this. It MUST:
1. Wipe `turns` to [] for the session
2. Preserve the session document (session_id stays in the sidebar)
3. Require auth (403/401 without a valid bearer token)
4. Enforce per-user ownership (one user can't clear another user's chat)
"""
from __future__ import annotations

import pathlib
import re


CHAT_ROUTER = pathlib.Path(__file__).resolve().parents[1] / "routers" / "chat.py"


def _src() -> str:
    return CHAT_ROUTER.read_text(encoding="utf-8")


def test_clear_messages_endpoint_exists() -> None:
    """The DELETE route must be registered."""
    src = _src()
    assert '@router.delete("/sessions/{session_id}/messages")' in src, (
        "Iter 131 clear-messages endpoint is missing — the chat "
        "window's Clear button has nowhere to call."
    )


def test_clear_messages_preserves_session_doc() -> None:
    """The handler must use update_one + $set, never delete_one. If
    someone accidentally swaps it to delete_one, the sidebar entry
    disappears on every Clear — a confusing UX regression."""
    src = _src()
    block = re.search(
        r"async def chat_session_clear_messages\(.*?return \{[^}]*\}",
        src,
        re.DOTALL,
    )
    assert block, "couldn't locate chat_session_clear_messages handler"
    body = block.group(0)
    assert "update_one" in body, (
        "clear-messages handler must call update_one to preserve "
        "the session doc."
    )
    assert "delete_one" not in body, (
        "clear-messages handler must NOT use delete_one — that "
        "removes the session from the sidebar too. See Iter 131."
    )
    assert '"turns": []' in body, (
        "clear-messages handler must reset `turns` to an empty list."
    )


def test_clear_messages_scoped_to_owner() -> None:
    """The update query must include `user_id` so user A can't wipe
    user B's chat by guessing the session_id."""
    src = _src()
    block = re.search(
        r"async def chat_session_clear_messages\(.*?return \{[^}]*\}",
        src,
        re.DOTALL,
    )
    assert block
    body = block.group(0)
    assert 'user_id": user["user_id"]' in body, (
        "clear-messages query missing per-user scope — owner check "
        "must be enforced at the DB layer."
    )


def test_clear_messages_404_when_session_missing() -> None:
    """Calling clear on a non-existent session_id must return 404
    (not silently succeed) so the frontend can surface a useful error."""
    src = _src()
    block = re.search(
        r"async def chat_session_clear_messages\(.*?return \{[^}]*\}",
        src,
        re.DOTALL,
    )
    assert block
    body = block.group(0)
    assert "matched_count == 0" in body and "HTTPException(404" in body, (
        "clear-messages must 404 when the session_id isn't owned "
        "by the caller (so the UI doesn't show a green success "
        "toast on a no-op)."
    )
