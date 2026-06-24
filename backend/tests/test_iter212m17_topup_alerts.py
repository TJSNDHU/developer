"""Iter 212m-17 — Top-up Alerts engine tests.

Tests the alert classifier, dedupe logic, email payload renderer, and
the admin endpoint wiring. Uses an in-memory fake Mongo for unit tests
so we never need a live DB to run them.
"""
import asyncio
import time
from pathlib import Path

import pytest

from services.topup_alerts import (
    classify,
    _day_key,
    _render_email,
    upsert_alerts_from_snapshot,
    process_snapshot,
)

BACKEND = Path(__file__).resolve().parents[1]


# ── Classification ─────────────────────────────────────────────────


def test_classify_broken_is_critical():
    r = {"id": "tavily", "name": "Tavily", "status": "broken",
         "summary": "Unexpected HTTP 432"}
    assert classify(r) == "critical"


def test_classify_warn_with_money_keyword_is_critical():
    r = {"id": "openrouter", "name": "OpenRouter", "status": "warn",
         "summary": "$0.37 remaining ($15.63 used)"}
    assert classify(r) == "critical"


def test_classify_warn_credits_exhausted_is_critical():
    r = {"id": "firecrawl", "name": "Firecrawl", "status": "warn",
         "summary": "Credits exhausted"}
    assert classify(r) == "critical"


def test_classify_warn_core_integration_is_critical():
    # MongoDB / Stripe / Emergent LLM / OpenRouter are core path.
    r = {"id": "mongodb", "name": "MongoDB", "status": "warn",
         "summary": "Latency high"}
    assert classify(r) == "critical"


def test_classify_warn_non_core_is_warning():
    r = {"id": "sentry", "name": "Sentry", "status": "warn",
         "summary": "No verified domains"}
    assert classify(r) == "warning"


def test_classify_ok_is_none():
    r = {"id": "stripe", "name": "Stripe", "status": "ok",
         "summary": "Live"}
    assert classify(r) is None


def test_classify_missing_is_none():
    # missing = no API key configured — not actionable
    r = {"id": "tavily", "name": "Tavily", "status": "missing",
         "summary": "TAVILY_API_KEY not configured"}
    assert classify(r) is None


# ── Email renderer ─────────────────────────────────────────────────


def test_email_subject_critical_only():
    alerts = [
        {"severity": "critical", "integration_name": "OpenRouter",
         "summary": "$0.37 left", "fix_hint": "Top up"},
    ]
    subject, body = _render_email(alerts)
    assert "🚨" in subject
    assert "1 critical" in subject
    assert "OpenRouter" in body
    assert "$0.37 left" in body
    assert "Top up" in body


def test_email_subject_warning_only():
    alerts = [
        {"severity": "warning", "integration_name": "Sentry",
         "summary": "No verified domains", "fix_hint": ""},
    ]
    subject, _ = _render_email(alerts)
    assert "warning" in subject.lower()
    assert "1" in subject


def test_email_subject_mixed():
    alerts = [
        {"severity": "critical", "integration_name": "OpenRouter",
         "summary": "$0.37", "fix_hint": ""},
        {"severity": "warning", "integration_name": "Sentry",
         "summary": "warn", "fix_hint": ""},
    ]
    subject, body = _render_email(alerts)
    assert "1 critical" in subject
    assert "1 warning" in subject
    assert "🚨 CRITICAL" in body
    assert "⚠️ WARNING" in body


# ── Day key (UTC dedupe boundary) ──────────────────────────────────


def test_day_key_format():
    k = _day_key(time.time())
    assert len(k) == 10
    assert k.count("-") == 2


# ── Persistence dedupe ─────────────────────────────────────────────


class _FakeColl:
    """In-memory Mongo collection stub with the subset of methods
    upsert_alerts_from_snapshot calls."""
    def __init__(self):
        self.docs: list[dict] = []

    async def find_one(self, query, projection=None):
        for d in self.docs:
            if all(d.get(k) == v for k, v in query.items()):
                # Honour the {"_id": 0} projection style
                if projection and projection.get("_id") == 0:
                    return {k: v for k, v in d.items() if k != "_id"}
                return d
        return None

    async def update_one(self, query, update, upsert=False):
        for d in self.docs:
            if all(d.get(k) == v for k, v in query.items()):
                if "$set" in update:
                    d.update(update["$set"])
                if "$inc" in update:
                    for k, v in update["$inc"].items():
                        d[k] = d.get(k, 0) + v
                class R: matched_count = 1
                return R()
        if upsert and "$set" in update:
            new = {**query, **update["$set"]}
            self.docs.append(new)
        class R: matched_count = 0
        return R()

    async def update_many(self, query, update):
        n = 0
        for d in self.docs:
            if all(d.get(k) == v for k, v in query.items()):
                if "$set" in update:
                    d.update(update["$set"])
                n += 1
        class R: matched_count = n
        return R()

    async def insert_one(self, doc):
        self.docs.append(doc)

    def find(self, query=None, projection=None):
        return _FakeCursor(self.docs)

    async def count_documents(self, query):
        n = 0
        for d in self.docs:
            if all(d.get(k) == v for k, v in (query or {}).items()):
                n += 1
        return n


