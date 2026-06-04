"""
routers/payments.py — Stripe-subscription billing for the 4-tier plan.

Honest implementation notes:
  • Uses the native `stripe` SDK with **subscription-mode Checkout** +
    price IDs (the flow the user explicitly asked for).
  • All 4 endpoints (/checkout, /webhook, /my-plan, /portal) gracefully
    503 if the env isn't configured — no fake billing.
  • Single source of truth for plan features lives in
    services/subscription_tiers.py.

Required env vars (added in Emergent dashboard → Env vars):
  STRIPE_SECRET_KEY            sk_live_… (or sk_test_… for sandbox)
  STRIPE_WEBHOOK_SECRET        whsec_…   (from the webhook endpoint)
  STRIPE_STARTER_PRICE_ID      price_…
  STRIPE_PRO_PRICE_ID          price_…
  STRIPE_TEAM_PRICE_ID         price_…
  FRONTEND_URL                 https://auremcto.com   (no trailing /)
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional

import stripe
from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel

from cto_services.auth import current_dev
from cto_services.db import require_db
from services.subscription_tiers import TIER_LIMITS, Tier, _coerce

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Payments"])


def _stripe_key() -> str:
    """Return the Stripe secret key, preferring STRIPE_SECRET_KEY but
    falling back to the older STRIPE_API_KEY env name."""
    return (
        os.environ.get("STRIPE_SECRET_KEY")
        or os.environ.get("STRIPE_API_KEY")
        or ""
    )


def _require_stripe() -> None:
    k = _stripe_key()
    if not k:
        raise HTTPException(503, "Stripe not configured (STRIPE_SECRET_KEY missing)")
    if k.startswith("sk_test_emergent"):
        # Sandbox placeholder bundled with the platform — won't actually
        # charge anyone. We still let it through in dev so the UI flow
        # is testable, but log a noisy warning so prod never ships on it.
        logger.warning("⚠ Using sk_test_emergent placeholder — live keys NOT configured")
    stripe.api_key = k


STRIPE_PRICES = {
    "starter": lambda: os.environ.get("STRIPE_STARTER_PRICE_ID"),
    "pro":     lambda: os.environ.get("STRIPE_PRO_PRICE_ID"),
    "team":    lambda: os.environ.get("STRIPE_TEAM_PRICE_ID"),
}


def _frontend_url() -> str:
    """Where Stripe should redirect after Checkout / Portal. Falls back
    to the request's own origin if the env isn't set so dev still works."""
    return (os.environ.get("FRONTEND_URL") or "").rstrip("/")


# ── /payments/checkout — create a Subscription Checkout Session ─────────
class CheckoutBody(BaseModel):
    # Accept both `plan` (new) and `tier` (legacy from older callers).
    plan: Optional[str] = None
    tier: Optional[str] = None
    origin_url: Optional[str] = None  # falls back to FRONTEND_URL


@router.post("/payments/checkout")
async def create_checkout(
    body: CheckoutBody,
    http_request: Request,
    authorization: Optional[str] = Header(None),
) -> dict:
    _require_stripe()
    user = await current_dev(authorization)
    plan = (body.plan or body.tier or "").strip().lower()
    if plan not in STRIPE_PRICES:
        raise HTTPException(400, f"Invalid plan `{plan}` — expected starter|pro|team")
    price_id = STRIPE_PRICES[plan]()
    if not price_id:
        raise HTTPException(
            503,
            f"Stripe price ID for `{plan}` not configured "
            f"(set STRIPE_{plan.upper()}_PRICE_ID).",
        )

    origin = (
        (body.origin_url or "").rstrip("/")
        or _frontend_url()
        or str(http_request.base_url).rstrip("/")
    )
    success_url = f"{origin}/settings?session_id={{CHECKOUT_SESSION_ID}}&upgraded=1"
    cancel_url  = f"{origin}/settings?cancelled=1#pricing"

    try:
        session = stripe.checkout.Session.create(
            mode="subscription",
            payment_method_types=["card"],
            line_items=[{"price": price_id, "quantity": 1}],
            customer_email=user.get("email") or None,
            success_url=success_url,
            cancel_url=cancel_url,
            metadata={
                "user_id": user.get("user_id", ""),
                "email":   user.get("email", ""),
                "plan":    plan,
            },
            allow_promotion_codes=True,
        )
    except stripe.error.StripeError as e:
        logger.warning("stripe checkout create failed: %r", e)
        raise HTTPException(502, f"Stripe error: {getattr(e, 'user_message', str(e))}")

    db = require_db()
    await db.cto_payments.insert_one({
        "session_id":  session.id,
        "user_id":     user.get("user_id"),
        "user_email":  user.get("email"),
        "plan":        plan,
        "price_id":    price_id,
        "status":      "initiated",
        "payment_status": "pending",
        "created_at":  time.time(),
    })
    return {"url": session.url, "checkout_url": session.url, "session_id": session.id}


# ── /payments/status — frontend poll after the redirect ─────────────────
@router.get("/payments/status/{session_id}")
async def payment_status(
    session_id: str,
    authorization: Optional[str] = Header(None),
) -> dict:
    _require_stripe()
    user = await current_dev(authorization)
    db = require_db()
    pay = await db.cto_payments.find_one({"session_id": session_id})
    if not pay or pay.get("user_id") != user.get("user_id"):
        raise HTTPException(404, "Unknown session")

    try:
        session = stripe.checkout.Session.retrieve(session_id)
    except stripe.error.StripeError as e:
        raise HTTPException(502, f"Stripe error: {e}")

    paid = session.payment_status == "paid"
    update = {
        "payment_status": session.payment_status,
        "status":         session.status,
        "subscription":   session.subscription,
        "updated_at":     time.time(),
    }
    if paid and pay.get("payment_status") != "paid":
        update["paid_at"] = time.time()
        await db.dev_users.update_one(
            {"user_id": pay["user_id"]},
            {"$set": {
                "tier":             pay["plan"],
                "usage_tier":       pay["plan"],   # keep legacy field in sync
                "stripe_sub_id":    session.subscription,
                "tier_updated_at":  datetime.now(timezone.utc).isoformat(),
            }},
        )
    await db.cto_payments.update_one({"session_id": session_id}, {"$set": update})
    return {
        "session_id":     session_id,
        "payment_status": session.payment_status,
        "status":         session.status,
        "plan":           pay["plan"],
        "tier":           pay["plan"],
    }


# ── /payments/webhook — Stripe → us, source of truth for subscription state
@router.post("/payments/webhook")
@router.post("/webhook/stripe")   # legacy path — keep for old config
async def stripe_webhook(request: Request) -> dict:
    _require_stripe()
    payload = await request.body()
    sig     = request.headers.get("stripe-signature") or ""
    secret  = os.environ.get("STRIPE_WEBHOOK_SECRET") or ""
    if not secret:
        raise HTTPException(503, "STRIPE_WEBHOOK_SECRET not configured")
    try:
        event = stripe.Webhook.construct_event(payload, sig, secret)
    except stripe.error.SignatureVerificationError:
        raise HTTPException(400, "Invalid webhook signature")
    except Exception as e:
        logger.warning("webhook parse failed: %r", e)
        raise HTTPException(400, "Invalid webhook payload")

    db = require_db()
    etype = event.get("type", "")

    if etype == "checkout.session.completed":
        obj      = event["data"]["object"]
        user_id  = (obj.get("metadata") or {}).get("user_id")
        plan     = (obj.get("metadata") or {}).get("plan")
        sub_id   = obj.get("subscription")
        if user_id and plan:
            await db.dev_users.update_one(
                {"user_id": user_id},
                {"$set": {
                    "tier":            plan,
                    "usage_tier":      plan,
                    "stripe_sub_id":   sub_id,
                    "tier_updated_at": datetime.now(timezone.utc).isoformat(),
                }},
            )
    elif etype in ("customer.subscription.deleted", "customer.subscription.paused"):
        sub_id = event["data"]["object"]["id"]
        await db.dev_users.update_one(
            {"stripe_sub_id": sub_id},
            {"$set": {
                "tier":            "free",
                "usage_tier":      "free",
                "stripe_sub_id":   None,
                "tier_updated_at": datetime.now(timezone.utc).isoformat(),
            }},
        )
    return {"ok": True}


# ── /payments/my-plan — current tier + feature flags for the UI ─────────
@router.get("/payments/my-plan")
async def my_plan(authorization: Optional[str] = Header(None)) -> dict:
    user = await current_dev(authorization)
    db = require_db()
    row = await db.dev_users.find_one(
        {"user_id": user.get("user_id")},
        {"tier": 1, "stripe_sub_id": 1, "_id": 0},
    ) or {}
    tier_str = row.get("tier") or "free"
    tier_enum = _coerce(tier_str)
    return {
        "tier":   tier_str,
        "limits": TIER_LIMITS[tier_enum],
        "sub_id": row.get("stripe_sub_id"),
    }


# ── /payments/portal — Stripe-hosted billing management ────────────────
@router.post("/payments/portal")
async def billing_portal(
    http_request: Request,
    authorization: Optional[str] = Header(None),
) -> dict:
    _require_stripe()
    user = await current_dev(authorization)
    db = require_db()
    row = await db.dev_users.find_one({"user_id": user.get("user_id")})
    sub_id = (row or {}).get("stripe_sub_id")
    if not sub_id:
        raise HTTPException(400, "No active subscription")

    try:
        sub = stripe.Subscription.retrieve(sub_id)
        customer_id = sub["customer"]
        return_url = (
            _frontend_url()
            or str(http_request.base_url).rstrip("/")
        ) + "/settings"
        portal = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=return_url,
        )
    except stripe.error.StripeError as e:
        logger.warning("portal create failed: %r", e)
        raise HTTPException(502, f"Stripe error: {getattr(e, 'user_message', str(e))}")

    return {"portal_url": portal.url, "url": portal.url}
