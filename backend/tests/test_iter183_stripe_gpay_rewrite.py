"""
test_iter183_stripe_gpay_rewrite.py — Regression test for the Stripe
`/g/pay/` → `/c/pay/` URL-rewrite fix shipped in iter 183.

Why this test exists:
  Stripe inconsistently returns two checkout URL formats for the SAME
  Checkout Session: the new "Guest/Link-optimized" `/g/pay/…` path and
  the canonical "hosted Checkout" `/c/pay/…` path. For our live account
  (subscription mode, payment_method_types=["card"]), `/g/pay/` URLs
  render a generic "Something went wrong … the link might be expired"
  error page, while the same session_id at `/c/pay/` shows a fully
  functional Stripe payment form. So we rewrite `/g/pay/` → `/c/pay/`
  before returning the URL to the client.

  This test mocks the Stripe SDK to deterministically return a
  `/g/pay/` URL and asserts the response surfaces `/c/pay/`.
"""
from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client():
    # Import the FastAPI app lazily so any env tweaks above apply first.
    from main import app  # type: ignore
    return TestClient(app)


def _fake_session(url: str):
    return SimpleNamespace(id="cs_live_test_fake", url=url, status="open")


@pytest.mark.asyncio
async def test_gpay_url_is_rewritten_to_cpay(client, monkeypatch):
    """When Stripe returns /g/pay/, we MUST rewrite to /c/pay/."""
    # Stub auth → returns a fake user (no real JWT verification needed).
    async def fake_current_dev(*args, **kwargs):
        return {"user_id": "uid_test_183", "email": "test@aurem.dev"}

    monkeypatch.setattr("routers.payments.current_dev", fake_current_dev)
    monkeypatch.setenv("STRIPE_PRO_PRICE_ID", "price_fake_pro")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_live_fake")

    # Stub the Stripe SDK Checkout.Session.create through _stripe_call.
    fake_url = "https://checkout.stripe.com/g/pay/cs_live_test_fake#frag"
    expected = "https://checkout.stripe.com/c/pay/cs_live_test_fake#frag"

    async def fake_stripe_call(fn, *args, **kwargs):
        return _fake_session(fake_url)

    monkeypatch.setattr("routers.payments._stripe_call", fake_stripe_call)

    # Stub the DB insert.
    fake_db = SimpleNamespace(cto_payments=SimpleNamespace(
        insert_one=AsyncMock(return_value=None),
    ))
    monkeypatch.setattr("routers.payments.require_db", lambda: fake_db)

    r = client.post(
        "/api/aurem-dev/payments/checkout",
        json={"plan": "pro", "origin_url": "https://example.com"},
        headers={"Authorization": "Bearer fake"},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["checkout_url"] == expected
    assert data["url"] == expected
    assert "/g/pay/" not in data["checkout_url"]


@pytest.mark.asyncio
async def test_cpay_url_is_passed_through_unchanged(client, monkeypatch):
    """When Stripe returns /c/pay/, we MUST NOT touch it."""
    async def fake_current_dev(*args, **kwargs):
        return {"user_id": "uid_test_183b", "email": "test@aurem.dev"}

    monkeypatch.setattr("routers.payments.current_dev", fake_current_dev)
    monkeypatch.setenv("STRIPE_PRO_PRICE_ID", "price_fake_pro")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_live_fake")

    fake_url = "https://checkout.stripe.com/c/pay/cs_live_already_canonical#frag"

    async def fake_stripe_call(fn, *args, **kwargs):
        return _fake_session(fake_url)

    monkeypatch.setattr("routers.payments._stripe_call", fake_stripe_call)
    fake_db = SimpleNamespace(cto_payments=SimpleNamespace(
        insert_one=AsyncMock(return_value=None),
    ))
    monkeypatch.setattr("routers.payments.require_db", lambda: fake_db)

    r = client.post(
        "/api/aurem-dev/payments/checkout",
        json={"plan": "pro", "origin_url": "https://example.com"},
        headers={"Authorization": "Bearer fake"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["checkout_url"] == fake_url
