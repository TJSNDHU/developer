"""
services/llm.py — AUREM Dev LLM gateway

Iter 166 — Emergent SDK fully removed. ALL LLM traffic now goes
through OpenRouter (single key: OPENROUTER_API_KEY).

  chat / review / title  → DeepSeek via OpenRouter
  code / ship tasks      → Claude Sonnet 4.5 via OpenRouter
  maxx mode              → Claude + watchdog review pass (both OpenRouter)

Routing logic lives in call_llm_with_meta via `mode` param:
  mode="code"   → Claude Sonnet 4.5 (OpenRouter)
  mode="chat"   → DeepSeek V3 (OpenRouter)
  mode="review" → DeepSeek
  mode="title"  → DeepSeek
  mode="default"→ DeepSeek

Privacy:
  DeepSeek path: data_collection=deny (OpenRouter enforced)
  Claude path:   anthropic/claude-sonnet-4-5-20250929 via OpenRouter
"""
from __future__ import annotations
import os
import asyncio
import json
import logging
import random
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Iter 124 — retry policy for transient upstream failures (rate limit / 5xx).
# Exponential backoff with full jitter so concurrent callers don't sync up.
_RETRY_STATUS = {408, 425, 429, 500, 502, 503, 504}
_MAX_RETRIES = 1      # Iter 129 — was 3. 4 attempts × 5.6s of backoff
                      # was killing chat latency under load (every 429
                      # cascade burned ~10 s of wall time for no
                      # behavioural win — LLM rate limits are STICKY,
                      # repeated retries hit the same wall). 1 retry
                      # catches the flapping-network case and surfaces
                      # genuine outages to the user fast.
_BASE_DELAY_S = 0.4   # Iter 129 — was 0.8. Halves the worst-case
                      # delay window. With _MAX_RETRIES=1 this is the
                      # ONLY backoff that fires.


def _retryable(exc: Exception) -> tuple[bool, int | None]:
    """Return (should_retry, http_status). Status is None for non-HTTP errors."""
    if isinstance(exc, httpx.HTTPStatusError):
        return (exc.response.status_code in _RETRY_STATUS,
                exc.response.status_code)
    # Network / timeout — transient by default
    if isinstance(exc, (httpx.TimeoutException, httpx.ConnectError,
                        httpx.ReadError, httpx.RemoteProtocolError)):
        return (True, None)
    return (False, None)


def _retry_delay(attempt: int) -> float:
    """Full-jitter exponential backoff. attempt is 1-indexed."""
    cap = _BASE_DELAY_S * (2 ** (attempt - 1))
    return random.uniform(0, cap)

