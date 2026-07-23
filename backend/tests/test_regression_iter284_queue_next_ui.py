"""
Iter 284 — Queue-next UX: visible chip + auto-queue (no OS confirm)

Bug from real user screenshot:
  1. Send button was HIDDEN while agent was running (busy=true) —
     only `chat-stop` rendered, so the queue-next feature was
     reachable only by keyboard Enter (undiscoverable).
  2. Queue-confirm popup was `window.confirm()` — narrow, unstyled,
     visually detached from the chat container.

Fix:
  1. Show `[data-testid="chat-queue-send"]` alongside `chat-stop`
     when execMode=LOOP AND user has text AND session is ready.
  2. Auto-queue on 409 (no confirm dialog); surface via a
     `[data-testid="queued-chip"]` chip + "Agent is running…" bar
     that spans the composer width. Matches the reference UI.

Source-level tests only — full E2E requires an active loop on prod.
"""
from __future__ import annotations


def test_regression_iter284_chat_queue_send_button_renders_during_busy():
    """
    ChatPanel.jsx MUST render a `chat-queue-send` button in the
    busy branch of the send/stop conditional. Without it, the
    queue-next feature (Iter 279) is only reachable via Enter key.
    """
    src = open("/app/frontend/src/components/ChatPanel.jsx").read()

    # Find the busy branch of the send/stop render.
    idx = src.find("{busy ? (")
    assert idx > -1, "busy branch of send/stop render must exist"
    body = src[idx: idx + 3000]

    assert 'data-testid="chat-queue-send"' in body, (
        "busy branch MUST render a chat-queue-send button so the "
        "queue flow is reachable by click."
    )
    # The queue button must only render when we're in LOOP mode
    # with text present — otherwise it would misfire during regular
    # chat streaming.
    assert "execMode === EXEC_MODES.LOOP" in body, (
        "chat-queue-send must be gated on execMode === LOOP"
    )
    assert "input.trim()" in body, (
        "chat-queue-send must be gated on input having text"
    )


def test_regression_iter284_window_confirm_removed():
    """
    The narrow OS-native `window.confirm(...)` in the 409-queue
    branch of runLoopPlan MUST be gone. It has been replaced by
    a silent auto-queue + visible "N queued" chip.
    """
    src = open("/app/frontend/src/components/ChatPanel.jsx").read()

    # Find the queue-next handling block (Iter 279 handler).
    idx = src.find("loop_already_running")
    assert idx > -1, "queue-next handler block must exist"
    # Look at the 2000 chars around the handler.
    window = src[max(0, idx - 500): idx + 2500]

    assert "window.confirm(" not in window, (
        "window.confirm() must NOT be used in the queue-next path — "
        "the reference UX auto-queues silently and surfaces the "
        "queue via a chip (Iter 284)."
    )


def test_regression_iter284_queued_chip_and_agent_running_present():
    """
    A `[data-testid="queued-chip"]` chip and an `[data-testid=
    "agent-status-bar"]` row MUST render above the composer while
    the agent is busy. This makes the queue discoverable and
    matches the reference UI pattern (visual status pairing).
    """
    src = open("/app/frontend/src/components/ChatPanel.jsx").read()

    assert 'data-testid="agent-status-bar"' in src, (
        "must render an agent-status-bar row while busy"
    )
    assert 'data-testid="queued-chip"' in src, (
        "must render a queued-chip when queuedCount > 0"
    )
    assert "Agent is running" in src, (
        "must include the 'Agent is running…' human label so the "
        "state is clear without decoding the pulse dot"
    )
