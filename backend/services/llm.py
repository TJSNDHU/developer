"""
services/llm.py — AUREM Dev
Single-provider LLM gateway: OpenRouter → DeepSeek V3 only.

Privacy posture:
  - data_collection: deny  — OpenRouter enforces this across every provider
    in the routing pool, so no host stores/trains on user traffic.
  - allow_fallbacks: false — never silently routes to a non-DeepSeek model.

Note on `provider.order`: OpenRouter does not currently expose DeepSeek's
first-party endpoint for this account; the privacy-compliant DeepSeek-V3
hosts available are streamlake / deepinfra / novita. They are all bound by
`data_collection: deny`. We allow OpenRouter to pick the cheapest of the
three; `allow_fallbacks: false` still pins us to the DeepSeek-V3 *model*.

If OpenRouter is unreachable we return ok=False — we never fall back to
Emergent / Anthropic / Groq. Surface AI downtime to the user, don't mask it.
"""
from __future__ import annotations
import os
import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Providers that host DeepSeek-V3 with data-collection compliant terms.
# OpenRouter will pick the best within this set; if none accept
# data_collection=deny we surface a 404.
_DEEPSEEK_HOSTS = ["deepseek", "streamlake", "deepinfra", "novita"]


def _api_key() -> str:
    return os.getenv("OPENROUTER_API_KEY", "")


def _model() -> str:
    return os.getenv("LLM_MODEL", "deepseek/deepseek-chat")


async def call_llm(messages: list, system: str = "",
                   max_tokens: int = 4000) -> str:
    """Direct OpenRouter → DeepSeek-V3 call. Returns assistant content.
    Raises on any non-2xx so the caller knows AI is down."""
    api_key = _api_key()
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY not set")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "HTTP-Referer": os.getenv("APP_URL", "https://aurem.dev"),
        "X-Title": "AUREM Dev",
        "X-No-Cache": "true",
    }
    msgs = ([{"role": "system", "content": system}] + messages) if system else messages
    payload = {
        "model": _model(),
        "messages": msgs,
        "max_tokens": max_tokens,
        "temperature": 0.7,
        "provider": {
            "data_collection": "deny",
            "order": _DEEPSEEK_HOSTS,
            "allow_fallbacks": False,
        },
    }
    async with httpx.AsyncClient(timeout=60.0) as c:
        r = await c.post(OPENROUTER_URL, headers=headers, json=payload)
        r.raise_for_status()
        data = r.json()
    try:
        return data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError) as e:
        raise RuntimeError(f"OpenRouter returned malformed response: {e}: {data!r}")


async def call_llm_with_meta(system: str, user: str,
                              max_tokens: int = 1500) -> dict:
    """Orchestrator-facing entry point.
    Returns {ok, provider, content, fallback_chain} so existing callers keep
    working unchanged. provider is hard-coded to 'deepseek' on success."""
    try:
        content = await call_llm(
            messages=[{"role": "user", "content": user}],
            system=system,
            max_tokens=max_tokens,
        )
        return {
            "ok": True,
            "provider": "deepseek",
            "content": content,
            "fallback_chain": ["deepseek"],
        }
    except httpx.HTTPStatusError as e:
        logger.error(
            f"OpenRouter HTTP {e.response.status_code}: {e.response.text[:300]}"
        )
        return {
            "ok": False,
            "provider": None,
            "content": "",
            "fallback_chain": ["deepseek"],
            "error": f"LLM unavailable (HTTP {e.response.status_code})",
        }
    except Exception as e:
        logger.error(f"OpenRouter call failed: {e!r}")
        return {
            "ok": False,
            "provider": None,
            "content": "",
            "fallback_chain": ["deepseek"],
            "error": f"LLM unavailable: {e}",
        }