# Token caps per mode
MAX_TOKENS = {
    "chat":    1500,
    "code":    3500,   # iter 35: raised for code tasks
    "review":  4096,   # iter 40: bumped for Claude Two-Agent review
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
_CLAUDE_MODES = {"code", "review"}


def cap_for(mode: str) -> int:
    return MAX_TOKENS.get(mode, MAX_TOKENS["default"])


def temperature_for(mode: str) -> float:
    return TEMPERATURE.get(mode, TEMPERATURE["default"])


def _openrouter_key() -> str:
    return os.getenv("OPENROUTER_API_KEY", "")


def _deepseek_model() -> str:
    return os.getenv("LLM_MODEL", "deepseek/deepseek-chat")


# Claude model slug on OpenRouter
_CLAUDE_MODEL = os.getenv(
    "CLAUDE_MODEL", "anthropic/claude-sonnet-4-5-20250929"
)


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
    # Iter 157 — was 60s. DeepSeek typically returns in 5-15s; a
    # 60s budget gave OpenRouter cold-start queues room to gobble up
    # most of the chat turn's wall-clock budget.
    # Iter 160 — tightened further from 35s → 25s after founder
    # reported still-100s+ stalls on production. With _MAX_RETRIES=1
    # worst case is now 2 × 25s = 50s per LLM call. Override per-deploy
    # via LLM_HTTP_TIMEOUT_S.
    _LLM_TIMEOUT_S = float(os.getenv("LLM_HTTP_TIMEOUT_S", "25.0"))
    async with httpx.AsyncClient(timeout=_LLM_TIMEOUT_S) as c:
        for attempt in range(1, _MAX_RETRIES + 2):  # 1..4
            try:
                r = await c.post(OPENROUTER_URL, headers=headers, json=payload)
                r.raise_for_status()
                data = r.json()
                break
            except Exception as e:
                retryable, status = _retryable(e)
                if not retryable or attempt > _MAX_RETRIES:
                    logger.error(
                        "OpenRouter call failed (attempt %d, status=%s, retryable=%s): %r",
                        attempt, status, retryable, e,
                    )
                    raise
                delay = _retry_delay(attempt)
                logger.warning(
                    "OpenRouter transient failure (status=%s, attempt %d/%d) — "
                    "retrying in %.2fs: %r",
                    status, attempt, _MAX_RETRIES + 1, delay, e,
                )
                await asyncio.sleep(delay)
    try:
        msg = data["choices"][0]["message"]
        # If DeepSeek returned native tool_calls, serialize them back to
        # markdown fence so extract_tool_calls() in tools_bridge.py can
        # parse them with its existing Shape-1/2/3 logic.
        tool_calls = msg.get("tool_calls") or []
        if tool_calls and not msg.get("content"):
            parts = []
            for tc in tool_calls:
                fn = tc.get("function", {})
                name = fn.get("name", "")
                try:
                    args = json.loads(fn.get("arguments", "{}"))
                except json.JSONDecodeError:
                    args = {}
                parts.append(
                    "```tool_call\n"
                    + json.dumps({"tool": name, "args": args})
                    + "\n```"
                )
            return "\n".join(parts)
        return msg.get("content") or ""
    except (KeyError, IndexError, TypeError) as e:
        raise RuntimeError(f"OpenRouter malformed response: {e}: {data!r}")


# ── Claude path (code tasks) ─────────────────────────────────────────────────

async def _call_claude(system: str, user: str,
                       max_tokens: int = 3500,
                       temperature: float = 0.0) -> str:
    """Call Claude Sonnet 4.5 via OpenRouter for code tasks.

    Iter 166 — Migrated from Emergent SDK to OpenRouter. Single key
    (OPENROUTER_API_KEY) now serves DeepSeek + Claude + all agents.
    Falls back to DeepSeek if Claude call returns empty (network /
    upstream failure) so code tasks never hard-fail.
    """
    if not _openrouter_key():
        logger.info("OPENROUTER_API_KEY not set — falling back to DeepSeek for code task")
        return await _call_deepseek(
            messages=[{"role": "user", "content": user}],
            system=system,
            max_tokens=max_tokens,
            temperature=temperature,
        )

    content = await call_openrouter_model(
        model=_CLAUDE_MODEL,
        system=system,
        user=user,
        max_tokens=max_tokens,
        temperature=temperature,
    )
    if content:
        return content

    # Empty content → fall back to DeepSeek so we never silently 500 a code task.
    logger.warning("Claude (OpenRouter) returned empty — falling back to DeepSeek")
    return await _call_deepseek(
        messages=[{"role": "user", "content": user}],
        system=system,
        max_tokens=max_tokens,
        temperature=temperature,
    )


# ── Unified entry point ───────────────────────────────────────────────────────

async def call_openrouter_model(
    model: str,
    system: str,
    user: str,
    max_tokens: int = 1000,
    temperature: float = 0.2,
) -> str:
    """Iter 165 — generic OpenRouter caller for the agents pipeline.

    Used by services.agents._call for ANY OpenRouter model (Kimi K2,
    Kimi K2.7 Code, Kimi K2.5, Kimi Thinking, Claude via OpenRouter,
    DeepSeek). The legacy `_call_claude` path goes through the
    Emergent LLM key instead — this function is the unified
    OpenRouter-only path so smart_router can pick any provider.

    Returns "" on any failure; the agents layer handles fallback so we
    keep this function thin and predictable.
    """
    api_key = _openrouter_key()
    if not api_key:
        logger.warning("OPENROUTER_API_KEY missing — call_openrouter_model returning empty")
        return ""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "HTTP-Referer": os.getenv("APP_URL", "https://auremcto.com"),
        "X-Title": "AUREM Dev",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
    }
    _timeout_s = float(os.getenv("LLM_HTTP_TIMEOUT_S", "25.0"))
    try:
        async with httpx.AsyncClient(timeout=_timeout_s) as c:
            r = await c.post(OPENROUTER_URL, headers=headers, json=payload)
            r.raise_for_status()
            data = r.json()
        return (data["choices"][0]["message"].get("content") or "").strip()
    except Exception as e:
        logger.warning("call_openrouter_model(%s) failed: %r", model, e)
        return ""


async def call_llm(messages: list, system: str = "",
                   max_tokens: int = 4000,
                   temperature: float = 0.7) -> str:
    """Direct OpenRouter → DeepSeek call (backwards compat). Returns content."""
    return await _call_deepseek(messages, system, max_tokens, temperature)


