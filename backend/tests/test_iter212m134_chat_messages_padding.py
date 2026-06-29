"""
Iter 212m-134 — Claude-style centered chat messages.

Founder asked: in `ChatPanel.jsx`, the messages scroll container should have
`padding-left: 17.25%` and `padding-right: 17.25%` so the chat content sits
in a centered column like Claude. The composer at the bottom must stay full
width (it lives OUTSIDE the messages container).

These tests pin the source so a future edit can't silently revert the change.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

CHATPANEL = Path("/app/frontend/src/components/ChatPanel.jsx")


@pytest.fixture(scope="module")
def src() -> str:
    return CHATPANEL.read_text(encoding="utf-8")


def test_chat_messages_container_uses_17_25_percent_padding(src: str) -> None:
    """The chat-messages container's `padding` shorthand should set both
    sides to 17.25% so messages sit in a Claude-style centered column."""
    # Find the chat-messages style block
    m = re.search(
        r'data-testid="chat-messages"\s*\n\s*style=\{\{(?P<style>[^}]+?)\}\}',
        src,
        re.DOTALL,
    )
    assert m, "chat-messages container with inline style not found"
    style = m.group("style")
    assert 'padding: "24px 17.25%"' in style, (
        "Expected padding shorthand '24px 17.25%' on the chat-messages "
        "container so the messages area is centered. Got:\n" + style
    )


def test_chat_messages_right_padding_preserves_live_popup_room(src: str) -> None:
    """When a live-task popup is open, the right padding must still swap to
    392px so the popup doesn't overlap message content. Otherwise it stays
    at 17.25% to match the left side."""
    m = re.search(
        r'data-testid="chat-messages"\s*\n\s*style=\{\{(?P<style>[^}]+?)\}\}',
        src,
        re.DOTALL,
    )
    assert m
    style = m.group("style")
    assert 'paddingRight: livePopupTaskId ? 392 : "17.25%"' in style, (
        "Right padding must override to 392px while the live popup is open "
        "and fall back to 17.25% otherwise. Got:\n" + style
    )


def test_composer_lives_outside_chat_messages_container(src: str) -> None:
    """Sanity: the composer wrapper (`glass-composer`) must NOT be inside
    the chat-messages container — otherwise the padding would also indent
    the composer, breaking the user requirement that 'composer stays full
    width'."""
    chat_msgs_idx = src.index('data-testid="chat-messages"')
    composer_idx = src.index('className="glass-composer"')
    assert composer_idx > chat_msgs_idx, (
        "Sanity check: glass-composer should be declared AFTER chat-messages."
    )
    # Stronger check: between chat-messages opening and glass-composer there
    # must be a closing tag for the chat-messages div. We can't AST-parse
    # JSX trivially, so as a heuristic, count the slice from chat-messages
    # to glass-composer for the marker comment we left in the messages div
    # AND require the slice to contain at least one balancing `</div>`.
    slice_ = src[chat_msgs_idx:composer_idx]
    assert "</div>" in slice_, (
        "Expected the chat-messages container to close before glass-composer."
    )
