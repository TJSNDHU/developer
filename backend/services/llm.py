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

# ─── Iter 212m-47 — Free-model fallback chain ─────────────────────────────
# When OpenRouter rejects a paid-model call with one of these statuses
# (most commonly 402 = insufficient credits, also 429 / 5xx / network),
# we retry the SAME prompt against OpenRouter's `:free` models. These
# don't consume credits and live on independent infra so a credit-
# exhaustion or paid-model outage still gets the user a response.
#
# Order matters: best quality → smallest. Env override:
#   OPENROUTER_FREE_MODELS=model1,model2,model3
_FALLBACK_STATUSES = {402, 404, 408, 425, 429, 500, 502, 503, 504}
# 404 → primary model slug isn't routable on OpenRouter (rare config
# drift). Treat as "this candidate is broken — try the next" rather
# than a hard fail; the legitimate "your prompt is broken" 4xx codes
# (400, 422) still abort the chain.
_DEFAULT_FREE_MODELS = [
    # Verified-live slugs via GET /api/v1/models (Feb 2026). Order:
    # best quality → smallest. The chain stops at the first model that
    # produces non-empty content.
    "openai/gpt-oss-120b:free",
    "qwen/qwen3-coder:free",
    "meta-llama/llama-3.3-70b-instruct:free",
    "qwen/qwen3-next-80b-a3b-instruct:free",
    "meta-llama/llama-3.2-3b-instruct:free",
]

# ─── Iter 212m-49 — Groq as TRUE last-resort fallback ─────────────────
# Vendor-independent safety net for when OpenRouter (paid AND free
# tier) is unreachable / quota-exhausted / globally rate-limited.
# Groq's own free tier has its own quota that's independent of
# OpenRouter — so a credit-stuffing attack on OpenRouter, an
# OpenRouter outage, or a "global free-tier 429 storm" still gets the
# user a response. Active only when GROQ_API_KEY is set.
#
# Per founder's spec (2026-02-27): "Groq sirf emergency net hai,
# primary nahi banana." → Groq is ONLY reached after BOTH the
# primary OpenRouter call AND every entry in the OpenRouter `:free`
# fallback chain has failed. Never called speculatively.
_GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")


# ─── Iter 212m-51 — DeepSeek direct API as second-hop fallback ────────
# Independent vendor (api.deepseek.com, separate billing account from
# OpenRouter) — covers the case where the user's OpenRouter credits
# are exhausted but they still want PAID quality before dropping to
# the free tier. Sits between OpenRouter primary and the OR :free
# chain so the priority order is:
#   1. OpenRouter primary (paid)        — best routing flexibility
#   2. DeepSeek direct (paid)           — independent vendor failover
#   3. OpenRouter :free chain           — free models, throttled
#   4. Groq emergency (free)            — true last resort
#
# Per the integration playbook (Feb 2026): model slug pinned to
# `deepseek-v4-flash` (the legacy `deepseek-chat` / `deepseek-coder`
# aliases hard-deprecate on 2026-07-24). DeepSeek's API is OpenAI-
# compatible so the existing payload shape passes straight through.
_DEEPSEEK_DIRECT_URL   = "https://api.deepseek.com/chat/completions"
_DEEPSEEK_DIRECT_MODEL = os.getenv("DEEPSEEK_DIRECT_MODEL", "deepseek-v4-flash")


def _deepseek_direct_key() -> str:
    return os.environ.get("DEEPSEEK_API_KEY", "")


async def _call_deepseek_direct(
    messages: list,
    system: str = "",
    max_tokens: int = 1500,
    temperature: float = 0.7,
) -> str:
    """Call DeepSeek's own API directly (NOT via OpenRouter).

    Used as the second hop in the fallback chain. Raises on any error
    so the caller (`_call_deepseek` / `call_openrouter_model`) can
    decide whether to walk forward to the next hop. Returns "" only
    if the API responds 200 with empty content (treat as soft fail
    so we walk forward).
    """
    key = _deepseek_direct_key()
    if not key:
        raise RuntimeError("DEEPSEEK_API_KEY not set — direct fallback unavailable")
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    msgs = ([{"role": "system", "content": system}] + messages) if system else messages
    payload = {
        "model": _DEEPSEEK_DIRECT_MODEL,
        "messages": msgs,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    # Playbook-prescribed timeout (30s standard; 120s only for reasoning).
    timeout_s = float(os.getenv("DEEPSEEK_DIRECT_TIMEOUT_S", "30.0"))
    async with httpx.AsyncClient(timeout=timeout_s) as c:
        r = await c.post(_DEEPSEEK_DIRECT_URL, headers=headers, json=payload)
        r.raise_for_status()
        data = r.json()
    return (data["choices"][0]["message"].get("content") or "").strip()

# Iter 212m-50 — Groq-only house rules. The file is read once at
# module load (cheap, ~1 KB) and silently absent → defaults apply.
# These rules nudge Groq toward the same shape/voice that ORA uses
# on GLM-5.2 / Claude so the fallback feels seamless to the user.
_GROQ_HOUSE_RULES_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "prompts",
    "groq_house_rules.md",
)


def _load_groq_house_rules() -> str:
    """Read the Groq-only house rules from disk. Silent-skip on any
    error (file missing, permission, encoding) — Groq must still
    work even if the rules file is removed. Cached after the first
    successful read for the lifetime of the process."""
    cached = getattr(_load_groq_house_rules, "_cached", None)
    if cached is not None:
        return cached
    try:
        with open(_GROQ_HOUSE_RULES_PATH, "r", encoding="utf-8") as fh:
            text = fh.read().strip()
        setattr(_load_groq_house_rules, "_cached", text)
        return text
    except (FileNotFoundError, PermissionError, UnicodeDecodeError, OSError) as e:
        logger.debug("groq_house_rules.md not loaded: %r — defaults apply", e)
        setattr(_load_groq_house_rules, "_cached", "")
        return ""


def _groq_key() -> str:
    return os.environ.get("GROQ_API_KEY", "")


