"""
services/onboarding_email.py — Onboarding "connect a repo" email nudge.

Targets fresh signups who haven't connected a GitHub repo yet. Sent
via the existing Resend integration. Idempotency + dedupe live in the
`onboarding_emails` Mongo collection so the same user is never paged
twice on the same campaign-stage.

Stages (per the user spec):
    stage="t24"    — first nudge, ~24 h after signup
    stage="t72"    — single retry, ~72 h after signup if still no repo

Public surface:
    eligible_users(db, *, stage)              → list[dict]
    send_connect_repo_nudge(user, *, stage)   → dict
    run_nudge_batch(db, *, stage, dry_run)    → dict
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import quote

import httpx

from cto_services.db import get_db   # re-exported so tests can patch us

logger = logging.getLogger(__name__)

# ── Tunables ─────────────────────────────────────────────────────────
CAMPAIGN          = "connect_repo_nudge"
STAGE_T24_HOURS   = 24
STAGE_T72_HOURS   = 72
PUBLIC_BASE       = os.environ.get(
    "PUBLIC_APP_URL", "https://auremcto.com",
).rstrip("/")
SUBJECT           = "Your Aurem account is ready — one step left"
SIGNOFF           = "— Tejinder Sandhu, Founder, Aurem"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _created_at_dt(raw) -> Optional[datetime]:
    """Coerce dev_users.created_at into a tz-aware datetime.
    Handles the legacy epoch-seconds / epoch-ms shapes too."""
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    if isinstance(raw, (int, float)):
        secs = float(raw) / (1000.0 if raw > 10**12 else 1.0)
        return datetime.fromtimestamp(secs, tz=timezone.utc)
    if isinstance(raw, str):
        try:
            d = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _first_name(user: dict) -> str:
    """Pick a friendly first-name with a sensible fallback to the local
    part of the user's email."""
    name = (user.get("name") or "").strip()
    if name:
        return name.split()[0]
    email = (user.get("email") or "").strip()
    return (email.split("@", 1)[0] or "there").split(".", 1)[0]


def click_url(user_id: str) -> str:
    """Tracked CTA URL. Hits the click logger, which 302-redirects to
    `/dashboard?action=connect-repo&utm_source=email&utm_campaign=onboarding`."""
    return (
        f"{PUBLIC_BASE}/api/aurem-dev/onboarding/click"
        f"?uid={quote(user_id)}&c={CAMPAIGN}"
    )


def render_text(user: dict) -> str:
    """Plain-text body — matches the user-signed-off copy exactly."""
    first = _first_name(user)
    cta = click_url(user.get("user_id", ""))
    return (
        f"Hey {first},\n"
        "\n"
        "You signed up for Aurem but haven't connected a repo yet.\n"
        "\n"
        "Here's what happens when you do:\n"
        "→ Your codebase gets mapped instantly\n"
        "→ Free SEO fix applied automatically\n"
        "→ One of 500 founder spots — yours\n"
        "\n"
        f"Connect your repo → {cta}\n"
        "\n"
        "Takes 2 minutes.\n"
        "\n"
        f"{SIGNOFF}\n"
    )


def render_html(user: dict) -> str:
    """Minimal HTML wrapper so the CTA renders as a button in Gmail /
    Outlook / Apple Mail. Inline styles only — email-client safe."""
    first = _first_name(user)
    cta = click_url(user.get("user_id", ""))
    # Bullet character used via &rarr; for max client compat.
    return f"""<!doctype html>
<html><body style="margin:0;padding:0;background:#0b0b0b;color:#e8e8e8;
font-family:'Helvetica Neue',Arial,sans-serif;line-height:1.55;">
  <table role="presentation" cellpadding="0" cellspacing="0" border="0"
         width="100%" style="background:#0b0b0b;padding:32px 16px;">
    <tr><td align="center">
      <table role="presentation" cellpadding="0" cellspacing="0" border="0"
             width="560" style="max-width:560px;background:#141414;
                                 border:1px solid rgba(234,179,8,0.18);
                                 border-radius:12px;padding:32px;">
        <tr><td style="color:#e8e8e8;font-size:15px;">
          Hey {first},<br><br>
          You signed up for Aurem but haven't connected a repo yet.<br><br>
          Here's what happens when you do:<br>
          &rarr; Your codebase gets mapped instantly<br>
          &rarr; Free SEO fix applied automatically<br>
          &rarr; <strong>One of 500 founder spots — yours</strong><br><br>
          <a href="{cta}"
             style="display:inline-block;padding:12px 22px;background:#eab308;
                    color:#0b0b0b;text-decoration:none;font-weight:600;
                    border-radius:8px;font-size:14px;">
            Connect your repo &rarr;
          </a><br><br>
          <span style="color:#888;font-size:13px;">Takes 2 minutes.</span><br><br>
          <span style="color:#aaa;font-size:13px;">{SIGNOFF}</span>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body></html>"""


async def _resend_send(to_email: str, *, text: str, html: str) -> tuple[bool, Optional[str]]:
    """POST to Resend. Returns (ok, error_text)."""
    key = os.environ.get("RESEND_API_KEY") or ""
    if not key:
        return False, "RESEND_API_KEY not configured"
    sender = (
        os.environ.get("RESEND_FROM_EMAIL")
        or "AUREM <ora@aurem.live>"
    )
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {key}",
                         "Content-Type":   "application/json"},
                json={
                    "from":    sender,
                    "to":      [to_email],
                    "subject": SUBJECT,
                    "text":    text,
                    "html":    html,
                },
            )
            if r.status_code in (200, 201, 202):
                return True, None
            return False, f"HTTP {r.status_code}: {r.text[:200]}"
    except Exception as e:  # noqa: BLE001 — Resend is best-effort
        return False, f"{type(e).__name__}: {e}"


