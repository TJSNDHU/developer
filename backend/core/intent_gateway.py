"""
core/intent_gateway.py — Iter 212m-149

Replaces the binary `execution_mode = prompt | loop` toggle with a
3-tier intent classification gateway:

  TIER 1 — casual    : direct LLM reply, no tools, no pipeline
  TIER 2 — query     : limited tool calls (max 2 iters), context-only
  TIER 3 — agentic   : full chat_with_tools pipeline (current default)

Architecture per founder spec:

  1. Fast heuristics first  — microseconds, no API call.
  2. LLM fallback           — only when heuristic confidence < 0.75,
                              uses cheap fast model, 2 s hard timeout.
  3. Ambiguity handler      — if final conf < 0.72, return a
                              `clarify` tier with a suggested probe
                              instead of guessing.
  4. Mongo logging          — every classification is persisted to
                              `intent_classifications` for analytics.

This module is intentionally pure-Python with no FastAPI imports so
it can be reused from any caller and unit-tested without spinning
the app.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

logger = logging.getLogger("aurem-dev.intent_gateway")


# ─────────────────────────────────────────────────────────────────────
#  Tier constants — exported for the router + tests.
# ─────────────────────────────────────────────────────────────────────
TIER_CASUAL  = "casual"
TIER_QUERY   = "query"
TIER_AGENTIC = "agentic"
TIER_CLARIFY = "clarify"   # internal — caller emits a clarifying probe

VALID_TIERS = (TIER_CASUAL, TIER_QUERY, TIER_AGENTIC, TIER_CLARIFY)


# ─────────────────────────────────────────────────────────────────────
#  Heuristic vocabularies.
# ─────────────────────────────────────────────────────────────────────

#  Action verbs at the START of a sentence are the strongest agentic
#  signal we have.  All matched as whole-word, case-insensitive.
_AGENTIC_VERBS = {
    "fix", "send", "create", "run", "ship", "deploy", "scan",
    "commit", "push", "invoice", "email", "call", "book",
    "schedule", "build", "generate", "execute", "start",
    "launch", "trigger", "delete", "rename", "refactor",
    "migrate", "upgrade", "merge", "rollback", "redeploy",
    # AUREM-specific developer actions.
    "implement", "add", "remove", "update", "patch",
    "configure", "wire", "install", "uninstall", "import",
    "export", "publish",
}

#  Question / read-only words that signal a query (Tier 2).
_QUERY_LEADS = {
    "show", "what", "what's", "whats", "how", "list", "find",
    "get", "explain", "summarize", "summarise", "tell",
    "describe", "report", "status", "details", "search",
    "lookup", "look", "view", "display", "where",
    "when", "who", "why", "which", "compare", "diff",
}

#  Casual seeds — greetings, ack, light social.
_CASUAL_GREETINGS = {
    "hi", "hello", "hey", "yo", "sup", "hola", "namaste",
    "good", "gm", "gn", "morning", "evening", "afternoon", "night",
}
_CASUAL_ACK = {
    "ok", "okay", "k", "kk", "got", "cool", "nice", "awesome",
    "great", "perfect", "lol", "haha", "yeah", "yes", "yep",
    "no", "nope", "sure", "fine", "alright", "right",
}
_CASUAL_THANKS = {
    "thanks", "thank", "ty", "thx", "thankyou", "appreciated",
    "cheers", "kudos",
}


# Compiled patterns used by the heuristic pass.
_WORD_TOKEN_RX = re.compile(r"[A-Za-z'\u2019]+")


def _tokens(text: str) -> list[str]:
    """Lower-cased word tokens from `text`, ignoring punctuation."""
    return [t.lower() for t in _WORD_TOKEN_RX.findall(text or "")]


def _first_meaningful_token(tokens: list[str]) -> str:
    """First non-trivial token (skips one-letter fillers like 'i', 'a')."""
    for t in tokens:
        if t in {"i", "a"}:
            continue
        return t
    return tokens[0] if tokens else ""


# ─────────────────────────────────────────────────────────────────────
#  Heuristic classifier — returns (tier, confidence, signals).
# ─────────────────────────────────────────────────────────────────────

def _classify_heuristic(message: str) -> dict[str, Any]:
    """Pure heuristic pass.  Returns a dict with:
        {tier, confidence, method:"heuristic", signals: [...]}
    Confidence is intentionally calibrated so that mixed-signal inputs
    fall below the 0.75 LLM-fallback threshold."""
    text = (message or "").strip()
    if not text:
        return {
            "tier":       TIER_CASUAL,
            "confidence": 0.50,
            "method":     "heuristic",
            "signals":    ["empty"],
            "reasoning":  "empty message",
        }

    tokens = _tokens(text)
    word_count = len(tokens)
    first = _first_meaningful_token(tokens)
    signals: list[str] = []

    # ── Tier 3 (agentic) — action verb at start ────────────────────
    if first in _AGENTIC_VERBS:
        signals.append(f"agentic_lead_verb:{first}")
        # Stronger confidence when the message is short + imperative
        # (e.g. "Fix it" vs "Fix services/llm.py and add tests for ...").
        # Longer, more specific imperatives are slightly less confident
        # (could be a question phrased as a verb-led sentence).
        confidence = 0.97 if word_count <= 8 else 0.92
        return {
            "tier":       TIER_AGENTIC,
            "confidence": confidence,
            "method":     "heuristic",
            "signals":    signals,
            "reasoning":  f"Imperative verb '{first}' at start of message",
        }

    # ── Tier 1 (casual) — short, no action / question signals ──────
    has_question_word = any(t in _QUERY_LEADS for t in tokens)
    has_question_mark = "?" in text
    casual_seed = (
        first in _CASUAL_GREETINGS
        or first in _CASUAL_ACK
        or first in _CASUAL_THANKS
        or any(t in _CASUAL_THANKS for t in tokens)
    )
    # Very short with NO action / question signal → almost certainly chit-chat.
    if (
        word_count < 8
        and not has_question_word
        and not has_question_mark
        and (casual_seed or word_count <= 3)
    ):
        if casual_seed:
            signals.append("casual_seed")
            confidence = 0.94 if word_count <= 5 else 0.88
        else:
            # 1-3 word messages with NO meaningful intent verb tend to
            # be filler / chit-chat ("okay", "got it", "right then").
            signals.append("short_no_intent")
            confidence = 0.86
        return {
            "tier":       TIER_CASUAL,
            "confidence": confidence,
            "method":     "heuristic",
            "signals":    signals,
            "reasoning":  f"Short message ({word_count} words) with no action / query signal",
        }

    # ── Tier 2 (query) — question words / question mark ────────────
    if has_question_word or has_question_mark:
        signals.append("query_signal")
        # Stronger confidence when a query-lead is at the START
        # ("show me my leads", "what is the status").
        if first in _QUERY_LEADS:
            confidence = 0.86
            signals.append(f"query_lead:{first}")
        else:
            # Question-y but ambiguous (e.g. "the leads — show?")
            confidence = 0.76
        return {
            "tier":       TIER_QUERY,
            "confidence": confidence,
            "method":     "heuristic",
            "signals":    signals,
            "reasoning":  "Question word or '?' present",
        }

    # ── Default — ambiguous mid-length statement ───────────────────
    # Fall through with deliberately low confidence so the LLM
    # fallback gets called by the orchestrator.
    signals.append("ambiguous")
    return {
        "tier":       TIER_QUERY,
        "confidence": 0.62,
        "method":     "heuristic",
        "signals":    signals,
        "reasoning":  "No strong signal — fallback to query tier (LLM should escalate)",
    }


# ─────────────────────────────────────────────────────────────────────
#  LLM fallback — only invoked when heuristic conf < 0.75.
# ─────────────────────────────────────────────────────────────────────

_LLM_SYSTEM = (
    "Classify this developer-platform chat message into exactly one tier:\n"
    " - casual : chitchat, greetings, thanks, acknowledgements\n"
    " - query  : asking for information, reports, summaries, data\n"
    " - agentic: requesting an action to be performed (write code, "
    "ship, scan, deploy, etc.)\n\n"
    "Respond ONLY with JSON: {\"tier\":\"casual|query|agentic\",\"conf\":0.0-1.0}.\n"
    "No explanation. No other text."
)


async def _classify_llm(message: str, history: list[dict] | None) -> dict[str, Any]:
    """Calls the cheapest fast LLM available with a 2 s timeout and a
    hard 15-token output budget. Returns the parsed JSON or a safe
    fallback if anything blows up."""
    import asyncio
    try:
        from services.llm import call_llm
    except Exception as e:
        logger.debug("intent_gateway: llm import failed %r", e)
        return {
            "tier":       TIER_QUERY,
            "confidence": 0.70,
            "method":     "llm_unavailable",
            "signals":    ["llm_import_error"],
            "reasoning":  "Fallback (LLM unavailable)",
        }

    ctx_lines: list[str] = []
    for msg in (history or [])[-2:]:
        role = msg.get("role", "user")[:9]
        content = (msg.get("content") or "")[:160]
        ctx_lines.append(f"{role}: {content}")
    user_prompt = f"RECENT:\n{chr(10).join(ctx_lines)}\nMESSAGE:\n{message[:400]}"

    t0 = time.monotonic()
    try:
        raw = await asyncio.wait_for(
            call_llm(
                [{"role": "user", "content": user_prompt}],
                system=_LLM_SYSTEM,
                max_tokens=20,
                temperature=0.0,
            ),
            timeout=2.0,
        )
    except asyncio.TimeoutError:
        logger.warning("intent_gateway: LLM classifier timeout")
        return {
            "tier":       TIER_QUERY,
            "confidence": 0.70,
            "method":     "llm_timeout",
            "signals":    ["timeout"],
            "reasoning":  "LLM exceeded 2 s budget",
        }
    except Exception as e:
        logger.warning("intent_gateway: LLM classifier error %r", e)
        return {
            "tier":       TIER_QUERY,
            "confidence": 0.70,
            "method":     "llm_error",
            "signals":    [f"err:{type(e).__name__}"],
            "reasoning":  "LLM error",
        }

    latency_ms = round((time.monotonic() - t0) * 1000, 1)
    parsed = _parse_llm_json(raw)
    if parsed is None:
        return {
            "tier":       TIER_QUERY,
            "confidence": 0.70,
            "method":     "llm_parse_fail",
            "signals":    ["parse_fail"],
            "reasoning":  f"Could not parse LLM response: {raw[:80]!r}",
            "llm_latency_ms": latency_ms,
        }
    tier = parsed.get("tier", TIER_QUERY)
    conf = float(parsed.get("conf", 0.70))
    if tier not in (TIER_CASUAL, TIER_QUERY, TIER_AGENTIC):
        tier = TIER_QUERY
    # Clamp.
    conf = max(0.0, min(1.0, conf))
    return {
        "tier":            tier,
        "confidence":      conf,
        "method":          "llm",
        "signals":         ["llm_classified"],
        "reasoning":       f"LLM classified as {tier}@{conf:.2f}",
        "llm_latency_ms":  latency_ms,
    }


def _parse_llm_json(text: str) -> dict[str, Any] | None:
    """Best-effort JSON extraction from a possibly-noisy LLM reply."""
    if not text:
        return None
    text = text.strip()
    # Direct parse first.
    try:
        return json.loads(text)
    except Exception:
        pass
    # Try to pluck the first {...} block out.
    m = re.search(r"\{[^}]*\}", text)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────
#  Ambiguity handler.
# ─────────────────────────────────────────────────────────────────────

_AMBIGUITY_THRESHOLD = 0.72


def _build_clarifying_probe(message: str) -> str:
    """One-line clarifying question template.  Picks the verb-ish
    seed of the message to make the probe concrete."""
    tokens = _tokens(message)
    seed = _first_meaningful_token(tokens)
    if seed in _AGENTIC_VERBS:
        action = f"{seed} {' '.join(tokens[1:6])}".strip()
        return (
            f"Just checking — did you want me to {action}, "
            "or were you just thinking out loud?"
        )
    if any(t in _QUERY_LEADS for t in tokens):
        return (
            "Did you want me to look this up and report back, "
            "or were you just sharing context?"
        )
    return (
        "Just checking — did you want me to take an action on this, "
        "or were you just chatting?"
    )


# ─────────────────────────────────────────────────────────────────────
#  Public entry point.
# ─────────────────────────────────────────────────────────────────────

async def classify(
    message: str,
    history: list[dict] | None = None,
    *,
    db=None,
    user_id: str | None = None,
    project_id: str | None = None,
    escalate_to_llm: bool = True,
) -> dict[str, Any]:
    """Classify `message` into one of the 3 intent tiers.

    Returns a dict shaped like::

        {
            "tier":          "casual" | "query" | "agentic" | "clarify",
            "confidence":    float 0-1,
            "method":        "heuristic" | "llm" | ...,
            "reasoning":     str,
            "signals":       [str, ...],
            "was_ambiguous": bool,
            "clarify":       str | None,
            "gateway_ms":    float,
        }

    Pass `db` to enable Mongo logging.  Returns immediately on db=None.
    """
    t0 = time.monotonic()
    result = _classify_heuristic(message)

    # ── LLM escalation when heuristic is uncertain ────────────────
    if escalate_to_llm and result["confidence"] < 0.75:
        llm_result = await _classify_llm(message, history)
        # Prefer the LLM if it landed with higher confidence; otherwise
        # keep the heuristic guess but surface the LLM's reasoning.
        if llm_result["confidence"] > result["confidence"]:
            result = llm_result

    was_ambiguous = result["confidence"] < _AMBIGUITY_THRESHOLD
    clarify_text  = None
    final_tier    = result["tier"]
    if was_ambiguous:
        clarify_text = _build_clarifying_probe(message)
        final_tier   = TIER_CLARIFY

    gateway_ms = round((time.monotonic() - t0) * 1000, 1)

    payload = {
        "tier":          final_tier,
        "raw_tier":      result["tier"],
        "confidence":    result["confidence"],
        "method":        result["method"],
        "reasoning":     result.get("reasoning"),
        "signals":       result.get("signals") or [],
        "was_ambiguous": was_ambiguous,
        "clarify":       clarify_text,
        "gateway_ms":    gateway_ms,
    }
    if "llm_latency_ms" in result:
        payload["llm_latency_ms"] = result["llm_latency_ms"]

    if db is not None:
        try:
            await db.intent_classifications.insert_one({
                "message_preview": (message or "")[:60],
                "tier":            final_tier,
                "raw_tier":        result["tier"],
                "confidence":      result["confidence"],
                "method":          result["method"],
                "gateway_ms":      gateway_ms,
                "was_ambiguous":   was_ambiguous,
                "user_id":         user_id,
                "project_id":      project_id,
                "ts":              time.time(),
            })
        except Exception as e:
            logger.debug("intent_classifications log failed: %r", e)

    return payload


# Convenience for callers that want a sync best-effort heuristic only.
def classify_heuristic_sync(message: str) -> dict[str, Any]:
    """Sync, heuristic-only.  Useful for places where awaiting is not
    practical (UI hint computation, etc)."""
    return _classify_heuristic(message)


# ─────────────────────────────────────────────────────────────────────
# Iter 212m-175 — Public JSON classifier helper.
#
# Exposes the SAME call path `_classify_llm` uses (services.llm →
# DeepSeek via OpenRouter, temp=0.0, 2 s hard timeout) but returns the
# raw parsed JSON rather than the tier-shape payload. This lets other
# modules (services.mcp_scoped_tools first) reuse the exact same LLM
# transport WITHOUT copy-pasting classifier plumbing.
# ─────────────────────────────────────────────────────────────────────
async def classify_llm_json(
    prompt: str,
    *,
    timeout: float = 2.0,
    max_tokens: int = 30,
    system: str = "",
) -> Any:
    """Generic DeepSeek JSON classifier — returns parsed JSON or None.

    Contract:
      • Calls services.llm.call_llm with temp=0.0 and a hard timeout.
      • Attempts direct JSON parse first, then plucks the first {...}
        or [...] block out of the response.
      • Returns None on ANY failure (import, timeout, error, parse) so
        the caller must always supply its own safe default.

    Caller MUST NOT depend on any specific shape — that is the whole
    point of a generic helper.
    """
    import asyncio
    try:
        from services.llm import call_llm
    except Exception as e:
        logger.debug("classify_llm_json: llm import failed %r", e)
        return None

    try:
        raw = await asyncio.wait_for(
            call_llm(
                [{"role": "user", "content": prompt}],
                system=system,
                max_tokens=max_tokens,
                temperature=0.0,
            ),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        logger.warning("classify_llm_json: timeout after %.1fs", timeout)
        return None
    except Exception as e:
        logger.warning("classify_llm_json: llm error %r", e)
        return None

    if not raw:
        return None
    text = raw.strip()
    # Try direct parse first.
    try:
        return json.loads(text)
    except Exception:
        pass
    # Pluck the first array / object.
    for pat in (r"\[[^\]]*\]", r"\{[^}]*\}"):
        m = re.search(pat, text)
        if not m:
            continue
        try:
            return json.loads(m.group(0))
        except Exception:
            continue
    return None


__all__ = [
    "classify",
    "classify_heuristic_sync",
    "classify_llm_json",
    "TIER_CASUAL", "TIER_QUERY", "TIER_AGENTIC", "TIER_CLARIFY",
    "VALID_TIERS",
]
