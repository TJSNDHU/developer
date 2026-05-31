"""
routers/payments.py — Stripe Checkout for tier upgrades.

Three tiers (server-defined to prevent client price manipulation):
  pro:  $29
  team: $99

Flow per the Emergent Stripe playbook:
  1) Frontend POSTs {tier, origin_url} → we create a Checkout session,
     record an INITIATED transaction in `cto_payments`, return the URL
  2) Stripe redirects to <origin>/dashboard?session_id={CHECKOUT_SESSION_ID}
  3) Frontend polls GET /payments/status/{session_id}
  4) On `payment_status == 'paid'` we flip the user's `tier` and mark
     the transaction `paid` exactly once (idempotent)
  5) Stripe also POSTs to /api/webhook/stripe — same idempotent update
"""
from __future__ import annotations

import logging
import os
import time
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel

from cto_services.auth import current_dev
from cto_services.db import require_db

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Payments"])

# Server-defined packages — NEVER trust amount from client.
PACKAGES = {
    "pro":  {"amount": 29.00,  "currency": "usd", "label": "AUREM Pro"},
    "team": {"amount": 99.00,  "currency": "usd", "label": "AUREM Team"},
}


def _api_key() -> str:
    k = os.environ.get("STRIPE_API_KEY", "")
    if not k:
        raise HTTPException(503, "Stripe not configured (STRIPE_API_KEY missing)")
    return k


class CheckoutBody(BaseModel):
    tier: str           # "pro" | "team"
    origin_url: str     # window.location.origin — used for redirects


@router.post("/payments/checkout")
async def create_checkout(
    body: CheckoutBody,
    http_request: Request,
    authorization: Optional[str] = Header(None),
) -> dict:
    user = await current_dev(authorization)
    pkg = PACKAGES.get(body.tier)
    if not pkg:
        raise HTTPException(400, f"Unknown tier `{body.tier}`")

    from emergentintegrations.payments.stripe.checkout import (
        StripeCheckout, CheckoutSessionRequest,
    )
    host_url = str(http_request.base_url).rstrip("/")
    webhook_url = f"{host_url}/api/aurem-dev/webhook/stripe"
    sc = StripeCheckout(api_key=_api_key(), webhook_url=webhook_url)

    success_url = f"{body.origin_url.rstrip('/')}/admin?session_id={{CHECKOUT_SESSION_ID}}"
    cancel_url = f"{body.origin_url.rstrip('/')}/admin"
    req = CheckoutSessionRequest(
        amount=pkg["amount"],
        currency=pkg["currency"],
        success_url=success_url,
        cancel_url=cancel_url,
        metadata={
            "user_id": user.get("user_id", ""),
            "email": user.get("email", ""),
            "tier": body.tier,
        },
    )
    session = await sc.create_checkout_session(req)

    db = require_db()
    await db.cto_payments.insert_one({
        "session_id": session.session_id,
        "user_id": user.get("user_id"),
        "user_email": user.get("email"),
        "tier": body.tier,
        "amount": pkg["amount"],
        "currency": pkg["currency"],
        "status": "initiated",
        "payment_status": "pending",
        "created_at": time.time(),
    })
    return {"url": session.url, "session_id": session.session_id}


async def _flip_tier_idempotent(db, session_id: str) -> dict:
    """Read Stripe status, flip user tier exactly once. Returns the
    payment doc (with updated status)."""
    from emergentintegrations.payments.stripe.checkout import StripeCheckout
    sc = StripeCheckout(api_key=_api_key(), webhook_url="")
    status = await sc.get_checkout_status(session_id)

    pay = await db.cto_payments.find_one({"session_id": session_id})
    if not pay:
        raise HTTPException(404, "Unknown session")

    # Always refresh the payment_status from Stripe
    update: dict = {
        "payment_status": status.payment_status,
        "status": status.status,
        "updated_at": time.time(),
    }
    # Only flip tier ONCE — guard against double-credit
    if (status.payment_status == "paid"
            and pay.get("payment_status") != "paid"):
        update["paid_at"] = time.time()
        await db.dev_users.update_one(
            {"user_id": pay["user_id"]},
            {"$set": {"tier": pay["tier"], "tier_paid_at": time.time()}},
        )
    await db.cto_payments.update_one({"session_id": session_id}, {"$set": update})
    return {**pay, **update}


@router.get("/payments/status/{session_id}")
async def get_payment_status(
    session_id: str,
    authorization: Optional[str] = Header(None),
) -> dict:
    user = await current_dev(authorization)
    db = require_db()
    pay = await db.cto_payments.find_one({"session_id": session_id})
    if not pay or pay.get("user_id") != user.get("user_id"):
        raise HTTPException(404, "Unknown session")
    refreshed = await _flip_tier_idempotent(db, session_id)
    return {
        "session_id": session_id,
        "payment_status": refreshed.get("payment_status"),
        "status": refreshed.get("status"),
        "tier": refreshed.get("tier"),
        "amount": refreshed.get("amount"),
    }


@router.post("/webhook/stripe")
async def stripe_webhook(request: Request) -> dict:
    """Stripe → us. We just refresh the status using the same idempotent
    flow (Stripe's library verifies the signature for us)."""
    raw = await request.body()
    sig = request.headers.get("Stripe-Signature", "")
    from emergentintegrations.payments.stripe.checkout import StripeCheckout
    sc = StripeCheckout(api_key=_api_key(), webhook_url="")
    try:
        event = await sc.handle_webhook(raw, sig)
    except Exception as e:
        logger.warning(f"stripe webhook verify failed: {e!r}")
        raise HTTPException(400, "Invalid webhook")
    if event.session_id:
        db = require_db()
        try:
            await _flip_tier_idempotent(db, event.session_id)
        except Exception as e:
            logger.warning(f"stripe webhook flip failed: {e!r}")
    return {"ok": True}
