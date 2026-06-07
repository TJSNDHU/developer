"""
test_iter90_stripe_real_prices.py — locks in the real Stripe price/product
IDs for Starter / Pro / Team plus the env-loading fix that bypasses the
platform's stale `sk_test_emergent…` placeholder.

We do NOT hit the Stripe network here (CI must run offline) — instead we
assert:
  • The .env file contains real `price_*` IDs matching the expected
    Stripe account suffix `_Exg9gU93t` (not the fake `XYZ` placeholders).
  • `_stripe_key()` ignores the `sk_test_emergent` placeholder when a
    real key is present in .env.
  • The /payments/checkout route correctly maps `plan` → env price ID.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest


REAL_ACCT_SUFFIX = "0Exg9gU93t"   # tail of acct_1TKUU90Exg9gU93t, embedded in every price_*
ENV_PATH = Path(__file__).resolve().parents[1] / ".env"


def _dotenv_value(key: str) -> str:
    """Read raw value from .env file (bypassing process env)."""
    text = ENV_PATH.read_text()
    for line in text.splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


@pytest.mark.parametrize("plan, env_key, expected_amount_cents", [
    ("starter", "STRIPE_STARTER_PRICE_ID",  900),
    ("pro",     "STRIPE_PRO_PRICE_ID",     1900),
    ("team",    "STRIPE_TEAM_PRICE_ID",    3500),
])
def test_real_stripe_price_ids_in_env(plan, env_key, expected_amount_cents):
    """Each plan must have a real price_* ID rooted in the aurem Stripe account."""
    val = _dotenv_value(env_key)
    assert val, f"{env_key} missing from .env"
    assert val.startswith("price_"), f"{env_key} must start with `price_`, got {val!r}"
    assert REAL_ACCT_SUFFIX in val, (
        f"{env_key}={val!r} does not belong to the real aurem Stripe account "
        f"(missing account suffix `{REAL_ACCT_SUFFIX}`)"
    )


def test_stripe_key_ignores_emergent_placeholder(monkeypatch):
    """The supervisor-exported `sk_test_emergent…` placeholder must NOT
    win over the real key in .env."""
    monkeypatch.setenv("STRIPE_API_KEY", "sk_test_emergentXXXXXXXX")
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    # Re-import to pick up the patched env
    from routers.payments import _stripe_key
    resolved = _stripe_key()
    assert resolved, "expected a real key from .env, got empty"
    assert not resolved.startswith("sk_test_emergent"), (
        f"_stripe_key() returned the placeholder: {resolved[:20]}..."
    )
    assert resolved.startswith("sk_live_") or resolved.startswith("sk_test_"), (
        f"expected sk_live_/sk_test_ from .env, got: {resolved[:20]}..."
    )


def test_stripe_prices_dict_resolves_for_all_plans():
    """The STRIPE_PRICES lookup in payments.py must return non-None for
    every supported plan once .env is loaded."""
    from dotenv import load_dotenv
    load_dotenv(str(ENV_PATH), override=True)
    from routers.payments import STRIPE_PRICES
    for plan in ("starter", "pro", "team"):
        v = STRIPE_PRICES[plan]()
        assert v, f"STRIPE_PRICES[{plan!r}] resolved to falsy: {v!r}"
        assert v.startswith("price_"), f"{plan} → {v!r} not a price ID"