# Iter 212m-49 — provenance stash. The non-streaming `_call_deepseek`
# returns a plain string so the existing SSE pipeline can't attach
# "served by Groq" metadata to the response object directly. We park
# the most-recent provider in a contextvar so the orchestrator + SSE
# `done` frame can read it and surface a "⚡ free mode" pill on the
# frontend. ContextVar (not module-global) so concurrent requests
# never read each other's provenance.
from contextvars import ContextVar


# Iter 212m-49 — provenance stash. The non-streaming `_call_deepseek`
# returns a plain string so the existing SSE pipeline can't attach
# "served by Groq" metadata to the response object directly. We park
# the most-recent provider in a MUTABLE dict referenced from a
# ContextVar so concurrent requests never read each other's
# provenance, AND child asyncio tasks (the chat_stream worker) can
# WRITE to the same dict the parent task reads from. Plain
# ContextVar.set() in a child task is invisible to the parent
# because each task gets its own context copy — so we mutate the
# dict in place instead.
def _new_provenance_slot() -> dict:
    return {"provider": "openrouter", "model": "", "is_emergency": False}


_last_provider_ctx: ContextVar[dict] = ContextVar(
    "aurem_last_llm_provider",
    default=_new_provenance_slot(),
)


def _set_last_provider(provider: str, model: str) -> None:
    slot = _last_provider_ctx.get()
    slot["provider"]     = provider
    slot["model"]        = model
    slot["is_emergency"] = (provider == "groq")


def get_last_provider() -> dict:
    """Returns provenance for the last LLM call on THIS request context.
    Shape: {"provider": "openrouter"|"groq", "model": "<slug>",
    "is_emergency": True iff served by the Groq emergency fallback}.
    Reset per-request via the contextvar so concurrent users never
    cross-pollute."""
    return dict(_last_provider_ctx.get())


def reset_last_provider() -> None:
    """Call at the START of a request to clear stale provenance from a
    previous turn in the same worker. Installs a FRESH mutable dict
    so child tasks spawned later inherit a clean slot AND mutations
    they perform are visible to the parent (the dict reference is
    shared, only the ContextVar copy semantics break parent-child
    propagation of `ContextVar.set` calls)."""
    _last_provider_ctx.set(_new_provenance_slot())


async def _call_groq(
    messages: list,
    system: str = "",
    max_tokens: int = 1500,
    temperature: float = 0.7,
) -> str:
    """Async call to Groq Cloud — only reached when OpenRouter primary
    AND every free-tier candidate have failed. Returns the completion
    string. Raises on any error so callers can decide whether to log
    or re-raise; this function never silently returns "" because Groq
    is the LAST link — we want a loud failure to surface that the
    whole chain is dead and the user should know to retry later.

    Note: the official `groq` Python SDK exposes an `AsyncGroq` client
    that mirrors OpenAI's `/v1/chat/completions` schema, so no payload
    surgery is needed."""
    key = _groq_key()
    if not key:
        raise RuntimeError("GROQ_API_KEY not set — emergency fallback unavailable")
    # Imported lazily so deploys without groq installed don't crash at
    # module import time.
    from groq import AsyncGroq
    client = AsyncGroq(api_key=key, timeout=float(os.getenv("GROQ_TIMEOUT_S", "30.0")))
    # Iter 212m-50 — Groq-only house rules. Prepend the markdown rules
    # to the caller-supplied system prompt so the fallback maintains
    # ORA's voice, never breaks character, and refuses destructive ops
    # without confirmation. Silent-skip if the rules file is missing.
    house_rules = _load_groq_house_rules()
    if house_rules:
        effective_system = (
            f"{house_rules}\n\n---\n\n{system}".strip()
            if system else house_rules
        )
    else:
        effective_system = system
    msgs = ([{"role": "system", "content": effective_system}]
            + messages) if effective_system else messages
    completion = await client.chat.completions.create(
        model=_GROQ_MODEL,
        messages=msgs,
        max_tokens=max_tokens,
        temperature=temperature,
    )
    content = (completion.choices[0].message.content or "").strip()
    return content


def _free_fallback_models() -> list[str]:
    raw = os.getenv("OPENROUTER_FREE_MODELS", "").strip()
    if not raw:
        return list(_DEFAULT_FREE_MODELS)
    return [m.strip() for m in raw.split(",") if m.strip()]


def _is_fallback_worthy(exc: Exception) -> bool:
    """Return True when the exception means the PRIMARY model is the
    problem (credit issue, rate limit, upstream 5xx, network blip) and
    we should retry the same prompt against a free model. False when
    the prompt itself is broken (4xx other than 402/429) — retrying
    would just burn another model's quota for no win."""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _FALLBACK_STATUSES
    if isinstance(exc, (httpx.TimeoutException, httpx.ConnectError,
                        httpx.ReadError, httpx.RemoteProtocolError)):
        return True
    return False


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

# Token caps per mode.
# Iter 212m-26 — Production fix: raised `chat` from 1500 → 4000.
# A 1500-token ceiling on chat replies meant GLM-5.2 was truncating
# multi-paragraph answers mid-sentence, surfacing as the "ORA only
# replies one line then stops" bug. Allow an env override so the
# value can be tuned in production without redeploying.
MAX_TOKENS = {
    "chat":    int(os.getenv("LLM_CHAT_MAX_TOKENS", "4000")),
    "code":    int(os.getenv("LLM_CODE_MAX_TOKENS", "3500")),
    "review":  int(os.getenv("LLM_REVIEW_MAX_TOKENS", "4096")),
    "analysis": int(os.getenv("LLM_ANALYSIS_MAX_TOKENS", "2000")),
    # Iter 212m-161 — Ask Advisor dedicated budget (was hard-coded
    # in routers/chat.py before the P1 advisor-fallback refactor).
    "advisor": int(os.getenv("LLM_ADVISOR_MAX_TOKENS", "2500")),
    "title":     30,
    "default": int(os.getenv("LLM_DEFAULT_MAX_TOKENS", "1500")),
}

