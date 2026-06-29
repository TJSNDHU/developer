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
    """The chat-messages container's horizontal gutter must come from
    CSS (index.css line ~957) — `padding: 24px clamp(16px, 17.25%, 240px)`
    — and NOT from inline JSX style (Iter 212m-140: mixing the two hit
    a real browser CSSOM quirk where the `padding-right` longhand
    inside the inline shorthand wasn't reliably overridable by the
    container query's `!important` rule). Inline style on the messages
    container must NOT redeclare `padding`."""
    anchor = src.index('data-testid="chat-messages"')
    style_window = src[anchor:anchor + 4000]
    # Inline style must NOT set the `padding` shorthand anymore.
    assert "padding:" not in style_window[:1500].replace("// ", "").replace(
        "transition: padding-right", ""
    ).split("display:")[0], (
        "Inline JSX style on chat-messages must NOT contain `padding:` "
        "anymore — padding lives in index.css so container queries can "
        "override cleanly."
    )
    # CSS must own the rule.
    from pathlib import Path
    css = Path("/app/frontend/src/index.css").read_text(encoding="utf-8")
    assert (
        '[data-testid="chat-messages"] {' in css
        and "padding: 24px clamp(16px, 17.25%, 240px)" in css
    ), (
        "index.css must declare "
        '`[data-testid="chat-messages"] { padding: 24px clamp(16px, 17.25%, 240px); }` '
        "as the single source of truth for messages padding."
    )


def test_chat_messages_right_padding_preserves_live_popup_room(src: str) -> None:
    """When a live-task popup is open, the right padding must still
    swap to 392px so the popup doesn't overlap message content. This
    override STAYS inline (JS-driven runtime state, not layout state).
    When no popup, the property is omitted so CSS clamp drives both
    sides."""
    anchor = src.index('data-testid="chat-messages"')
    style_window = src[anchor:anchor + 4000]
    assert "...(livePopupTaskId ? { paddingRight: 392 } : {})" in style_window, (
        "Right padding must override to 392px while the live popup is "
        "open via a conditional spread (NOT a ternary) so the property "
        "is omitted otherwise."
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
    """Iter 212m-135 / 212m-140 — composer padding lives in CSS now
    (same reason as messages: browser CSSOM quirk with shorthand vs
    container-query longhand !important). CSS owns
    `padding: 14px clamp(16px, 17.25%, 240px)` on
    `[data-testid="chat-form"].glass-composer`."""
    # Anchor on glass-composer className which is unique to the form.
    m = re.search(
        r'className="glass-composer"(?P<form>.+?)style=\{\{(?P<style>.+?)\}\}\s*>',
        src,
        re.DOTALL,
    )
    assert m, "glass-composer form with inline style not found"
    style = m.group("style")
    # Inline style must NOT set padding anymore.
    assert "padding:" not in style, (
        "Inline JSX style on glass-composer must NOT contain `padding:` "
        "anymore — composer padding lives in index.css for container "
        "query parity with messages."
    )
    from pathlib import Path
    css = Path("/app/frontend/src/index.css").read_text(encoding="utf-8")
    assert (
        '[data-testid="chat-form"].glass-composer {' in css
        and "padding: 14px clamp(16px, 17.25%, 240px)" in css
    ), "index.css must own the composer padding rule."


def test_chat_panel_opts_into_container_queries() -> None:
    """Iter 212m-140 — `index.css` must mark the chat-panel as a
    container so the @container queries can adapt the padding when
    Preview or Ask Advisor opens and the chat shrinks."""
    from pathlib import Path
    css = Path("/app/frontend/src/index.css").read_text(encoding="utf-8")
    assert "container-type: inline-size" in css, (
        "Chat panel needs container-type: inline-size to enable adaptive "
        "padding via @container queries."
    )
    assert "@container chat-panel (max-width: 900px)" in css
    assert "@container chat-panel (max-width: 600px)" in css
