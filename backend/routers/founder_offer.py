"""
routers/founder_offer.py — Founder's free-SEO-fix offer.

500 spots, atomic decrement, per-user cap of 3 repos.

Flow (matches the frontend in `FounderOfferCard.jsx`):
  1. `POST /claim`    — reserves one of the 500 spots (atomic $inc),
                        records a claim row with `fix_status="preview"`,
                        runs `services.seo.orchestrator.run_seo_fixes`
                        in **dry-run** mode, returns the preview to the
                        UI for confirmation.
  2. `POST /confirm`  — fires the real commit run in the background and
                        flips the claim row to `fix_status="running"`.
                        When the run finishes we update to "completed"
                        or "failed".
  3. `POST /cancel`   — only valid while `fix_status="preview"`. Restores
                        one spot and deletes the claim row.

All atomic mutations go through `find_one_and_update` so concurrent
clicks can't over-allocate spots.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from cto_services.auth import current_dev
from cto_services.db import get_db
from services.seo.orchestrator import run_seo_fixes, SeoOptions

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/founder-offer", tags=["Founder Offer"])

# ── Tunables ─────────────────────────────────────────────────────────
TOTAL_SPOTS         = 500
MAX_CLAIMS_PER_USER = 3
SINGLETON_ID        = "global"


# ── Mongo singleton helpers ──────────────────────────────────────────
async def _ensure_singleton(db) -> dict:
    """Idempotent — creates the `{_id: 'global'}` row on first call.

    Returns the current state of the offer document."""
    doc = await db.founder_offer.find_one_and_update(
        {"_id": SINGLETON_ID},
        {
            "$setOnInsert": {
                "_id":             SINGLETON_ID,
                "total_spots":     TOTAL_SPOTS,
                "spots_claimed":   0,
                "is_active":       True,
                "created_at":      datetime.now(timezone.utc),
            },
        },
        upsert=True,
        return_document=True,
    )
    # PyMongo returns None for `return_document=True` on a fresh upsert
    # in older driver versions — defensive re-read.
    if not doc:
        doc = await db.founder_offer.find_one({"_id": SINGLETON_ID})
    return doc or {
        "total_spots": TOTAL_SPOTS, "spots_claimed": 0, "is_active": True,
    }


async def _user_claims(db, user_id: str) -> list[dict]:
    """All non-cancelled claims for `user_id`, newest first."""
    cur = db.user_seo_claims.find(
        {"user_id": user_id, "fix_status": {"$ne": "cancelled"}},
    ).sort("created_at", -1)
    return await cur.to_list(length=50)


def _days_since(ts) -> Optional[float]:
    """Return days elapsed since `ts` (tz-aware datetime, epoch ms,
    epoch seconds, or ISO string) or None when missing/unparseable."""
    if ts is None:
        return None
    # ── int/float epoch ───────────────────────────────────────────
    if isinstance(ts, (int, float)):
        # Treat huge numbers as epoch milliseconds (>10^12).
        secs = float(ts) / (1000.0 if ts > 10**12 else 1.0)
        delta = datetime.now(timezone.utc).timestamp() - secs
        return delta / 86400.0 if delta >= 0 else None
    # ── ISO string ────────────────────────────────────────────────
    if isinstance(ts, str):
        try:
            parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - parsed
        return delta.total_seconds() / 86400.0
    # ── datetime ──────────────────────────────────────────────────
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - ts
        return delta.total_seconds() / 86400.0
    return None


# ── Public endpoints ─────────────────────────────────────────────────
@router.get("/status")
async def offer_status() -> dict:
    """Public — returns the global counter so the UI can render the
    'X spots remaining' badge without auth."""
    db = get_db()
    if db is None:
        return {"remaining": 0, "total": TOTAL_SPOTS, "is_active": False}
    doc = await _ensure_singleton(db)
    remaining = max(0, int(doc.get("total_spots", TOTAL_SPOTS)) -
                       int(doc.get("spots_claimed", 0)))
    return {
        "remaining": remaining,
        "total":     int(doc.get("total_spots", TOTAL_SPOTS)),
        "is_active": bool(doc.get("is_active", True)),
    }


@router.get("/user-status")
async def offer_user_status(
    authorization: Optional[str] = Header(None),
) -> dict:
    """Authenticated — per-user view used by the FounderOfferCard to
    decide whether to render at all."""
    me = await current_dev(authorization)
    db = get_db()
    if db is None:
        raise HTTPException(503, "database unavailable")
    await _ensure_singleton(db)

    claims = await _user_claims(db, me["user_id"])
    repos_claimed = len(claims)
    # Iter 212m-44 — surface the actual claimed repo_ids so the
    # FounderOfferCard can hide itself in the project window where
    # the user already applied the offer (per-project dismissal).
    claimed_repo_ids = [c.get("repo_id") for c in claims if c.get("repo_id")]

    user = await db.dev_users.find_one(
        {"user_id": me["user_id"]},
        {"_id": 0, "created_at": 1},
    )
    days = _days_since((user or {}).get("created_at"))

    return {
        "repos_claimed":        repos_claimed,
        "claimed_repo_ids":     claimed_repo_ids,
        "has_fully_claimed":    repos_claimed >= MAX_CLAIMS_PER_USER,
        "days_since_signup":    days,
        "max_claims_per_user":  MAX_CLAIMS_PER_USER,
    }


class _ClaimBody(BaseModel):
    repo_id:  str           # cto_projects.project_id
    site_url: Optional[str] = ""


@router.post("/claim")
async def claim_offer(
    body: _ClaimBody,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Reserve one of the 500 spots for `repo_id`, run the SEO fixer in
    dry-run mode, and return the preview for the user to confirm."""
    me = await current_dev(authorization)
    user_id = me["user_id"]
    db = get_db()
    if db is None:
        raise HTTPException(503, "database unavailable")
    await _ensure_singleton(db)

    # ── Per-user cap ─────────────────────────────────────────────
    existing = await _user_claims(db, user_id)
    if len(existing) >= MAX_CLAIMS_PER_USER:
        return {"success": False, "action": "upgrade",
                "claims_used": len(existing),
                "max": MAX_CLAIMS_PER_USER}

    # Same user trying to re-claim the same repo (and that claim isn't
    # cancelled) — idempotent: hand them back the existing claim.
    for c in existing:
        if c.get("repo_id") == body.repo_id:
            return {
                "success":      True,
                "claim_id":     c.get("claim_id"),
                "already_claimed": True,
                "fix_status":   c.get("fix_status"),
                "preview":      c.get("preview") or {},
            }

    # ── Atomic spot decrement ────────────────────────────────────
    # find_one_and_update guarantees we never hand out the same spot twice
    # under concurrent requests — Mongo's storage engine serialises the
    # update on the singleton document.
    res = await db.founder_offer.find_one_and_update(
        {"_id": SINGLETON_ID,
         "is_active": True,
         "$expr": {"$lt": ["$spots_claimed", "$total_spots"]}},
        {"$inc": {"spots_claimed": 1}},
        return_document=True,
    )
    if res is None:
        # Either inactive or sold out — return a uniform "soft no" so
        # the frontend can hide the card without showing an angry error.
        return {"success": False, "action": "sold_out"}

    # ── Insert claim row ─────────────────────────────────────────
    claim_id = f"claim_{uuid.uuid4().hex[:12]}"
    now      = datetime.now(timezone.utc)
    claim    = {
        "claim_id":      claim_id,
        "user_id":       user_id,
        "repo_id":       body.repo_id,
        "site_url":      (body.site_url or "").strip(),
        "fix_status":    "preview",   # pending user confirmation
        "preview":       None,
        "errors":        [],
        "created_at":    now,
        "updated_at":    now,
    }
    try:
        await db.user_seo_claims.insert_one(claim)
    except Exception as e:
        # Roll the spot back to keep the counter honest.
        await db.founder_offer.update_one(
            {"_id": SINGLETON_ID}, {"$inc": {"spots_claimed": -1}},
        )
        raise HTTPException(500, f"failed to record claim: {e!r}")

    # ── Dry-run preview ──────────────────────────────────────────
    try:
        preview = await run_seo_fixes(
            user_id=user_id,
            project_id=body.repo_id,
            options=SeoOptions(
                plan="swift", site_url=body.site_url or "",
                dry_run=True,
            ),
        )
    except Exception as e:
        preview = {"ok": False, "errors": [f"dry_run crashed: {e!r}"]}

    summary = _preview_summary(preview)
    await db.user_seo_claims.update_one(
        {"claim_id": claim_id},
        {"$set": {
            "preview":     summary,
            "preview_raw": preview,
            "updated_at":  datetime.now(timezone.utc),
        }},
    )

    return {
        "success":   True,
        "claim_id":  claim_id,
        "preview":   summary,
    }


