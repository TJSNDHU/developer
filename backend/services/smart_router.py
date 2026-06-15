"""
services/smart_router.py — Iter 165

Single source of truth for model selection across all AUREM agents.
Maps (task_type, mode) → OpenRouter model ID + token budget.

Design rules:
  - Swift  = cheap + fast (Kimi K2.7 Code for writing, Kimi K2.5 for diff review)
  - Pro    = quality code (Kimi K2.7 Code) + smart review (Kimi K2 Thinking)
  - Maxx   = Claude writes, Claude does security (no Kimi review needed)
  - Security scan = ALWAYS Claude (accuracy non-negotiable)
  - Fallback = deepseek/deepseek-chat on any error

Per-task cost (estimated, input + output combined):
  Swift  ~$0.040
  Pro    ~$0.045
  Maxx   ~$0.085

Override any model ID via env var: AUREM_MODEL_<KEY> (e.g.
AUREM_MODEL_SWIFT_CODE) so we can A/B without redeploying.
"""
from __future__ import annotations
import logging
import os

logger = logging.getLogger(__name__)

# Canonical model IDs. Wrapped through _env() so any deploy can pin a
# different OpenRouter slug without touching code (useful when Kimi
# drops a new minor or we need to hot-swap a provider).
def _env(key: str, default: str) -> str:
    return os.getenv(f"AUREM_MODEL_{key.upper()}", default).strip() or default


MODELS = {
    # Reading repo files — cheapest, used by all modes
    "read":         _env("READ",         "moonshotai/kimi-k2"),

    # Swift mode
    "swift_code":   _env("SWIFT_CODE",   "moonshotai/kimi-k2.7-code"),
    "swift_review": _env("SWIFT_REVIEW", "moonshotai/kimi-k2.5"),

    # Pro mode
    "pro_code":     _env("PRO_CODE",     "moonshotai/kimi-k2.7-code"),
    "pro_review":   _env("PRO_REVIEW",   "moonshotai/kimi-k2-thinking"),

    # Maxx mode — Claude writes
    "maxx_code":    _env("MAXX_CODE",    "anthropic/claude-sonnet-4-5-20250929"),
    "maxx_review":  _env("MAXX_REVIEW",  "moonshotai/kimi-k2-thinking"),

    # Security — always Claude (accuracy non-negotiable)
    "security":     _env("SECURITY",     "anthropic/claude-sonnet-4-5-20250929"),

    # Fallback — any model error → DeepSeek
    "fallback":     _env("FALLBACK",     "deepseek/deepseek-chat"),
}

# Per-task max_tokens. Review budgets are intentionally tight — the
# reviewer should emit PASS or a short diff/correction, not write
# essays. Code budgets accommodate full-file rewrites.
TOKEN_BUDGETS = {
    "read":         1000,
    "swift_code":   3500,
    "swift_review":  400,
    "pro_code":     3500,
    "pro_review":   2000,
    "maxx_code":    4000,
    "maxx_review":  2000,
    "security":      600,
    "fallback":     3500,
}


def get_model(task: str, mode: str = "swift") -> str:
    """Return OpenRouter model ID for (task, mode).

    task: "read" | "code" | "review" | "security"
    mode: "swift" | "pro" | "maxx"
    """
    if task == "security":
        return MODELS["security"]
    if task == "read":
        return MODELS["read"]
    key = f"{mode}_{task}"
    model = MODELS.get(key)
    if not model:
        logger.warning("smart_router: no model for task=%s mode=%s → fallback", task, mode)
        return MODELS["fallback"]
    return model


def get_budget(task: str, mode: str = "swift") -> int:
    """Return max_tokens for (task, mode), with sane fallback."""
    if task in ("read", "security", "fallback"):
        return TOKEN_BUDGETS.get(task, 1000)
    key = f"{mode}_{task}"
    return TOKEN_BUDGETS.get(key, 3500)


def get_provider_name(task: str, mode: str = "swift") -> str:
    """Human-readable label for the UI transparency chip."""
    return _humanize(get_model(task, mode))


def _humanize(model_id: str) -> str:
    m = (model_id or "").lower()
    if "claude" in m:           return "Claude Sonnet"
    if "kimi-k2.7" in m:        return "Kimi K2.7"
    if "kimi-k2-thinking" in m: return "Kimi Thinking"
    if "kimi-k2.5" in m:        return "Kimi K2.5"
    if "kimi-k2" in m:          return "Kimi K2"
    if "deepseek" in m:         return "DeepSeek V3"
    # Final fallback — show just the slug tail
    return (model_id or "model").split("/")[-1]