async def call_llm_with_meta(system: str, user: str,
                              max_tokens: int = 1500,
                              mode: str = "chat",
                              user_id: Optional[str] = None) -> dict:
    """
    Orchestrator-facing entry point.

    mode="code"  → Claude Sonnet (better code quality, higher token budget)
    mode="chat"  → DeepSeek (fast, cheap)
    mode=other   → DeepSeek

    Iter 94 — Maxx-mode cap (Pro tier = 100/mo):
    If `user_id` is provided and the caller would normally use Claude
    (mode in {code, review}), we first check the user's Maxx budget.
    Capped users transparently fall back to DeepSeek and the response
    includes `maxx_capped=True` + `maxx_remaining=0` so the UI can show
    an upgrade nudge.
    """
    temperature = temperature_for(mode)
    actual_tokens = min(max_tokens, cap_for(mode))
    wants_claude = mode in _CLAUDE_MODES and bool(_openrouter_key())

    # ── Iter 94/101: Maxx-mode budget gate + overage tracking ────────
    # 100-task included monthly. Past that:
    #   • Pro tier: KEEP using Claude (don't degrade UX), track overage
    #     for end-of-month $0.50/task invoice.
    #   • Free/Starter: fall back to DeepSeek (tier has 0 included).
    maxx_capped     = False        # legacy field — true only when we degraded
    maxx_overage    = False        # iter 101 — true when this call is billable overage
    maxx_remaining: Optional[int] = None
    if wants_claude and user_id:
        try:
            from services.usage import get_maxx_usage
            u = await get_maxx_usage(user_id)
            maxx_remaining = u.get("remaining")
            if u.get("capped"):
                tier = u.get("tier", "free")
                if tier in ("pro", "team", "founder"):
                    # Pro+: keep Claude, charge overage (real billing impact).
                    maxx_overage = True
                else:
                    # Free/Starter: degrade to DeepSeek (zero overage policy).
                    maxx_capped = True
                    wants_claude = False
        except Exception as e:
            # Never block on the meter — fall through to whatever was
            # planned. Maxx-cap is a soft commercial guard, not a
            # hard correctness gate.
            logger.warning(f"maxx budget check failed (allowing): {e!r}")

    use_claude = wants_claude
    provider_name = "claude-sonnet-openrouter" if use_claude else "deepseek"

    try:
        if use_claude:
            content = await _call_claude(system, user, actual_tokens, temperature)
            # Count the Claude call against the user's monthly Maxx quota.
            if user_id:
                try:
                    from services.usage import incr_maxx_usage, get_maxx_usage as _u
                    await incr_maxx_usage(user_id)
                    # Recompute remaining so the UI can show "97 left"
                    # without a second DB hit.
                    fresh = await _u(user_id)
                    maxx_remaining = fresh.get("remaining")
                except Exception as e:
                    logger.warning(f"maxx counter incr failed: {e!r}")
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
            "maxx_capped":    maxx_capped,
            "maxx_overage":   maxx_overage,
            "maxx_remaining": maxx_remaining,
        }
    except httpx.HTTPStatusError as e:
        status = e.response.status_code
        logger.error(f"LLM HTTP {status}: {e.response.text[:300]}")
        # Iter 124 — surface a friendly, specific message for rate limits
        # so the UI doesn't say a generic 'API rate limits' line.
        if status == 429:
            err_msg = ("Upstream model is rate-limited right now — I retried "
                       "but couldn't get a slot. Try again in ~10 seconds.")
        elif status in (502, 503, 504):
            err_msg = (f"Upstream model is briefly unavailable (HTTP {status}) "
                       "— try again in a moment.")
        else:
            err_msg = f"LLM unavailable (HTTP {status})"
        return {
            "ok": False, "provider": None, "content": "",
            "temperature": temperature, "mode": mode,
            "fallback_chain": [provider_name],
            "maxx_capped":    maxx_capped,
            "maxx_overage":   maxx_overage,
            "maxx_remaining": maxx_remaining,
            "error": err_msg,
        }
    except Exception as e:
        logger.error(f"LLM call failed: {e!r}")
        return {
            "ok": False, "provider": None, "content": "",
            "temperature": temperature, "mode": mode,
            "fallback_chain": [provider_name],
            "maxx_capped":    maxx_capped,
            "maxx_overage":   maxx_overage,
            "maxx_remaining": maxx_remaining,
            "error": f"LLM unavailable: {e}",
        }


# ── Watchdog (Maxx mode review pass) ─────────────────────────────────────────

async def call_emergent_watchdog(text_to_review: str) -> dict:
    """Maxx mode: ask Claude (via OpenRouter) to grade DeepSeek's output.

    Iter 166 — name kept for backwards-compat with existing imports, but
    the implementation now uses OpenRouter exclusively.

    Returns {ok, score, issues, review, error}. passed=True iff score >= 7.
    """
    if not _openrouter_key():
        return {
            "ok": False, "score": None, "issues": [], "review": "",
            "error": "OPENROUTER_API_KEY not set",
        }
    system = (
        "Strict reviewer. Score AI reply 0-10 for correctness, "
        "hallucinations, broken code. Reply exactly:\n"
        "SCORE: <0-10>\n"
        "ISSUES: <semicolon list; 'none' if perfect>\n"
        "VERDICT: <one sentence>"
    )
    try:
        review_txt = await call_openrouter_model(
            model=_CLAUDE_MODEL,
            system=system,
            user=f"Review (score 1-10):\n\n{text_to_review[:3000]}",
            max_tokens=cap_for("review"),
            temperature=temperature_for("review"),
        )
        review_txt = (review_txt or "").strip()
        if not review_txt:
            return {
                "ok": False, "score": None, "issues": [], "review": "",
                "error": "watchdog returned empty content",
            }

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
        logger.warning(f"watchdog (OpenRouter) failed: {e!r}")
        return {
            "ok": False, "score": None, "issues": [], "review": "",
            "error": f"watchdog unavailable: {e}",
        }
