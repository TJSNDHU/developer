"""
routers/onboarding.py — Admin trigger + public click-tracking.

Two endpoints:
  • POST /api/aurem-dev/admin/onboarding/send-connect-nudge
      Admin-only. No per-call cap (user explicitly requested "direct
      send, no cap"). Supports `dry_run=true` to preview the batch
      without sending. Supports `user_ids=[...]` to scope a manual
      send to a subset.
  • GET  /api/aurem-dev/onboarding/click?uid=X&c=connect_repo_nudge
      Public 302 redirector. Logs the click against the most recent
      onboarding_emails row for that user, then bounces them to the
      dashboard with `?action=connect-repo&utm_source=email&utm_campaign=onboarding`
      so the wizard opens automatically.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from cto_services.auth import current_dev
from cto_services.db import get_db
from services.onboarding_email import (
    CAMPAIGN, eligible_users, run_nudge_batch,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Onboarding"])


# ── Admin trigger ────────────────────────────────────────────────────
class _NudgeBody(BaseModel):
    dry_run:  bool = False
    stages:   list[str] = ["t24", "t72"]
    user_ids: Optional[list[str]] = None


@router.post("/admin/onboarding/send-connect-nudge")
async def send_connect_nudge(
    body: _NudgeBody,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Trigger the connect-repo nudge campaign. Admin/founder only.

    `dry_run=true` returns the would-be recipients and a preview of
    the rendered body without calling Resend or writing to Mongo (no
    audit row, no email)."""
    me = await current_dev(authorization)
    if not (me.get("is_admin") or me.get("tier") == "founder"):
        raise HTTPException(403, "Admin access required")

    db = get_db()
    if db is None:
        raise HTTPException(503, "database unavailable")

    # Validate stage names defensively.
    stages = tuple(s for s in body.stages if s in ("t24", "t72"))
    if not stages:
        raise HTTPException(400, "stages must contain t24 and/or t72")

    if body.dry_run:
        # Preview path — no audit row, no send.
        preview = []
        for stage in stages:
            cohort = await eligible_users(db, stage=stage)
            if body.user_ids:
                keep = set(body.user_ids)
                cohort = [u for u in cohort if u.get("user_id") in keep]
            for u in cohort:
                preview.append({
                    "user_id": u.get("user_id"),
                    "email":   u.get("email"),
                    "name":    u.get("name"),
                    "stage":   stage,
                })
        return {
            "ok":         True,
            "dry_run":    True,
            "stages":     list(stages),
            "recipients": preview,
            "count":      len(preview),
        }

    result = await run_nudge_batch(
        db, stages=stages, dry_run=False,
        user_ids=body.user_ids,
    )
    return result


# ── Public click tracker ─────────────────────────────────────────────
@router.get("/onboarding/click")
async def onboarding_click(
    uid: str = "",
    c:   str = CAMPAIGN,
):
    """Public 302 — logs the click and redirects to the dashboard.

    Always returns a redirect, even when `uid` is missing or the
    audit row can't be located, so a malformed link still drops the
    user on the right page rather than showing an error.
    """
    target_path = "/dashboard?" + urlencode({
        "action":       "connect-repo",
        "utm_source":   "email",
        "utm_campaign": "onboarding",
    })

    public_base = os.environ.get(
        "PUBLIC_APP_URL", "https://auremcto.com",
    ).rstrip("/")
    target = f"{public_base}{target_path}"

    db = get_db()
    if db is not None and uid:
        try:
            # Latest sent row for this user+campaign. Idempotent: if
            # the user clicks twice we bump `click_count` and update
            # `last_clicked_at`, but `clicked_at` (first-click) sticks.
            now = datetime.now(timezone.utc)
            doc = await db.onboarding_emails.find_one(
                {"user_id": uid, "campaign": c, "sent_ok": True},
                sort=[("sent_at", -1)],
            )
            if doc:
                update: dict = {
                    "$set": {"last_clicked_at": now},
                    "$inc": {"click_count": 1},
                }
                if not doc.get("clicked_at"):
                    update["$set"]["clicked_at"] = now
                await db.onboarding_emails.update_one(
                    {"_id": doc["_id"]}, update,
                )
        except Exception as e:
            logger.warning("onboarding click log failed: %r", e)

    return RedirectResponse(url=target, status_code=302)
