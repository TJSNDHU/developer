"""
services/loop_token_ledger.py — Iter 309 · Pre-Phase-1

Per-loop LLM token accounting.  Uses `contextvars` so a loop's
LLM calls carry (loop_id, phase_tag) transparently through every
nested function call — including the Parliament / vanguard-verify
paths that don't know they're being invoked inside a loop.

Design goals
------------
• Zero API surface change for the LLM callers themselves.  The
  low-level `_call_deepseek` / `_call_claude` / `_call_glm`
  functions in `services/llm.py` only need to call
  `log_llm_usage(model, usage_dict)` after a successful response;
  this ledger reads the ambient context and forwards to the
  already-shipped `cost_tracker.log_call()` when — and ONLY when
  — a loop context is active.  Regular chat / scaffold / deep-
  research callers are unaffected.

• Uses the existing `ora_chat_usage` collection so `/admin/
  loop-token-metrics` doesn't need a new schema or index.  Every
  loop-originated row carries `session_id = loop_id`,
  `route = "loop.<phase_tag>"`, and `user_id` from the ambient
  engine.  `/admin/loop-metrics` continues to work; loop-token
  metrics filter `route ^= "loop."`.

• Founder review 2026-07-26 — Pricing lookup is already shipped
  in `cost_tracker.compute_cost_usd()`.  We reuse it (no new
  price tables) so cost_usd lands automatically.
"""
from __future__ import annotations

import contextvars
import logging
from contextlib import asynccontextmanager
from typing import Optional

logger = logging.getLogger(__name__)


# ── Ambient context ────────────────────────────────────────────────
# ContextVars are async-safe: each asyncio task gets its own copy,
# so a Parliament fan-out (3 concurrent LLM calls) doesn't leak
# one file's phase tag into another file's ledger row.
_LOOP_ID_CV: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "loop_token_ledger.loop_id", default=None,
)
_PHASE_TAG_CV: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "loop_token_ledger.phase_tag", default=None,
)
_USER_ID_CV: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "loop_token_ledger.user_id", default=None,
)


@asynccontextmanager
async def loop_call_context(*, loop_id: str, phase_tag: str,
                             user_id: Optional[str] = None):
    """Wrap the code that will trigger LLM calls so every downstream
    `_call_deepseek` / `_call_claude` / `_call_glm` writes a row
    into `ora_chat_usage` tagged with this loop_id + phase_tag.

    Usage in `loop_engine.py::_with_budget`:

        async with loop_call_context(
            loop_id=self.loop_id, phase_tag=phase,
            user_id=self.user_id,
        ):
            await asyncio.wait_for(coro(), timeout=budget)

    All LLM calls fired from `coro()` — even three levels deep in
    Parliament — will carry the same tag.  Nested contexts stack
    (inner replaces outer for its scope, then restores).  Never
    raises; ledger errors are logged and swallowed so an LLM call
    is never blocked by an accounting hiccup.
    """
    t_lid = _LOOP_ID_CV.set(loop_id)
    t_ph  = _PHASE_TAG_CV.set(phase_tag)
    t_uid = _USER_ID_CV.set(user_id or "")
    try:
        yield
    finally:
        _LOOP_ID_CV.reset(t_lid)
        _PHASE_TAG_CV.reset(t_ph)
        _USER_ID_CV.reset(t_uid)


def current_loop_context() -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Return (loop_id, phase_tag, user_id) or (None, None, None)."""
    return _LOOP_ID_CV.get(), _PHASE_TAG_CV.get(), _USER_ID_CV.get()


# ── Fire-and-forget ledger write ───────────────────────────────────
async def log_llm_usage(model: str, usage: dict,
                         *, temperature: float = 0.0,
                         error: Optional[str] = None) -> None:
    """Called by the low-level LLM callers immediately after a
    successful OpenRouter response is parsed.  If no loop context
    is active (regular chat / scaffold call), returns immediately —
    the call becomes a no-op for non-loop callers.

    `usage` is the raw dict from the response, e.g.:
      {"prompt_tokens": 812, "completion_tokens": 240, "total_tokens": 1052}

    We accept a couple of naming conventions so the caller doesn't
    have to normalize:
      • OpenRouter-native: prompt_tokens / completion_tokens
      • ora_chat-style:    input_tokens  / output_tokens
    """
    loop_id, phase_tag, user_id = current_loop_context()
    if not loop_id or not phase_tag:
        return  # No loop context → no-op.  Regular chat unchanged.

    input_tokens  = int(
        usage.get("prompt_tokens")
        or usage.get("input_tokens")
        or 0
    )
    output_tokens = int(
        usage.get("completion_tokens")
        or usage.get("output_tokens")
        or 0
    )

    # Skip zero-token rows so a fallback-chain path that hit an
    # error before receiving usage doesn't spam the ledger.
    if input_tokens == 0 and output_tokens == 0 and not error:
        return

    try:
        # Route encodes loop-phase so `/admin/loop-token-metrics`
        # can filter with `route ^= "loop."` and group by phase.
        route = f"loop.{phase_tag}"
        # Reuse the shipped cost_tracker so cost_usd is computed
        # from the existing price table (founder-approved reuse
        # per iter 309 pre-Phase-1 scope).
        from services.ora_chat import cost_tracker as _ct
        await _ct.log_call(
            user_id=user_id or "loop-orphan",
            session_id=loop_id,
            route=route,
            model=model,
            temperature=temperature,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            error=error,
        )
    except Exception as e:      # noqa: BLE001
        # Ledger MUST never break an LLM call.  Warn once and move on.
        logger.warning(
            "loop_token_ledger.log_llm_usage failed "
            "(loop=%s phase=%s model=%s): %r",
            loop_id, phase_tag, model, e,
        )
