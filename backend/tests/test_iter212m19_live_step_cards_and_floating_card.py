"""Iter 212m-19 — Live step cards UI + floating progress card.

Backend SSE step stream landed in Iter 212m-18. This iter consumes
those events on the frontend:

  1. <StepCards/> inside the assistant bubble — stacked ✅/⏳ cards.
  2. <LiveStepFloatingCard/> pinned top-right of the chat panel —
     phase pills + step log + `model · Xk tokens` footer + 3s
     auto-close on `done`.
  3. `streamChat({onStep})` handler in lib/api.js dispatches each
     `{type:"step", text, done}` to ChatPanel.jsx which mirrors the
     event into BOTH the message's `steps` array and the floating
     card's state.

Tests here are static-source pins so a future refactor that drops the
SSE wiring trips the suite immediately.
"""
from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src"


# ── lib/api.js — SSE consumer must dispatch step events ────────────


def test_streamchat_accepts_on_step_handler():
    src = (FRONTEND / "lib" / "api.js").read_text(encoding="utf-8")
    # onStep parameter must be in the destructured signature.
    assert "onStep" in src, (
        "streamChat() must accept an `onStep` callback so the SSE "
        "consumer can dispatch `{type:'step', …}` frames"
    )
    # And the parser must route step frames to it.
    assert 'payload.type === "step"' in src
    assert "onStep?.(payload)" in src


# ── StepCards component ────────────────────────────────────────────


def test_step_cards_component_exists():
    f = FRONTEND / "components" / "StepCards.jsx"
    assert f.exists()
    src = f.read_text(encoding="utf-8")
    # The component must expose data-testid for both the wrapper and
    # each individual card so the testing agent can drive it.
    assert 'data-testid="step-cards"' in src
    assert "data-testid={`step-card-${idx}`}" in src
    # Visual contract: ⏳ for running, ✅ for done.
    assert "⏳" in src
    assert "✅" in src
    # Monospace font (terminal feel).
    assert "JetBrains Mono" in src
    # Track which step is in-progress.
    assert "isInProgress" in src
    # Cards stack with no border-radius on each — they share one
    # rounded container instead. The wrapper has overflow:hidden so
    # children visually connect.
    assert "overflow: \"hidden\"" in src or 'overflow:"hidden"' in src


# ── LiveStepFloatingCard component ────────────────────────────────


def test_floating_card_component_exists():
    f = FRONTEND / "components" / "LiveStepFloatingCard.jsx"
    assert f.exists()
    src = f.read_text(encoding="utf-8")
    assert 'data-testid="live-step-floating-card"' in src
    # Phase pills, one per phase.
    for phase in ("thinking", "reading", "writing", "committing", "done"):
        assert f'data-testid={{`live-step-pill-${{p.id}}`}}' in src or \
               f'live-step-pill-{phase}' in src or \
               f'id: "{phase}"' in src, f"phase pill '{phase}' missing"
    # Footer with model + tokens.
    assert 'data-testid="live-step-model"' in src
    assert 'data-testid="live-step-tokens"' in src
    # Auto-close 3s after done. The component must set a timeout for
    # ~3 seconds.
    assert "setTimeout" in src
    # Pretty token formatting: "X.Xk tokens" for ≥1000.
    assert "tokens" in src.lower()
    assert "1000" in src
    # The component must consume `done` from the last step to trigger
    # auto-close.
    assert "isDone" in src


def test_floating_card_phase_mapping_covers_emoji_prefixes():
    """The phaseFor() helper must classify every emoji prefix the
    backend emits (📖 reading, ✍️ writing, 🚀 committing, ✅ done,
    🔍/⚙️/🤔 thinking)."""
    src = (FRONTEND / "components" / "LiveStepFloatingCard.jsx").read_text(
        encoding="utf-8"
    )
    assert '"✅"' in src
    assert '"🚀"' in src
    assert '"✍️"' in src
    assert '"📖"' in src
    # Claude review pass + tool fallback bucket back under "thinking".
    assert '"🔍"' in src
    assert '"⚙️"' in src


# ── ChatPanel wiring ───────────────────────────────────────────────


def test_chatpanel_imports_floating_card():
    src = (FRONTEND / "components" / "ChatPanel.jsx").read_text(
        encoding="utf-8"
    )
    assert "import LiveStepFloatingCard from \"./LiveStepFloatingCard\"" in src


def test_chatpanel_registers_on_step_handler():
    src = (FRONTEND / "components" / "ChatPanel.jsx").read_text(
        encoding="utf-8"
    )
    # streamChat call must pass an onStep callback that appends to BOTH
    # the message's `steps` array AND the liveStepCard state.
    assert "onStep:" in src
    assert "setLiveStepCard" in src
    assert "liveStepCard" in src
    # Card must reset on a new turn so previous-turn steps don't bleed.
    assert "setLiveStepCard({ steps: [], provider: null, tokens: 0, visible: true })" \
        in src


def test_chatpanel_renders_floating_card_when_active():
    src = (FRONTEND / "components" / "ChatPanel.jsx").read_text(
        encoding="utf-8"
    )
    # The floating card must render conditionally on liveStepCard +
    # steps presence.
    assert "<LiveStepFloatingCard" in src
    assert "liveStepCard.visible" in src
    assert "liveStepCard.steps" in src
    # And on close (auto or manual) it must reset to null.
    assert "onClose={() => setLiveStepCard(null)}" in src


def test_chatpanel_passes_provider_into_floating_card():
    src = (FRONTEND / "components" / "ChatPanel.jsx").read_text(
        encoding="utf-8"
    )
    # The meta frame must feed `provider` so the footer shows
    # "glm-5.2" / "claude-sonnet-pro-fallback" / "glm-5.2+claude-review".
    assert 'provider={liveStepCard.provider}' in src
    # And the onMeta handler must copy provider into the card.
    assert "provider: m.provider" in src


def test_chatpanel_marks_card_done_on_stream_done():
    src = (FRONTEND / "components" / "ChatPanel.jsx").read_text(
        encoding="utf-8"
    )
    # onDone must flip the tail step's done flag so the card's
    # auto-close 3s timer fires.
    assert "copy[lastIdx] = { ...copy[lastIdx], done: true };" in src


def test_chatpanel_clears_card_on_error():
    src = (FRONTEND / "components" / "ChatPanel.jsx").read_text(
        encoding="utf-8"
    )
    # If the SSE stream errors, the card must vanish — not sit there
    # with a stale ⏳.
    assert "setLiveStepCard(null);" in src


# ── MessageBubble wiring ───────────────────────────────────────────


def test_message_bubble_renders_step_cards():
    src = (FRONTEND / "components" / "MessageBubble.jsx").read_text(
        encoding="utf-8"
    )
    assert 'import StepCards from "./StepCards"' in src
    # StepCards must be rendered inside the streaming bubble.
    assert "<StepCards" in src
    assert "m.steps" in src


def test_message_bubble_hides_legacy_thinking_pill_when_steps_present():
    src = (FRONTEND / "components" / "MessageBubble.jsx").read_text(
        encoding="utf-8"
    )
    # The legacy "<span data-testid='chat-thinking'>" pill must
    # collapse (display:none) when m.steps is populated — the step
    # cards subsume it.
    assert 'display: Array.isArray(m.steps) && m.steps.length > 0' in src
    assert '"none"' in src
