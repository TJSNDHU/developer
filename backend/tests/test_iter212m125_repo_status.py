"""
Iter 212m-125 — Live repo connection-status ping.

Verifies:
  • Endpoint requires auth (no JWT → 401 via current_dev).
  • A project with no github_owner/repo returns
    status=disconnected, error=repo_not_set, http_code=0.
  • A project with creds but a 200 response from GitHub returns
    status=connected, http_code=200, auth=pat (or oauth fallback).
  • A 401/403/404 from GitHub maps to status=disconnected with the
    right error label.
  • Cache TTL coalesces a second call within the window.

We mock httpx.AsyncClient so no real GitHub traffic happens.
"""
from __future__ import annotations

from unittest.mock import patch, AsyncMock

import pytest
from fastapi.testclient import TestClient


class _Resp:
    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        self._body = body or {}
    def json(self): return self._body


class _FakeColl:
    def __init__(self, rows=None): self.rows = rows or []
    def find(self, query, projection=None):
        rows = [r for r in self.rows
                if all(r.get(k) == v for k, v in query.items()
                       if not isinstance(v, dict))]
        return _Cursor(rows)
    async def find_one(self, query, projection=None):
        for r in self.rows:
            if all(r.get(k) == v for k, v in query.items()
                   if not isinstance(v, dict)):
                return dict(r)
        return None


class _Cursor:
    def __init__(self, rows): self.rows = rows
    def sort(self, *_a, **_kw): return self
    async def to_list(self, n): return self.rows[:n]


class _FakeDB:
    def __init__(self, projs, user):
        self.cto_projects = _FakeColl(projs)
        self.dev_users    = _FakeColl([user])


@pytest.fixture
def client(monkeypatch):
    from main import app
    from cto_services import db as cto_db
    from cto_services.auth import create_token

    user = {
        "user_id": "u1", "email": "u1@aurem.dev",
        "tokens_remaining": 100, "tier": "free",
        "is_admin": False, "is_unlimited": False,
        "github": {"access_token": "gho_OAUTH_FALLBACK"},
    }
    projs = [
        {"user_id": "u1", "project_id": "p_ok", "github_owner": "a", "github_repo": "b",
         "branch": "main", "github_token": "enc_pat_ok"},
        {"user_id": "u1", "project_id": "p_404", "github_owner": "ghost", "github_repo": "gone",
         "branch": "main", "github_token": "enc_pat_404"},
        {"user_id": "u1", "project_id": "p_norepo", "github_owner": "", "github_repo": "",
         "branch": "main", "github_token": None},
        {"user_id": "u1", "project_id": "p_oauth_only", "github_owner": "c", "github_repo": "d",
         "branch": "main", "github_token": None},
    ]
    db = _FakeDB(projs, user)

    # Force the crypto.decrypt to just echo back a known token so we
    # can match in the mocked httpx call.
    async def fake_decrypt(uid, ct, kind=None):
        return {"enc_pat_ok": "tok_ok", "enc_pat_404": "tok_404"}.get(ct)
    monkeypatch.setattr("routers.repo_status.decrypt", fake_decrypt)
    # Wipe the cache across tests — clear the dict in place so the
    # router's `_CACHE` reference still points at the same object.
    from routers import repo_status as rs
    rs._CACHE.clear()

    with TestClient(app) as c:
        cto_db.set_db(db)
        c.headers["Authorization"] = f"Bearer {create_token('u1', 'u1@aurem.dev')}"
        yield c, db


def _mock_httpx_responses(by_repo):
    """Build an httpx.AsyncClient mock that returns mapped responses
    based on the requested URL (`...repos/{owner}/{repo}`)."""
    class _Ctx:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return None
        async def get(self, url, headers=None):
            for k, resp in by_repo.items():
                if k in url:
                    return resp
            return _Resp(500)
    return _Ctx


def test_requires_auth():
    from main import app
    with TestClient(app) as c:
        r = c.get("/api/aurem-dev/cto/projects/connection-status")
        assert r.status_code in (401, 403)


def test_mixed_results(client, monkeypatch):
    """Three projects, three outcomes — connected, disconnected
    (404), disconnected (no_token / no_repo)."""
    c, _ = client
    mock_client = _mock_httpx_responses({
        "a/b":     _Resp(200),
        "ghost/gone": _Resp(404),
        "c/d":     _Resp(200),
    })
    with patch("routers.repo_status.httpx.AsyncClient", return_value=mock_client()):
        r = c.get("/api/aurem-dev/cto/projects/connection-status")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    by = {s["project_id"]: s for s in body["statuses"]}

    assert by["p_ok"]["status"]    == "connected"
    assert by["p_ok"]["http_code"] == 200
    assert by["p_ok"]["auth"]      == "pat"

    assert by["p_404"]["status"] == "disconnected"
    assert by["p_404"]["http_code"] == 404
    assert by["p_404"]["error"] == "repo_not_found"

    assert by["p_norepo"]["status"] == "disconnected"
    assert by["p_norepo"]["error"]  == "repo_not_set"

    # OAuth fallback used because project had no PAT row.
    assert by["p_oauth_only"]["status"] == "connected"
    assert by["p_oauth_only"]["auth"]   == "oauth"


def test_bad_token_returns_disconnected(client):
    c, _ = client
    mock_client = _mock_httpx_responses({
        "a/b": _Resp(401),
        "ghost/gone": _Resp(403),
        "c/d": _Resp(401),
    })
    with patch("routers.repo_status.httpx.AsyncClient", return_value=mock_client()):
        r = c.get("/api/aurem-dev/cto/projects/connection-status")
    by = {s["project_id"]: s for s in r.json()["statuses"]}
    assert by["p_ok"]["status"] == "disconnected"
    assert by["p_ok"]["error"] == "github_rejected"
    assert by["p_ok"]["http_code"] == 401


def test_cache_ttl_coalesces(client):
    """Two back-to-back calls within the 8 s TTL must reuse the
    cached value and NOT re-fan-out to GitHub."""
    c, _ = client
    call_count = {"n": 0}
    class _Ctx:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return None
        async def get(self, url, headers=None):
            call_count["n"] += 1
            return _Resp(200)
    with patch("routers.repo_status.httpx.AsyncClient", return_value=_Ctx()):
        r1 = c.get("/api/aurem-dev/cto/projects/connection-status")
        r2 = c.get("/api/aurem-dev/cto/projects/connection-status")
    assert r1.status_code == 200 and r2.status_code == 200
    # First call should fire 3 live checks (p_ok, p_404 with token,
    # p_oauth_only via OAuth).  Second call should hit zero — all
    # three cached entries are <8 s old.
    assert call_count["n"] == 3


def test_network_error_marks_disconnected(client):
    c, _ = client
    import httpx
    class _Ctx:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return None
        async def get(self, url, headers=None):
            raise httpx.TimeoutException("simulated")
    with patch("routers.repo_status.httpx.AsyncClient", return_value=_Ctx()):
        r = c.get("/api/aurem-dev/cto/projects/connection-status")
    by = {s["project_id"]: s for s in r.json()["statuses"]}
    assert by["p_ok"]["status"] == "disconnected"
    assert by["p_ok"]["error"].startswith("network: TimeoutException")
    assert by["p_ok"]["http_code"] == 0
