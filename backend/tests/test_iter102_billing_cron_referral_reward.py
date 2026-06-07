"""
test_iter102_billing_cron_referral_reward.py — locks in:
  • End-of-month Maxx overage billing cron (real Stripe API calls)
  • Referral reward on paid conversion (real Stripe Subscription.modify)
  • Stripe webhook wires both — captures customer_id + invokes reward
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


def test_billing_cron_module_exports():
    from services import billing_cron as b
    assert hasattr(b, "bill_maxx_overages")
    assert hasattr(b, "grant_referral_reward")


def test_bill_maxx_overages_iterates_real_db():
    """Live: seed 3 users with overages → run cron → confirm processed
    count, graceful failure handling, and that failed rows don't reset
    overage_count (so we can retry next month)."""
    async def _run():
        from motor.motor_asyncio import AsyncIOMotorClient
        c = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = c[os.environ.get("DB_NAME", "aurem_dev")]
        from cto_services.db import set_db
        set_db(db)

        bucket = datetime.now(timezone.utc).strftime("%Y-%m")
        try:
            for uid, cust, n in [
                ("test_iter102_a", "cus_FAKE_A", 5),
                ("test_iter102_b", None,         7),
            ]:
                upd = {"user_id": uid, "email": f"{uid}@t", "tier": "pro"}
                if cust: upd["stripe_customer_id"] = cust
                await db.dev_users.update_one({"user_id": uid}, {"$set": upd}, upsert=True)
                await db.cto_maxx_usage.update_one(
                    {"user_id": uid, "month": bucket},
                    {"$set": {"user_id": uid, "month": bucket,
                              "count": 100 + n, "overage_count": n}},
                    upsert=True,
                )
            from services.billing_cron import bill_maxx_overages
            result = await bill_maxx_overages(db)
            assert result["processed"] == 2
            assert result["billed"] == 0       # both fail (fake / missing)
            assert result["failed"] == 2
            # Both rows retain their overage_count for next month's retry.
            for uid, want in [("test_iter102_a", 5), ("test_iter102_b", 7)]:
                row = await db.cto_maxx_usage.find_one({"user_id": uid, "month": bucket})
                assert row["overage_count"] == want, (
                    f"failed billing must NOT reset overage_count for {uid}"
                )
        finally:
            for uid in ("test_iter102_a", "test_iter102_b"):
                await db.dev_users.delete_one({"user_id": uid})
                await db.cto_maxx_usage.delete_one({"user_id": uid, "month": bucket})
            c.close()
    asyncio.run(_run())


def test_grant_referral_reward_free_tier_credit_path():
    """Free-tier referrer: no Stripe call, just status mutation."""
    async def _run():
        from motor.motor_asyncio import AsyncIOMotorClient
        c = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = c[os.environ.get("DB_NAME", "aurem_dev")]
        from cto_services.db import set_db
        set_db(db)
        try:
            await db.dev_users.update_one(
                {"user_id": "test_iter102_refF"},
                {"$set": {"user_id": "test_iter102_refF", "email": "f@t", "tier": "free"}},
                upsert=True,
            )
            await db.referrals.insert_one({
                "referrer_user_id": "test_iter102_refF",
                "new_user_id":      "test_iter102_newF",
                "status":           "pending_paid_conversion",
                "attributed_at":    datetime.now(timezone.utc).isoformat(),
            })
            from services.billing_cron import grant_referral_reward
            r = await grant_referral_reward(db, "test_iter102_newF")
            assert r["granted"] is False
            assert "free tier" in r["reason"]
            row = await db.referrals.find_one({"referrer_user_id": "test_iter102_refF"})
            assert row["status"] == "free_month_pending_upgrade"
            assert "credited_at" in row
        finally:
            await db.dev_users.delete_one({"user_id": "test_iter102_refF"})
            await db.referrals.delete_many({"referrer_user_id": "test_iter102_refF"})
            c.close()
    asyncio.run(_run())


def test_grant_referral_reward_handles_missing_referral():
    """No pending referral → clean false, no side effects."""
    async def _run():
        from motor.motor_asyncio import AsyncIOMotorClient
        c = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = c[os.environ.get("DB_NAME", "aurem_dev")]
        from cto_services.db import set_db
        set_db(db)
        try:
            from services.billing_cron import grant_referral_reward
            r = await grant_referral_reward(db, "test_iter102_no_such_user")
            assert r["granted"] is False
            assert r["reason"] == "no pending referral"
        finally:
            c.close()
    asyncio.run(_run())


def test_webhook_captures_customer_id_and_calls_reward():
    """Stripe webhook handler must:
       1. Persist `stripe_customer_id` (needed for overage InvoiceItem)
       2. Invoke `grant_referral_reward()` on `checkout.session.completed`
    """
    src = (Path(__file__).resolve().parents[1] / "routers" / "payments.py").read_text()
    assert 'obj.get("customer")' in src, (
        "Webhook must extract Stripe customer id and persist it for overage billing"
    )
    assert '"stripe_customer_id": cust_id' in src
    assert "grant_referral_reward(db, user_id)" in src


def test_daily_digest_schedules_overage_cron_on_first_of_month():
    src = (Path(__file__).resolve().parents[1] / "services" / "daily_digest.py").read_text()
    assert "bill_maxx_overages" in src, (
        "daily_digest must invoke bill_maxx_overages on month-start"
    )
    assert ".day == 1" in src, (
        "Cron must guard with .day == 1 to run only on month-start"
    )


def test_admin_manual_trigger_endpoint_registered():
    from routers.admin import router
    paths = {r.path for r in router.routes}
    assert "/admin/billing/run-overage-cron" in paths, (
        f"manual overage trigger missing. Routes: {sorted(paths)[:30]}"
    )


def test_referral_email_uses_aurem_live_from_address():
    """Resend email body must come from ora@aurem.live (verified)."""
    src = (Path(__file__).resolve().parents[1] / "services" / "billing_cron.py").read_text()
    assert "ora@aurem.live" in src or 'RESEND_FROM_EMAIL' in src
    assert "1 free month" in src or "free month" in src
    assert "api.resend.com/emails" in src
