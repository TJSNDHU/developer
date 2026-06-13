"""Iter 129 regression: chat latency budget.

User reported chat reply taking 30 s+ on prod for 1-2 days. Root cause
was a 4-way pile-up:

1. Persona prompt grew to **25 231 chars (~6.3k tokens)** when the
   TOP-OF-MIND / INVENTORY MODE rules were added. Sent on EVERY tool
   iteration.
2. Tool iteration cap was 6 (orchestrator) / 8-12 (stream path) —
   6 iters × 5 s = 30 s+ wall-clock.
3. `_MAX_RETRIES=3` with exponential backoff: 0.8 + 1.6 + 3.2 = 5.6 s
   of WAITING per 429 cascade. Under any load these cascade.
4. (Background) The persona contained ~5 duplicates of the same
   "no permission for reads" rule — pure token waste.

This test pins the four fixes so a future "let's strengthen the
persona one more rule" change can't silently re-introduce the
30-second chat.
"""
from __future__ import annotations


PERSONA_CHAR_BUDGET = 22_000   # current ~19.8k; +10% headroom for tweaks
MAX_TOOL_ITERS_BUDGET = 6      # streaming path caps at min(max(N, 4), 6)
MAX_RETRIES_BUDGET = 2         # we ship with 1; allow up to 2
MAX_BASE_DELAY_S_BUDGET = 0.6  # we ship with 0.4; allow up to 0.6


def test_persona_under_budget() -> None:
    from services.orchestrator import AUREM_CTO_PERSONA
    n = len(AUREM_CTO_PERSONA)
    assert n <= PERSONA_CHAR_BUDGET, (
        f"AUREM_CTO_PERSONA is {n} chars — over the {PERSONA_CHAR_BUDGET} "
        f"budget. Every chat turn re-sends this on every tool iteration. "
        f"Dedupe a rule (most likely 'no permission for reads' has been "
        f"restated in a new section) before merging."
    )


def test_llm_retry_config_bounded() -> None:
    from services.llm import _MAX_RETRIES, _BASE_DELAY_S
    assert _MAX_RETRIES <= MAX_RETRIES_BUDGET, (
        f"_MAX_RETRIES={_MAX_RETRIES} — under load, every cascade "
        f"adds 0.8 + 1.6 + 3.2 ... s of pure wait. Cap at "
        f"{MAX_RETRIES_BUDGET} so users see real errors fast."
    )
    assert _BASE_DELAY_S <= MAX_BASE_DELAY_S_BUDGET, (
        f"_BASE_DELAY_S={_BASE_DELAY_S} — the first backoff already "
        f"costs the user this much wall-clock per 429."
    )


def test_orchestrator_max_iters_default_bounded() -> None:
    """The default `max_iters` parameter of the chat orchestrator
    bounds the WORST-case latency (each iter = one full LLM round-trip
    with the persona attached). 6 iters × 4 s = 24 s — too slow."""
    import inspect
    from services import orchestrator
    sig = inspect.signature(orchestrator.chat_with_tools)
    default = sig.parameters["max_iters"].default
    assert default <= MAX_TOOL_ITERS_BUDGET, (
        f"chat_with_tools(max_iters={default}) — bound is "
        f"{MAX_TOOL_ITERS_BUDGET}. Higher iters → unbounded chat "
        f"latency on inventory-heavy prompts."
    )


def test_chat_router_caps_iters() -> None:
    """Both the non-streaming (`/chat`) and streaming (`/chat/stream`)
    routes cap max_tool_iters at the orchestrator's budget so a stray
    body parameter can't lift the ceiling."""
    import pathlib
    src = (
        pathlib.Path(__file__).resolve().parents[1] / "routers" / "chat.py"
    ).read_text(encoding="utf-8")
    # Non-streaming path: must cap at the same budget.
    assert "min(body.max_tool_iters, 4)" in src or \
           "min(body.max_tool_iters, 5)" in src or \
           "min(body.max_tool_iters, 6)" in src, (
        "non-streaming chat route doesn't cap max_tool_iters at "
        "<=6 — request bodies can drive chat latency unbounded."
    )
    # Streaming path uses min(max(N, lo), hi) — hi must be <= budget.
    assert "min(max(body.max_tool_iters, 4), 6)" in src or \
           "min(max(body.max_tool_iters, 5), 6)" in src, (
        "streaming chat route's max_iters cap exceeds the budget. "
        "See Iter 129."
    )