# ── DB helpers ───────────────────────────────────────────────────────
async def _has_been_sent(db, user_id: str, stage: str) -> bool:
    """True if `user_id` already received the `stage` nudge."""
    doc = await db.onboarding_emails.find_one(
        {"user_id": user_id, "campaign": CAMPAIGN, "stage": stage},
        {"_id": 1},
    )
    return doc is not None


async def _record_send(
    db, user_id: str, email: str, stage: str,
    sent_ok: bool, error: Optional[str], dry_run: bool,
) -> None:
    """Insert one audit row per send attempt. `clicked_at` filled later
    by the click-tracker endpoint."""
    try:
        await db.onboarding_emails.insert_one({
            "user_id":    user_id,
            "email":      email,
            "campaign":   CAMPAIGN,
            "stage":      stage,
            "sent_at":    _now(),
            "sent_ok":    bool(sent_ok),
            "error":      error,
            "dry_run":    bool(dry_run),
            "clicked_at": None,
            "click_count": 0,
        })
    except Exception as e:
        logger.warning("onboarding_emails insert failed: %r", e)


async def eligible_users(db, *, stage: str) -> list[dict]:
    """Return the list of dev_users that qualify for `stage`. A user
    qualifies when:
      - they have an `email`,
      - their `created_at` falls between the cutoff for `stage` and now,
      - they have ZERO rows in `cto_projects` for `user_id`,
      - and they have not yet received this `stage`'s email.
    """
    hours = {"t24": STAGE_T24_HOURS, "t72": STAGE_T72_HOURS}.get(stage)
    if hours is None:
        return []
    cutoff = _now().timestamp() - hours * 3600

    # `created_at` may be a datetime, an epoch number, or an ISO string
    # across historical rows. We can't pre-filter on type in Mongo, so
    # we pull a small batch and filter in Python — the dev_users
    # collection is tiny in this pre-launch window.
    candidates = await db.dev_users.find(
        {"email": {"$exists": True, "$ne": ""}},
        {"_id": 0, "user_id": 1, "email": 1, "name": 1, "created_at": 1},
    ).to_list(length=10_000)

    out: list[dict] = []
    for u in candidates:
        ca = _created_at_dt(u.get("created_at"))
        if ca is None:
            continue
        if ca.timestamp() > cutoff:
            continue   # too new for this stage
        # No connected repo?
        proj = await db.cto_projects.find_one(
            {"user_id": u["user_id"]}, {"_id": 1},
        )
        if proj is not None:
            continue
        if await _has_been_sent(db, u["user_id"], stage):
            continue
        out.append(u)
    return out


async def send_connect_repo_nudge(
    user: dict, *, stage: str = "t24", dry_run: bool = False,
) -> dict:
    """Send one nudge to one user. Caller is responsible for the
    eligibility check (use `eligible_users` for the batch path)."""
    db = get_db()
    if db is None:
        return {"ok": False, "error": "db unavailable",
                "user_id": user.get("user_id")}
    text = render_text(user)
    html = render_html(user)
    if dry_run:
        return {
            "ok":       True,
            "user_id":  user.get("user_id"),
            "email":    user.get("email"),
            "stage":    stage,
            "dry_run":  True,
            "preview":  {"subject": SUBJECT, "text": text},
        }
    sent_ok, err = await _resend_send(user["email"], text=text, html=html)
    await _record_send(
        db, user["user_id"], user["email"], stage,
        sent_ok, err, dry_run=False,
    )
    return {
        "ok":      bool(sent_ok),
        "user_id": user.get("user_id"),
        "email":   user.get("email"),
        "stage":   stage,
        "error":   err,
    }


async def run_nudge_batch(
    db, *, stages: tuple[str, ...] = ("t24", "t72"),
    dry_run: bool = False,
    user_ids: Optional[list[str]] = None,
) -> dict:
    """Run the nudge for every eligible user across `stages`. When
    `user_ids` is set, restrict to that subset (admin manual override)."""
    summary = {
        "ok":         True,
        "dry_run":    bool(dry_run),
        "stages":     list(stages),
        "sent":       0,
        "skipped":    0,
        "failed":     0,
        "recipients": [],
        "errors":     [],
    }
    for stage in stages:
        cohort = await eligible_users(db, stage=stage)
        if user_ids:
            keep = set(user_ids)
            cohort = [u for u in cohort if u.get("user_id") in keep]
        for u in cohort:
            res = await send_connect_repo_nudge(
                u, stage=stage, dry_run=dry_run,
            )
            summary["recipients"].append({
                "user_id": res.get("user_id"),
                "email":   res.get("email"),
                "stage":   stage,
                "ok":      bool(res.get("ok")),
                "dry_run": bool(res.get("dry_run")),
            })
            if res.get("ok"):
                summary["sent"] += 1
            else:
                summary["failed"] += 1
                if res.get("error"):
                    summary["errors"].append(res["error"])
    return summary


# ── Cron loop ────────────────────────────────────────────────────────
async def nudge_cron(interval_seconds: int = 3600) -> None:
    """Hourly loop: run both t24 and t72 stages. Idempotent (the
    `_has_been_sent` guard inside `eligible_users` is the gate)."""
    import asyncio
    while True:
        try:
            db = get_db()
            if db is not None:
                result = await run_nudge_batch(
                    db, stages=("t24", "t72"), dry_run=False,
                )
                if result["sent"] or result["failed"]:
                    logger.info(
                        "🪧 onboarding nudge cron — sent=%d failed=%d",
                        result["sent"], result["failed"],
                    )
        except Exception as e:
            logger.warning("nudge_cron tick failed: %r", e)
        await asyncio.sleep(interval_seconds)
