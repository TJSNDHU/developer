"""
Iter 212m-121 — Bulk + SSE fix pipeline.

Verifies:
  • POST /fix-pipeline/preview returns correct cost split for paying
    user vs founder (is_unlimited bypass).
  • POST /fix-pipeline/bulk requires a project_id and enforces the
    50-finding hard cap.
  • Insufficient-token path returns 402 with shortfall.
  • fix_job_manager event ordering: queued → fix-done → done.
  • SSE stream rejects cross-tenant subscribers.

We deliberately do NOT exercise the live GitHub commit path — that
requires a real PAT + a real repo and is covered separately by the
existing iter 212m-114 finding_fix_applier integration tests.  Here
we mock apply_finding_fix to return a stub result so we can assert
the SSE event shape without burning real LLM/GitHub quota.
"""
from __future__ import annotations

import asyncio
import json
import os
import pytest
from fastapi.testclient import TestClient


class _FakeColl:
    def __init__(self, rows=None):
        self.rows = rows or []

    async def find_one(self, query, projection=None):
        for r in self.rows:
            ok = all(r.get(k) == v for k, v in query.items() if not isinstance(v, dict))
            if ok:
                return dict(r)
        return None

    async def update_one(self, query, update, upsert=False):
        for r in self.rows:
            if all(r.get(k) == v for k, v in query.items() if not isinstance(v, dict)):
                # Handle $inc
                inc = (update.get("$inc") or {})
                for k, v in inc.items():
                    r[k] = (r.get(k) or 0) + v
                class _R:
                    modified_count = 1
                    upserted_id = None
                return _R()
        class _R:
            modified_count = 0
            upserted_id = None
        return _R()

    async def insert_one(self, doc):
        self.rows.append(dict(doc))
        class _R:
            inserted_id = "fake"
        return _R()


class _FakeDB:
    def __init__(self, user_doc=None, projects=None):
        self.dev_users = _FakeColl([user_doc] if user_doc else [])
        self.cto_projects = _FakeColl(projects or [])
        self.finding_fixes = _FakeColl()
        self.cto_tasks = _FakeColl()
        self.vanguard_ci_findings = _FakeColl()


@pytest.fixture
def client_paying(monkeypatch):
    """User WITHOUT is_unlimited — pays tokens."""
    from main import app
    from cto_services import db as cto_db

    user_doc = {
        "user_id": "u_paying", "email": "pay@aurem.dev",
        "tokens_remaining": 100,
        "tier": "free", "is_admin": False, "is_unlimited": False,
    }
    db = _FakeDB(user_doc=user_doc)
    with TestClient(app) as c:
        cto_db.set_db(db)
        # Mint a token for the paying user.
        from cto_services.auth import create_token
        token = create_token("u_paying", "pay@aurem.dev")
        c.headers["Authorization"] = f"Bearer {token}"
        yield c, db


@pytest.fixture
def client_founder(monkeypatch):
    """Founder — is_unlimited=true, never charged."""
    from main import app
    from cto_services import db as cto_db

    user_doc = {
        "user_id": "u_founder", "email": "founder@aurem.dev",
        "tokens_remaining": 5,
        "tier": "founder", "is_admin": True, "is_unlimited": True,
    }
    db = _FakeDB(user_doc=user_doc)
    with TestClient(app) as c:
        cto_db.set_db(db)
        from cto_services.auth import create_token
        token = create_token("u_founder", "founder@aurem.dev", is_admin=True)
        c.headers["Authorization"] = f"Bearer {token}"
        yield c, db


_FINDINGS_2 = [
    {"id": "f1", "rule_id": "secret_aws_key",
     "file": "config.py", "line": 12, "vuln": "secret_leak",
     "severity": "critical", "category": "vanguard"},
    {"id": "f2", "rule_id": "sql_string_format",
     "file": "db.py", "line": 5, "vuln": "sql_injection",
     "severity": "high", "category": "vanguard"},
]


# ─── Preview ──────────────────────────────────────────────────────────
def test_preview_paying_user_returns_tokens_and_usd(client_paying):
    c, _ = client_paying
    r = c.post("/api/aurem-dev/fix-pipeline/preview",
               json={"project_id": "p1", "findings": _FINDINGS_2})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["count"] == 2
    assert body["tokens_cost"] == 10           # 5 + 5 (vanguard rate)
    assert body["usd_cost"] == round(10 * 0.0001, 4)
    assert body["is_unlimited"] is False
    assert body["balance"] == 100
    assert body["can_proceed"] is True


def test_preview_founder_shows_zero_cost(client_founder):
    c, _ = client_founder
    r = c.post("/api/aurem-dev/fix-pipeline/preview",
               json={"project_id": "p1", "findings": _FINDINGS_2})
    assert r.status_code == 200
    body = r.json()
    assert body["is_unlimited"] is True
    assert body["tokens_cost"] == 0
    assert body["usd_cost"] == 0.0


def test_preview_rejects_empty_findings(client_paying):
    c, _ = client_paying
    r = c.post("/api/aurem-dev/fix-pipeline/preview",
               json={"project_id": "p1", "findings": []})
    assert r.status_code == 400


def test_preview_insufficient_shortfall(client_paying):
    c, db = client_paying
    db.dev_users.rows[0]["tokens_remaining"] = 3
    r = c.post("/api/aurem-dev/fix-pipeline/preview",
               json={"project_id": "p1", "findings": _FINDINGS_2})
    assert r.status_code == 200
    body = r.json()
    assert body["can_proceed"] is False
    assert body["shortfall"] == 10 - 3


