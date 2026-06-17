"""
test_iter179_payments_defensive.py — verify the defensive catch-all in
payments.py turns ANY non-Stripe exception inside the threaded stripe.*
call into a clean HTTPException(502) JSON instead of letting it bubble
up as a raw Python exception (which in prod gets converted to a generic
CF 502 HTML page by the edge proxy).

User-visible bug: clicking "Upgrade to Pro" on https://auremcto.com
returned a Cloudflare 502 HTML body in the red pricing-error pill,
instead of a JSON {detail: ...} from FastAPI. The fix:

  1. `_stripe_call` now catches `Exception` (after TimeoutError /
     HTTPException / StripeError) and re-raises as HTTPException(502)
     with a user-friendly detail.
  2. `create_checkout`, `billing_portal`, `payment_status` each grew a
     final `except Exception` that does the same at the handler level
     for any non-stripe failure paths we missed.
"""
from __future__ import annotations

import asyncio
import importlib
import os
import sys
import types

import pytest

# Ensure backend module is importable
sys.path.insert(0, "/app/backend")

# Force a non-empty key so _require_stripe doesn't 503 in unit tests
os.environ.setdefault("STRIPE_SECRET_KEY", "sk_test_dummy_for_unit_test")
os.environ.setdefault("STRIPE_STARTER_PRICE_ID", "price_starter_test")
os.environ.setdefault("STRIPE_PRO_PRICE_ID", "price_pro_test")
os.environ.setdefault("STRIPE_TEAM_PRICE_ID", "price_team_test")


@pytest.fixture()
def payments_mod(monkeypatch):
    import stripe  # type: ignore
    # Strip any real network exposure
    monkeypatch.setattr(stripe, "api_key", "sk_test_dummy", raising=False)
    mod = importlib.import_module("routers.payments")
    return mod


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_stripe_call_wraps_generic_exception_as_502(payments_mod):
    """A bare Python exception in the threaded fn must become 502, not bubble."""
    from fastapi import HTTPException

    def boom():
        raise RuntimeError("worker crashed in stripe sdk")

    loop = asyncio.new_event_loop()
    try:
        with pytest.raises(HTTPException) as ei:
            loop.run_until_complete(payments_mod._stripe_call(boom))
        assert ei.value.status_code == 502
        assert "unavailable" in str(ei.value.detail).lower()
    finally:
        loop.close()


def test_stripe_call_wraps_import_error_as_502(payments_mod):
    """ImportError from a misconfigured prod pod must become 502."""
    from fastapi import HTTPException

    def bad_import():
        raise ImportError("stripe.http_client missing in prod")

    loop = asyncio.new_event_loop()
    try:
        with pytest.raises(HTTPException) as ei:
            loop.run_until_complete(payments_mod._stripe_call(bad_import))
        assert ei.value.status_code == 502
    finally:
        loop.close()


def test_stripe_call_preserves_stripe_error(payments_mod):
    """StripeError must NOT be swallowed — callers format it."""
    import stripe  # type: ignore

    def card_decline():
        raise stripe.error.CardError("declined", param="card", code="card_declined")

    loop = asyncio.new_event_loop()
    try:
        with pytest.raises(stripe.error.StripeError):
            loop.run_until_complete(payments_mod._stripe_call(card_decline))
    finally:
        loop.close()


def test_stripe_call_preserves_httpexception(payments_mod):
    """An HTTPException raised inside the fn must propagate unchanged."""
    from fastapi import HTTPException

    def already_http():
        raise HTTPException(403, "forbidden")

    loop = asyncio.new_event_loop()
    try:
        with pytest.raises(HTTPException) as ei:
            loop.run_until_complete(payments_mod._stripe_call(already_http))
        assert ei.value.status_code == 403
    finally:
        loop.close()


def test_stripe_call_timeout_returns_504(payments_mod, monkeypatch):
    """Slow stripe call > timeout becomes 504, not a hung worker."""
    from fastapi import HTTPException

    monkeypatch.setattr(payments_mod, "STRIPE_CALL_TIMEOUT", 0.1)

    def slow():
        import time
        time.sleep(0.5)

    loop = asyncio.new_event_loop()
    try:
        with pytest.raises(HTTPException) as ei:
            loop.run_until_complete(payments_mod._stripe_call(slow))
        assert ei.value.status_code == 504
    finally:
        loop.close()
