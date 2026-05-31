"""
services/llm.py — AUREM Dev LLM gateway

Iter 35 — Model routing upgrade:

  chat / review / title  → DeepSeek via OpenRouter (fast, cheap, private)
  code / ship tasks      → Claude Sonnet via Emergent (better code quality)
  maxx mode              → Claude + watchdog review pass (unchanged)

Routing logic lives in call_llm_with_meta via `mode` param:
  mode="code"   → Claude Sonnet 4.5 (EMERGENT_LLM_KEY)
  mode="chat"   → DeepSeek V3 (OPENROUTER_API_KEY)
  mode="review" → DeepSeek (fast, cheap)
  mode="title"  → DeepSeek (tiny output)
  mode="default"→ DeepSeek

Privacy:
  DeepSeek path: data_collection=deny (OpenRouter enforced)
  Claude path:   Emergent platform key — no training on user data
"""
from __future__ import annotations
import os
import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Token caps per mode
MAX_TOKENS = {
    "chat":    1500,
    "code":    3500,   # iter 35: raised for code tasks
    "review":   500,
    "title":     30,
    "default": 1000,
}

# Temperature per mode
TEMPERATURE = {
    "code":    0.0,
    "review":  0.0,
    "title":   0.0,
    "chat":    0.7,
    "default": 0.3,
}

_DEEPSEEK_HOSTS = ["deepseek", "streamlake", "deepinfra", "novita"]

# Modes that use Claude for better code quality
_CLAUDE_MODES = {"code"}


def cap_for(mode: str) -> int:
    return MAX_TOKENS.get(mode, MAX_TOKENS["default"])


def temperature_for(mode: str) -> float:
    return TEMPERATURE.get(mode, TEMPERATURE["default"])


def _openrouter_key() -> str:
    return os.getenv("OPENROUTER_API_KEY", "")


def _emergent_key() -> str:
    return os.getenv("EMERGENT_LLM_KEY", "")


def _deepseek_model() -> str:
    return os.getenv("LLM_MODEL", "deepseek/deepseek-chat")


# ── DeepSeek path (chat, review, title) ─────────────────────────────────────

async def _call_deepseek(messages: list, system: str = "",
                         max_tokens: int = 1500,
                         temperature: float = 0.7) -> str:
    api_key = _openrouter_key()
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY not set")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "HTTP-Referer": os.getenv("APP_URL", "https://auremcto.com"),
        "X-Title": "AUREM Dev",
        "X-No-Cache": "true",
    }
    msgs = ([{"role": "system", "content": system}] + messages) if system else messages
    payload = {
        "model": _deepseek_model(),
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
        raise RuntimeError(f"OpenRouter malformed response: {e}: {data!r}")


# ── Claude path (code tasks) ─────────────────────────────────────────────────

async def _call_claude(system: str, user: str,
                       max_tokens: int = 3500,
                       temperature: float = 0.0) -> str:
    """Call Claude Sonnet via Emergent LLM key for code tasks."""
    emergent_key = _emergent_key()
    if not emergent_key:
        # Fall back to DeepSeek if Emergent key not configured
        logger.info("EMERGENT_LLM_KEY not set — falling back to DeepSeek for code task")
        return await _call_deepseek(
            messages=[{"role": "user", "content": user}],
            system=system,
            max_tokens=max_tokens,
            temperature=temperature,
        )

    try:
        from emergentintegrations.llm.chat import LlmChat, UserMessage
        import uuid as _uuid

        chat = (
            LlmChat(
                api_key=emergent_key,
                session_id=f"cto-code-{_uuid.uuid4().hex[:8]}",
                system_message=system,
            )
            .with_model("anthropic", "claude-sonnet-4-5-20250929")
            .with_params(max_tokens=max_tokens, temperature=temperature)
        )
        result = await chat.send_message(UserMessage(text=user))
        return result or ""
    except Exception as e:
        logger.warning(f"Claude call failed, falling back to DeepSeek: {e!r}")
        return await _call_deepseek(
            messages=[{"role": "user", "content": user}],
            system=system,
            max_tokens=max_tokens,
            temperature=temperature,
        )


# ── Unified entry point ───────────────────────────────────────────────────────

async def call_llm(messages: list, system: str = "",
                   max_tokens: int = 4000,
                   temperature: float = 0.7) -> str:
    """Direct OpenRouter → DeepSeek call (backwards compat). Returns content."""
    return await _call_deepseek(messages, system, max_tokens, temperature)


async def call_llm_with_meta(system: str, user: str,
                              max_tokens: int = 1500,
                              mode: str = "chat") -> dict:
    """
    Orchestrator-facing entry point.

    mode="code"  → Claude Sonnet (better code quality, higher token budget)
    mode="chat"  → DeepSeek (fast, cheap)
    mode=other   → DeepSeek
    """
    temperature = temperature_for(mode)
    actual_tokens = min(max_tokens, cap_for(mode))
    use_claude = mode in _CLAUDE_MODES and bool(_emergent_key())

    provider_name = "claude-sonnet" if use_claude else "deepseek"

    try:
        if use_claude:
            content = await _call_claude(system, user, actual_tokens, temperature)
        else:
            content = await _call_deepseek(
                messages=[{"role": "user", "content": user}],
                system=system,
                max_tokens=actual_tokens,
                temperature=temperature,
            )
        return {
            "ok":           True,
            "provider":     provider_name,
            "content":      content,
            "temperature":  temperature,
            "mode":         mode,
            "fallback_chain": [provider_name],
        }
    except httpx.HTTPStatusError as e:
        logger.error(f"LLM HTTP {e.response.status_code}: {e.response.text[:300]}")
        return {
            "ok": False, "provider": None, "content": "",
            "temperature": temperature, "mode": mode,
            "fallback_chain": [provider_name],
            "error": f"LLM unavailable (HTTP {e.response.status_code})",
        }
    except Exception as e:
        logger.error(f"LLM call failed: {e!r}")
        return {
            "ok": False, "provider": None, "content": "",
            "temperature": temperature, "mode": mode,
            "fallback_chain": [provider_name],
            "error": f"LLM unavailable: {e}",
        }


# ── Emergent watchdog (Maxx mode) ─────────────────────────────────────────────

async def call_emergent_watchdog(text_to_review: str) -> dict:
    """Maxx mode: ask Claude to grade DeepSeek's output.
    Returns {ok, score, issues, review, error}. passed=True iff score >= 7."""
    emergent_key = _emergent_key()
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
        chat = (
            LlmChat(
                api_key=emergent_key,
                session_id=f"watchdog-{_uuid.uuid4().hex[:8]}",
                system_message=system,
            )
            .with_model("anthropic", "claude-sonnet-4-5-20250929")
            .with_params(max_tokens=cap_for("review"), temperature=temperature_for("review"))
        )
        review = await chat.send_message(
            UserMessage(text=f"Review (score 1-10):\n\n{text_to_review[:3000]}")
        )
        review_txt = (review or "").strip()

        score = None
        issues_str = ""
        verdict = ""
        for line in review_txt.splitlines():
            ls = line.strip()
            if ls.upper().startswith("SCORE:"):
                try:
                    score = int("".join(ch for ch in ls.split(":", 1)[1] if ch.isdigit())[:2] or "0")
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
            "ok": True, "score": score, "issues": issues,
            "verdict": verdict, "review": review_txt,
            "passed": (score is not None and score >= 7),
        }
    except Exception as e:
        logger.warning(f"emergent watchdog failed: {e!r}")
        return {
            "ok": False, "score": None, "issues": [], "review": "",
            "error": f"watchdog unavailable: {e}",
        }
