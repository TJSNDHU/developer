"""
services/ora_chat/hallucination_classifier.py — Iter 212m-255

Batch job that reads unreviewed rows from `ora_hallucination_log`
and asks DeepSeek V3: "What common pattern is in these hallucination
cases?" Detected patterns are stored in `ora_hallucination_patterns`
with `status: "pending"` — a human MUST approve before a candidate
rule is promoted into house rules.

Trigger:
  - Cron / manual admin endpoint POST /api/aurem-dev/ora-chat/
    hallucination-patterns/classify-now
  - Automatically kicks off when unreviewed_count >= _BATCH_TRIGGER

The classifier NEVER auto-adds a rule to `ora_chat_house_rules`.
Human-in-the-loop is enforced at the API layer.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from cto_services.db import get_db
from services.ora_chat.providers import one_shot
from services.ora_chat.router import resolve

logger = logging.getLogger(__name__)

_BATCH_TRIGGER   = 20      # unreviewed rows before we auto-classify
_MIN_OCCURRENCES = 3       # pattern must recur this often to be a candidate
_BATCH_MAX       = 40      # cap the batch so the LLM call stays cheap


async def unreviewed_count() -> int:
    try:
        db = get_db()
    except Exception:
        return 0
    return await db.ora_hallucination_log.count_documents({"reviewed": False})


async def _pull_recent(limit: int = _BATCH_MAX) -> list[dict]:
    db = get_db()
    rows = await (db.ora_hallucination_log
                  .find({"reviewed": False},
                        {"_id": 0, "query": 1, "reply": 1,
                         "ungrounded": 1, "route": 1, "created_at": 1})
                  .sort("created_at", -1)
                  .limit(limit)
                  .to_list(limit))
    return rows


def _build_prompt(rows: list[dict]) -> str:
    """Compact JSONL representation — keep prompt lean."""
    lines = []
    for i, r in enumerate(rows, 1):
        lines.append(json.dumps({
            "case":       i,
            "query":      (r.get("query") or "")[:400],
            "ungrounded": (r.get("ungrounded") or [])[:10],
            "route":      r.get("route"),
        }, ensure_ascii=False))
    return "\n".join(lines)


_PROMPT_TEMPLATE = (
    "You are an internal QA classifier for the ORA Chat system.\n"
    "Below are recent cases where the ORA assistant made a specific "
    "claim (file path / function name / test name) that could NOT be "
    "verified against the retrieved context or the AUREM repo tree.\n\n"
    "Each case shows the user's query, the ungrounded tokens the "
    "system flagged, and the route that fired.\n\n"
    "TASK: Find the ≤3 STRONGEST recurring patterns (each seen in "
    ">= 3 cases). For each, output:\n"
    "  - pattern_name: short kebab-case slug (e.g. 'fabricated-test-file')\n"
    "  - description: one sentence in plain English + Hinglish\n"
    "  - example_cases: list of case numbers from the input\n"
    "  - proposed_rule: a single sentence rule that would prevent this "
    "class of hallucination, phrased as an imperative addition to the "
    "ORA safety layer (starts with 'NEVER' or 'ALWAYS' or 'When ... '). "
    "Keep it OBJECTIVE and TESTABLE.\n\n"
    "Respond with ONLY a JSON array. Empty array [] if no pattern "
    "recurs >= 3 times.\n\n"
    "CASES:\n{cases}\n"
)


async def classify_batch(force: bool = False) -> dict:
    """Run one classification pass. Returns summary dict.
    No-ops if the unreviewed queue is below the trigger and `force` is
    False.
    """
    try:
        db = get_db()
    except Exception as e:
        return {"ok": False, "error": f"db_unavailable:{e}"}
    n = await unreviewed_count()
    if not force and n < _BATCH_TRIGGER:
        return {"ok": True, "skipped": True, "unreviewed": n,
                 "reason": f"below_trigger_of_{_BATCH_TRIGGER}"}
    rows = await _pull_recent(_BATCH_MAX)
    if not rows:
        return {"ok": True, "skipped": True, "unreviewed": 0}

    cfg = resolve("general")  # DeepSeek V3 — cheapest capable
    prompt = _PROMPT_TEMPLATE.format(cases=_build_prompt(rows))
    text, usage, err = await one_shot(
        model=cfg["model"],
        messages=[{"role": "user", "content": prompt}],
        temperature=0.15, top_p=cfg["top_p"],
        presence_penalty=0.0, max_tokens=1500,
    )
    if err:
        return {"ok": False, "error": err, "cases_reviewed": len(rows)}

    patterns = _parse_patterns(text or "")
    # Filter: only keep patterns that hit >= _MIN_OCCURRENCES cases
    strong = [p for p in patterns
              if len(p.get("example_cases") or []) >= _MIN_OCCURRENCES]

    now = datetime.now(timezone.utc).isoformat()
    inserted: list[str] = []
    for p in strong:
        slug = (p.get("pattern_name") or "").strip().lower()
        if not slug:
            continue
        # Dedup on slug — bump `seen_count` instead of duplicating.
        existing = await db.ora_hallucination_patterns.find_one({"slug": slug})
        if existing:
            await db.ora_hallucination_patterns.update_one(
                {"slug": slug},
                {"$inc": {"seen_count": 1},
                 "$set": {"last_seen_at": now}},
            )
            continue
        await db.ora_hallucination_patterns.insert_one({
            "slug":          slug,
            "description":   p.get("description", "")[:600],
            "proposed_rule": p.get("proposed_rule", "")[:800],
            "example_cases": (p.get("example_cases") or [])[:20],
            "cases_reviewed": len(rows),
            "seen_count":    1,
            "status":        "pending",   # pending / approved / rejected
            "created_at":    now,
            "last_seen_at":  now,
            "approved_at":   None,
            "approved_by":   None,
        })
        inserted.append(slug)

    # Mark the rows we consumed as reviewed so we don't reclassify them.
    row_queries = [r["query"] for r in rows]
    if row_queries:
        await db.ora_hallucination_log.update_many(
            {"query": {"$in": row_queries}, "reviewed": False},
            {"$set": {"reviewed": True,
                       "reviewed_at": now,
                       "classifier_usage": usage or {}}},
        )
    return {"ok": True, "cases_reviewed": len(rows),
             "patterns_found": len(patterns),
             "candidates_inserted": len(inserted),
             "usage": usage or {}}


def _parse_patterns(text: str) -> list[dict]:
    """Extract the JSON array from the LLM reply — tolerates ```json
    fences and surrounding prose."""
    s = text.strip()
    if s.startswith("```"):
        # Strip the fence + optional language marker
        parts = s.split("```")
        for p in parts:
            p = p.strip()
            if p.startswith("["):
                s = p; break
            if p.startswith("json"):
                s = p[4:].strip()
                break
    # Find the first `[` and last `]`
    lb, rb = s.find("["), s.rfind("]")
    if lb == -1 or rb <= lb:
        return []
    try:
        parsed = json.loads(s[lb:rb+1])
        return parsed if isinstance(parsed, list) else []
    except json.JSONDecodeError:
        return []


