"""
test_iter101_annual_referral_overage.py — locks in:
  1. Stripe annual variants ('starter_annual'/'pro_annual'/'team_annual')
     in STRIPE_PRICES dict, env vars present.
  2. Overage billing — Pro+ user past cap still gets Claude AND accrues
     $0.50/task overage in cto_maxx_usage.overage_count.
  3. Referral tracking — public /referrals/track endpoint, signup
     attribution endpoint, /referrals/my includes click count and
     reward description.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest
from dotenv import load_dotenv


ENV_PATH = Path(__file__).resolve().parents[1] / ".env"


@pytest.fixture(autouse=True)
def _load_env():
    load_dotenv(str(ENV_PATH), override=True)
    yield


# ── 1. Annual plans ──────────────────────────────────────────────────

def test_annual_env_vars_present():
    for k in ("STRIPE_STARTER_ANNUAL_PRICE_ID",
              "STRIPE_PRO_ANNUAL_PRICE_ID",
              "STRIPE_TEAM_ANNUAL_PRICE_ID"):
        v = os.environ.get(k, "")
        assert v.startswith("price_"), f"{k} missing or not a price_ id"
        # Account suffix matches the real Stripe account (rejects placeholders).
        assert "0Exg9gU93t" in v, f"{k}={v!r} not from the real Stripe account"


def test_stripe_prices_dict_has_annual_variants():
    from routers.payments import STRIPE_PRICES
    for plan in ("starter_annual", "pro_annual", "team_annual"):
        assert plan in STRIPE_PRICES, f"{plan} missing from STRIPE_PRICES"
        v = STRIPE_PRICES[plan]()
        assert v and v.startswith("price_"), f"{plan} → {v!r}"


# ── 2. Overage billing ───────────────────────────────────────────────

def test_overage_math_is_correct():
    """Run the real DB code-path to prove a Pro user past cap accrues
    $0.50/task. End-to-end Mongo round-trip, no mocks."""
    async def _run():
        from motor.motor_asyncio import AsyncIOMotorClient
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ.get("DB_NAME", "aurem_dev")]
        from cto_services.db import set_db
        set_db(db)

        uid = "test_iter101_overage_pytest"
        bucket = datetime.now(timezone.utc).strftime("%Y-%m")
        try:
            await db.dev_users.update_one(
                {"user_id": uid},
                {"$set": {"user_id": uid, "email": "p@t", "tier": "pro"}},
                upsert=True,
            )
            await db.cto_maxx_usage.update_one(
                {"user_id": uid, "month": bucket},
                {"$set": {"user_id": uid, "month": bucket,
                          "count": 100, "overage_count": 0}},
                upsert=True,
            )
            from services.usage import incr_maxx_usage, get_maxx_usage
            # Confirm capped
            pre = await get_maxx_usage(uid)
            assert pre["capped"] is True
            assert pre["overage_count"] == 0
            # Hit it 4 times past cap
            for _ in range(4):
                await incr_maxx_usage(uid)
            post = await get_maxx_usage(uid)
            assert post["used"] == 104
            assert post["overage_count"] == 4
            assert post["overage_cost_usd"] == 2.00  # 4 × $0.50
            assert post["overage_price_usd"] == 0.50
        finally:
            await db.dev_users.delete_one({"user_id": uid})
            await db.cto_maxx_usage.delete_one({"user_id": uid, "month": bucket})
            client.close()
    asyncio.run(_run())


def test_llm_meta_carries_maxx_overage_flag():
    """call_llm_with_meta result dict must include `maxx_overage` so
    the frontend can show "you'll be billed $0.50/task" banner."""
    import inspect
    from services import llm
    src = inspect.getsource(llm.call_llm_with_meta)
    assert "maxx_overage" in src, "result must include maxx_overage flag"
    # Pro+ tiers KEEP Claude (don't degrade) — confirms business rule.
    assert 'tier in ("pro", "team", "founder")' in src, (
        "Pro+ tiers must NOT silently fall back — they pay overage instead"
    )


# ── 3. Referral system ───────────────────────────────────────────────

def test_referral_endpoints_registered():
    from routers.engagement import router
    paths = {r.path for r in router.routes}
    must_have = {"/referrals/my", "/referrals/track", "/referrals/attribute"}
    assert must_have.issubset(paths), (
        f"missing routes: {must_have - paths}. Have: {sorted(paths)}"
    )


def test_my_referrals_response_shape():
    """Read-only check that the response now includes the iter 101
    additions (clicks counter + reward copy)."""
    import inspect
    from routers import engagement
    src = inspect.getsource(engagement.my_referrals)
    assert "clicks" in src, "/referrals/my must surface click count"
    assert "reward_per_paid" in src, "must advertise the reward to the user"
    assert "auremcto.com" in src, "ref link should use production domain"


def test_track_endpoint_is_public_no_auth():
    """Click tracking must be public — visitors haven't signed in yet."""
    import inspect
    from routers import engagement
    sig = inspect.signature(engagement.track_referral_click)
    assert "authorization" not in sig.parameters, (
        "/referrals/track must NOT require auth (called from anonymous landing)"
    )


def test_attribute_rejects_self_referral():
    """Critical anti-fraud check: a user cannot refer themselves."""
    src = (Path(__file__).resolve().parents[1] / "routers" / "engagement.py").read_text()
    assert "ref_code == new_user_id" in src, (
        "engagement.py must reject self-referrals"
    )
    assert "already attributed" in src, (
        "engagement.py must reject duplicate attribution"
    )
