"""Iter 335b — Stripe price self-healing (prod stale-env fix).

Prod deploy logs: 404 `No such price` on the three MONTHLY price IDs
(old-account values still in the prod env store) while annual IDs
worked. Fix: checkout pre-flight now auto-discovers the correct live
price (product name + interval + USD, unambiguous match only) and
uses it, logging a loud STALE ENV error.
"""
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from routers import payments as pay

# Shaped like Stripe's expanded Price.list rows (verified live).
LIVE_PRICES = [
    {"id": "price_starter_m", "currency": "usd",
     "recurring": {"interval": "month"}, "product": {"name": "Starter"}},
    {"id": "price_pro_m", "currency": "usd",
     "recurring": {"interval": "month"}, "product": {"name": "Pro"}},
    {"id": "price_team_m", "currency": "usd",
     "recurring": {"interval": "month"}, "product": {"name": "Team"}},
    {"id": "price_starter_y", "currency": "usd",
     "recurring": {"interval": "year"}, "product": {"name": "Starter"}},
    # CAD duplicates exist in the live account — must be ignored.
    {"id": "price_starter_m_cad", "currency": "cad",
     "recurring": {"interval": "month"}, "product": {"name": "Starter"}},
    {"id": "price_other", "currency": "usd",
     "recurring": {"interval": "month"},
     "product": {"name": "Site Monitor — Pro"}},
]


class TestMatcher:
    def test_matches_monthly_usd_by_product_name(self):
        assert pay._match_discovered_price(LIVE_PRICES, "starter") == "price_starter_m"
        assert pay._match_discovered_price(LIVE_PRICES, "pro") == "price_pro_m"
        assert pay._match_discovered_price(LIVE_PRICES, "team") == "price_team_m"

    def test_matches_annual_variant(self):
        assert pay._match_discovered_price(LIVE_PRICES, "starter_annual") == "price_starter_y"

    def test_cad_duplicate_does_not_create_ambiguity(self):
        # starter has usd + cad monthly rows — must resolve to the usd one.
        assert pay._match_discovered_price(LIVE_PRICES, "starter") == "price_starter_m"

    def test_ambiguous_returns_none(self):
        dup = LIVE_PRICES + [{"id": "price_pro_m2", "currency": "usd",
                               "recurring": {"interval": "month"},
                               "product": {"name": "Pro"}}]
        assert pay._match_discovered_price(dup, "pro") is None

    def test_unknown_plan_returns_none(self):
        assert pay._match_discovered_price(LIVE_PRICES, "enterprise") is None


class TestPreflightHeal:
    def setup_method(self):
        pay._RESOLVED_PRICES.clear()

    async def test_valid_price_passes_through(self):
        with patch.object(pay, "_stripe_call", new=AsyncMock(return_value={"id": "ok"})):
            out = await pay._preflight_price("pro", "price_env_ok")
        assert out == "price_env_ok"
        assert pay._RESOLVED_PRICES == {}

    async def test_stale_env_heals_via_discovery(self):
        calls = {"n": 0}

        async def fake_stripe_call(fn, *a, **kw):
            calls["n"] += 1
            if calls["n"] == 1:   # Price.retrieve → No such price
                raise HTTPException(502, "No such price")
            return {"data": LIVE_PRICES}   # Price.list

        with patch.object(pay, "_stripe_call", new=fake_stripe_call):
            out = await pay._preflight_price("pro", "price_stale_old_acct")
        assert out == "price_pro_m"
        assert pay._RESOLVED_PRICES["pro"] == "price_pro_m"

    async def test_healed_price_cached_no_repeat_lookup(self):
        pay._RESOLVED_PRICES["team"] = "price_team_m"
        boom = AsyncMock(side_effect=AssertionError("must not call stripe"))
        with patch.object(pay, "_stripe_call", new=boom):
            out = await pay._preflight_price("team", "whatever")
        assert out == "price_team_m"

    async def test_no_match_raises_precise_503(self):
        calls = {"n": 0}

        async def fake_stripe_call(fn, *a, **kw):
            calls["n"] += 1
            if calls["n"] == 1:   # Price.retrieve → No such price
                raise HTTPException(502, "No such price")
            return {"data": []}   # Price.list → nothing to heal with

        with patch.object(pay, "_stripe_call", new=fake_stripe_call):
            with pytest.raises(HTTPException) as ei:
                await pay._preflight_price("pro", "price_stale")
        assert ei.value.status_code == 503
        assert "STRIPE_PRO_PRICE_ID" in ei.value.detail
