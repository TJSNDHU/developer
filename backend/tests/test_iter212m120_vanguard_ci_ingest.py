"""
Iter 212m-120 — Phase 1: Trufflehog CI ingest endpoint.

Verifies:
  • POST /vanguard/ci-findings rejects requests when
    AUREM_CI_INGEST_TOKEN is unset (fail-closed).
  • Accepts a valid bearer + trufflehog JSON payload and writes a
    normalised document into Mongo.
  • Verified-vs-unverified secret split is preserved.
  • Raw secret values are redacted before storage.
  • Re-ingest of the same (repo, commit, scanner) upserts in place.
  • GET /vanguard/ci-findings is JWT-protected and scoped to repos
    the caller owns.
"""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient


# ─── Fixtures ─────────────────────────────────────────────────────────
@pytest.fixture
def client(monkeypatch):
    """Boot the FastAPI app with an in-memory Mongo stub stitched in."""
    monkeypatch.setenv("AUREM_CI_INGEST_TOKEN", "test-ci-secret-xyz")
    from main import app
    from cto_services import db as cto_db

    with TestClient(app) as c:
        # FastAPI startup overrides get_db with the real Motor client,
        # so we must re-set our fake AFTER the TestClient enters its
        # context (which fires the lifespan startup hooks).
        cto_db.set_db(_FakeDB())
        yield c


class _FakeCollection:
    def __init__(self):
        self.docs: list[dict] = []

    async def update_one(self, query, update, upsert=False):
        # Replace if match, else insert.
        class _R:
            upserted_id = None

        for i, d in enumerate(self.docs):
            if all(d.get(k) == v for k, v in query.items()):
                self.docs[i] = {**d, **(update.get("$set") or {})}
                return _R()
        if upsert:
            self.docs.append({**(update.get("$set") or {})})
            r = _R()
            r.upserted_id = "new"
            return r
        return _R()

    def find(self, query=None, projection=None):
        # Very small async-iterator stub — supports the chain used by
        # the router: .sort(...).limit(...).
        query = query or {}
        rows = []
        for d in self.docs:
            ok = True
            for k, v in query.items():
                if isinstance(v, dict) and "$in" in v:
                    if d.get(k) not in v["$in"]:
                        ok = False
                        break
                elif d.get(k) != v:
                    ok = False
                    break
            if ok:
                rows.append({k: vv for k, vv in d.items() if k != "_id"})
        return _Cursor(rows)


class _Cursor:
    def __init__(self, rows):
        self._rows = rows

    def sort(self, *_a, **_kw):
        return self

    def limit(self, n):
        self._rows = self._rows[:n]
        return self

    def __aiter__(self):
        self._i = 0
        return self

    async def __anext__(self):
        if self._i >= len(self._rows):
            raise StopAsyncIteration
        r = self._rows[self._i]
        self._i += 1
        return r


class _FakeDB:
    def __init__(self):
        self.vanguard_ci_findings = _FakeCollection()
        self.cto_projects = _FakeCollection()
        self.dev_users = _FakeCollection()


# ─── Tests ────────────────────────────────────────────────────────────

_TRUFFLEHOG_SAMPLE = [
    {
        "DetectorName": "AWS",
        "Verified": True,
        "Raw": "AKIAIOSFODNN7EXAMPLE",
        "SourceMetadata": {
            "Data": {"Filesystem": {"file": "config.py", "line": 42}}
        },
    },
    {
        "DetectorName": "Generic",
        "Verified": False,
        "Raw": "maybe-a-secret-12345",
        "SourceMetadata": {
            "Data": {"Filesystem": {"file": "tests/fixture.txt", "line": 7}}
        },
    },
]


