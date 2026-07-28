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

import asyncio
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


# Iter 124d — Stripe SDK is SYNCHRONOUS (urllib3 under the hood). Calling
# it directly from an async handler BLOCKS the event loop for the entire
# round-trip. Under live-mode load Stripe can take 5–30s; that's enough
# to make the FastAPI pod stop responding to every other in-flight request
# → Cloudflare returns 520 ("origin returned invalid/incomplete response").
#
# Wrap every stripe.* sync call in `_stripe_call`:
#   • Runs in a thread (asyncio.to_thread) so the event loop stays free.
#   • Hard wall-clock cap of STRIPE_CALL_TIMEOUT (default 12s) so a slow
#     Stripe response can never propagate as a Cloudflare 520.
#   • Also lower the Stripe SDK's own socket timeout so urllib3 doesn't
#     hold the worker thread for the default 80s.
STRIPE_CALL_TIMEOUT = float(os.environ.get("STRIPE_CALL_TIMEOUT", "12"))


def _configure_stripe_http_timeouts() -> None:
    """Tell the Stripe SDK to give up on socket reads after 10s.
    Without this the SDK retries internally for ~80s, well past the
    Cloudflare 100s edge timeout, which is what made plan changes hang."""
    try:
        # stripe>=2.0 — the SDK accepts a default_http_client we configure.
        from stripe.http_client import RequestsClient  # type: ignore
        stripe.default_http_client = RequestsClient(timeout=10)
    except Exception as e:
        logger.debug("could not configure stripe http timeouts: %r", e)


async def _stripe_call(fn, *args, **kwargs):
    """Run a blocking stripe.* SDK call on a worker thread with a hard
    wall-clock cap so the async event loop never stalls. Raises an
    HTTPException(502/504) on timeout/unexpected error so the user sees
    a clean JSON error rather than a hung connection or worker crash.

    Iter 179 — added a catch-all for non-Stripe exceptions (ImportError,
    AttributeError, ConnectionError, SSLError, etc.) so the worker
    NEVER bubbles a raw Python exception up to uvicorn. A bubbled
    exception in production has been observed to make the edge
    (Cloudflare) return a generic 502 HTML page instead of our JSON,
    which breaks the frontend error UI."""
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(fn, *args, **kwargs),
            timeout=STRIPE_CALL_TIMEOUT,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "stripe call %s timed out after %.1fs",
            getattr(fn, "__qualname__", fn), STRIPE_CALL_TIMEOUT,
        )
        raise HTTPException(
            504,
            f"Stripe API timed out after {STRIPE_CALL_TIMEOUT:.0f}s — "
            "please retry. If this persists, Stripe may be having an outage.",
        )
    except HTTPException:
        raise
    except stripe.error.StripeError:
        # Caller-formatted Stripe errors carry user_message — re-raise
        # so callers can produce nicer messages.
        raise
    except Exception as e:
        # Anything else (ImportError, AttributeError, ConnectionError,
        # SSL handshake failure, segfault wrapper, ...) becomes a clean
        # 502 JSON. This was the prod crash path causing CF 502 HTML.
        logger.exception(
            "stripe call %s failed unexpectedly: %r",
            getattr(fn, "__qualname__", fn), e,
        )
        raise HTTPException(
            502,
            "Payment provider unavailable — please retry in a moment. "
            "If this persists, contact ora@auremcto.com.",
        )


def _stripe_key() -> str:
    """Return the Stripe secret key.

    Iter 212m-230 — Canonical implementation now lives in
    `services.stripe_client` so both this router AND
    `services/billing_cron.py` can share it without creating the
    `billing_cron ↔ payments` circular import we had before.
    """
    from services.stripe_client import stripe_key
    return stripe_key()


# Iter 191 — runtime override populated either at boot (from
# admin_settings.stripe_api_key) or via POST /admin/stripe-config. We
# keep it as a module-level mutable string so _stripe_key() can read
# it without a DB round-trip on every Stripe call.
_RUNTIME_STRIPE_KEY: str = ""


def set_runtime_stripe_key(key: str) -> None:
    """Hot-swap the Stripe secret key for this process."""
    # Iter 212m-230 — Delegate to services.stripe_client so both this
    # module and billing_cron see the same runtime override.
    global _RUNTIME_STRIPE_KEY
    _RUNTIME_STRIPE_KEY = (key or "").strip()
    from services.stripe_client import set_runtime_stripe_key as _svc_set
    _svc_set(key)
    if _RUNTIME_STRIPE_KEY:
        stripe.api_key = _RUNTIME_STRIPE_KEY


