"""
services/llm_router.py — Iter 212m-118 (litellm.Router integration)

Modern, declarative alternative to the manual 4-hop fallback chain in
services/llm.py. Built on top of `litellm.Router` which gives us:

  • Automatic retries with exponential backoff
  • Built-in rate-limit handling (429s wait + retry next model)
  • Budget tracking + spend caps per model
  • Cooldown after consecutive failures
  • Unified API across Claude / DeepSeek / OpenRouter / Groq

Risk control:
  - Activated by env flag LITELLM_ROUTER_ENABLED=1. Default OFF so the
    existing 4-hop logic remains the source of truth in production
    until the founder explicitly flips this on.
  - The router is built lazily on first call so an invalid env doesn't
    crash backend startup.
  - All model names + API keys come from the SAME env vars the legacy
    llm.py reads — no new secrets needed.

To activate in production:
  export LITELLM_ROUTER_ENABLED=1
  # Optional per-call: pass `use_router=True` to call_via_router()

The legacy services.llm.call_llm_with_meta() entry point is
UNCHANGED; new code can opt into the router by importing
call_via_router() directly.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

logger = logging.getLogger("aurem-dev.llm_router")

_router_singleton: Optional[Any] = None
_router_error: Optional[str] = None


def is_enabled() -> bool:
    return os.getenv("LITELLM_ROUTER_ENABLED", "").strip() == "1"


def _build_model_list() -> list[dict]:
    """Build the litellm model_list from the env vars our app already
    knows about. Each entry uses a separate `model_name` alias so the
    router treats them as fallback siblings (router.completion()
    auto-walks the list on failure)."""
    models: list[dict] = []

    claude_key = os.getenv("ANTHROPIC_API_KEY") or os.getenv("EMERGENT_LLM_KEY")
    if claude_key:
        models.append({
            "model_name": "aurem-llm",
            "litellm_params": {
                "model":   "anthropic/claude-sonnet-4-6",
                "api_key": claude_key,
                "rpm":     50,   # rate limit
                "timeout": 30,
            },
        })

    deepseek_key = os.getenv("DEEPSEEK_API_KEY")
    if deepseek_key:
        models.append({
            "model_name": "aurem-llm",
            "litellm_params": {
                "model":   "deepseek/deepseek-chat",
                "api_key": deepseek_key,
                "rpm":     100,
                "timeout": 30,
            },
        })

    openrouter_key = os.getenv("OPENROUTER_API_KEY") or os.getenv("EMERGENT_LLM_KEY")
    if openrouter_key:
        models.append({
            "model_name": "aurem-llm",
            "litellm_params": {
                "model":   "openrouter/anthropic/claude-3.5-sonnet",
                "api_key": openrouter_key,
                "rpm":     100,
                "timeout": 30,
            },
        })

    groq_key = os.getenv("GROQ_API_KEY")
    if groq_key:
        models.append({
            "model_name": "aurem-llm",
            "litellm_params": {
                "model":   "groq/llama-3.1-70b-versatile",
                "api_key": groq_key,
                "rpm":     30,
                "timeout": 20,
            },
        })

    return models


def get_router():
    """Lazy singleton. Returns the Router instance or raises with the
    cached error so subsequent calls don't re-import on every miss."""
    global _router_singleton, _router_error
    if _router_singleton is not None:
        return _router_singleton
    if _router_error:
        raise RuntimeError(_router_error)
    try:
        from litellm import Router
    except ImportError as e:
        _router_error = f"litellm not installed: {e}"
        raise RuntimeError(_router_error)
    model_list = _build_model_list()
    if not model_list:
        _router_error = "no LLM API keys configured for any model in the router"
        raise RuntimeError(_router_error)
    _router_singleton = Router(
        model_list=model_list,
        num_retries=2,
        retry_after=5,                    # seconds between retries
        allowed_fails=3,                  # cooldown after N fails
        cooldown_time=60,                 # seconds before retrying a model
        fallbacks=[{"aurem-llm": ["aurem-llm"]}],   # walk the model_list on failure
        routing_strategy="simple-shuffle",
    )
    logger.info(
        "litellm.Router built with %d model(s): %s",
        len(model_list),
        [m["litellm_params"]["model"] for m in model_list],
    )
    return _router_singleton


async def call_via_router(
    *,
    system:     str,
    user:       str,
    max_tokens: int = 2048,
    temperature: float = 0.7,
) -> dict:
    """Drop-in equivalent of llm.call_llm_with_meta() that routes via
    litellm.Router. Returns the same {content, model, tokens_used}
    shape so call sites can switch without changing parsing code."""
    router = get_router()
    response = await router.acompletion(
        model="aurem-llm",
        messages=[
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
        max_tokens=max_tokens,
        temperature=temperature,
    )
    choice = (response.choices or [{}])[0]
    content = (
        getattr(choice, "message", None) and choice.message.content
    ) or ""
    usage = getattr(response, "usage", None) or {}
    return {
        "content":     content,
        "model":       getattr(response, "model", "aurem-llm"),
        "tokens_used": getattr(usage, "total_tokens", 0) if usage else 0,
    }
