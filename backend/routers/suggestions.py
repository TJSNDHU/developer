"""
routers/suggestions.py — Founder Suggestion Box
================================================

POST /api/aurem-dev/suggestions
    JWT-authenticated.  Body: {text: string} only.  Every identity
    field (user_id, email, tier) comes from the authenticated JWT;
    project_id is resolved server-side to the caller's most-recent
    `cto_projects` row so the client cannot spoof it.

    Rate limit: one suggestion per calendar day (UTC) per user_id,
    enforced via a DB query against `cto_founder_suggestions`
    (`created_at >= today_utc_00:00`).  This is deliberately
    date-based rather than session-based — a session limit is bypassed
    by logout/login, a date-in-DB limit is not.

    Trigger for the LLM pre-analysis is fired as a background task via
    FastAPI's `BackgroundTasks` so the user's POST returns fast.  The
    analysis path (`_analyze_with_groq`) calls Groq directly through
    `services.llm._call_groq`; it does NOT route through
    `services.orchestrator.chat_with_tools` or any Ask Advisor /
    Council chain, so a future Ask Advisor degradation cannot take
    the suggestion box down with it.

Iter 212m-193.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Header
from pydantic import BaseModel, Field

from cto_services.auth import current_dev, require_admin
from cto_services.db import get_db, require_db

router = APIRouter(prefix="/suggestions", tags=["Founder Suggestions"])
logger = logging.getLogger(__name__)


class SubmitBody(BaseModel):
    text: str = Field(..., min_length=8, max_length=4000)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _today_utc_start() -> datetime:
    now = _now_utc()
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


async def _resolve_active_project(db, user_id: str) -> Optional[str]:
    """Server-side project resolution — the client MUST NOT pass
    `project_id` in the body. We pick the caller's most-recent
    `cto_projects` row (matches the frontend `useActiveProject`
    default). Returns None when the user has no projects yet.
    """
    proj = await db.cto_projects.find_one(
        {"user_id": user_id},
        {"_id": 0, "project_id": 1},
        sort=[("created_at", -1)],
    )
    return (proj or {}).get("project_id")


# ══════════════════════════════════════════════════════════════════════
# LLM pre-analysis — Groq direct, ISOLATED from Ask Advisor's Council.
# ══════════════════════════════════════════════════════════════════════
_ANALYSIS_SYSTEM_PROMPT = """You are an ex-CTO reviewing product suggestions
for a small AI-CTO SaaS. Return ONLY strict JSON matching this schema:

{
  "summary":         string,
  "benefits":        [string] (max 3),
  "risks":           [string] (max 3),
  "effort_estimate": "small" | "medium" | "large",
  "overlaps_existing": boolean,
  "overlaps_note":   string,
  "recommendation":  "consider" | "unclear" | "likely_skip"
}

Rules:
- No preamble, no markdown fences, no trailing prose. JSON ONLY.
- `benefits` and `risks` MUST each have AT MOST 3 items.
- If unsure whether the idea overlaps existing features, set
  overlaps_existing=false and put "not_verified" in overlaps_note.
