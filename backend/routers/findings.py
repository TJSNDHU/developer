"""
routers/findings.py  —  Directive Session 3 · Part D backend
=============================================================

Read + control endpoints for the notification-strip backlog.

  GET  /api/aurem-dev/findings/backlog?project_id=…
       Returns the eligible-to-surface backlog for the strip:
       critical/high, status=open, exposure_count < 4, not dismissed
       right now, not snoozed. Also returns strip-decision metadata
       (should_show, reason, cadence check) so the frontend does no
       policy math itself.

  POST /api/aurem-dev/findings/dismiss
       Body: { project_id, finding_batch_id }
       Persists a 24-hour dismissal keyed on the batch id — cross
       device / cross tab because the row lives in
       `cto_notification_dismissals` with a TTL index.

  POST /api/aurem-dev/findings/{finding_id}/snooze
       Body: { days: 7 }
       Sets `snoozed_until` on the finding and resets its idle
       clock so the 30-day backlog reminder starts over.

  POST /api/aurem-dev/findings/{finding_id}/dismiss
       Individual finding dismiss (Directive: "review panel can
       dismiss individually"). Sets status="fixed" so it drops out
       of the backlog query permanently.

  POST /api/aurem-dev/findings/expose-batch
       Body: { project_id, finding_ids: [...] }
       Called by the strip immediately BEFORE showing the reminder.
       Increments exposure_count on each id (capped at 4) and stamps
       last_exposed_at so the once-per-week cadence check works.
       If exposure_count hits 4 → status flips to "aged-out".

Every endpoint is founder-gated via the standard `current_dev` +
project-ownership check (mirrors codebase_health / security_scan
patterns). No leaking of another user's findings.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel

from cto_services.auth import current_dev
from cto_services.db import get_db

router = APIRouter(prefix="/findings", tags=["Findings Backlog"])
logger = logging.getLogger(__name__)

# ── Policy constants (mirror Directive Part D) ────────────────────────
BACKLOG_IDLE_DAYS      = 30
BACKLOG_WEEKLY_CAP     = timedelta(days=7)
BACKLOG_MAX_EXPOSURES  = 4
DISMISS_TTL_SECONDS    = 24 * 60 * 60   # 24 h
SNOOZE_DEFAULT_DAYS    = 7


# ══════════════════════════════════════════════════════════════════════
# Ownership helper
# ══════════════════════════════════════════════════════════════════════
async def _assert_owns_project(db, user_id: str, project_id: str) -> None:
    p = await db.cto_projects.find_one({"project_id": project_id})
    if not p:
        raise HTTPException(404, "project_not_found")
    if p.get("user_id") and p.get("user_id") != user_id:
        raise HTTPException(403, "not_your_project")


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(dt) -> Optional[datetime]:
    """MongoDB returns naive datetimes (all stored as UTC by
    convention). Coerce to tz-aware so comparisons against
    _now_utc() don't raise TypeError."""
    if dt is None:
        return None
    if isinstance(dt, datetime) and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _batch_id_for(findings: list[dict]) -> str:
    """Deterministic batch id from a sorted finding-id list — same
    findings dismissed today match tomorrow's re-surface even if the
    query returns them in a different order."""
    if not findings:
        return "empty"
    ids = sorted({(f.get("finding_id") or "") for f in findings})
    return f"batch::{len(ids)}::{'|'.join(ids)[:200]}"