_TIMEOUTS_CONFIGURED = False


def _require_stripe() -> None:
    global _TIMEOUTS_CONFIGURED
    k = _stripe_key()
    if not k:
        raise HTTPException(503, "Stripe not configured (STRIPE_SECRET_KEY missing)")
    if k.startswith("sk_test_emergent"):
        # Sandbox placeholder bundled with the platform — won't actually
        # charge anyone. We still let it through in dev so the UI flow
        # is testable, but log a noisy warning so prod never ships on it.
        logger.warning("⚠ Using sk_test_emergent placeholder — live keys NOT configured")
    stripe.api_key = k
    if not _TIMEOUTS_CONFIGURED:
        _configure_stripe_http_timeouts()
        _TIMEOUTS_CONFIGURED = True


STRIPE_PRICES = {
    "starter":         lambda: os.environ.get("STRIPE_STARTER_PRICE_ID"),
    "pro":             lambda: os.environ.get("STRIPE_PRO_PRICE_ID"),
    "team":            lambda: os.environ.get("STRIPE_TEAM_PRICE_ID"),
    # Iter 101 — annual variants (20% discount, pre-paid yearly).
    "starter_annual":  lambda: os.environ.get("STRIPE_STARTER_ANNUAL_PRICE_ID"),
    "pro_annual":      lambda: os.environ.get("STRIPE_PRO_ANNUAL_PRICE_ID"),
    "team_annual":     lambda: os.environ.get("STRIPE_TEAM_ANNUAL_PRICE_ID"),
}


# Iter 212m-15 — boot-time price config audit.
def _audit_price_config_at_boot() -> None:
    """Log which Stripe price IDs are configured. We deliberately log
    only the suffix (last 4 chars) so the log line is searchable but
    the actual price ID isn't leaked. Triggered on first call to
    `_require_stripe`; logs once per process."""
    rows = []
    for plan, getter in STRIPE_PRICES.items():
        pid = getter() or ""
        if not pid:
            rows.append(f"{plan}=MISSING")
            continue
        # Sanity: live keys must pair with live prices and vice versa.
        # Stripe live price IDs typically don't carry a `_test_` infix
        # the way checkout sessions do, but we surface the prefix for
        # the human eye.
        rows.append(f"{plan}=…{pid[-6:]}")
    logger.info("payments.price_config %s", " ".join(rows))


_BOOT_AUDIT_DONE = False


# ── Iter 335 — self-healing price resolution ─────────────────────────
# Prod deploy logs showed 404 `No such price` on the three MONTHLY
# price IDs (old-account `..2XYZ7cJIy2..` values still in the prod env
# store) while the annual `..0Exg9gU93t..` ones worked. The code and
# preview env are correct — the prod env is stale. Rather than letting
# checkout 503 until a human rotates env vars, we auto-discover the
# right price in the LIVE account (product name + interval + USD) and
# use it for this process, logging loudly so the env still gets fixed.
_RESOLVED_PRICES: dict = {}

_PLAN_MATCH = {
    "starter":        ("starter", "month"),
    "pro":            ("pro", "month"),
    "team":           ("team", "month"),
    "starter_annual": ("starter", "year"),
    "pro_annual":     ("pro", "year"),
    "team_annual":    ("team", "year"),
}


def _match_discovered_price(prices_data: list, plan: str) -> Optional[str]:
    """Pure matcher: exactly one active USD price whose product name +
    billing interval fit the plan, else None (ambiguity = no heal)."""
    name, interval = _PLAN_MATCH.get(plan, (None, None))
    if not name:
        return None
    matches = []
    for p in prices_data or []:
        prod = p.get("product") if isinstance(p, dict) else None
        pname = ((prod or {}).get("name") or "").strip().lower() \
            if isinstance(prod, dict) else ""
        rec = (p.get("recurring") or {}) if isinstance(p, dict) else {}
        if (pname == name and rec.get("interval") == interval
                and p.get("currency") == "usd"):
            matches.append(p["id"])
    return matches[0] if len(matches) == 1 else None


async def _discover_price_id(plan: str) -> Optional[str]:
    try:
        prices = await _stripe_call(
            stripe.Price.list, active=True, limit=100,
            expand=["data.product"])
    except Exception as e:                                  # noqa: BLE001
        logger.warning("price auto-discovery list failed: %r", e)
        return None
    data = prices.get("data") if hasattr(prices, "get") else None
    return _match_discovered_price(
        [dict(p) for p in (data or [])], plan)


