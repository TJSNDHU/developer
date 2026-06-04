"""
services/mode_classifier.py — Confidence-scored mode classification.

The existing `routers.chat.classify_intent()` returns a single char A-F
based on regex rules in a fixed priority order. That works well in
practice but tells the caller nothing about HOW confident the decision
was — so the UI can't ask the user "did you mean a code change (C) or
a discussion (B)?" when the message is ambiguous.

This module wraps the existing classifier and adds:
  • per-mode score (0.0 – 1.0, normalized)
  • primary mode + confidence
  • `needs_confirm` boolean (true when confidence < 0.55)

It does NOT replace the rule-based classifier — that one is still the
source of truth for the primary mode, because its regex rules have
been hand-tuned over many iterations. We just attach signal counts on
top so the UI gets richer information.
"""
from __future__ import annotations

import re
from typing import Optional, Any


_NEEDS_CONFIRM_THRESHOLD = 0.55


# Signal vocabularies — each match adds 1 point to the agent's bucket.
# Kept deliberately short + high-precision; over-broad keywords erode
# confidence by spreading score across modes.
_SIGNALS_C = (
    "add", "build", "create", "implement", "write", "fix", "update",
    "change", "refactor", "ship", "commit", "push", "deploy", "scaffold",
    "rename", "delete", "move", "extract", "wire",
)
_SIGNALS_D = (
    "error", "bug", "broken", "not working", "failing", "exception",
    "crash", "stuck", "doesn't work", "doesn't load", "stack trace",
    "traceback", "422", "500", "404", "403", "502", "503", "cors",
    "undefined", "null pointer", "nullpointer", "regression",
)
_SIGNALS_E = (
    "audit", "security scan", "review all", "check vulnerabilities",
    "scan repo", "security review", "ast scan", "secrets leak",
    "vuln", "owasp",
)
_SIGNALS_B = (
    "should i", "best way", "how would", "recommend", "advise",
    "what if", "which is better", "compare", "pros and cons",
    "thoughts on", "opinion on", "vs ", " vs.",
)
_SIGNALS_F = (
    "launch", "tweet", "twitter post", "marketing", "headline", "tagline",
    "positioning", "gtm", "copy for", "pitch", "competitor", "value prop",
)


def _count_hits(msg: str, vocab: tuple[str, ...]) -> int:
    """Case-insensitive substring count for any vocab term in msg."""
    low = msg.lower()
    return sum(1 for w in vocab if w in low)


def classify_intent_v2(
    message: str,
    f12_payload: Optional[dict] = None,
) -> dict[str, Any]:
    """Confidence-scored classification.

    Returns:
        {
          "mode":         "A".."F",
          "confidence":   0.0..1.0,
          "scores":       {"A":..., "B":..., "C":..., "D":..., "E":..., "F":...},
          "needs_confirm": bool,    # True when ambiguous (confidence < 0.55)
        }
    """
    msg = (message or "").strip()

    # Hard short-circuit: F12 payload with real errors → Mode D, 100% confident.
    # The user pressed F12, that's unambiguous intent.
    if f12_payload and (
        f12_payload.get("console_errors") or f12_payload.get("network_errors")
    ):
        return {
            "mode": "D",
            "confidence": 1.0,
            "scores": {"A": 0.0, "B": 0.0, "C": 0.0, "D": 1.0, "E": 0.0, "F": 0.0},
            "needs_confirm": False,
            "f12_forced": True,
        }

    # Short message / greeting → Mode A with high confidence.
    # The existing `classify_intent` already does this via _GREETING regex.
    # Mirror it here so we don't depend on import-time order quirks.
    if len(msg.split()) < 4 and re.match(
        r"^(hi|hello|hey|yo|sup|namaste|kya haal|ok|thanks|thx|cool|nice|wow)\b",
        msg.lower(),
    ):
        return {
            "mode": "A",
            "confidence": 0.95,
            "scores": {"A": 0.95, "B": 0.01, "C": 0.01, "D": 0.01, "E": 0.01, "F": 0.01},
            "needs_confirm": False,
        }

    # Count signals per mode. A always gets a base 0.5 so a totally
    # off-vocab message (e.g. "what's up?") doesn't end up at 0/0/0/0
    # and divide-by-zero.
    raw = {
        "A": 0.5,  # base — chat fallback
        "B": _count_hits(msg, _SIGNALS_B) * 1.5,   # weighted: explicit "should i"
        "C": _count_hits(msg, _SIGNALS_C) * 1.0,
        "D": _count_hits(msg, _SIGNALS_D) * 1.8,   # weighted: bug signals are strong
        "E": _count_hits(msg, _SIGNALS_E) * 2.0,   # weighted: audit is rare + explicit
        "F": _count_hits(msg, _SIGNALS_F) * 1.5,
    }

    # Length-based A boost: very short messages without code/error/build
    # signals default to chat.
    if len(msg.split()) < 6 and raw["C"] == 0 and raw["D"] == 0 and raw["E"] == 0:
        raw["A"] += 1.5

    total = sum(raw.values()) or 1.0
    scores = {k: round(v / total, 3) for k, v in raw.items()}

    primary = max(scores, key=scores.get)
    confidence = scores[primary]

    return {
        "mode": primary,
        "confidence": confidence,
        "scores": scores,
        "needs_confirm": confidence < _NEEDS_CONFIRM_THRESHOLD,
        "f12_forced": False,
    }