# ══════════════════════════════════════════════════════════════════════
# GET /findings/backlog
# ══════════════════════════════════════════════════════════════════════
@router.get("/backlog")
async def backlog_list(
    project_id: str,
    authorization: Optional[str] = Header(None),
):
    user = await current_dev(authorization)
    user_id = user["user_id"]
    db = get_db()
    await _assert_owns_project(db, user_id, project_id)

    now = _now_utc()
    idle_cutoff = now - timedelta(days=BACKLOG_IDLE_DAYS)

    # Base query — findings that qualify to eventually surface. We
    # apply the cadence + dismiss check AFTER fetching so the strip
    # payload can explain why it did or didn't show up.
    cursor = db.cto_open_findings.find({
        "user_id":         user_id,
        "project_id":      project_id,
        "status":          "open",
        "severity":        {"$in": ["critical", "high"]},
        "exposure_count":  {"$lt": BACKLOG_MAX_EXPOSURES},
        "$or": [
            {"snoozed_until": {"$exists": False}},
            {"snoozed_until": None},
            {"snoozed_until": {"$lt": now}},
        ],
    }).sort([("severity", 1), ("last_seen_at", -1)]).limit(500)

    findings_all = await cursor.to_list(500)
    # Drop MongoDB _id so the payload is JSON-serialisable and keeps
    # our internal fields stable.
    for f in findings_all:
        f.pop("_id", None)

    # Eligible-for-reminder = idle > 30 days.
    eligible = [
        f for f in findings_all
        if (_as_utc(f.get("last_seen_at")) or now) < idle_cutoff
    ]

    critical_count = sum(1 for f in eligible if f.get("severity") == "critical")
    high_count     = sum(1 for f in eligible if f.get("severity") == "high")

    # Once-per-week cadence — check the newest last_exposed_at on the
    # eligible batch. If ANY of them was exposed within the last 7
    # days, the whole batch waits.
    last_exposure = max(
        (_as_utc(f.get("last_exposed_at")) for f in eligible
         if f.get("last_exposed_at")),
        default=None,
    )
    within_cadence_window = bool(
        last_exposure and (now - last_exposure) < BACKLOG_WEEKLY_CAP
    )

    # Dismiss check.
    batch_id = _batch_id_for(eligible)
    dismissal = await db.cto_notification_dismissals.find_one({
        "user_id":         user_id,
        "project_id":      project_id,
        "finding_batch_id": batch_id,
    })
    dismissed_active = bool(
        dismissal
        and (_as_utc(dismissal.get("expires_at")) or now) > now
    )

    should_show = bool(
        eligible
        and not within_cadence_window
        and not dismissed_active
    )
    reason = "ok" if should_show else (
        "no_eligible_findings" if not eligible
        else "cadence_wait_weekly"     if within_cadence_window
        else "dismissed_active"        if dismissed_active
        else "unknown"
    )

    return {
        "ok":                True,
        "project_id":        project_id,
        "critical_count":    critical_count,
        "high_count":        high_count,
        "total_open":        len(findings_all),
        "eligible_for_strip": len(eligible),
        "eligible":          eligible[:50],   # cap payload size
        "batch_id":          batch_id,
        "should_show_strip": should_show,
        "reason":            reason,
        "last_exposure":     last_exposure.isoformat() if last_exposure else None,
    }


# ══════════════════════════════════════════════════════════════════════
# POST /findings/expose-batch
# Called immediately BEFORE the strip renders — bumps exposure_count
# and stamps last_exposed_at so cadence + auto-archive work.
# ══════════════════════════════════════════════════════════════════════
class ExposeBody(BaseModel):
    project_id: str
    finding_ids: list[str]


@router.post("/expose-batch")
async def expose_batch(body: ExposeBody,
                       authorization: Optional[str] = Header(None)):
    user = await current_dev(authorization)
    user_id = user["user_id"]
    db = get_db()
    await _assert_owns_project(db, user_id, body.project_id)

    if not body.finding_ids:
        return {"ok": True, "exposed": 0, "aged_out": 0}

    now = _now_utc()
    exposed = 0
    aged_out = 0
    # Iter 212m-228 — N+1 fix. Was up to 100 sequential find_one calls.
    # Prefetch every existing row in ONE `$in` query, then do the
    # per-item update logic locally.
    fids = list(body.finding_ids[:100])
    existing_map: dict[str, dict] = {}
    if fids:
        cur = db.cto_open_findings.find(
            {"user_id": user_id, "project_id": body.project_id,
             "finding_id": {"$in": fids}},
            projection={"finding_id": 1, "exposure_count": 1, "status": 1},
        )
        async for row in cur:
            fk = row.get("finding_id")
            if fk:
                existing_map[fk] = row

    for fid in fids:   # hard cap so a bad client can't loop us
        existing = existing_map.get(fid)
        if not existing:
            continue
        if existing.get("status") == "aged-out":
            continue
        new_count = min(BACKLOG_MAX_EXPOSURES,
                        int(existing.get("exposure_count") or 0) + 1)
        update: dict = {
            "$set": {
                "exposure_count":  new_count,
                "last_exposed_at": now,
            },
        }
        if new_count >= BACKLOG_MAX_EXPOSURES:
            update["$set"]["status"] = "aged-out"
            aged_out += 1
        await db.cto_open_findings.update_one(
            {"user_id": user_id, "project_id": body.project_id,
             "finding_id": fid},
            update,
        )
        exposed += 1

    return {"ok": True, "exposed": exposed, "aged_out": aged_out}


