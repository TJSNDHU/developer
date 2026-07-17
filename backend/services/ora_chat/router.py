"""
services/ora_chat/router.py — Iter 212m-238

Intent-based model routing with per-route temperature/top_p settings.

Rules are deliberately simple keyword/regex (no ML classifier) so the
routing decision is:
  - Fully auditable (grep the rules, verify each path)
  - Cheap (zero extra LLM call for classification)
  - Overrideable via env config without a redeploy

Every route is registered in `_ROUTES` and pulls its sampling params
from env vars named `ORA_TEMP_<ROUTE>`, `ORA_TOP_P_<ROUTE>`,
`ORA_PP_<ROUTE>` — so temperature can be tuned in production without
touching code (spec addendum requirement).
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Optional


# ────────────────────────────────────────────────────────────────────
# Route table — model slug + sampling params per intent
# ────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class RouteConfig:
    """One row of the routing table.

    All numeric fields are read from env vars at each call so ops can
    tune temperature/top_p in production without a redeploy.
    """
    name:            str          # human label (also used in logs + UI badge)
    model:           str          # OpenRouter model slug
    temp_env:        str          # env var for temperature
    temp_default:    float
    top_p_env:       str
    top_p_default:   float
    pp_env:          str          # presence_penalty env var
    pp_default:      float
    max_tokens_env:  str
    max_tokens_default: int


# Route defaults come from spec addendum table. All values env-tunable.
_ROUTES: dict[str, RouteConfig] = {
    "research": RouteConfig(
        name="research", model=os.getenv("ORA_MODEL_RESEARCH",
                                          "perplexity/llama-3.1-sonar-large-128k-online"),
        temp_env="ORA_TEMP_RESEARCH",  temp_default=0.15,
        top_p_env="ORA_TOP_P_RESEARCH", top_p_default=0.9,
        pp_env="ORA_PP_RESEARCH",       pp_default=0.1,
        max_tokens_env="ORA_MAX_TOKENS", max_tokens_default=2048,
    ),
    "general": RouteConfig(
        name="general", model=os.getenv("ORA_MODEL_GENERAL",
                                         "deepseek/deepseek-chat"),
        temp_env="ORA_TEMP_GENERAL",   temp_default=0.4,
        top_p_env="ORA_TOP_P_GENERAL",  top_p_default=0.9,
        pp_env="ORA_PP_GENERAL",        pp_default=0.1,
        max_tokens_env="ORA_MAX_TOKENS", max_tokens_default=2048,
    ),
    "reasoning": RouteConfig(
        name="reasoning", model=os.getenv("ORA_MODEL_REASONING",
                                           "deepseek/deepseek-r1"),
        temp_env="ORA_TEMP_REASONING", temp_default=0.25,
        top_p_env="ORA_TOP_P_REASONING", top_p_default=0.9,
        pp_env="ORA_PP_REASONING",       pp_default=0.1,
        max_tokens_env="ORA_MAX_TOKENS", max_tokens_default=2048,
    ),
    "fallback": RouteConfig(
        name="fallback", model=os.getenv("ORA_MODEL_FALLBACK",
                                          "z-ai/glm-5.2"),
        temp_env="ORA_TEMP_FALLBACK",  temp_default=0.4,
        top_p_env="ORA_TOP_P_FALLBACK", top_p_default=0.9,
        pp_env="ORA_PP_FALLBACK",       pp_default=0.1,
        max_tokens_env="ORA_MAX_TOKENS", max_tokens_default=2048,
    ),
    "slash_explain": RouteConfig(
        # Used ONLY to format/explain an already-fetched slash-command
        # result. Low temperature = deterministic, boring, factual.
        name="slash_explain", model=os.getenv("ORA_MODEL_SLASH_EXPLAIN",
                                                "deepseek/deepseek-chat"),
        temp_env="ORA_TEMP_SLASH",     temp_default=0.1,
        top_p_env="ORA_TOP_P_SLASH",    top_p_default=0.9,
        pp_env="ORA_PP_SLASH",          pp_default=0.1,
        max_tokens_env="ORA_MAX_TOKENS_SLASH", max_tokens_default=512,
    ),
    # Iter 212m-245 — Auto Deep-Research synthesis route.
    # Runs ONE final call over pooled results from
    # GitHub/Reddit/GDELT/Sonar. DeepSeek V3 kept cheap; temperature
    # slightly higher than research (0.3) so the model can meaningfully
    # combine claims across sources instead of just quoting one.
    "deep": RouteConfig(
        name="deep", model=os.getenv("ORA_MODEL_DEEP",
                                      "deepseek/deepseek-chat"),
        temp_env="ORA_TEMP_DEEP",      temp_default=0.3,
        top_p_env="ORA_TOP_P_DEEP",     top_p_default=0.9,
        pp_env="ORA_PP_DEEP",           pp_default=0.1,
        max_tokens_env="ORA_MAX_TOKENS", max_tokens_default=2048,
    ),
    # Iter 212m-245 — Feature-flagged stub for Anthropic Claude
    # Haiku 4.5 with server-side web_search + web_fetch tools.
    # DISABLED by default (`ORA_ENABLE_CLAUDE_TOOLS=0`). When the
    # founder provides `ANTHROPIC_API_KEY` and flips the flag, this
    # route replaces the free-API fan-out for multi-source queries.
    # NOTE: Actual Anthropic-direct HTTP client is NOT wired here —
    # see deep_research.py::use_claude_tools() for the check-only
    # gate that keeps this route inert until keys arrive.
    "tool_orchestration": RouteConfig(
        name="tool_orchestration",
        model=os.getenv("ORA_MODEL_TOOL_ORCH", "claude-haiku-4-5"),
        temp_env="ORA_TEMP_TOOL_ORCH", temp_default=0.15,
        top_p_env="ORA_TOP_P_TOOL_ORCH", top_p_default=0.9,
        pp_env="ORA_PP_TOOL_ORCH",       pp_default=0.0,
        max_tokens_env="ORA_MAX_TOKENS", max_tokens_default=2048,
    ),
}


def _f(env: str, default: float) -> float:
    """Parse float env var, fall back to default on error."""
    try:
        return float(os.getenv(env, "").strip() or default)
    except (TypeError, ValueError):
        return default


def _i(env: str, default: int) -> int:
    try:
        return int(os.getenv(env, "").strip() or default)
    except (TypeError, ValueError):
        return default


def resolve(route_name: str) -> dict:
    """Return the live config dict for a route (env values applied).

    Raises KeyError if the route name is unknown — callers must have
    already picked a valid name via `classify_intent()`.
    """
    r = _ROUTES[route_name]
    return {
        "route":            r.name,
        "model":            r.model,
        "temperature":      _f(r.temp_env,       r.temp_default),
        "top_p":            _f(r.top_p_env,      r.top_p_default),
        "presence_penalty": _f(r.pp_env,         r.pp_default),
        "max_tokens":       _i(r.max_tokens_env, r.max_tokens_default),
    }


# ────────────────────────────────────────────────────────────────────
# Intent classifier — pure regex/keyword rules
# ────────────────────────────────────────────────────────────────────
# Order matters — first match wins. Keep patterns tight to avoid
# accidental mis-routing (e.g. "code review" shouldn't go to research).
_RESEARCH_KEYWORDS = (
    r"latest\b", r"current\b", r"today\b", r"aaj\b", r"news\b",
    r"trending\b", r"kaunsa\b", r"who\s+is\b", r"what\s+is\s+the\s+latest\b",
    r"recent\b", r"\bhal\s+hi\s+mein\b", r"kya\s+chal\s+raha",
)
_REASONING_KEYWORDS = (
    r"step\s*by\s*step", r"analyze\s+deeply", r"deep\s+analysis",
    r"reason\s+through", r"plan\s+out",
    r"pros\s+and\s+cons", r"trade[-\s]?off",
)


def classify_intent(user_message: str) -> str:
    """Pick a route name for the raw user message.

    Rules (first match wins):
      1. Message starts with "/" and matches a known slash-command → 'slash'
         (handled OUTSIDE this function — see safety.parse_slash_command).
         classify_intent is only called on non-slash messages.
      2. Research keywords → 'research'   (Sonar with web search)
      3. Reasoning keywords → 'reasoning' (DeepSeek R1)
      4. Long, multi-paragraph input (>200 words) → 'reasoning'
      5. Otherwise → 'general' (DeepSeek V3)
    """
    if not user_message or not user_message.strip():
        return "general"
    text = user_message.lower()
    for pat in _RESEARCH_KEYWORDS:
        if re.search(pat, text):
            return "research"
    for pat in _REASONING_KEYWORDS:
        if re.search(pat, text):
            return "reasoning"
    if len(text.split()) > 200:
        return "reasoning"
    return "general"


def fallback_route() -> str:
    """Route name to use when the primary call errors/rate-limits."""
    return "fallback"


def all_route_names() -> list[str]:
    """Return every registered route name — used by tests + admin UI."""
    return list(_ROUTES.keys())


def route_config_snapshot(route_name: Optional[str] = None) -> dict:
    """Debug helper — dump every route's live config (or one route).
    Used by the admin usage endpoint for the ops dashboard."""
    if route_name:
        return {route_name: resolve(route_name)}
    return {name: resolve(name) for name in _ROUTES}
