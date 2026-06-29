"""
Iter 212m-134/135 — Claude-style centered chat layout.

Founder asked: in `ChatPanel.jsx`, both the messages scroll container AND
the composer (chat input) should have `padding-left: 17.25%` and
`padding-right: 17.25%` so the chat content sits in a centered column
like Claude.

These tests pin the source so a future edit can't silently revert.
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
    the composer twice."""
    chat_msgs_idx = src.index('data-testid="chat-messages"')
    composer_idx = src.index('className="glass-composer"')
    assert composer_idx > chat_msgs_idx, (
        "Sanity check: glass-composer should be declared AFTER chat-messages."
    )
    slice_ = src[chat_msgs_idx:composer_idx]
    assert "</div>" in slice_, (
        "Expected the chat-messages container to close before glass-composer."
    )


def test_composer_form_uses_17_25_percent_horizontal_padding(src: str) -> None:
    """Iter 212m-135 — the composer <form data-testid="chat-form"> must use
    `padding: "14px 17.25%"` so the textarea + toolbar inside it sit in the
    same centered column as the messages above, matching Claude's UI."""
    # Anchor on glass-composer className which is unique to the form.
    m = re.search(
        r'className="glass-composer"(?P<form>.+?)style=\{\{(?P<style>.+?)\}\}\s*>',
        src,
        re.DOTALL,
    )
    assert m, "glass-composer form with inline style not found"
    style = m.group("style")
    assert 'padding: "14px 17.25%"' in style, (
        "Expected composer form padding shorthand '14px 17.25%' so the "
        "input content is centered like Claude's chat. Got:\n" + style
    )
