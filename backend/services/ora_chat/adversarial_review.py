"""
services/ora_chat/adversarial_review.py — Iter 268

Draft (DeepSeek V3) + hostile review (GLM-5.2) on HIGH_STAKES turns
only. Cross-family reviewer (GLM, not R1) — sibling models share
blind spots. Flag-only: the reviewer NEVER rewrites; one regen max.

Anti-hallucination guard on the reviewer itself: every flag's quote
is string-checked verbatim against the draft — fake quote = reviewer
hallucinated = flag dropped + logged to `ora_reviewer_errors`.

Budget guard: within $0.50 of daily cap → skip review (logged), never
block the response. Metrics per reviewed turn → `ora_review_log`.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional

from cto_services.db import get_db

from . import cost_tracker
from .providers import one_shot
from .router import fallback_route, resolve

logger = logging.getLogger(__name__)

_BUDGET_HEADROOM_USD = float(os.getenv("ORA_REVIEW_BUDGET_HEADROOM_USD", "0.50"))
_FLAG_TYPES = ("FABRICATED", "UNVERIFIED", "OVERSTATED",
               "CONTRADICTS_CONTEXT", "IGNORED_TASK")
_HARD_TYPES = ("FABRICATED", "CONTRADICTS_CONTEXT", "IGNORED_TASK")

REVIEWER_SYSTEM = (
    "You are a hostile reviewer. Your only job is to find what is wrong, "
    "unverified, or overstated in the draft below. You get credit for "
    "finding real problems. You get zero credit for approving.\n\n"
    "For each problem output an object:\n"
    '  { "quote": "<exact sentence copied verbatim from the draft>",\n'
    '    "type": "FABRICATED | UNVERIFIED | OVERSTATED | '
    'CONTRADICTS_CONTEXT | IGNORED_TASK",\n'
    '    "reason": "<one line>" }\n\n'
    "Rules:\n"
    "- Any specific claim (number, file, date, capability) not supported "
    "by the provided context must be flagged. No benefit of the doubt.\n"
    "- IGNORED_TASK: the draft failed to do something the user "
    "EXPLICITLY asked for (e.g. asked for a scan but no scan output is "
    "present). For this type only, \"quote\" should be the ignored "
    "request copied from the USER QUERY. Max ONE such flag.\n"
    "- The \"quote\" field MUST be copied character-for-character from "
    "the draft — never paraphrase, never trim mid-word.\n"
    "- Do NOT suggest rewrites. Do NOT add new claims. Do NOT summarize.\n"
    '- If genuinely nothing is flaggable, output exactly: {"result":"PASS"}\n'
    "Output a JSON array of flag objects (or the PASS object). JSON only."
)


def trigger_reason(labels: Optional[list],
                   grounding: Optional[dict]) -> Optional[str]:
    """Which turns get reviewed. Everything else stays single-pass."""
    if "HIGH_STAKES" in (labels or []):
        return "high_stakes_label"
    if grounding and grounding.get("unverified"):
        return "grounding_unverified"
    return None


def corrective_prompt(hard_flags: list[dict]) -> str:
    ignored = [f for f in hard_flags if f["type"] == "IGNORED_TASK"]
    claims = [f for f in hard_flags if f["type"] != "IGNORED_TASK"]
    parts = []
    if claims:
        quotes = "; ".join(f'"{f["quote"]}"' for f in claims[:6])
        parts.append("Your previous draft contained these unsupported "
                      f"claims: {quotes}. Remove them or explicitly mark "
                      "them unverified. Do not defend them.")
    if ignored:
        parts.append("Your draft also FAILED to address what the user "
                      f"explicitly asked: \"{ignored[0]['quote'][:200]}\" — "
                      "answer what was asked, or state clearly why you "
                      "cannot.")
    parts.append("Rewrite the full answer.")
    return " ".join(parts)


def _parse_flags(text: str) -> tuple[list[dict], bool]:
    """Returns (flags, parse_ok). PASS → ([], True)."""
    s = (text or "").strip()
    s = s.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        data = json.loads(s)
    except ValueError:
        return [], False
    if isinstance(data, dict):
        if data.get("result") == "PASS":
            return [], True
        data = data.get("flags") or []
    if not isinstance(data, list):
        return [], False
    out: list[dict] = []
    for f in data:
        if isinstance(f, dict) and f.get("quote") \
                and f.get("type") in _FLAG_TYPES:
            out.append({"quote":  str(f["quote"])[:400],
                        "type":   f["type"],
                        "reason": str(f.get("reason") or "")[:200]})
    return out, True


def verify_quotes(flags: list[dict], draft: str,
                  query: str = "") -> tuple[list, list]:
    """Deterministic guard on the REVIEWER: quote not found verbatim in
    the draft = the reviewer hallucinated = drop that flag.
    IGNORED_TASK quotes come from the USER QUERY instead (an omission
    has nothing to quote in the draft) — capped at one."""
    kept, dropped = [], []
    d = draft or ""
    q = query or ""
    seen_ignored = False
    for f in flags:
        if f["type"] == "IGNORED_TASK":
            if not seen_ignored and (not f["quote"] or f["quote"] in q
                                      or f["quote"] in d):
                kept.append(f)
                seen_ignored = True
            else:
                dropped.append(f)
            continue
        (kept if f["quote"] in d else dropped).append(f)
    return kept, dropped


async def _log_reviewer_errors(user_id: str, session_id: str,
                               dropped: list, raw: str) -> None:
    if not dropped:
        return
    try:
        db = get_db()
        if db is None:
            return
        await db.ora_reviewer_errors.insert_one({
            "user_id": user_id, "session_id": session_id,
            "dropped_flags": dropped[:10],
            "raw_head": (raw or "")[:2000],
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as e:                                   # noqa: BLE001
        logger.warning("reviewer_errors log failed: %r", e)


async def run_review(*, user_id: str, session_id: str, query: str,
                     draft: str, context: str,
                     reason: str = "") -> dict:
    """One hostile-review pass. Never raises. Returns:
    {flags, hard, soft, dropped, latency_s, usage, cost_usd,
     skipped, passed}"""
    empty = {"flags": [], "hard": [], "soft": [], "dropped": 0,
             "latency_s": 0.0, "usage": {}, "cost_usd": 0.0,
             "skipped": None, "passed": True}
    if not (draft or "").strip():
        return {**empty, "skipped": "empty_draft"}

    # Budget guard — same graceful-degrade pattern as deep-research.
    try:
        b = await cost_tracker.budget_status()
        headroom = (float(b.get("day_cap_usd") or 0)
                    - float(b.get("day_spent_usd") or 0))
        if b.get("mode") in ("economy", "spike_hard_stop") \
                or headroom <= _BUDGET_HEADROOM_USD:
            logger.info("ora review skipped: budget (headroom=%.2f mode=%s)",
                        headroom, b.get("mode"))
            return {**empty, "skipped": "review_skipped_budget"}
    except Exception as e:                                   # noqa: BLE001
        logger.warning("review budget check failed: %r", e)

    cfg = resolve(fallback_route())   # GLM-5.2 — cross-family reviewer
    user_prompt = (
        f"USER QUERY:\n{(query or '')[:2000]}\n\n"
        "GROUNDING CONTEXT the drafter saw (judge the draft ONLY against "
        "this — is the draft supported by what the drafter actually "
        f"saw?):\n{(context or '(none — no retrieved context this turn)')[:8000]}\n\n"
        f"DRAFT TO REVIEW:\n{draft[:8000]}"
    )
    t0 = time.time()
    text, usage, err = await one_shot(
        model=cfg["model"],
        messages=[{"role": "system", "content": REVIEWER_SYSTEM},
                  {"role": "user",   "content": user_prompt}],
        temperature=0.0, top_p=0.9, presence_penalty=0.0,
        max_tokens=1024,
    )
    latency = round(time.time() - t0, 2)
    cost = 0.0
    if usage:
        try:
            cost = await cost_tracker.log_call(
                user_id=user_id, session_id=session_id, route="review",
                model=cfg["model"], temperature=0.0,
                input_tokens=usage.get("input_tokens", 0),
                output_tokens=usage.get("output_tokens", 0),
                error=err)
        except Exception:                                    # noqa: BLE001
            pass
    if err or not text:
        return {**empty, "skipped": f"reviewer_error:{err or 'empty'}",
                "latency_s": latency, "cost_usd": cost}
    flags, parse_ok = _parse_flags(text)
    if not parse_ok:
        return {**empty, "skipped": "reviewer_unparseable",
                "latency_s": latency, "cost_usd": cost}
    kept, dropped = verify_quotes(flags, draft, query)
    if dropped:
        await _log_reviewer_errors(user_id, session_id, dropped, text)
    hard = [f for f in kept if f["type"] in _HARD_TYPES]
    soft = [f for f in kept if f["type"] not in _HARD_TYPES]
    return {"flags": kept, "hard": hard, "soft": soft,
            "dropped": len(dropped), "latency_s": latency,
            "usage": usage or {}, "cost_usd": cost,
            "skipped": None, "passed": not kept}


async def log_metrics(*, user_id: str, session_id: str, route: str,
                      reason: str, review: dict,
                      regen_fired: bool, regen_cleared: bool) -> None:
    """Per-reviewed-turn metrics row → weekly rubber-stamp /
    false-positive / reviewer-hallucination checks."""
    try:
        db = get_db()
        if db is None:
            return
        flags = review.get("flags") or []
        await db.ora_review_log.insert_one({
            "user_id": user_id, "session_id": session_id, "route": route,
            "reason": reason, "skipped": review.get("skipped"),
            "flags_count": len(flags),
            "types": sorted({f["type"] for f in flags}),
            "regen_fired": regen_fired,
            "regen_cleared": regen_cleared,
            "reviewer_hallucination_count": review.get("dropped", 0),
            "latency_added_s": review.get("latency_s", 0.0),
            "cost_usd": review.get("cost_usd", 0.0),
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as e:                                   # noqa: BLE001
        logger.warning("review metrics log failed: %r", e)