# Temperature per mode
TEMPERATURE = {
    "code":    0.0,
    "review":  0.0,
    "title":   0.0,
    "chat":    0.7,
    "analysis": 0.4,
    # Iter 212m-161 — Ask Advisor (slightly creative but factual).
    "advisor": 0.2,
    "default": 0.3,
}

_DEEPSEEK_HOSTS = ["deepseek", "streamlake", "deepinfra", "novita"]

# Modes that use Claude for better code quality
_CLAUDE_MODES = {"code", "review"}

# ─── Iter 212m-159 — V2 council routing flags ─────────────────────────────────
# Per /app/memory/V2_ROUTING_ROADMAP.md. All flags default False so
# existing callers see zero behaviour change until env enables them.
#
#   LONGCAT_ENABLED       → Council A primary swaps GLM-5.2 → LongCat-2.0
#                           Claude Sonnet 4.5 remains the rescue for pro/maxx.
#   COUNCIL_B_GLM_ENABLED → mode="analysis" primary uses GLM-5.2 with DeepSeek
#                           rescue.  When False, mode="analysis" behaves like
#                           mode="chat" (DeepSeek only) — Council B unchanged.
#   CEO_RESCUE_ENABLED    → CEO judge wraps its GLM-5.2 call in a
#                           2 s timeout; on timeout falls back to DeepSeek.
LONGCAT_ENABLED       = os.getenv("LONGCAT_ENABLED", "false").lower() == "true"
COUNCIL_B_GLM_ENABLED = os.getenv("COUNCIL_B_GLM_ENABLED", "false").lower() == "true"
CEO_RESCUE_ENABLED    = os.getenv("CEO_RESCUE_ENABLED", "false").lower() == "true"

# OpenRouter model strings
_LONGCAT_MODEL  = os.getenv("LONGCAT_MODEL", "meituan/longcat-2.0")

# CEO rescue config (used when CEO_RESCUE_ENABLED=True — see core/loop hub)
CEO_PRIMARY_TIMEOUT_S = float(os.getenv("CEO_PRIMARY_TIMEOUT_S", "2.0"))
CEO_RESCUE_MODEL      = os.getenv("CEO_RESCUE_MODEL", "deepseek/deepseek-chat")


# Iter 212m-160 — LongCat live-availability flag.
# Default True (optimistic) — flipped to False by `probe_longcat_availability()`
# on app boot when OpenRouter rejects the model slug. When False, `_call_longcat`
# skips the wasted 400-round-trip and goes straight to GLM-5.2. A supervisor
# restart re-probes, so the moment LongCat goes live upstream the flag flips
# back True without a code change.
LONGCAT_LIVE = True


async def probe_longcat_availability() -> bool:
    """Probe OpenRouter to see whether `_LONGCAT_MODEL` is a live slug.

    Sets the module-level `LONGCAT_LIVE` flag and returns the resolved
    boolean.  Logs a single WARNING when LongCat is unavailable so the
    on-call sees it once at boot (instead of a flood on every call).

    Safe to call from a background task — never raises.
    """
    global LONGCAT_LIVE
    if not LONGCAT_ENABLED:
        return LONGCAT_LIVE
    api_key = _openrouter_key()
    if not api_key:
        LONGCAT_LIVE = False
        logger.warning(
            "LongCat probe skipped — OPENROUTER_API_KEY missing. "
            "Council A will use GLM-5.2 fallback."
        )
        return False
    try:
        async with httpx.AsyncClient(timeout=5.0) as c:
            r = await c.post(
                OPENROUTER_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type":  "application/json",
                },
                json={
                    "model":       _LONGCAT_MODEL,
                    "messages":    [{"role": "user", "content": "ping"}],
                    "max_tokens":  1,
                    "temperature": 0,
                },
            )
    except Exception as e:
        LONGCAT_LIVE = False
        logger.warning(
            "LongCat probe network error (%r) — assuming unavailable. "
            "Council A will use GLM-5.2 fallback until next restart.", e,
        )
        return False
    if r.status_code == 200:
        LONGCAT_LIVE = True
        logger.info("✅ LongCat probe OK — Council A primary = %s", _LONGCAT_MODEL)
        return True
    # 400 invalid-model / 404 no-endpoints / 5xx upstream → treat as unavailable
    try:
        err_msg = (r.json().get("error") or {}).get("message") or r.text[:120]
    except Exception:
        err_msg = r.text[:120]
    LONGCAT_LIVE = False
    logger.warning(
        "LongCat unavailable (HTTP %s: %s) — Council A on GLM-5.2 fallback "
        "until next restart. Re-probe by restarting the backend once "
        "%s is published upstream.",
        r.status_code, err_msg, _LONGCAT_MODEL,
    )
    return False


def council_a_primary_model() -> str:
    """Returns the Council A primary model id.

    V2: LongCat-2.0 when LONGCAT_ENABLED=True AND the live-probe flag
    `LONGCAT_LIVE` is True; otherwise legacy GLM-5.2.  The flag is
    refreshed on every supervisor restart by `probe_longcat_availability()`,
    so the moment LongCat publishes upstream the next boot picks it up
    without a code change.
    """
    if LONGCAT_ENABLED and LONGCAT_LIVE:
        return _LONGCAT_MODEL
    return _GLM_MODEL


def council_b_primary_model() -> str:
    """Returns the Council B primary model id.

    V2: GLM-5.2 when COUNCIL_B_GLM_ENABLED=True, else DeepSeek V3 (legacy).
    Council C is unchanged (always DeepSeek) and routed via mode="chat".
    """
    if COUNCIL_B_GLM_ENABLED:
        return _GLM_MODEL
    return _deepseek_model()


def cap_for(mode: str) -> int:
    return MAX_TOKENS.get(mode, MAX_TOKENS["default"])


def temperature_for(mode: str) -> float:
    return TEMPERATURE.get(mode, TEMPERATURE["default"])


def _openrouter_key() -> str:
    return os.getenv("OPENROUTER_API_KEY", "")