- `recommendation` must be exactly one of the three literal strings.
"""


async def _analyze_with_groq(suggestion_id: str, text: str) -> None:
    """Background task: call Groq, validate JSON shape, persist the
    result. Any failure sets `analysis_failed: true` and leaves
    `llm_analysis: null` — never retries silently, never fabricates.

    Explicit design: this function bypasses `services.orchestrator`
    and the Council routing so an Ask Advisor primary-model outage
    (see iter 212m-192 LongCat degradation) does not cascade into
    the suggestion box.
    """
    from services.llm import _call_groq  # local import — same
    #                                       reason the caller's Groq
    #                                       path uses (SDK optional)
    db = get_db()
    if db is None:
        logger.warning("suggestion analysis: DB not available, skipping")
        return
    try:
        raw = await _call_groq(
            messages=[{"role": "user", "content": text}],
            system=_ANALYSIS_SYSTEM_PROMPT,
            max_tokens=600,
            temperature=0.2,
        )
    except Exception as e:
        logger.warning("suggestion %s: Groq call failed: %r", suggestion_id, e)
        await db.cto_founder_suggestions.update_one(
            {"suggestion_id": suggestion_id},
            {"$set": {
                "llm_analysis":     None,
                "analysis_failed":  True,
                "analysis_error":   f"groq_call: {e!r}"[:200],
                "analyzed_at":      _now_utc().isoformat(),
            }},
        )
        return

    # Strict-JSON validation — no lenient parsing, no field guessing.
    try:
        parsed = json.loads(raw)
        assert isinstance(parsed, dict)
        summary       = parsed["summary"]
        benefits      = parsed["benefits"]
        risks         = parsed["risks"]
        effort        = parsed["effort_estimate"]
        overlaps_bool = parsed["overlaps_existing"]
        overlaps_note = parsed.get("overlaps_note", "")
        recommendation = parsed["recommendation"]

        assert isinstance(summary, str) and summary
        assert isinstance(benefits, list) and len(benefits) <= 3
        assert all(isinstance(b, str) for b in benefits)
        assert isinstance(risks, list) and len(risks) <= 3
        assert all(isinstance(r, str) for r in risks)
        assert effort in {"small", "medium", "large"}
        assert isinstance(overlaps_bool, bool)
        assert isinstance(overlaps_note, str)
        assert recommendation in {"consider", "unclear", "likely_skip"}
    except (AssertionError, KeyError, ValueError, TypeError) as e:
        logger.warning(
            "suggestion %s: LLM returned malformed JSON (err=%r, raw=%r)",
            suggestion_id, e, raw[:400],
        )
        await db.cto_founder_suggestions.update_one(
            {"suggestion_id": suggestion_id},
            {"$set": {
                "llm_analysis":    None,
                "analysis_failed": True,
                "analysis_error":  f"malformed_json: {e!r}"[:200],
                "raw_llm_output":  raw[:1500],
                "analyzed_at":     _now_utc().isoformat(),
            }},
        )
        return

    # Happy path — persist the validated shape.
    await db.cto_founder_suggestions.update_one(
        {"suggestion_id": suggestion_id},
        {"$set": {
            "llm_analysis": {
                "summary":           summary,
                "benefits":          benefits[:3],
                "risks":             risks[:3],
                "effort_estimate":   effort,
                "overlaps_existing": overlaps_bool,
                "overlaps_note":     overlaps_note,
                "recommendation":    recommendation,
                "analyzed_at":       _now_utc().isoformat(),
            },
            "analysis_failed": False,
            "analyzed_at":     _now_utc().isoformat(),
        }},
    )


# ══════════════════════════════════════════════════════════════════════
# Public endpoint — submit a suggestion.
# ══════════════════════════════════════════════════════════════════════
@router.post("")
async def submit_suggestion(
    body: SubmitBody,
    background_tasks: BackgroundTasks,
    authorization: Optional[str] = Header(None),
):
    """Submit a suggestion for the founder. Rate-limited to one per
    UTC day per user_id."""
    user = await current_dev(authorization)
    user_id = user.get("user_id")
    if not user_id:
        raise HTTPException(401, "invalid_session")

    db = require_db()

    # ── Date-based rate limit (NOT session-based) ─────────────────────
    today_start = _today_utc_start()
    already = await db.cto_founder_suggestions.find_one(
        {"user_id": user_id, "created_at": {"$gte": today_start}},
        {"_id": 0, "suggestion_id": 1},
    )
    if already:
        raise HTTPException(
            status_code=429,
            detail="You've already submitted a suggestion today. Try again tomorrow.",
        )

    project_id = await _resolve_active_project(db, user_id)
    suggestion_id = uuid.uuid4().hex[:16]
    now = _now_utc()

    doc = {
        "suggestion_id":   suggestion_id,
        "user_id":         user_id,
        "email":           user.get("email"),
        "tier":            user.get("tier"),
        "project_id":      project_id,
        "text":            body.text.strip(),
        "llm_analysis":    None,     # populated by background task
        "analysis_failed": False,    # flipped True if Groq or JSON fails
        "admin_decision":  "pending",
        "decided_at":      None,
        "decided_by":      None,
        "created_at":      now,
    }
    await db.cto_founder_suggestions.insert_one(doc)

    # Fire-and-forget Groq analysis. Runs after the HTTP response is
    # written so the submit call stays fast (< 200 ms).
    background_tasks.add_task(_analyze_with_groq, suggestion_id, doc["text"])

    return {
        "ok":            True,
        "suggestion_id": suggestion_id,
        "created_at":    now.isoformat(),
        "message":       "Thanks — your suggestion is being analyzed and will "
                          "reach the founder shortly.",
    }


# ══════════════════════════════════════════════════════════════════════
# Admin endpoints (founder-gated via require_admin).
# ══════════════════════════════════════════════════════════════════════
@router.get("/admin/list")
async def list_suggestions_admin(
    status: Optional[str] = None,
    sort:   Optional[str] = "recent",
    authorization: Optional[str] = Header(None),
):
    """Admin listing for the Suggestions panel. `status` filters on
    `admin_decision` (`pending|approved|rejected`); `sort=recent`
    returns newest first."""
    await require_admin(authorization)
    db = require_db()
    q: dict = {}
    if status in {"pending", "approved", "rejected"}:
        q["admin_decision"] = status
    cursor = db.cto_founder_suggestions.find(q, {"_id": 0}) \
        .sort("created_at", -1 if sort != "oldest" else 1) \
        .limit(200)
    rows = []
    async for r in cursor:
        # Normalise datetime → iso string so JSON serialises cleanly.
        for k in ("created_at", "decided_at"):
            v = r.get(k)
            if isinstance(v, datetime):
                r[k] = v.isoformat()
        rows.append(r)
    return {"suggestions": rows, "count": len(rows)}


class DecideBody(BaseModel):
    decision: str = Field(..., pattern="^(approved|rejected)$")


@router.post("/admin/{sid}/decide")
async def decide_suggestion(
    sid: str,
    body: DecideBody,
    authorization: Optional[str] = Header(None),
):
    """Approve or reject a suggestion. Records `decided_by` as the
    calling admin's user_id for an audit trail."""
    admin = await require_admin(authorization)
    db = require_db()
    r = await db.cto_founder_suggestions.update_one(
        {"suggestion_id": sid},
        {"$set": {
            "admin_decision": body.decision,
            "decided_at":     _now_utc().isoformat(),
            "decided_by":     admin.get("user_id"),
        }},
    )
    if r.matched_count == 0:
        raise HTTPException(404, "suggestion_not_found")
    return {"ok": True, "suggestion_id": sid, "decision": body.decision}
