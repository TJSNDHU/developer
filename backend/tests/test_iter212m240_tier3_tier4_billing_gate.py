"""
tests/test_iter212m240_tier3_tier4_billing_gate.py

Locks in Tier 3 (billing gate + transfer endpoints) + Tier 4 (daily rate
limit) behaviour so future edits don't accidentally drop the guardrails.

No live Supabase/GitHub calls — every external boundary is stubbed.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from services.subscription_tiers import TIER_LIMITS, Tier
from services import personal_track_quotas as ptq


# ── Tier flag matrix ─────────────────────────────────────────────
@pytest.mark.parametrize("tier,feature,expected", [
    ("free",    "dedicated_db",       False),
    ("starter", "dedicated_db",       False),
    ("pro",     "dedicated_db",       True),
    ("team",    "dedicated_db",       True),
    ("founder", "dedicated_db",       True),
    ("free",    "transfer_ownership", False),
    ("starter", "transfer_ownership", True),
    ("pro",     "transfer_ownership", True),
    ("free",    "custom_domain",      False),
    ("pro",     "custom_domain",      True),
])
def test_feature_gate_matrix(tier, feature, expected):
    assert ptq.check_feature_allowed(tier, feature) is expected


# ── Numeric daily caps ───────────────────────────────────────────
@pytest.mark.parametrize("tier,expected", [
    ("free",    3),
    ("starter", 10),
    ("pro",     50),
    ("team",    100),
    ("founder", None),
])
def test_daily_scaffold_limits(tier, expected):
    assert ptq.get_numeric_limit(tier, "scaffold_drafts_per_day") == expected


# ── Enforce daily rate — under cap → ok=True and increments ──────
class _FakeDB:
    """Minimal in-memory Mongo replacement for the counter collection."""
    def __init__(self):
        self._data: dict[str, dict] = {}
        self.dev_users = _FakeCollection({})

    def __getitem__(self, name):
        if name not in self._data:
            self._data[name] = {}
        return _FakeCollection(self._data[name])


class _FakeCollection:
    def __init__(self, store):
        self.store = store

    async def find_one(self, filt, projection=None):
        key = _hash(filt)
        return self.store.get(key)

    async def update_one(self, filt, update, upsert=False):
        key = _hash(filt)
        doc = self.store.get(key)
        if not doc and upsert:
            doc = {**filt}
            self.store[key] = doc
        if not doc:
            return type("R", (), {"modified_count": 0})()
        if "$inc" in update:
            for k, v in update["$inc"].items():
                doc[k] = (doc.get(k) or 0) + v
        if "$set" in update:
            doc.update(update["$set"])
        if "$setOnInsert" in update and not doc.get("_seeded"):
            for k, v in update["$setOnInsert"].items():
                doc.setdefault(k, v)
            doc["_seeded"] = True
        return type("R", (), {"modified_count": 1})()

    async def create_index(self, *a, **kw):
        return None


def _hash(filt):
    return tuple(sorted(filt.items()))


def test_check_and_increment_daily_under_cap():
    db = _FakeDB()
    async def run():
        for i in range(3):
            r = await ptq.check_and_increment_daily(db, "u1", "free", "scaffold_drafts_per_day")
            assert r["ok"] is True, f"under-cap call {i+1} rejected"
        # 4th call must be rejected (limit=3 for free)
        r = await ptq.check_and_increment_daily(db, "u1", "free", "scaffold_drafts_per_day")
        assert r["ok"] is False
        assert r["limit"] == 3
        assert r["used"] == 3
        assert "reset_at_utc" in r
    asyncio.run(run())


def test_check_and_increment_daily_unlimited_for_founder():
    db = _FakeDB()
    async def run():
        for _ in range(200):
            r = await ptq.check_and_increment_daily(db, "u1", "founder", "scaffold_drafts_per_day")
            assert r["ok"] is True
            assert r["limit"] is None
    asyncio.run(run())


# ── enforce_feature_or_402 raises HTTPException(402) for missing tier ──
def test_enforce_feature_or_402_blocks_free():
    from fastapi import HTTPException
    db = _FakeDB()
    # dev_users has no row → tier defaults to 'free'
    async def run():
        with pytest.raises(HTTPException) as ei:
            await ptq.enforce_feature_or_402(db, {"user_id": "u1"}, "dedicated_db")
        assert ei.value.status_code == 402
        assert ei.value.detail["reason"] == "tier_upgrade_required"
        assert ei.value.detail["current_tier"] == "free"
    asyncio.run(run())


def test_enforce_feature_or_402_bypasses_founder_role():
    db = _FakeDB()
    async def run():
        tier = await ptq.enforce_feature_or_402(
            db, {"user_id": "u1", "is_founder": True}, "dedicated_db",
        )
        assert tier == "founder"
    asyncio.run(run())


def test_enforce_daily_rate_or_429_bypasses_founder():
    db = _FakeDB()
    async def run():
        # Founder can call unlimited times without any HTTPException.
        for _ in range(10):
            r = await ptq.enforce_daily_rate_or_429(
                db, {"user_id": "u1", "is_founder": True}, "scaffold_drafts_per_day",
            )
            assert r["ok"] is True
    asyncio.run(run())


# ── Tier limit dict has all expected feature flags ───────────────
def test_tier_limits_have_personal_track_flags():
    required = {"dedicated_db", "custom_domain", "transfer_ownership",
                "scaffold_drafts_per_day", "personal_track_projects"}
    for t in (Tier.FREE, Tier.STARTER, Tier.PRO, Tier.TEAM, Tier.FOUNDER):
        assert required.issubset(TIER_LIMITS[t].keys()), \
            f"Tier {t} missing Personal Track flags"


# ── Static wire checks: routers import the enforcer ──────────────
def test_supabase_router_gates_provision():
    """Confirm the Tier 3 gate is wired in routers/supabase.py."""
    import routers.supabase as rs
    src = _read_source(rs.__file__)
    assert "enforce_feature_or_402" in src
    assert "\"dedicated_db\"" in src


def test_scaffold_router_enforces_daily_rate():
    import routers.scaffold as rs
    src = _read_source(rs.__file__)
    assert "enforce_daily_rate_or_429" in src
    assert "\"scaffold_drafts_per_day\"" in src


def test_scaffold_router_has_transfer_repo():
    import routers.scaffold as rs
    src = _read_source(rs.__file__)
    assert "/transfer-repo" in src or "transfer-repo" in src
    assert "transfer_repo_to_user" in src


def test_supabase_router_has_transfer_to_user():
    import routers.supabase as rs
    src = _read_source(rs.__file__)
    assert "transfer-to-user" in src
    assert "transfer_project_to_org" in src


def test_stripe_webhook_triggers_supabase_downgrade():
    import routers.payments as rp
    src = _read_source(rp.__file__)
    assert "apply_downgrade" in src, "webhook must apply Supabase downgrade on sub deletion"


# ── github_org_client + supabase_provisioner export transfer helpers ──
def test_github_org_client_transfer_helper_exported():
    from services import github_org_client as gh
    assert hasattr(gh, "transfer_repo_to_user")


def test_supabase_provisioner_transfer_helper_exported():
    from services import supabase_provisioner as sp
    assert hasattr(sp, "transfer_project_to_org")


# ── Admin endpoints registered ───────────────────────────────────
def test_admin_endpoints_registered():
    import routers.scaffold as rs
    routes = [r.path for r in rs.router.routes]  # type: ignore
    assert "/scaffold/admin/blocked-drafts" in routes
    assert "/scaffold/admin/personal-projects" in routes
    assert "/scaffold/admin/draft-summary" in routes
    assert "/scaffold/quota/status" in routes


# ── Helpers ──────────────────────────────────────────────────────
def _read_source(path: str) -> str:
    with open(path, "r") as f:
        return f.read()
