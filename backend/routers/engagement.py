"""
aurem_cto.routers.engagement — Gap 4 (iter D-33)

Read-only surfaces over existing data:
  GET /aurem-cto/referrals/my   — referral link + clicks + conversions
  GET /aurem-cto/streak/me      — consecutive daily build streak

Re-uses existing `referrals`, `referral_profiles`, `verified_referrals`,
and `onboarding_token_wallets.ledger` — does not duplicate any storage.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Header

from cto_services.auth import current_dev
from cto_services.db import require_db

router = APIRouter(tags=["AUREM CTO Engagement"])


# ─── Iter 101: Public referral click tracking ────────────────────────
@router.post("/referrals/track")
async def track_referral_click(payload: dict) -> dict[str, Any]:
    """Public endpoint — no auth. Called from the landing page when a
    visitor lands via `?ref=<uid>`. We record the click so the referrer
    sees engagement signal even before the visitor converts.

    Body: {"ref_code": "<uid>", "path": "/", "user_agent": "…"} (best-effort).
    """
    code = (payload or {}).get("ref_code") or ""
    if not code or len(code) > 100:
        return {"ok": False, "reason": "invalid ref_code"}
    db = require_db()
    await db.referral_clicks.insert_one({
        "ref_code":   code,
        "path":       (payload.get("path") or "/")[:120],
        "user_agent": (payload.get("user_agent") or "")[:200],
        "clicked_at": datetime.now(timezone.utc).isoformat(),
    })
    return {"ok": True}


@router.post("/referrals/attribute")
async def attribute_signup_to_referrer(payload: dict,
                                        authorization: str = Header(None)) -> dict[str, Any]:
    """Called by the signup flow — links a NEW user account to the
    referrer who sent them. Idempotent: only attributes the first
    referral and refuses self-referrals.

    Body: {"ref_code": "<referrer_uid>"}
    """
    me  = await current_dev(authorization)
    db  = require_db()
    new_user_id = me["user_id"]
    ref_code = ((payload or {}).get("ref_code") or "").strip()
    if not ref_code or ref_code == new_user_id:
        return {"ok": False, "reason": "invalid or self-referral"}
    # Reject if the new user already has a referrer recorded.
    existing = await db.referrals.find_one({"new_user_id": new_user_id})
    if existing:
        return {"ok": False, "reason": "already attributed"}
    # Reject if the referrer doesn't exist.
    referrer = await db.dev_users.find_one({"user_id": ref_code}, {"_id": 0, "user_id": 1})
    if not referrer:
        return {"ok": False, "reason": "referrer not found"}
    await db.referrals.insert_one({
        "referrer_user_id": ref_code,
        "new_user_id":      new_user_id,
        "attributed_at":    datetime.now(timezone.utc).isoformat(),
        "status":           "pending_paid_conversion",
    })
    return {"ok": True, "referrer": ref_code}


# ─── Referrals ───────────────────────────────────────────────────────
@router.get("/referrals/my")
async def my_referrals(authorization: str = Header(None)) -> dict[str, Any]:
    me  = await current_dev(authorization)
    db  = require_db()
    uid = me["user_id"]
    # Re-use existing collections.
    profile = await db.referral_profiles.find_one(
        {"user_id": uid}, {"_id": 0},
    )
    invites = await db.referrals.count_documents({"referrer_user_id": uid})
    verified = await db.verified_referrals.count_documents({"referrer_user_id": uid})
    # Iter 101 — also count raw landing clicks for engagement signal.
    clicks  = await db.referral_clicks.count_documents({"ref_code": uid})
    # Public referral link uses account ID as ref param.
    link = f"https://auremcto.com/?ref={uid}"
    return {
        "ref_link":         link,
        "ref_code":         uid,
        "clicks":           clicks,
        "invites_sent":     invites,
        "verified_signups": verified,
        "reward_per_paid":  "1 month free",
        "profile":          profile,
    }


# ─── Build streak ────────────────────────────────────────────────────
@router.get("/streak/me")
async def my_streak(authorization: str = Header(None)) -> dict[str, Any]:
    """Reads onboarding_token_wallets.ledger and counts consecutive days
    on which the user spent at least one cheap/frontier debit."""
    me  = await current_dev(authorization)
    db  = require_db()
    uid = me["user_id"]
    wallet = await db.onboarding_token_wallets.find_one(
        {"user_id": uid}, {"_id": 0, "ledger": 1},
    )
    ledger = (wallet or {}).get("ledger") or []
    debit_days: set[str] = set()
    for e in ledger:
        kind = e.get("kind") or ""
        if not kind.startswith("debit_"):
            continue
        ts = e.get("ts")
        if not ts:
            continue
        # Normalise to UTC YYYY-MM-DD.
        if isinstance(ts, str):
            try:
                day = ts[:10]
            except Exception:
                continue
        elif hasattr(ts, "isoformat"):
            day = ts.astimezone(timezone.utc).date().isoformat()
        else:
            continue
        debit_days.add(day)

    # Walk back from today (UTC) and count consecutive days.
    today = datetime.now(timezone.utc).date()
    streak = 0
    cursor = today
    while cursor.isoformat() in debit_days:
        streak += 1
        cursor = cursor.fromordinal(cursor.toordinal() - 1)

    return {
        "user_id":        uid,
        "current_streak": streak,
        "total_build_days": len(debit_days),
        "today_active":   today.isoformat() in debit_days,
        "longest_streak": _longest_streak(debit_days),
    }


def _longest_streak(days: set[str]) -> int:
    if not days:
        return 0
    sorted_days = sorted(datetime.fromisoformat(d).date() for d in days)
    longest = 1
    run = 1
    for i in range(1, len(sorted_days)):
        if (sorted_days[i] - sorted_days[i - 1]).days == 1:
            run += 1
            longest = max(longest, run)
        else:
            run = 1
    return longest