# ══════════════════════════════════════════════════════════════════════
# POST /findings/dismiss (batch, 24h TTL)
# ══════════════════════════════════════════════════════════════════════
class DismissBatchBody(BaseModel):
    project_id: str
    finding_batch_id: str


@router.post("/dismiss")
async def dismiss_batch(body: DismissBatchBody,
                        authorization: Optional[str] = Header(None)):
    user = await current_dev(authorization)
    user_id = user["user_id"]
    db = get_db()
    await _assert_owns_project(db, user_id, body.project_id)

    now = _now_utc()
    expires = now + timedelta(seconds=DISMISS_TTL_SECONDS)
    # Upsert semantics: extending the dismissal is fine.
    await db.cto_notification_dismissals.update_one(
        {"user_id": user_id, "project_id": body.project_id,
         "finding_batch_id": body.finding_batch_id},
        {"$set": {
            "user_id":          user_id,
            "project_id":       body.project_id,
            "finding_batch_id": body.finding_batch_id,
            "dismissed_at":     now,
            "expires_at":       expires,
        }},
        upsert=True,
    )
    return {
        "ok":         True,
        "dismissed":  True,
        "expires_at": expires.isoformat(),
    }


# ══════════════════════════════════════════════════════════════════════
# POST /findings/snooze
# ══════════════════════════════════════════════════════════════════════
# Iter 212m-190 — finding_id contains slashes / colons (composed as
# `<scanner>::<file>:<line>:<rule>`), so we take it in the body
# rather than the URL path. Cleaner + no URL-encode dance for the
# frontend either.
class SnoozeBody(BaseModel):
    project_id:  str
    finding_id:  str
    days:        int = SNOOZE_DEFAULT_DAYS


@router.post("/snooze")
async def snooze_one(body: SnoozeBody,
                     authorization: Optional[str] = Header(None)):
    user = await current_dev(authorization)
    user_id = user["user_id"]
    db = get_db()
    await _assert_owns_project(db, user_id, body.project_id)

    days = max(1, min(30, int(body.days or SNOOZE_DEFAULT_DAYS)))
    now = _now_utc()
    snoozed_until = now + timedelta(days=days)

    r = await db.cto_open_findings.update_one(
        {"user_id": user_id, "project_id": body.project_id,
         "finding_id": body.finding_id},
        # Resetting last_seen_at defers the 30-day idle clock too —
        # snooze means "I'm on it, don't nag me for a week".
        {"$set": {
            "snoozed_until": snoozed_until,
            "last_seen_at":  now,
        }},
    )
    if r.matched_count == 0:
        raise HTTPException(404, "finding_not_found")
    return {"ok": True, "snoozed_until": snoozed_until.isoformat()}


# ══════════════════════════════════════════════════════════════════════
# POST /findings/resolve
# Individual finding dismiss ("I've fixed it manually / not a real
# issue"). Sets status="fixed" so it drops out of the backlog query
# permanently. Named `resolve` rather than `dismiss` to keep it
# distinct from the batch 24-hour dismiss above.
# ══════════════════════════════════════════════════════════════════════
class ResolveBody(BaseModel):
    project_id:  str
    finding_id:  str


@router.post("/resolve")
async def resolve_one(body: ResolveBody,
                      authorization: Optional[str] = Header(None)):
    user = await current_dev(authorization)
    user_id = user["user_id"]
    db = get_db()
    await _assert_owns_project(db, user_id, body.project_id)

    r = await db.cto_open_findings.update_one(
        {"user_id": user_id, "project_id": body.project_id,
         "finding_id": body.finding_id},
        {"$set": {"status": "fixed", "resolved_at": _now_utc()}},
    )
    if r.matched_count == 0:
        raise HTTPException(404, "finding_not_found")
    return {"ok": True, "status": "fixed"}
