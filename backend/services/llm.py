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

# Token caps per request mode — keeps LLM bills predictable.
MAX_TOKENS = {
    "chat": 1500,
    "code": 3000,
    "review": 500,
    "title": 30,
    "default": 1000,
}

# Temperature per mode — deterministic for code/review/title, warm for chat.
TEMPERATURE = {
    "code": 0.0,
    "review": 0.0,
    "title": 0.0,
    "chat": 0.7,
    "default": 0.3,
}


def cap_for(mode: str) -> int:
    return MAX_TOKENS.get(mode, MAX_TOKENS["default"])


def temperature_for(mode: str) -> float:
    return TEMPERATURE.get(mode, TEMPERATURE["default"])


# Providers that host DeepSeek-V3 with data-collection compliant terms.
# OpenRouter will pick the best within this set; if none accept
# data_collection=deny we surface a 404.
_DEEPSEEK_HOSTS = ["deepseek", "streamlake", "deepinfra", "novita"]


def _api_key() -> str:
    return os.getenv("OPENROUTER_API_KEY", "")


def _model() -> str:
    return os.getenv("LLM_MODEL", "deepseek/deepseek-chat")


async def call_llm(messages: list, system: str = "",
                   max_tokens: int = 4000,
                   temperature: float = 0.7) -> str:
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
        "temperature": temperature,
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
                              max_tokens: int = 1500,
                              mode: str = "chat") -> dict:
    """Orchestrator-facing entry point. `mode` selects temperature."""
    temperature = temperature_for(mode)
    try:
        content = await call_llm(
            messages=[{"role": "user", "content": user}],
            system=system,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return {
            "ok": True,
            "provider": "deepseek",
            "content": content,
            "temperature": temperature,
            "mode": mode,
            "fallback_chain": ["deepseek"],
        }
    except httpx.HTTPStatusError as e:
        logger.error(
            f"OpenRouter HTTP {e.response.status_code}: {e.response.text[:300]}"
        )
        return {
            "ok": False, "provider": None, "content": "",
            "temperature": temperature, "mode": mode,
            "fallback_chain": ["deepseek"],
            "error": f"LLM unavailable (HTTP {e.response.status_code})",
        }
    except Exception as e:
        logger.error(f"OpenRouter call failed: {e!r}")
        return {
            "ok": False, "provider": None, "content": "",
            "temperature": temperature, "mode": mode,
            "fallback_chain": ["deepseek"],
            "error": f"LLM unavailable: {e}",
        }


# ── Emergent watchdog ─────────────────────────────────────────────────────
async def call_emergent_watchdog(text_to_review: str) -> dict:
    """Maxx mode: ask Emergent Universal LLM (Claude) to grade DeepSeek's
    output. Returns {ok, score, issues, review, error}.
    Score 0-10, passed=True iff score >= 7."""
    emergent_key = os.getenv("EMERGENT_LLM_KEY", "")
    if not emergent_key:
        return {
            "ok": False, "score": None, "issues": [], "review": "",
            "error": "EMERGENT_LLM_KEY not set",
        }
    try:
        from emergentintegrations.llm.chat import LlmChat, UserMessage
        import uuid as _uuid

        system = (
            "Strict reviewer. Score AI reply 0-10 for correctness, "
            "hallucinations, broken code. Reply exactly:\n"
            "SCORE: <0-10>\n"
            "ISSUES: <semicolon list; 'none' if perfect>\n"
            "VERDICT: <one sentence>"
        )
        review_prompt = (
            f"Review this reply (score 1-10, issues only if score<7):\n\n"
            f"{text_to_review[:3000]}"
        )
        chat = (
            LlmChat(
                api_key=emergent_key,
                session_id=f"watchdog-{_uuid.uuid4().hex[:8]}",
                system_message=system,
            )
            .with_model("anthropic", "claude-sonnet-4-5-20250929")
            .with_params(max_tokens=cap_for("review"), temperature=temperature_for("review"))
        )
        review = await chat.send_message(UserMessage(text=review_prompt))
        review_txt = (review or "").strip()

        # Parse
        score = None
        issues_str = ""
        verdict = ""
        for line in review_txt.splitlines():
            ls = line.strip()
            if ls.upper().startswith("SCORE:"):
                try:
                    score = int(
                        "".join(ch for ch in ls.split(":", 1)[1] if ch.isdigit())[:2]
                        or "0"
                    )
                except Exception:
                    score = None
            elif ls.upper().startswith("ISSUES:"):
                issues_str = ls.split(":", 1)[1].strip()
            elif ls.upper().startswith("VERDICT:"):
                verdict = ls.split(":", 1)[1].strip()

        issues = []
        if issues_str and issues_str.lower() not in ("none", "n/a", "-"):
            issues = [s.strip() for s in issues_str.split(";") if s.strip()]

        return {
            "ok": True,
            "score": score,
            "issues": issues,
            "verdict": verdict,
            "review": review_txt,
            "passed": (score is not None and score >= 7),
        }
    except Exception as e:
        logger.warning(f"emergent watchdog failed: {e!r}")
        return {
            "ok": False, "score": None, "issues": [], "review": "",
            "error": f"watchdog unavailable: {e}",
        }