class _FakeCursor:
    def __init__(self, docs):
        self.docs = list(docs)

    def sort(self, *_, **__):
        return self

    def limit(self, n):
        self.docs = self.docs[:n]
        return self

    async def to_list(self, _n):
        return list(self.docs)


class _FakeDB:
    def __init__(self):
        self.topup_alerts = _FakeColl()


@pytest.mark.asyncio
async def test_upsert_first_sighting_creates_alert():
    db = _FakeDB()
    snap = {
        "generated_at": time.time(),
        "results": [
            {"id": "openrouter", "name": "OpenRouter", "status": "warn",
             "summary": "$0.37 remaining", "fix_hint": "Top up",
             "detail": ""},
        ],
    }
    new = await upsert_alerts_from_snapshot(db, snap)
    assert len(new) == 1
    assert new[0]["severity"] == "critical"
    assert new[0]["integration_id"] == "openrouter"
    assert len(db.topup_alerts.docs) == 1
    assert db.topup_alerts.docs[0]["status"] == "active"


@pytest.mark.asyncio
async def test_upsert_dedupes_same_day():
    db = _FakeDB()
    snap = {
        "generated_at": time.time(),
        "results": [
            {"id": "tavily", "name": "Tavily", "status": "broken",
             "summary": "HTTP 432", "fix_hint": "", "detail": ""},
        ],
    }
    new1 = await upsert_alerts_from_snapshot(db, snap)
    assert len(new1) == 1
    # Second call same day, same status → no new alerts emitted.
    new2 = await upsert_alerts_from_snapshot(db, snap)
    assert len(new2) == 0
    # But seen_count must increment.
    assert db.topup_alerts.docs[0]["seen_count"] == 2


@pytest.mark.asyncio
async def test_upsert_auto_resolves_when_ok():
    db = _FakeDB()
    bad_snap = {
        "generated_at": time.time(),
        "results": [
            {"id": "openrouter", "name": "OpenRouter", "status": "broken",
             "summary": "down", "fix_hint": "", "detail": ""},
        ],
    }
    await upsert_alerts_from_snapshot(db, bad_snap)
    assert db.topup_alerts.docs[0]["status"] == "active"
    # Next probe says ok → previous alert auto-resolves.
    good_snap = {
        "generated_at": time.time(),
        "results": [
            {"id": "openrouter", "name": "OpenRouter", "status": "ok",
             "summary": "Live", "fix_hint": "", "detail": ""},
        ],
    }
    await upsert_alerts_from_snapshot(db, good_snap)
    assert db.topup_alerts.docs[0]["status"] == "resolved"


@pytest.mark.asyncio
async def test_process_snapshot_does_not_email_when_no_admin_email(monkeypatch):
    # Strip ADMIN_EMAIL to ensure we don't try the Resend network call.
    monkeypatch.delenv("ADMIN_EMAIL", raising=False)
    db = _FakeDB()
    snap = {
        "generated_at": time.time(),
        "results": [
            {"id": "openrouter", "name": "OpenRouter", "status": "broken",
             "summary": "down", "fix_hint": "", "detail": ""},
        ],
    }
    res = await process_snapshot(db, snap)
    assert res["new_alert_count"] == 1
    assert res["emailed"] is False  # no ADMIN_EMAIL → no email
    # Alert still persisted.
    assert len(db.topup_alerts.docs) == 1


# ── Router wiring pins ─────────────────────────────────────────────


def test_admin_alerts_endpoints_registered():
    src = (BACKEND / "routers" / "admin.py").read_text(encoding="utf-8")
    assert '@router.get("/alerts")' in src
    assert '@router.post("/alerts/{alert_id}/dismiss")' in src
    # The refresh handler must also kick off alerts processing
    # so emails fire immediately on a manual refresh.
    assert "from services.topup_alerts import process_snapshot" in src


def test_daily_digest_fires_topup_alerts():
    src = (BACKEND / "services" / "daily_digest.py").read_text(
        encoding="utf-8"
    )
    assert "from services.topup_alerts import process_snapshot" in src


def test_admin_overview_renders_alerts_banner():
    src = (
        BACKEND.parent / "frontend" / "src" / "pages" / "AdminOverview.jsx"
    ).read_text(encoding="utf-8")
    assert "TopupAlertsBanner" in src
    # Component must consume /admin/alerts + /admin/integrations/refresh +
    # /admin/alerts/{id}/dismiss
    assert '/admin/alerts' in src
    assert 'integrations/refresh' in src
    assert '/dismiss' in src
    # Per testid contract: data-testid="topup-alerts-banner" or banner-ok.
    assert 'data-testid="topup-alerts-banner' in src