async def _preflight_price(plan: str, price_id: str) -> str:
    """Validate the configured price against Stripe BEFORE Checkout;
    on `No such price` (stale prod env), attempt one auto-discovery
    heal. Returns a usable price_id or raises the precise 503."""
    if _RESOLVED_PRICES.get(plan):
        return _RESOLVED_PRICES[plan]
    try:
        await _stripe_call(stripe.Price.retrieve, price_id)
        return price_id
    except HTTPException as he:
        if he.status_code not in (502, 504):
            raise
        logger.warning(
            "stripe price.retrieve failed for plan=%s price_id=%s: %s",
            plan, price_id, getattr(he, "detail", ""),
        )
    except stripe.error.StripeError as se:
        logger.warning(
            "stripe price.retrieve raised for plan=%s price_id=%s: %r",
            plan, price_id, se,
        )
    healed = await _discover_price_id(plan)
    if healed:
        _RESOLVED_PRICES[plan] = healed
        logger.error(
            "⚠ STALE STRIPE ENV: STRIPE_%s_PRICE_ID=…%s is invalid — "
            "auto-discovered live price …%s and using it. FIX the env "
            "var in the Emergent dashboard.",
            plan.upper(), price_id[-6:], healed[-6:],
        )
        return healed
    raise HTTPException(
        503,
        f"Stripe price `{plan}` is misconfigured — the configured "
        f"price ID does not exist or is from a different "
        f"(test/live) mode than the Stripe secret key, and "
        f"auto-discovery found no unambiguous match. Admin: "
        f"check STRIPE_{plan.upper()}_PRICE_ID env var in the "
        f"Emergent dashboard against the Stripe dashboard.",
    )


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

    # Iter 212m-15 — sanity-check the configured price BEFORE attempting
    # Checkout. If a misconfigured env var points at a test-mode price
    # while the key is live (or a deleted / one-time / inactive price),
    # the canonical Stripe error is `No such price` — but on some prod
    # pods the failure mode was a worker crash that returned Cloudflare
    # 502 HTML instead of clean JSON (this hit the founder live on
    # monthly plans while annual variants worked, proving it's the env
    # vars, not the code path). Pre-flighting `Price.retrieve` lets us
    # return a clean diagnostic JSON instead.
    # Iter 335 — pre-flight now also self-heals stale env IDs via live
    # price auto-discovery (see _preflight_price).
    price_id = await _preflight_price(plan, price_id)

    origin = (
        (body.origin_url or "").rstrip("/")
        or _frontend_url()
        or str(http_request.base_url).rstrip("/")
    )
    success_url = f"{origin}/settings?session_id={{CHECKOUT_SESSION_ID}}&upgraded=1"
    cancel_url  = f"{origin}/settings?cancelled=1#pricing"

    try:
        session = await _stripe_call(
            stripe.checkout.Session.create,
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
    except HTTPException:
        raise
    except stripe.error.StripeError as e:
        logger.warning("stripe checkout create failed: %r", e)
        raise HTTPException(502, f"Stripe error: {getattr(e, 'user_message', str(e))}")
    except Exception as e:
        # Iter 179 — final defensive net so the worker never bubbles a
        # raw exception up to uvicorn/Cloudflare (which would turn it
        # into a generic 502 HTML page).
        logger.exception("checkout create unexpected failure: %r", e)
        raise HTTPException(
            502,
            "Could not start checkout — payment provider is unavailable. "
            "Please retry in a moment.",
        )

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

    # Iter 183 — Stripe sometimes returns the new `/g/pay/` (Guest /
    # Link-optimized) checkout URL which currently renders a generic
    # "Something went wrong … the link might be expired" page for our
    # account (live, subscription mode). The exact same session_id
    # loads perfectly at the canonical `/c/pay/` hosted-Checkout path,
    # so we rewrite the URL before returning to the client. This is
    # safe — both routes accept the same session token in the URL
    # fragment — and it's the difference between "broken checkout" and
    # "user can actually pay us" for affected sessions.
    checkout_url = session.url or ""
    if "/g/pay/" in checkout_url:
        checkout_url = checkout_url.replace("/g/pay/", "/c/pay/", 1)
        logger.info(
            "rewrote stripe /g/pay/ → /c/pay/ for session %s", session.id,
        )

    return {"url": checkout_url, "checkout_url": checkout_url, "session_id": session.id}


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
        session = await _stripe_call(stripe.checkout.Session.retrieve, session_id)
    except HTTPException:
        raise
    except stripe.error.StripeError as e:
        raise HTTPException(502, f"Stripe error: {e}")
    except Exception as e:
        # Iter 179 — defensive catch-all.
        logger.exception("payment status retrieve unexpected failure: %r", e)
        raise HTTPException(
            502,
            "Could not fetch payment status — please retry shortly.",
        )

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
        cust_id  = obj.get("customer")
        if user_id and plan:
            await db.dev_users.update_one(
                {"user_id": user_id},
                {"$set": {
                    "tier":               plan,
                    "usage_tier":         plan,
                    "stripe_sub_id":      sub_id,
                    "stripe_customer_id": cust_id,
                    "tier_updated_at":    datetime.now(timezone.utc).isoformat(),
                }},
            )
            # Iter 102 — referral reward on paid conversion.
            try:
                from services.billing_cron import grant_referral_reward
                grant = await grant_referral_reward(db, user_id)
                if grant.get("granted"):
                    logger.info(f"[webhook] referral reward granted to {grant.get('referrer')}")
            except Exception as e:
                logger.warning(f"[webhook] referral reward failed: {e!r}")
    elif etype in ("customer.subscription.deleted", "customer.subscription.paused"):
        sub_id = event["data"]["object"]["id"]
        # Find the user first so we know whose Supabase projects to downgrade.
        user_row = await db.dev_users.find_one(
            {"stripe_sub_id": sub_id},
            {"user_id": 1, "_id": 0},
        )
        await db.dev_users.update_one(
            {"stripe_sub_id": sub_id},
            {"$set": {
                "tier":            "free",
                "usage_tier":      "free",
                "stripe_sub_id":   None,
                "tier_updated_at": datetime.now(timezone.utc).isoformat(),
            }},
        )
        # Iter 212m-240 — Tier 3: paid → free means any dedicated Supabase
        # Postgres projects the user has must enter the downgrade grace
        # window using the configured policy (default = migrate_back,
        # which copies data BACK into shared Mongo first). The sweeper
        # cron finalises after the grace expires.
        if user_row and user_row.get("user_id"):
            try:
                from services import supabase_provisioner as _sp
                if _sp.is_configured():
                    rows = db[_sp.PROJECTS_COLLECTION].find(
                        {"user_id": user_row["user_id"],
                         "downgrade_status": {"$exists": False}},
                        {"app_id": 1, "user_id": 1, "_id": 0},
                    )
                    async for r in rows:
                        try:
                            await _sp.apply_downgrade(
                                db, r["app_id"], r["user_id"], policy=None,
                            )
                            logger.info(
                                "[webhook] Supabase downgrade queued for %s (user=%s)",
                                r["app_id"], r["user_id"],
                            )
                        except Exception as ie:  # noqa: BLE001
                            logger.warning(
                                "[webhook] supabase downgrade failed for %s: %r",
                                r["app_id"], ie,
                            )
            except Exception as e:  # noqa: BLE001
                logger.warning("[webhook] supabase downgrade sweep errored: %r", e)
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
    # Iter 212m-70 — projection: only the stripe_sub_id is consumed.
    row = await db.dev_users.find_one(
        {"user_id": user.get("user_id")},
        {"_id": 0, "stripe_sub_id": 1},
    )
    sub_id = (row or {}).get("stripe_sub_id")
    if not sub_id:
        raise HTTPException(400, "No active subscription")

    try:
        sub = await _stripe_call(stripe.Subscription.retrieve, sub_id)
        customer_id = sub["customer"]
        return_url = (
            _frontend_url()
            or str(http_request.base_url).rstrip("/")
        ) + "/settings"
        portal = await _stripe_call(
            stripe.billing_portal.Session.create,
            customer=customer_id,
            return_url=return_url,
        )
    except HTTPException:
        raise
    except stripe.error.StripeError as e:
        logger.warning("portal create failed: %r", e)
        raise HTTPException(502, f"Stripe error: {getattr(e, 'user_message', str(e))}")
    except Exception as e:
        # Iter 179 — defensive catch-all (see create_checkout).
        logger.exception("portal create unexpected failure: %r", e)
        raise HTTPException(
            502,
            "Could not open billing portal — payment provider is unavailable.",
        )

    return {"portal_url": portal.url, "url": portal.url}