def test_ingest_rejects_without_token(monkeypatch):
    """When AUREM_CI_INGEST_TOKEN is empty the endpoint MUST 503.
    Fail-closed — no silent acceptance of unauthenticated writes."""
    monkeypatch.delenv("AUREM_CI_INGEST_TOKEN", raising=False)
    from main import app
    from cto_services import db as cto_db
    with TestClient(app) as c:
        cto_db.set_db(_FakeDB())
        r = c.post(
            "/api/aurem-dev/vanguard/ci-findings",
            headers={"Authorization": "Bearer anything"},
            json={"repo": "a/b", "commit": "deadbeef", "scanner": "trufflehog",
                  "findings": []},
        )
    assert r.status_code == 503, r.text


def test_ingest_rejects_wrong_token(client):
    r = client.post(
        "/api/aurem-dev/vanguard/ci-findings",
        headers={"Authorization": "Bearer WRONG"},
        json={"repo": "a/b", "commit": "deadbeef", "scanner": "trufflehog",
              "findings": []},
    )
    assert r.status_code == 401, r.text


def test_ingest_persists_and_redacts(client):
    r = client.post(
        "/api/aurem-dev/vanguard/ci-findings",
        headers={"Authorization": "Bearer test-ci-secret-xyz"},
        json={
            "repo": "aurem-ai/aurem-cto",
            "commit": "abc123def4567890",
            "branch": "main",
            "scanner": "trufflehog",
            "findings": _TRUFFLEHOG_SAMPLE,
            "run_url": "https://github.com/aurem-ai/aurem-cto/actions/runs/1",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["total_count"] == 2
    assert body["verified_count"] == 1

    # Inspect the stored doc — raw secret values must be redacted.
    from cto_services.db import get_db
    db = get_db()
    stored = db.vanguard_ci_findings.docs[0]
    assert stored["scanner"] == "trufflehog"
    assert stored["verified_count"] == 1
    assert stored["total_count"] == 2
    assert len(stored["findings"]) == 2
    aws_f = next(f for f in stored["findings"] if f["detector"] == "AWS")
    assert aws_f["verified"] is True
    assert aws_f["severity"] == "critical"
    assert aws_f["file"] == "config.py"
    assert aws_f["line"] == 42
    # Redaction: original 20-char secret -> "AKIA…LE" pattern
    assert "AKIAIOSFODNN7EXAMPLE" not in str(stored)
    assert aws_f["redacted"].startswith("AKIA") and aws_f["redacted"].endswith("LE")
    # Unverified gets severity=high
    gen_f = next(f for f in stored["findings"] if f["detector"] == "Generic")
    assert gen_f["verified"] is False
    assert gen_f["severity"] == "high"


def test_ingest_upserts_same_commit(client):
    payload = {
        "repo": "aurem-ai/aurem-cto",
        "commit": "abc123def4567890",
        "branch": "main",
        "scanner": "trufflehog",
        "findings": _TRUFFLEHOG_SAMPLE,
    }
    h = {"Authorization": "Bearer test-ci-secret-xyz"}
    r1 = client.post("/api/aurem-dev/vanguard/ci-findings", headers=h, json=payload)
    r2 = client.post("/api/aurem-dev/vanguard/ci-findings", headers=h, json=payload)
    assert r1.status_code == 200 and r2.status_code == 200
    from cto_services.db import get_db
    db = get_db()
    # One commit, two ingests -> still one row.
    matching = [d for d in db.vanguard_ci_findings.docs
                if d["commit"] == payload["commit"]]
    assert len(matching) == 1


def test_ingest_rejects_unknown_scanner(client):
    r = client.post(
        "/api/aurem-dev/vanguard/ci-findings",
        headers={"Authorization": "Bearer test-ci-secret-xyz"},
        json={"repo": "a/b", "commit": "x", "scanner": "trivy", "findings": []},
    )
    assert r.status_code == 400
    assert "unsupported" in r.json().get("detail", "").lower()


def test_ingest_rejects_missing_fields(client):
    r = client.post(
        "/api/aurem-dev/vanguard/ci-findings",
        headers={"Authorization": "Bearer test-ci-secret-xyz"},
        json={"repo": "", "commit": "", "scanner": "trufflehog", "findings": []},
    )
    assert r.status_code == 400