# Rolling-window telemetry — last 100 classifications kept in MongoDB
# for ambiguity tuning. Capped via a delete-oldest sweep on each insert
# so the collection never grows unbounded.
_TELEMETRY_WINDOW = 100


async def log_classification(db, result: dict, message: str) -> None:
    """Fire-and-forget. Caller should wrap in asyncio.create_task or
    a swallowing try/except — failures here MUST NOT break the chat
    path. Stores only the result metadata + message length (not the
    message itself, for privacy)."""
    if db is None or not result:
        return
    try:
        import time as _time
        await db["mode_classifications"].insert_one({
            "mode":          result.get("mode"),
            "confidence":    float(result.get("confidence", 0.0)),
            "scores":        result.get("scores", {}),
            "needs_confirm": bool(result.get("needs_confirm", False)),
            "f12_forced":    bool(result.get("f12_forced", False)),
            "msg_len":       len(message or ""),
            "ts":            _time.time(),
        })
        count = await db["mode_classifications"].count_documents({})
        if count > _TELEMETRY_WINDOW:
            # Delete the oldest entries in one batched call instead of
            # looping count-100 times — cheap on a 100-doc collection.
            overflow = count - _TELEMETRY_WINDOW
            old_docs = await db["mode_classifications"].find(
                {}, {"_id": 1}
            ).sort("ts", 1).limit(overflow).to_list(overflow)
            if old_docs:
                await db["mode_classifications"].delete_many(
                    {"_id": {"$in": [d["_id"] for d in old_docs]}}
                )
    except Exception:
        # swallow — telemetry must never break the chat
        return


# Ops-intent detector — separate from mode classification because these
# requests don't belong to any of A-F. They're infra ops AUREM cannot
# (and shouldn't) run on the user's own server. Instead of letting ORA
# fabricate bash commands, the frontend renders a deep-link to /admin/ops.
_OPS_VOCAB = (
    "restart supervisor", "supervisor restart", "supervisorctl",
    "systemctl restart", "service restart", "reboot server",
    "free disk space", "disk full", "no space left",
    "mongod is down", "mongo connection refused", "mongodb crashed",
    "free up memory", "out of memory", "oom killed",
    "ssh into", "log into server", "check server logs",
    "nginx restart", "caddy restart", "docker restart",
)


def looks_like_ops_request(message: str) -> bool:
    """True when the user is asking AUREM to perform a server operation
    we can't reach. Caller should redirect to /admin/ops instead of
    letting ORA fabricate bash."""
    if not message:
        return False
    low = message.lower()
    return any(phrase in low for phrase in _OPS_VOCAB)