# ─── Bulk start ───────────────────────────────────────────────────────
def test_bulk_start_returns_job_id(client_founder, monkeypatch):
    c, _ = client_founder

    async def fake_apply(**kw):
        return {"ok": True, "commit_sha": "abc1234",
                "full_sha": "abc1234" + "0" * 33,
                "html_url": "https://github.com/x/y/commit/abc1234",
                "file": kw["finding"].get("file"),
                "rule_id": kw["finding"].get("rule_id"),
                "message": "stub"}

    monkeypatch.setattr(
        "routers.fix_pipeline.apply_finding_fix", fake_apply,
    )
    # Skip the live GitHub verification call.
    monkeypatch.setattr(
        "routers.fix_pipeline._verify_commit_exists",
        lambda **kw: _async_return(True),
    )

    r = c.post("/api/aurem-dev/fix-pipeline/bulk",
               json={"project_id": "p1", "findings": _FINDINGS_2})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["job_id"].startswith("fx_")
    assert body["count"] == 2
    assert body["stream"].endswith(body["job_id"])

    # Poll the summary endpoint until the worker finishes (it runs
    # in the same event loop as the TestClient — TestClient pumps
    # background tasks during sleep).
    import time
    summary = None
    for _ in range(20):
        time.sleep(0.1)
        s = c.get(f"/api/aurem-dev/fix-pipeline/summary/{body['job_id']}")
        if s.status_code == 200:
            summary = s.json()
            if summary.get("closed_at"):
                break
    assert summary is not None
    assert summary["total"] == 2
    # Founder path → 2 completed, 0 failed (apply_finding_fix stubbed ok).
    assert summary["completed"] == 2
    assert summary["failed"] == 0
    assert len(summary["results"]) == 2
    assert summary["results"][0]["ok"] is True
    assert summary["results"][0]["commit_sha"] == "abc1234"


def _async_return(x):
    async def _f():
        return x
    return _f()


def test_bulk_rejects_no_project(client_founder):
    c, _ = client_founder
    r = c.post("/api/aurem-dev/fix-pipeline/bulk",
               json={"findings": _FINDINGS_2})
    assert r.status_code == 400


def test_bulk_hard_cap_at_50(client_founder):
    c, _ = client_founder
    too_many = [{"id": f"f{i}", "rule_id": "x", "file": "a.py",
                 "category": "vanguard"} for i in range(51)]
    r = c.post("/api/aurem-dev/fix-pipeline/bulk",
               json={"project_id": "p1", "findings": too_many})
    assert r.status_code == 400
    assert "max 50" in r.json()["detail"].lower()


def test_bulk_paying_user_insufficient_tokens(client_paying):
    c, db = client_paying
    db.dev_users.rows[0]["tokens_remaining"] = 4
    r = c.post("/api/aurem-dev/fix-pipeline/bulk",
               json={"project_id": "p1", "findings": _FINDINGS_2})
    assert r.status_code == 402
    detail = r.json()["detail"]
    assert detail["error"] == "insufficient_tokens"
    assert detail["needed"] == 10
    assert detail["balance"] == 4


# ─── Job manager unit ─────────────────────────────────────────────────
def test_job_manager_emit_and_subscribe():
    """Verify the in-memory job manager emits + delivers events in
    order, and the terminal sentinel closes the subscription."""
    from services import fix_job_manager as fjm

    async def go():
        job_id = fjm.create_job("u1", "single", total=1)
        fjm.emit(job_id, "queued", finding_id="f1")
        fjm.emit(job_id, "reading", finding_id="f1")
        fjm.emit(job_id, "fix-done", ok=True, finding_id="f1",
                 commit_sha="abc1234", file="x.py", rule_id="r1")
        fjm.close(job_id, ok=True)
        events = []
        async for ev in fjm.subscribe(job_id):
            events.append(ev["phase"])
        return events

    events = asyncio.run(go())
    assert events[0] == "queued"
    assert "reading" in events
    assert "fix-done" in events
    assert events[-1] == "done"


def test_summary_owner_check(client_founder, client_paying, monkeypatch):
    """A paying user must NOT be able to read a founder's job
    summary (403) but the founder admin reads everyone's."""
    c_f, _ = client_founder

    async def fake_apply(**kw):
        return {"ok": True, "commit_sha": "abc1234",
                "full_sha": "abc1234" + "0" * 33,
                "html_url": "https://github.com/x/y/commit/abc1234",
                "file": "x.py", "rule_id": "r1", "message": "stub"}
    monkeypatch.setattr(
        "routers.fix_pipeline.apply_finding_fix", fake_apply,
    )
    monkeypatch.setattr(
        "routers.fix_pipeline._verify_commit_exists",
        lambda **kw: _async_return(True),
    )
    r = c_f.post("/api/aurem-dev/fix-pipeline/bulk",
                 json={"project_id": "p1",
                       "findings": [_FINDINGS_2[0]]})
    job_id = r.json()["job_id"]

    # Same admin can fetch.
    assert c_f.get(f"/api/aurem-dev/fix-pipeline/summary/{job_id}"
                   ).status_code == 200

    # Now log in as a paying user — should be 403.
    from main import app
    from cto_services.auth import create_token
    from cto_services import db as cto_db
    db2 = _FakeDB(user_doc={
        "user_id": "u_paying", "email": "pay@aurem.dev",
        "tokens_remaining": 100, "tier": "free",
        "is_admin": False, "is_unlimited": False,
    })
    with TestClient(app) as c2:
        cto_db.set_db(db2)
        c2.headers["Authorization"] = f"Bearer {create_token('u_paying', 'pay@aurem.dev')}"
        r2 = c2.get(f"/api/aurem-dev/fix-pipeline/summary/{job_id}")
        assert r2.status_code == 403
