"""
services/billing_cron.py — Iter 102 billing automation.

Two functions, both safe to call repeatedly (idempotent within a
billing month):

  1. `bill_maxx_overages(db)` — At month-end, for every user with
     `cto_maxx_usage.overage_count > 0`, create a Stripe InvoiceItem
     for overage_count × $0.50, finalise the invoice via
     `auto_advance=True`, and reset overage_count to 0 so they don't
     get double-billed next month.

  2. `grant_referral_reward(db, referrer_user_id)` — When a referred
     user converts to paid (called from the Stripe webhook), extend
     the referrer's active subscription by 30 days via Stripe's
     `trial_end` update, send a Resend congrats email, and mark the
     referrals row as `rewarded`.

Both wrap every Stripe call in try/except so a single bad row doesn't
poison the whole batch.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone, timedelta

import httpx

logger = logging.getLogger(__name__)


def _stripe_client():
    # Iter 212m-230 — Canonical Stripe key resolver now lives in
    # services/stripe_client.  Removes the circular import
    # billing_cron → routers/payments → billing_cron.
    from services.stripe_client import stripe_client as _svc_stripe_client
    return _svc_stripe_client()


# ───────────────────────────────────────────────────────────────────────
# 1) Overage billing (end-of-month cron)
# ───────────────────────────────────────────────────────────────────────
async def bill_maxx_overages(db) -> dict:
    """For every user with Maxx overage_count > 0 in the just-closed
    month, post a Stripe InvoiceItem at $0.50/task and finalise an
    invoice that auto-charges their default payment method.

    Returns: {processed, billed, failed, total_revenue_usd}
    """
    stripe = _stripe_client()
    bucket = datetime.now(timezone.utc).strftime("%Y-%m")
    processed = billed = failed = 0
    total_usd = 0.0

    # Iter 212m-228 — N+1 fix. Was a `dev_users.find_one` per overage
    # row (up to N sequential round-trips). Now we prefetch every
    # candidate user in ONE `$in` batch keyed on user_id, then look
    # them up locally as we iterate the overage cursor.
    overage_rows = await db.cto_maxx_usage.find({
        "month": bucket,
        "overage_count": {"$gt": 0},
    }).to_list(length=10_000)
    uids = [r.get("user_id") for r in overage_rows if r.get("user_id")]
    users_map: dict[str, dict] = {}
    if uids:
        cur = db.dev_users.find(
            {"user_id": {"$in": uids}},
            {"_id": 0, "user_id": 1, "stripe_customer_id": 1,
             "stripe_sub_id": 1, "tier": 1},
        )
        async for u in cur:
            users_map[u.get("user_id", "")] = u

    for row in overage_rows:
        processed += 1
        uid = row.get("user_id")
        n = int(row.get("overage_count") or 0)
        if not (uid and n > 0):
            continue
        user = users_map.get(uid)
        if not user or not user.get("stripe_customer_id"):
            failed += 1
            logger.warning(f"[overage] {uid} has overage but no Stripe customer")
            continue
        try:
            stripe.InvoiceItem.create(
                customer=user["stripe_customer_id"],
                unit_amount=50,            # $0.50 in cents
                quantity=n,
                currency="usd",
                description=f"Maxx-mode overage — {n} task(s) above 100 included ({bucket})",
                metadata={"aurem_user_id": uid, "bucket": bucket, "type": "maxx_overage"},
            )
            invoice = stripe.Invoice.create(
                customer=user["stripe_customer_id"],
                auto_advance=True,         # collect automatically
                collection_method="charge_automatically",
                metadata={"aurem_user_id": uid, "bucket": bucket, "type": "maxx_overage"},
            )
            stripe.Invoice.finalize_invoice(invoice.id)
            # Reset overage AFTER successful invoice creation only.
            await db.cto_maxx_usage.update_one(
                {"user_id": uid, "month": bucket},
                {"$set": {"overage_count": 0,
                          "last_billed_at": datetime.now(timezone.utc).isoformat(),
                          "last_billed_invoice": invoice.id}},
            )
            billed += 1
            total_usd += n * 0.50
            logger.info(f"[overage] billed {uid} ${n*0.50:.2f} (invoice {invoice.id})")
        except Exception as e:
            failed += 1
            logger.warning(f"[overage] {uid} failed: {e!r}")
    return {
        "processed":         processed,
        "billed":            billed,
        "failed":            failed,
        "total_revenue_usd": round(total_usd, 2),
        "bucket":            bucket,
        "ran_at":            datetime.now(timezone.utc).isoformat(),
    }


# ───────────────────────────────────────────────────────────────────────
# 2) Referral reward — 30-day subscription extension + Resend email
# ───────────────────────────────────────────────────────────────────────
async def grant_referral_reward(db, new_user_id: str) -> dict:
    """Called by the Stripe webhook on `checkout.session.completed`
    when the converting user has a pending referral row. Extends the
    REFERRER's subscription by 30 days via Stripe `trial_end`."""
    referral = await db.referrals.find_one({
        "new_user_id": new_user_id,
        "status": "pending_paid_conversion",
    })
    if not referral:
        return {"granted": False, "reason": "no pending referral"}
    referrer_uid = referral.get("referrer_user_id")
    referrer = await db.dev_users.find_one({"user_id": referrer_uid},
                                            {"_id": 0, "email": 1, "name": 1,
                                             "stripe_sub_id": 1, "tier": 1})
    if not referrer:
        return {"granted": False, "reason": "referrer not found"}
    sub_id = referrer.get("stripe_sub_id")
    if not sub_id:
        # Referrer is still on free — record the credit, grant later when
        # they upgrade.
        await db.referrals.update_one(
            {"_id": referral["_id"]},
            {"$set": {"status": "free_month_pending_upgrade",
                      "credited_at": datetime.now(timezone.utc).isoformat()}},
        )
        return {"granted": False, "reason": "referrer on free tier — credit stored"}

    stripe = _stripe_client()
    try:
        sub = stripe.Subscription.retrieve(sub_id)
        current_end = int(sub.current_period_end or 0)
        new_end = current_end + 30 * 86400
        stripe.Subscription.modify(
            sub_id,
            trial_end=new_end,
            proration_behavior="none",
            metadata={"aurem_referral_reward": referral["_id"]},
        )
        await db.referrals.update_one(
            {"_id": referral["_id"]},
            {"$set": {"status": "rewarded",
                      "rewarded_at": datetime.now(timezone.utc).isoformat(),
                      "rewarded_via_extension_seconds": 30 * 86400}},
        )
        # Best-effort thank-you email via Resend.
        try:
            resend_key = os.environ.get("RESEND_API_KEY") or ""
            from_addr = os.environ.get("RESEND_FROM_EMAIL", "AUREM <ora@aurem.live>")
            if resend_key and referrer.get("email"):
                async with httpx.AsyncClient(timeout=8) as c:
                    await c.post(
                        "https://api.resend.com/emails",
                        headers={"Authorization": f"Bearer {resend_key}",
                                 "Content-Type": "application/json"},
                        json={
                            "from":    from_addr,
                            "to":      [referrer["email"]],
                            "subject": "You earned 1 free month on AUREM CTO 🎉",
                            "html":    (
                                f"<p>Hi {referrer.get('name') or 'there'},</p>"
                                f"<p>One of your referrals just upgraded to a paid "
                                f"plan — and as promised, <b>your next 30 days "
                                f"are on us</b>. Your subscription's renewal date "
                                f"has been pushed 30 days. No action needed.</p>"
                                f"<p>Keep the link going: "
                                f"<a href='https://auremcto.com/?ref={referrer_uid}'>"
                                f"auremcto.com/?ref={referrer_uid}</a></p>"
                                f"<p>— Team AUREM</p>"
                            ),
                        },
                    )
        except Exception as e:
            logger.warning(f"[referral_reward] email failed for {referrer_uid}: {e!r}")
        return {"granted": True, "referrer": referrer_uid,
                "new_period_end": new_end}
    except Exception as e:
        logger.warning(f"[referral_reward] Stripe extend failed: {e!r}")
        return {"granted": False, "reason": str(e)}