# ─── Admin surfaces (called by the router) ───────────────────────
async def list_pending_patterns() -> list[dict]:
    db = get_db()
    rows = await (db.ora_hallucination_patterns
                  .find({"status": "pending"}, {"_id": 0})
                  .sort("seen_count", -1)
                  .limit(50)
                  .to_list(50))
    return rows


async def approve_pattern(slug: str, user_id: str,
                            admin_email: str,
                            new_rule_text: Optional[str] = None) -> dict:
    """Promote a candidate pattern to an ACTIVE house rule.
    A human MUST call this — we never auto-approve.
    `user_id` is the founder's dev_users id (house_rules are per-user).
    """
    db = get_db()
    pat = await db.ora_hallucination_patterns.find_one({"slug": slug})
    if not pat:
        return {"ok": False, "error": "not_found"}
    if pat.get("status") == "approved":
        return {"ok": False, "error": "already_approved"}
    rule_text = (new_rule_text or pat.get("proposed_rule") or "").strip()
    if not rule_text:
        return {"ok": False, "error": "empty_rule"}
    now = datetime.now(timezone.utc).isoformat()
    # Append (not replace) into the founder's house_rules effective
    # text — the safety layer's `assemble_system_prompt` picks it up.
    from services.ora_chat import house_rules as ora_house_rules
    current = await ora_house_rules.get_effective_text(user_id) or ""
    new_text = (current + "\n\n"
                 + f"# Auto-promoted rule ({slug}, approved {now[:10]} by {admin_email})\n"
                 + rule_text).strip()
    await ora_house_rules.update(user_id, new_text)
    await db.ora_hallucination_patterns.update_one(
        {"slug": slug},
        {"$set": {"status": "approved",
                   "approved_at": now,
                   "approved_by": admin_email,
                   "approved_rule": rule_text}},
    )
    return {"ok": True, "slug": slug, "promoted_rule": rule_text}


async def reject_pattern(slug: str, admin_email: str,
                          reason: Optional[str] = None) -> dict:
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    r = await db.ora_hallucination_patterns.update_one(
        {"slug": slug},
        {"$set": {"status": "rejected",
                   "rejected_at": now,
                   "rejected_by": admin_email,
                   "reject_reason": (reason or "")[:400]}},
    )
    return {"ok": r.matched_count > 0, "slug": slug}
