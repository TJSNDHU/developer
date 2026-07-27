"""Iter 328 · #5 · integration_health_cron tests.

Contract:
  1. _is_enabled respects ENABLE_INTEGRATION_HEALTH_CRON env.
  2. _interval_seconds parses env + clamps to a 60s floor.
  3. _probe_and_persist_once writes latest + history docs on success.
  4. _probe_and_persist_once returns None + logs on probe failure
     (never raises).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


def test_is_enabled_default_on(monkeypatch):
    from services import integration_health_cron as m
    monkeypatch.delenv("ENABLE_INTEGRATION_HEALTH_CRON", raising=False)
    assert m._is_enabled() is True


@pytest.mark.parametrize("v", ["0", "false", "OFF", "no"])
def test_is_enabled_off_variants(monkeypatch, v):
    from services import integration_health_cron as m
    monkeypatch.setenv("ENABLE_INTEGRATION_HEALTH_CRON", v)
    assert m._is_enabled() is False


def test_interval_default(monkeypatch):
    from services import integration_health_cron as m
    monkeypatch.delenv("INTEGRATION_HEALTH_INTERVAL_SEC", raising=False)
    assert m._interval_seconds() == 600


def test_interval_custom(monkeypatch):
    from services import integration_health_cron as m
    monkeypatch.setenv("INTEGRATION_HEALTH_INTERVAL_SEC", "120")
    assert m._interval_seconds() == 120


def test_interval_clamps_to_60s_floor(monkeypatch):
    from services import integration_health_cron as m
    monkeypatch.setenv("INTEGRATION_HEALTH_INTERVAL_SEC", "5")
    assert m._interval_seconds() == 60


def test_interval_invalid_falls_back(monkeypatch):
    from services import integration_health_cron as m
    monkeypatch.setenv("INTEGRATION_HEALTH_INTERVAL_SEC", "notanumber")
    assert m._interval_seconds() == 600


@pytest.mark.asyncio
async def test_probe_persist_writes_latest_and_history(monkeypatch):
    from services import integration_health_cron as m
    # Stub probe path.
    async def _fake_run():
        return [
            {"id": "stripe", "name": "Stripe", "status": "ok"},
            {"id": "tavily", "name": "Tavily", "status": "warn"},
        ]
    def _fake_counts(r):
        return {"ok": 1, "warn": 1, "broken": 0, "missing": 0, "total": 2}
    from services import integration_health as ih
    monkeypatch.setattr(ih, "run_all_probes", _fake_run)
    monkeypatch.setattr(ih, "summary_counts", _fake_counts)

    db = MagicMock()
    db.integration_health.update_one = AsyncMock(return_value=None)
    db.integration_health_history.insert_one = AsyncMock(return_value=None)

    snap = await m._probe_and_persist_once(db)
    assert snap is not None
    assert snap["trigger"] == "periodic_cron"
    assert snap["summary"]["total"] == 2
    db.integration_health.update_one.assert_awaited_once()
    db.integration_health_history.insert_one.assert_awaited_once()


@pytest.mark.asyncio
async def test_probe_persist_fails_open(monkeypatch):
    from services import integration_health_cron as m
    from services import integration_health as ih
    async def _boom():
        raise RuntimeError("network unreachable")
    monkeypatch.setattr(ih, "run_all_probes", _boom)

    db = MagicMock()
    # If persist tries to write on error, verify it doesn't:
    db.integration_health.update_one = AsyncMock(return_value=None)
    db.integration_health_history.insert_one = AsyncMock(return_value=None)
    snap = await m._probe_and_persist_once(db)
    assert snap is None
    # Must NOT have persisted anything on probe failure.
    db.integration_health.update_one.assert_not_awaited()
    db.integration_health_history.insert_one.assert_not_awaited()
