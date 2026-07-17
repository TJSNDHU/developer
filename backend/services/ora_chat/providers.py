"""
services/ora_chat/providers.py — Iter 212m-238

Thin streaming caller over OpenRouter. Reuses the existing
`OPENROUTER_API_KEY` from `services/llm.py`; introduces NO new SDK
and NO new key.

Public API:
    async for chunk in stream_call(model, messages, temperature, top_p,
                                    presence_penalty, max_tokens): ...
    → yields dict events {type, ...}:
        {"type": "delta",   "content": "..."}     — token chunk
        {"type": "usage",   "input_tokens": N,
                            "output_tokens": M}   — final usage totals
        {"type": "done"}                          — stream complete
        {"type": "error",   "error": "reason"}    — mid-stream failure

    async def one_shot(...)  — non-streaming variant (used by the
    slash-explain formatter which is short + doesn't need SSE).
"""
from __future__ import annotations

import json
import logging
import os
from typing import AsyncIterator, Optional

import httpx

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
_TIMEOUT_S = float(os.getenv("ORA_LLM_TIMEOUT_S", "45.0"))


def _key() -> str:
    return os.getenv("OPENROUTER_API_KEY", "")


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {_key()}",
        "HTTP-Referer": os.getenv("APP_URL", "https://auremcto.com"),
        "X-Title": "AUREM ORA Chat",
        "Content-Type": "application/json",
    }


def _payload(*, model: str, messages: list, temperature: float,
             top_p: float, presence_penalty: float, max_tokens: int,
             stream: bool) -> dict:
    p: dict = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "top_p": top_p,
        "presence_penalty": presence_penalty,
        "max_tokens": max_tokens,
        "stream": stream,
    }
    if stream:
        # Request usage stats in the final chunk when streaming.
        p["usage"] = {"include": True}
    return p


async def stream_call(*,
                      model: str,
                      messages: list,
                      temperature: float,
                      top_p: float,
                      presence_penalty: float,
                      max_tokens: int) -> AsyncIterator[dict]:
    """Yield event dicts from an OpenRouter streaming completion.

    Never raises — errors are yielded as `{"type": "error", ...}` so
    the SSE consumer can render a partial-plus-error state cleanly.
    """
    if not _key():
        yield {"type": "error", "error": "openrouter_key_missing"}
        return

    body = _payload(model=model, messages=messages, temperature=temperature,
                    top_p=top_p, presence_penalty=presence_penalty,
                    max_tokens=max_tokens, stream=True)

    got_any = False
    usage: dict = {}
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as c:
            async with c.stream("POST", OPENROUTER_URL,
                                headers=_headers(), json=body) as r:
                if r.status_code != 200:
                    txt = ""
                    try:
                        txt = (await r.aread()).decode("utf-8", "ignore")[:400]
                    except Exception:
                        pass
                    yield {"type": "error",
                           "error": f"http_{r.status_code}",
                           "detail": txt}
                    return
                async for raw in r.aiter_lines():
                    if not raw or not raw.startswith("data:"):
                        continue
                    data = raw[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        obj = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    # Choice delta
                    choices = obj.get("choices") or []
                    if choices:
                        delta = (choices[0] or {}).get("delta") or {}
                        content = delta.get("content")
                        if content:
                            got_any = True
                            yield {"type": "delta", "content": content}
                    # Usage — OpenRouter emits this in a trailing chunk
                    # when `usage.include=true` is set.
                    u = obj.get("usage")
                    if u:
                        usage = {
                            "input_tokens":  int(u.get("prompt_tokens", 0) or 0),
                            "output_tokens": int(u.get("completion_tokens", 0) or 0),
                            "total_tokens":  int(u.get("total_tokens", 0) or 0),
                        }
    except httpx.TimeoutException:
        yield {"type": "error", "error": "timeout",
               "recoverable": got_any}
        return
    except Exception as e:  # noqa: BLE001
        logger.warning("ora_chat stream_call unexpected: %r", e)
        yield {"type": "error", "error": f"{type(e).__name__}",
               "detail": str(e)[:200], "recoverable": got_any}
        return

    yield {"type": "usage", **usage}
    yield {"type": "done"}


async def one_shot(*,
                   model: str,
                   messages: list,
                   temperature: float,
                   top_p: float,
                   presence_penalty: float,
                   max_tokens: int) -> tuple[str, dict, Optional[str]]:
    """Non-streaming call. Returns (content, usage_dict, error_or_none).

    Used for short deterministic responses (slash-command explanations,
    tests). Same key + endpoint as streaming path.
    """
    if not _key():
        return "", {}, "openrouter_key_missing"

    body = _payload(model=model, messages=messages, temperature=temperature,
                    top_p=top_p, presence_penalty=presence_penalty,
                    max_tokens=max_tokens, stream=False)
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as c:
            r = await c.post(OPENROUTER_URL, headers=_headers(), json=body)
            if r.status_code != 200:
                return "", {}, f"http_{r.status_code}: {r.text[:200]}"
            j = r.json()
            content = ((j.get("choices") or [{}])[0].get("message") or {}
                       ).get("content") or ""
            u = j.get("usage") or {}
            usage = {
                "input_tokens":  int(u.get("prompt_tokens", 0) or 0),
                "output_tokens": int(u.get("completion_tokens", 0) or 0),
                "total_tokens":  int(u.get("total_tokens", 0) or 0),
            }
            return content, usage, None
    except Exception as e:  # noqa: BLE001
        return "", {}, f"{type(e).__name__}: {str(e)[:200]}"