class _ConfirmBody(BaseModel):
    claim_id: str


@router.post("/confirm")
async def confirm_offer(
    body: _ConfirmBody,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Promote a `preview` claim to a real commit run. Returns 200
    immediately and runs the commit in the background."""
    me = await current_dev(authorization)
    user_id = me["user_id"]
    db = get_db()
    if db is None:
        raise HTTPException(503, "database unavailable")

    claim = await db.user_seo_claims.find_one(
        {"claim_id": body.claim_id, "user_id": user_id},
    )
    if not claim:
        raise HTTPException(404, "claim not found")
    if claim.get("fix_status") not in ("preview",):
        return {
            "success": False,
            "reason":  f"claim already {claim.get('fix_status')}",
            "fix_status": claim.get("fix_status"),
        }

    await db.user_seo_claims.update_one(
        {"claim_id": body.claim_id},
        {"$set": {
            "fix_status": "running",
            "updated_at": datetime.now(timezone.utc),
        }},
    )

    site_url = claim.get("site_url") or ""
    repo_id  = claim["repo_id"]
    asyncio.create_task(_run_real_fix(
        claim_id=body.claim_id, user_id=user_id,
        repo_id=repo_id, site_url=site_url,
    ))
    return {"success": True, "fix_status": "running"}


class _CancelBody(BaseModel):
    claim_id: str


@router.post("/cancel")
async def cancel_offer(
    body: _CancelBody,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Cancel a `preview` claim — restores one spot. No-op for any other
    fix_status because at that point a commit may already have landed."""
    me = await current_dev(authorization)
    user_id = me["user_id"]
    db = get_db()
    if db is None:
        raise HTTPException(503, "database unavailable")

    res = await db.user_seo_claims.find_one_and_update(
        {"claim_id": body.claim_id, "user_id": user_id,
         "fix_status": "preview"},
        {"$set": {
            "fix_status": "cancelled",
            "updated_at": datetime.now(timezone.utc),
        }},
        return_document=True,
    )
    if not res:
        # Either not found or already moved past preview — soft no.
        return {"success": False, "reason": "not_cancellable"}

    # Restore the spot.
    await db.founder_offer.update_one(
        {"_id": SINGLETON_ID, "spots_claimed": {"$gt": 0}},
        {"$inc": {"spots_claimed": -1}},
    )
    return {"success": True}


# ── Background commit runner ─────────────────────────────────────────
async def _run_real_fix(
    *, claim_id: str, user_id: str, repo_id: str, site_url: str,
) -> None:
    db = get_db()
    if db is None:
        return
    try:
        result = await run_seo_fixes(
            user_id=user_id, project_id=repo_id,
            options=SeoOptions(
                plan="swift", site_url=site_url, dry_run=False,
                commit_message="chore(seo): aurem founder fix",
            ),
        )
        await db.user_seo_claims.update_one(
            {"claim_id": claim_id},
            {"$set": {
                "fix_status":  "completed" if result.get("ok") else "failed",
                "result":      _preview_summary(result),
                "result_raw":  result,
                "updated_at":  datetime.now(timezone.utc),
            }},
        )
    except Exception as e:
        logger.exception("founder_offer: real fix crashed for %s", claim_id)
        await db.user_seo_claims.update_one(
            {"claim_id": claim_id},
            {"$set": {
                "fix_status": "failed",
                "errors":     [f"runner crashed: {e!r}"],
                "updated_at": datetime.now(timezone.utc),
            }},
        )


# ── Helpers ──────────────────────────────────────────────────────────
def _preview_summary(result: dict) -> dict:
    """Extract the small, JSON-safe summary the UI cares about from the
    full orchestrator dict."""
    patches = result.get("patches") or []
    return {
        "ok":             bool(result.get("ok")),
        "issues_found":   len(patches),
        "files_affected": sorted({p.get("path") for p in patches if p.get("path")}),
        "errors":         result.get("errors") or [],
        "note":           result.get("note") or "",
        "committed":      bool(result.get("committed")),
        "commit_sha":     result.get("commit_sha"),
        "commit_url":     result.get("commit_url"),
    }