def _deepseek_model() -> str:
    return os.getenv("LLM_MODEL", "deepseek/deepseek-chat")


# Claude model slug on OpenRouter.
# Iter 212g — OpenRouter accepts dotted version IDs (anthropic/claude-sonnet-4.5)
# NOT the dash-date Anthropic-native format (claude-sonnet-4-5-20250929)
# which we were sending until prod logs showed 400 Bad Request from
# OpenRouter on every Claude call. Verified against
# `GET https://openrouter.ai/api/v1/models`.
_CLAUDE_MODEL = os.getenv(
    "CLAUDE_MODEL", "anthropic/claude-sonnet-4.5"
)

# Iter 212m-18 — GLM-5.2 (Zhipu AI's flagship via OpenRouter) is the new
# primary model for Swift/Pro/Maxx review modes:
#   Swift → GLM only (fastest path)
#   Pro   → GLM, fall back to Claude on empty / error (resilience)
#   Maxx  → GLM first, then Claude reviews+improves the GLM output
# Override per-deploy via env so we can pin a specific revision.
_GLM_MODEL = os.getenv("GLM_MODEL", "z-ai/glm-5.2")


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

    def _build_payload(model: str, with_provider_block: bool) -> dict:
        p: dict = {
            "model": model,
            "messages": msgs,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if with_provider_block:
            # The host-routing block is OpenRouter-paid-tier specific; the
            # `:free` models live on different infra and 400-out on unknown
            # provider hosts. Only attach it for the primary call.
            p["provider"] = {
                "data_collection": "deny",
                "order": _DEEPSEEK_HOSTS,
                "allow_fallbacks": False,
            }
        return p

    _LLM_TIMEOUT_S = float(os.getenv("LLM_HTTP_TIMEOUT_S", "25.0"))

    # Iter 212m-47 — Try primary DeepSeek model first; if that fails on
    # 402/429/5xx/network, walk the free-model chain. Returns content
    # from whichever model succeeded.
    candidates: list[tuple[str, bool]] = [(_deepseek_model(), True)]
    for fm in _free_fallback_models():
        candidates.append((fm, False))

    last_exc: Exception | None = None
    data: dict | None = None
    served_by: str | None = None
    for ci, (cand_model, with_provider) in enumerate(candidates):
        payload = _build_payload(cand_model, with_provider)
        try:
            async with httpx.AsyncClient(timeout=_LLM_TIMEOUT_S) as c:
                # Per-model: 1 retry on transient errors (unchanged
                # legacy behaviour for the primary).
                for attempt in range(1, _MAX_RETRIES + 2):
                    try:
                        r = await c.post(OPENROUTER_URL, headers=headers, json=payload)
                        r.raise_for_status()
                        data = r.json()
                        served_by = cand_model
                        break
                    except Exception as e:
                        retryable, status = _retryable(e)
                        if not retryable or attempt > _MAX_RETRIES:
                            raise
                        delay = _retry_delay(attempt)
                        logger.warning(
                            "OpenRouter transient failure on %s (status=%s, attempt %d/%d) — "
                            "retrying in %.2fs: %r",
                            cand_model, status, attempt, _MAX_RETRIES + 1, delay, e,
                        )
                        await asyncio.sleep(delay)
            # Success — stop walking the fallback chain.
            if ci > 0:
                logger.warning(
                    "DeepSeek primary %r failed, served by free fallback %r",
                    _deepseek_model(), cand_model,
                )
            break
        except Exception as e:
            last_exc = e
            if not _is_fallback_worthy(e):
                logger.error(
                    "OpenRouter call (%s) failed non-retryably: %r", cand_model, e,
                )
                raise
            logger.warning(
                "OpenRouter %s failed (fallback-worthy, %d/%d): %r — walking chain",
                cand_model, ci + 1, len(candidates), e,
            )
            # Iter 212m-51 — after the PRIMARY OpenRouter model fails
            # (and before we walk the OpenRouter free chain), try the
            # DeepSeek direct API. Independent vendor / billing →
            # bypasses OpenRouter credit exhaustion entirely while
            # still delivering paid-tier quality. Only attempted once
            # per call; if it ALSO fails we silently continue down
            # the free chain.
            if ci == 0 and _deepseek_direct_key():
                try:
                    logger.warning(
                        "OpenRouter primary failed, trying DeepSeek direct (model=%s)…",
                        _DEEPSEEK_DIRECT_MODEL,
                    )
                    ds_content = await _call_deepseek_direct(
                        messages=messages,
                        system=system,
                        max_tokens=max_tokens,
                        temperature=temperature,
                    )
                    if ds_content:
                        logger.warning(
                            "DeepSeek call served by DeepSeek-direct model=%s",
                            _DEEPSEEK_DIRECT_MODEL,
                        )
                        _set_last_provider("deepseek_direct", _DEEPSEEK_DIRECT_MODEL)
                        return ds_content
                    logger.warning(
                        "DeepSeek direct returned empty content — walking free chain"
                    )
                except Exception as dse:
                    if isinstance(dse, httpx.HTTPStatusError) and \
                       dse.response.status_code in (400, 422):
                        # GENUINE prompt-level error from DeepSeek
                        # (request shape / parameters bad). Burning
                        # the free chain on the same broken prompt
                        # is pointless — abort.
                        logger.warning(
                            "DeepSeek direct rejected request (%d) — aborting chain",
                            dse.response.status_code,
                        )
                        raise
                    # 401 = bad key (config drift), 402 = balance,
                    # 429 = throttle, 5xx = vendor issue. None of
                    # these are the user's fault — keep walking the
                    # OR free chain so they still get a response.
                    logger.warning(
                        "DeepSeek direct failed (%r) — walking OpenRouter free chain",
                        dse,
                    )
            continue

    if data is None:
        # All OpenRouter candidates exhausted — try the Groq emergency
        # net as the absolute final link. This is the vendor-
        # independent safety hop (different infra, different account).
        if _groq_key():
            try:
                logger.warning(
                    "OpenRouter chain exhausted (%d candidates). "
                    "Trying Groq emergency fallback (model=%s)…",
                    len(candidates), _GROQ_MODEL,
                )
                content = await _call_groq(
                    messages=msgs,
                    system="",  # already prepended above
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
                if content:
                    logger.warning(
                        "DeepSeek call served by Groq fallback model=%s",
                        _GROQ_MODEL,
                    )
                    # Stash provenance on a module-global so the SSE
                    # pipeline can surface a "⚡ free mode" pill to
                    # the frontend on this turn.
                    _set_last_provider("groq", _GROQ_MODEL)
                    return content
            except Exception as ge:
                logger.error(
                    "Groq emergency fallback ALSO failed: %r — chain is "
                    "now fully exhausted",
                    ge,
                )
        logger.error(
            "OpenRouter exhausted all %d candidates. Last error: %r",
            len(candidates), last_exc,
        )
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("OpenRouter call failed: no candidate models returned")
    # Stash the served-by model in a logger context line; the data dict
    # itself is returned through legacy code paths so we don't change
    # the call contract — provenance is in the logs.
    if served_by:
        logger.info("DeepSeek call served by model=%s", served_by)
        _set_last_provider("openrouter", served_by)
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


# ── GLM path (Swift/Pro/Maxx primary — z-ai/glm-5.2) ───────────────────────

async def _call_glm(system: str, user: str,
                    max_tokens: int = 3500,
                    temperature: float = 0.0) -> str:
    """Iter 212m-18 — Call GLM-5.2 (`z-ai/glm-5.2`) via OpenRouter.

    Mirrors `_call_claude`'s shape so the orchestrator and review-mode
    router can swap models without code branching downstream. Returns
    the assistant content string (may be empty on upstream failure —
    callers in pro/maxx mode use that as the trigger to fall back to
    Claude).
    """
    if not _openrouter_key():
        logger.info(
            "OPENROUTER_API_KEY not set — GLM call falling back to DeepSeek"
        )
        return await _call_deepseek(
            messages=[{"role": "user", "content": user}],
            system=system,
            max_tokens=max_tokens,
            temperature=temperature,
        )
    content = await call_openrouter_model(
        model=_GLM_MODEL,
        system=system,
        user=user,
        max_tokens=max_tokens,
        temperature=temperature,
    )
    return content or ""


# ── LongCat-2.0 path (Iter 212m-159 — Council A primary) ─────

async def _call_longcat(system: str, user: str,
                        max_tokens: int = 3500,
                        temperature: float = 0.0) -> str:
    """Call LongCat-2.0 (`meituan/longcat-2.0`) via OpenRouter.

    Only invoked when `LONGCAT_ENABLED=true` and the caller routes via
    Council A (`mode="code"` + Swift/Pro/Maxx).  Mirrors `_call_glm`'s
    shape so the routing block can swap models with a single conditional.

    Iter 212m-160 — fast-path: if the boot-time probe already marked
    LongCat unavailable (`LONGCAT_LIVE=False`), skip the wasted
    OpenRouter round-trip and go straight to GLM-5.2. Saves ~200 ms
    + an OR rate-limit slot per Council A call until LongCat is live.
    """
    global LONGCAT_LIVE
    if not LONGCAT_LIVE:
        # Boot probe already detected LongCat is dead — straight to GLM.
        return await _call_glm(
            system=system, user=user,
            max_tokens=max_tokens, temperature=temperature,
        )
    if not _openrouter_key():
        logger.info(
            "OPENROUTER_API_KEY not set — LongCat call falling back to DeepSeek"
        )
        return await _call_deepseek(
            messages=[{"role": "user", "content": user}],
            system=system,
            max_tokens=max_tokens,
            temperature=temperature,
        )
    content = await call_openrouter_model(
        model=_LONGCAT_MODEL,
        system=system,
        user=user,
        max_tokens=max_tokens,
        temperature=temperature,
    )
    if not (content or "").strip():
        # LongCat suddenly unreachable mid-flight (probe said it was
        # live, but this call returned empty). Update the live flag
        # so subsequent calls take the fast-path, then fall back to GLM.
        if LONGCAT_LIVE:
            LONGCAT_LIVE = False
            logger.warning(
                "_call_longcat: %s returned empty mid-session — "
                "flipping LONGCAT_LIVE=False, Council A on GLM-5.2 "
                "until next restart.",
                _LONGCAT_MODEL,
            )
        try:
            return await _call_glm(
                system=system, user=user,
                max_tokens=max_tokens, temperature=temperature,
            )
        except Exception as e:
            logger.error("_call_longcat: GLM-5.2 fallback also failed: %r", e)
            return ""
    return content


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

    Iter 212m-47 — Free-model fallback chain. If the requested paid
    model returns 402 (insufficient credits) / 429 / 5xx / network
    error, we transparently retry the same prompt against OpenRouter's
    `:free` tier models. The caller never has to know — they still get
    a usable completion string, with a log line marking which model
    actually answered.

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
    base_messages = [
        {"role": "system", "content": system},
        {"role": "user",   "content": user},
    ]
    _timeout_s = float(os.getenv("LLM_HTTP_TIMEOUT_S", "25.0"))

    # Try the requested model first, then each free fallback in turn.
    candidates = [model] + [m for m in _free_fallback_models() if m != model]
    last_exc: Exception | None = None
    for i, candidate in enumerate(candidates):
        payload = {
            "model": candidate,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": base_messages,
        }
        try:
            async with httpx.AsyncClient(timeout=_timeout_s) as c:
                r = await c.post(OPENROUTER_URL, headers=headers, json=payload)
                r.raise_for_status()
                data = r.json()
            content = (data["choices"][0]["message"].get("content") or "").strip()
            if i > 0:
                logger.warning(
                    "call_openrouter_model: primary %r failed, served by free fallback %r",
                    model, candidate,
                )
                _set_last_provider("openrouter", candidate)
            else:
                _set_last_provider("openrouter", candidate)
            return content
        except Exception as e:
            last_exc = e
            if not _is_fallback_worthy(e):
                # Prompt-level bug (4xx other than 402/429). Don't burn
                # extra free quota — surface the failure.
                logger.warning("call_openrouter_model(%s) non-retryable: %r", candidate, e)
                return ""
            logger.warning(
                "call_openrouter_model(%s) fallback-worthy failure (%d/%d): %r",
                candidate, i + 1, len(candidates), e,
            )
            # Iter 212m-51 — after the PRIMARY OpenRouter model fails
            # (and before walking the OpenRouter :free chain), try
            # DeepSeek direct. Same priority order as `_call_deepseek`
            # so all paths (chat / agents / Vanguard / Mode D) share
            # the same vendor-independent failover.
            if i == 0 and _deepseek_direct_key():
                try:
                    logger.warning(
                        "call_openrouter_model: primary %r failed, "
                        "trying DeepSeek direct (model=%s)…",
                        model, _DEEPSEEK_DIRECT_MODEL,
                    )
                    ds_content = await _call_deepseek_direct(
                        messages=[{"role": "user", "content": user}],
                        system=system,
                        max_tokens=max_tokens,
                        temperature=temperature,
                    )
                    if ds_content:
                        logger.warning(
                            "call_openrouter_model: served by DeepSeek-direct model=%s",
                            _DEEPSEEK_DIRECT_MODEL,
                        )
                        _set_last_provider("deepseek_direct", _DEEPSEEK_DIRECT_MODEL)
                        return ds_content
                except Exception as dse:
                    if isinstance(dse, httpx.HTTPStatusError) and \
                       dse.response.status_code in (400, 422):
                        logger.warning(
                            "DeepSeek direct rejected agent prompt (%d) — aborting chain",
                            dse.response.status_code,
                        )
                        return ""
                    # 401/402/429/5xx → bad key / balance / vendor
                    # issue. Walk forward instead of failing the
                    # whole agent call.
                    logger.warning(
                        "DeepSeek direct failed inside call_openrouter_model (%r) — "
                        "walking OR free chain", dse,
                    )
    # Iter 212m-49 — All OpenRouter candidates exhausted. Try the Groq
    # emergency net before giving up. Vendor-independent infra so an
    # OpenRouter-wide outage / global-free-tier 429 storm still gets
    # the user a usable answer.
    if _groq_key():
        try:
            logger.warning(
                "call_openrouter_model: chain exhausted, trying Groq emergency "
                "(model=%s) for primary=%r",
                _GROQ_MODEL, model,
            )
            content = await _call_groq(
                messages=[{"role": "user", "content": user}],
                system=system,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            if content:
                logger.warning(
                    "call_openrouter_model: served by Groq fallback model=%s",
                    _GROQ_MODEL,
                )
                _set_last_provider("groq", _GROQ_MODEL)
                return content
        except Exception as ge:
            logger.error(
                "Groq emergency fallback ALSO failed inside call_openrouter_model: %r",
                ge,
            )
    logger.error(
        "call_openrouter_model: all %d candidates exhausted (primary=%r). Last error: %r",
        len(candidates), model, last_exc,
    )
    return ""


async def call_llm(messages: list, system: str = "",
                   max_tokens: int = 4000,
                   temperature: float = 0.7) -> str:
    """Direct OpenRouter → DeepSeek call (backwards compat). Returns content."""
    return await _call_deepseek(messages, system, max_tokens, temperature)


async def call_llm_with_meta(system: str, user: str,
                              max_tokens: int = 1500,
                              mode: str = "chat",
                              user_id: Optional[str] = None,
                              review_mode: Optional[str] = None,
                              step_hook=None) -> dict:
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

    Iter 212m-18 — `review_mode` (Swift/Pro/Maxx) overrides the legacy
    `mode` routing:
      • swift → GLM-5.2 only (no Claude under any circumstance)
      • pro   → GLM-5.2 first; if it returns empty or raises, fall back
                to Claude Sonnet so the user never sees a blank reply
      • maxx  → GLM-5.2 produces the initial response, then Claude is
                given that output with a "Review and improve this
                code:" instruction and the IMPROVED text is what
                ships to the user
    Legacy callers that don't pass `review_mode` keep the original
    behaviour. `step_hook(text, done=False)` is invoked at phase
    boundaries so the chat SSE worker can stream progress frames.

    Iter 212m-118 — `LITELLM_ROUTER_ENABLED=1` short-circuits this
    entire 4-hop chain in favour of the unified litellm.Router (see
    services/llm_router.py). Default OFF so production behaviour is
    unchanged; flip the env var on to migrate.

    Iter 212m-119 — Every call is auto-traced to Langfuse Cloud
    (https://us.cloud.langfuse.com) via services.langfuse_tracing.
    Tracing is silently disabled when LANGFUSE_*_KEY env vars are
    missing; a Langfuse outage never breaks an LLM call.
    """
    # Iter 212m-119 — Langfuse observability wrapper.
    from services.langfuse_tracing import trace_llm_call
    with trace_llm_call(
        name="ora.llm.call_llm_with_meta",
        mode=mode, review_mode=review_mode,
        user_id=user_id,
        system_prompt=system, user_prompt=user,
        extra_metadata={"max_tokens": max_tokens},
    ) as _lf:
        result = await _call_llm_with_meta_inner(
            system=system, user=user, max_tokens=max_tokens,
            mode=mode, user_id=user_id, review_mode=review_mode,
            step_hook=step_hook,
        )
        _lf["success"](result)
        return result


async def _call_llm_with_meta_inner(system: str, user: str,
                                     max_tokens: int = 1500,
                                     mode: str = "chat",
                                     user_id: Optional[str] = None,
                                     review_mode: Optional[str] = None,
                                     step_hook=None) -> dict:
    """Real body of call_llm_with_meta — Iter 212m-119 split for tracing."""
    # Iter 212m-118 — litellm router fast-path.
    try:
        from services.llm_router import is_enabled, call_via_router
        if is_enabled():
            return await call_via_router(
                system=system, user=user,
                max_tokens=min(max_tokens, cap_for(mode)),
                temperature=temperature_for(mode),
            )
    except Exception as _e:
        # Router failed → fall through to the legacy chain. Never
        # block the request on a router-init error.
        logger.warning("litellm router failed, falling back to legacy chain: %r", _e)
    temperature = temperature_for(mode)
    actual_tokens = min(max_tokens, cap_for(mode))

    # ── Iter 212m-18 — Review-mode routing (Swift / Pro / Maxx) ─────────
    # Iter 212m-159 — Council A primary swaps GLM-5.2 → LongCat-2.0 when
    # LONGCAT_ENABLED=true AND mode=="code".  Claude rescue path unchanged.
    rm = (review_mode or "").lower().strip()
    if rm in {"swift", "pro", "maxx"}:
        # Maxx-budget gate still applies — Pro/Maxx tiers track Claude
        # usage even when GLM is the primary because Claude is the
        # fallback/reviewer.
        maxx_remaining: Optional[int] = None
        maxx_capped = False
        maxx_overage = False
        if rm in {"pro", "maxx"} and user_id:
            try:
                from services.usage import get_maxx_usage
                u = await get_maxx_usage(user_id)
                maxx_remaining = u.get("remaining")
                if u.get("capped"):
                    tier = u.get("tier", "free")
                    if tier in ("pro", "team", "founder"):
                        maxx_overage = True
                    else:
                        # Free/Starter at cap → degrade Pro/Maxx to Swift
                        # (GLM only) so the chat path never silently
                        # falls back to Claude past the budget.
                        rm = "swift"
                        maxx_capped = True
            except Exception as e:
                logger.warning(f"maxx budget check failed (allowing): {e!r}")

        # Iter 212m-159 — pick Council A primary based on flag + mode.
        use_longcat = LONGCAT_ENABLED and mode == "code"
        primary_model_id   = _LONGCAT_MODEL if use_longcat else _GLM_MODEL
        primary_provider   = "longcat-2.0" if use_longcat else "glm-5.2"
        primary_caller     = _call_longcat if use_longcat else _call_glm

        # Step 1 — primary first.
        if step_hook:
            try:
                step_hook("🤔 Thinking…")
            except Exception:
                pass
        glm_content = ""
        glm_err: Optional[Exception] = None
        try:
            glm_content = await primary_caller(
                system=system, user=user,
                max_tokens=actual_tokens, temperature=temperature,
            )
        except Exception as e:
            glm_err = e
            logger.warning(f"{primary_provider} call raised: {e!r}")

        if rm == "swift":
            return {
                "ok":             True if (glm_content or not glm_err) else False,
                "provider":       primary_provider,
                "content":        glm_content,
                "temperature":    temperature,
                "mode":           mode,
                "review_mode":    "swift",
                "model":          primary_model_id,
                "fallback_chain": [primary_provider],
                "maxx_capped":    maxx_capped,
                "maxx_overage":   False,
                "maxx_remaining": maxx_remaining,
                **({"error": f"{primary_provider} unavailable: {glm_err}"} if glm_err else {}),
            }

        if rm == "pro":
            # GLM ok → use it. Otherwise fall back to Claude so the user
            # never sees an empty reply.
            if glm_content.strip():
                return {
                    "ok":             True,
                    "provider":       primary_provider,
                    "content":        glm_content,
                    "temperature":    temperature,
                    "mode":           mode,
                    "review_mode":    "pro",
                    "model":          primary_model_id,
                    "fallback_chain": [primary_provider],
                    "maxx_capped":    maxx_capped,
                    "maxx_overage":   maxx_overage,
                    "maxx_remaining": maxx_remaining,
                }
            logger.info(
                "Pro mode: %s returned empty (err=%r) — falling back to Claude",
                primary_provider, glm_err,
            )
            if step_hook:
                try:
                    step_hook(f"⚙️ {primary_provider} empty — falling back to Claude…")
                except Exception:
                    pass
            try:
                claude_content = await _call_claude(
                    system=system, user=user,
                    max_tokens=actual_tokens, temperature=temperature,
                )
            except Exception as e:
                logger.error(f"Pro mode: Claude fallback also failed: {e!r}")
                return {
                    "ok": False, "provider": None, "content": "",
                    "temperature": temperature, "mode": mode,
                    "review_mode": "pro",
                    "fallback_chain": [primary_provider, "claude-sonnet"],
                    "maxx_capped":    maxx_capped,
                    "maxx_overage":   maxx_overage,
                    "maxx_remaining": maxx_remaining,
                    "error": f"Both {primary_provider} and Claude unavailable: {e}",
                }
            return {
                "ok":             bool(claude_content.strip()),
                "provider":       "claude-sonnet-pro-fallback",
                "content":        claude_content,
                "temperature":    temperature,
                "mode":           mode,
                "review_mode":    "pro",
                "model":          _CLAUDE_MODEL,
                "fallback_chain": [primary_provider, "claude-sonnet"],
                "maxx_capped":    maxx_capped,
                "maxx_overage":   maxx_overage,
                "maxx_remaining": maxx_remaining,
            }

        # rm == "maxx" — primary produces the draft, Claude reviews+improves.
        if not glm_content.strip():
            # primary gave nothing → Claude has no draft to improve, so just
            # let Claude answer directly (graceful degrade vs hard fail).
            logger.info(
                "Maxx mode: %s empty — Claude answers directly (no review)",
                primary_provider,
            )
            if step_hook:
                try:
                    step_hook(f"⚙️ {primary_provider} empty — Claude answering directly…")
                except Exception:
                    pass
            try:
                claude_content = await _call_claude(
                    system=system, user=user,
                    max_tokens=actual_tokens, temperature=temperature,
                )
            except Exception as e:
                return {
                    "ok": False, "provider": None, "content": "",
                    "temperature": temperature, "mode": mode,
                    "review_mode": "maxx",
                    "fallback_chain": [primary_provider, "claude-sonnet"],
                    "maxx_capped":    maxx_capped,
                    "maxx_overage":   maxx_overage,
                    "maxx_remaining": maxx_remaining,
                    "error": f"{primary_provider} empty and Claude failed: {e}",
                }
            return {
                "ok":             bool(claude_content.strip()),
                "provider":       "claude-sonnet-maxx-direct",
                "content":        claude_content,
                "temperature":    temperature,
                "mode":           mode,
                "review_mode":    "maxx",
                "model":          _CLAUDE_MODEL,
                "fallback_chain": [primary_provider, "claude-sonnet"],
                "maxx_capped":    maxx_capped,
                "maxx_overage":   maxx_overage,
                "maxx_remaining": maxx_remaining,
            }

        if step_hook:
            try:
                step_hook("🔍 Claude reviewing & improving…")
            except Exception:
                pass
        # NB: the original `system` is preserved so Claude keeps the same
        # persona/safety rules. The review instruction lives in the user
        # turn so the orchestrator's tool-call grammar isn't disturbed.
        review_user = (
            "The following is an initial response. Review it for "
            "correctness, hallucinations, and code quality. Improve it "
            "where needed while preserving the same answer structure "
            "(if it contains tool_call code fences, keep them intact). "
            "Return ONLY the improved response — no preamble.\n\n"
            f"---\n{glm_content}\n---"
        )
        try:
            claude_content = await _call_claude(
                system=system, user=review_user,
                max_tokens=actual_tokens, temperature=temperature,
            )
        except Exception as e:
            logger.warning(
                f"Maxx mode: Claude review failed ({e!r}) — returning {primary_provider} draft"
            )
            return {
                "ok":             bool(glm_content.strip()),
                "provider":       f"{primary_provider}-no-review",
                "content":        glm_content,
                "temperature":    temperature,
                "mode":           mode,
                "review_mode":    "maxx",
                "model":          primary_model_id,
                "fallback_chain": [primary_provider],
                "maxx_capped":    maxx_capped,
                "maxx_overage":   maxx_overage,
                "maxx_remaining": maxx_remaining,
                "error": f"Claude review unavailable: {e}",
            }
        if not claude_content.strip():
            # Claude returned empty — keep the primary draft, never blank-ship.
            return {
                "ok":             True,
                "provider":       f"{primary_provider}-no-review",
                "content":        glm_content,
                "temperature":    temperature,
                "mode":           mode,
                "review_mode":    "maxx",
                "model":          primary_model_id,
                "fallback_chain": [primary_provider],
                "maxx_capped":    maxx_capped,
                "maxx_overage":   maxx_overage,
                "maxx_remaining": maxx_remaining,
            }
        # Count this Maxx call against the user's monthly quota.
        if user_id:
            try:
                from services.usage import incr_maxx_usage, get_maxx_usage as _u
                await incr_maxx_usage(user_id)
                fresh = await _u(user_id)
                maxx_remaining = fresh.get("remaining")
            except Exception as e:
                logger.warning(f"maxx counter incr failed: {e!r}")
        return {
            "ok":             True,
            "provider":       f"{primary_provider}+claude-review",
            "content":        claude_content,
            "temperature":    temperature,
            "mode":           mode,
            "review_mode":    "maxx",
            "model":          _CLAUDE_MODEL,
            "fallback_chain": [primary_provider, "claude-sonnet-review"],
            "maxx_capped":    maxx_capped,
            "maxx_overage":   maxx_overage,
            "maxx_remaining": maxx_remaining,
        }

    # ── Iter 212m-159 — Council B "analysis" mode routing ──────────────
    # When COUNCIL_B_GLM_ENABLED, analysis primary = GLM-5.2 (reasoning
    # model) with DeepSeek V3 rescue.  When the flag is OFF, behaves
    # identically to mode="chat" (DeepSeek only) so Council B falls
    # back to legacy behaviour with zero diff.
    if mode == "analysis":
        if not COUNCIL_B_GLM_ENABLED:
            # Pre-V2 behaviour: just DeepSeek (same as mode="chat" path
            # below — fall through by rebranding mode for the legacy
            # selector).
            mode = "chat"
        else:
            try:
                glm_content = await _call_glm(
                    system=system, user=user,
                    max_tokens=actual_tokens, temperature=temperature,
                )
            except Exception as e:
                logger.warning(f"Council B GLM-5.2 raised: {e!r} — using DeepSeek rescue")
                glm_content = ""
            if glm_content.strip():
                return {
                    "ok":             True,
                    "provider":       "glm-5.2",
                    "content":        glm_content,
                    "temperature":    temperature,
                    "mode":           "analysis",
                    "model":          _GLM_MODEL,
                    "fallback_chain": ["glm-5.2"],
                    "maxx_capped":    False,
                    "maxx_overage":   False,
                    "maxx_remaining": None,
                }
            # GLM empty/failure → DeepSeek rescue
            logger.info("Council B: GLM-5.2 empty — falling back to DeepSeek V3")
            try:
                ds_content = await _call_deepseek(
                    messages=[{"role": "user", "content": user}],
                    system=system,
                    max_tokens=actual_tokens, temperature=temperature,
                )
            except Exception as e:
                logger.error(f"Council B: both GLM and DeepSeek failed: {e!r}")
                return {
                    "ok": False, "provider": None, "content": "",
                    "temperature": temperature, "mode": "analysis",
                    "fallback_chain": ["glm-5.2", "deepseek-v3"],
                    "maxx_capped": False, "maxx_overage": False,
                    "maxx_remaining": None,
                    "error": f"Both GLM-5.2 and DeepSeek unavailable: {e}",
                }
            return {
                "ok":             bool(ds_content.strip()),
                "provider":       "deepseek-v3-council-b-rescue",
                "content":        ds_content,
                "temperature":    temperature,
                "mode":           "analysis",
                "model":          _deepseek_model(),
                "fallback_chain": ["glm-5.2", "deepseek-v3"],
                "maxx_capped":    False,
                "maxx_overage":   False,
                "maxx_remaining": None,
            }

    # ── Legacy mode routing (unchanged) ─────────────────────────────────
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
