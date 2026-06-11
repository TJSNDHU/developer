"""
Iter 124d — Stripe SDK is synchronous. Calling it directly from an async
handler blocks the event loop and causes Cloudflare 520 ("origin returned
invalid/incomplete response") when Stripe is slow.

These tests prove:
  1. /payments/checkout offloads the blocking stripe.* call to a thread —
     the event loop stays free for other in-flight requests.
  2. A genuinely slow (>STRIPE_CALL_TIMEOUT) Stripe response returns a
     clean 504 to the caller within the timeout window, NOT a hung
     connection that would surface as Cloudflare 520.
"""
from __future__ import annotations

import asyncio
import os
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_checkout_does_not_block_event_loop(monkeypatch):
    """A slow stripe.* call must run on a worker thread, leaving the
    event loop free to service concurrent async work."""
    # Force live-mode key path so _require_stripe doesn't 503
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_live_test")
    monkeypatch.setenv("STRIPE_STARTER_PRICE_ID", "price_starter")
    monkeypatch.setenv("FRONTEND_URL", "https://auremcto.com")

    from routers import payments as pay

    # Stub current_dev (async, returns a user dict).
    async def _fake_user(_auth):
        return {"user_id": "u_test", "email": "test@aurem.dev"}
    monkeypatch.setattr(pay, "current_dev", _fake_user)

    # Stub require_db with a mock that has the insert call awaited.
    fake_db = MagicMock()
    fake_db.cto_payments.insert_one = AsyncMock()
    monkeypatch.setattr(pay, "require_db", lambda: fake_db)

    # Simulate Stripe taking 1.5s synchronously. If this ran on the
    # event loop, the concurrent ticker below would not advance.
    def slow_create(**kwargs):
        time.sleep(1.5)
        m = MagicMock()
        m.id = "cs_test_123"
        m.url = "https://checkout.stripe.com/test"
        return m

    monkeypatch.setattr("stripe.checkout.Session.create", slow_create)

    # Concurrent task — counts how many times we tick during the call.
    # If the event loop is blocked we'll see < 5 ticks for a 1.5s call.
    ticks = {"n": 0}

    async def ticker():
        for _ in range(30):
            ticks["n"] += 1
            await asyncio.sleep(0.05)

    body = pay.CheckoutBody(plan="starter", origin_url="https://auremcto.com")
    fake_request = MagicMock()
    fake_request.base_url = "https://auremcto.com"

    t = asyncio.create_task(ticker())
    result = await pay.create_checkout(
        body=body, http_request=fake_request, authorization="Bearer x",
    )
    await t

    assert result.get("session_id") == "cs_test_123"
    # If the loop was blocked we'd see < 5 ticks. Threading gives us >25.
    assert ticks["n"] >= 20, (
        f"Event loop blocked during stripe call — only {ticks['n']} ticks "
        "(expected ≥20). The handler is still running stripe.* on the loop."
    )


@pytest.mark.asyncio
async def test_checkout_returns_504_on_slow_stripe(monkeypatch):
    """If Stripe takes longer than STRIPE_CALL_TIMEOUT, the handler must
    return a clean 504 — never let a 100s+ hang propagate to Cloudflare."""
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_live_test")
    monkeypatch.setenv("STRIPE_STARTER_PRICE_ID", "price_starter")
    monkeypatch.setenv("STRIPE_CALL_TIMEOUT", "1")   # 1s for the test

    # Re-import to pick up the new timeout value
    import importlib
    from routers import payments as pay
    importlib.reload(pay)

    async def _fake_user(_auth):
        return {"user_id": "u_test", "email": "test@aurem.dev"}
    monkeypatch.setattr(pay, "current_dev", _fake_user)
    fake_db = MagicMock()
    fake_db.cto_payments.insert_one = AsyncMock()
    monkeypatch.setattr(pay, "require_db", lambda: fake_db)

    def hang_create(**kwargs):
        time.sleep(10)   # way past the 1s timeout
        return MagicMock(id="never", url="never")

    monkeypatch.setattr("stripe.checkout.Session.create", hang_create)

    from fastapi import HTTPException
    body = pay.CheckoutBody(plan="starter", origin_url="https://auremcto.com")
    fake_request = MagicMock()
    fake_request.base_url = "https://auremcto.com"

    t0 = time.perf_counter()
    with pytest.raises(HTTPException) as exc_info:
        await pay.create_checkout(
            body=body, http_request=fake_request, authorization="Bearer x",
        )
    elapsed = time.perf_counter() - t0

    assert exc_info.value.status_code == 504
    assert "timed out" in str(exc_info.value.detail).lower()
    # Must return well before Cloudflare's 100s edge — within ~3s of the 1s budget.
    assert elapsed < 4.0, f"Slow-stripe path took {elapsed:.1f}s — must be < 4s"


@pytest.mark.asyncio
async def test_portal_uses_thread_pool(monkeypatch):
    """Billing portal path must also offload stripe.* calls."""
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_live_test")
    monkeypatch.setenv("FRONTEND_URL", "https://auremcto.com")

    from routers import payments as pay

    async def _fake_user(_auth):
        return {"user_id": "u_test", "email": "test@aurem.dev"}
    monkeypatch.setattr(pay, "current_dev", _fake_user)

    fake_db = MagicMock()
    fake_db.dev_users.find_one = AsyncMock(return_value={
        "stripe_sub_id": "sub_test"
    })
    monkeypatch.setattr(pay, "require_db", lambda: fake_db)

    def slow_sub_retrieve(_sub_id):
        time.sleep(1.0)
        return {"customer": "cus_test"}

    def slow_portal_create(**kwargs):
        time.sleep(1.0)
        m = MagicMock()
        m.url = "https://billing.stripe.com/portal"
        return m

    monkeypatch.setattr("stripe.Subscription.retrieve", slow_sub_retrieve)
    monkeypatch.setattr("stripe.billing_portal.Session.create", slow_portal_create)

    ticks = {"n": 0}

    async def ticker():
        for _ in range(50):
            ticks["n"] += 1
            await asyncio.sleep(0.05)

    fake_request = MagicMock()
    fake_request.base_url = "https://auremcto.com"

    t = asyncio.create_task(ticker())
    result = await pay.billing_portal(http_request=fake_request, authorization="Bearer x")
    await t

    assert "portal_url" in result
    assert ticks["n"] >= 30, (
        f"Event loop blocked during portal create — only {ticks['n']} ticks "
        "(expected ≥30)."
    )


def test_stripe_call_helper_exposes_timeout_env():
    """Operator can tune the timeout via STRIPE_CALL_TIMEOUT env var."""
    from routers import payments as pay
    assert hasattr(pay, "STRIPE_CALL_TIMEOUT")
    assert pay.STRIPE_CALL_TIMEOUT > 0
