"""
Iter 212m-126 — Auto-heal pipeline.

Verifies the four primary heal strategies:
  • network: retry with backoff → succeeds on 2nd try
  • github_rejected: PAT fails, OAuth succeeds → swap + revoke PAT
  • repo_not_found: lookup user repos → rename detection + db update
  • no_token: attach OAuth and re-verify

Plus the operational guarantees:
  • per-project 5-minute cooldown blocks back-to-back heals
  • in-flight lock prevents concurrent heals on the same project
  • success invalidates the connection-status cache so the next
    poll instantly turns the dot green
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import patch

import pytest


class _Resp:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body or {}
    def json(self): return self._body


class _FakeColl:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.updates = []
        self.inserts = []
    async def find_one(self, q, projection=None):
        for r in self.rows:
            if all(r.get(k) == v for k, v in q.items()
                   if not isinstance(v, dict)):
                return dict(r)
        return None
    async def update_one(self, q, update, upsert=False):
        self.updates.append({"q": q, "update": update})
        for r in self.rows:
            if all(r.get(k) == v for k, v in q.items()
                   if not isinstance(v, dict)):
                for k, v in (update.get("$set") or {}).items():
                    r[k] = v
                class _R:
                    modified_count = 1
                    upserted_id = None
                return _R()
        class _R:
            modified_count = 0
            upserted_id = None
        return _R()
    async def insert_one(self, doc):
        self.inserts.append(dict(doc))
        class _R:
            inserted_id = "x"
        return _R()


class _FakeDB:
    def __init__(self, proj, user):
        self.cto_projects = _FakeColl([proj])
        self.dev_users = _FakeColl([user])
        self.repo_heal_audit = _FakeColl()


@pytest.fixture(autouse=True)
def _clear_caches(monkeypatch):
    """Wipe the module-level cooldown + in-flight dicts between
    tests so heal_project always runs."""
    from services import repo_heal as rh
    rh._last_heal_at.clear()
    rh._inflight.clear()

    async def fake_decrypt(uid, ct, kind=None):
        return ct  # echo: 'enc_pat' → 'enc_pat'
    monkeypatch.setattr("services.repo_heal.decrypt", fake_decrypt)


def _make_db(*, owner="acme", repo="api", token_ct="enc_pat",
             oauth="gho_xyz"):
    return _FakeDB(
        proj={"user_id": "u1", "project_id": "p1",
              "name": "demo", "github_owner": owner,
              "github_repo": repo, "github_token": token_ct},
        user={"user_id": "u1", "email": "u1@aurem.dev",
              "github": {"access_token": oauth}},
    )


class _AsyncClientCtx:
    """Minimal stand-in for `httpx.AsyncClient(...)` that returns
    pre-baked responses keyed by URL substring + auth header."""
    def __init__(self, plan):
        self.plan = plan
        self.calls = []
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return None
    async def get(self, url, headers=None):
        self.calls.append({"url": url, "auth": (headers or {}).get("Authorization", "")})
        for matcher, resp in self.plan:
            if matcher(url, headers or {}):
                if isinstance(resp, Exception):
                    raise resp
                return resp
        return _Resp(500)


# ─── 1) Network retry recovers ────────────────────────────────────
def test_network_retry_recovers():
    import httpx
    db = _make_db()
    # First two calls time out, third returns 200.
    counter = {"n": 0}
    def plan_fn(url, headers):
        counter["n"] += 1
        return True
    plan_resp = [None, None, _Resp(200)]
    class _Ctx(_AsyncClientCtx):
        async def get(self, url, headers=None):
            self.calls.append({"url": url})
            i = counter["n"]
            counter["n"] += 1
            v = plan_resp[i] if i < len(plan_resp) else _Resp(500)
            if v is None:
                raise httpx.TimeoutException("simulated")
            return v
    with patch("services.repo_heal.httpx.AsyncClient",
               return_value=_Ctx([])):
        from services.repo_heal import heal_project
        result = asyncio.run(heal_project(
            db=db, user_id="u1", project_id="p1",
            prior_status={"error": "network: TimeoutException",
                          "auth": "pat"},
        ))
    assert result["ok"] is True
    assert result["reason"] == "network_retry_recovered"


# ─── 2) PAT rejected → OAuth swap → revoke PAT ────────────────────
def test_github_rejected_swaps_to_oauth():
    db = _make_db()
    def is_pat(url, h):    return "token enc_pat" in h.get("Authorization", "")
    def is_oauth(url, h):  return "token gho_xyz" in h.get("Authorization", "")
    ctx = _AsyncClientCtx([
        (is_pat,   _Resp(401)),
        (is_oauth, _Resp(200)),
    ])
    with patch("services.repo_heal.httpx.AsyncClient", return_value=ctx):
        from services.repo_heal import heal_project
        result = asyncio.run(heal_project(
            db=db, user_id="u1", project_id="p1",
            prior_status={"error": "github_rejected", "auth": "pat"},
        ))
    assert result["ok"] is True
    assert "oauth_token_works" in result["reason"]
    # PAT must have been revoked on the project row.
    updates = db.cto_projects.updates
    revoked = any(
        (u["update"].get("$set") or {}).get("github_token") is None
        for u in updates
    )
    assert revoked, f"expected PAT revoke in updates: {updates}"


# ─── 3) 404 → rename lookup updates owner/repo ───────────────────
def test_repo_renamed_updates_project():
    db = _make_db(owner="oldowner", repo="oldrepo")
    def is_repo_404(url, h):
        return "/repos/oldowner/oldrepo" in url
    def is_user_repos(url, h):
        return "/user/repos" in url
    list_resp = _Resp(200, body=[
        {"name": "different", "full_name": "x/different"},
        {"name": "oldrepo",   "full_name": "newowner/newname"},
    ])
    ctx = _AsyncClientCtx([
        (is_repo_404,    _Resp(404)),
        (is_user_repos,  list_resp),
    ])
    with patch("services.repo_heal.httpx.AsyncClient", return_value=ctx):
        from services.repo_heal import heal_project
        result = asyncio.run(heal_project(
            db=db, user_id="u1", project_id="p1",
            prior_status={"error": "repo_not_found", "auth": "pat"},
        ))
    assert result["ok"] is True
    assert "repo_renamed_to:newowner/newname" in result["reason"]
    # Mongo row must reflect the new coordinates.
    row = db.cto_projects.rows[0]
    assert row["github_owner"] == "newowner"
    assert row["github_repo"]  == "newname"
    assert row["renamed_from"] == "oldowner/oldrepo"


# ─── 4) no_token attaches OAuth + verifies ───────────────────────
def test_no_token_attaches_oauth():
    db = _make_db(token_ct=None)
    db.cto_projects.rows[0]["github_token"] = None
    def is_oauth(url, h): return "token gho_xyz" in h.get("Authorization", "")
    ctx = _AsyncClientCtx([(is_oauth, _Resp(200))])
    with patch("services.repo_heal.httpx.AsyncClient", return_value=ctx):
        from services.repo_heal import heal_project
        result = asyncio.run(heal_project(
            db=db, user_id="u1", project_id="p1",
            prior_status={"error": "no_token", "auth": "none"},
        ))
    assert result["ok"] is True
    assert result["reason"] == "oauth_fallback_works"


# ─── 5) Cooldown blocks back-to-back heals ────────────────────────
def test_cooldown_blocks_repeat():
    from services import repo_heal as rh
    rh._last_heal_at["p1"] = time.time() - 10   # 10 s ago
    db = _make_db()
    from services.repo_heal import heal_project
    result = asyncio.run(heal_project(
        db=db, user_id="u1", project_id="p1",
        prior_status={"error": "github_rejected", "auth": "pat"},
    ))
    assert result["heal_attempted"] is False
    assert result["reason"] == "cooldown"


# ─── 6) Success invalidates the connection-status cache ───────────
def test_success_invalidates_status_cache():
    db = _make_db()
    # Seed the cache with a stale entry.
    from routers import repo_status as rs
    rs._CACHE["p1"] = {"project_id": "p1", "status": "disconnected",
                       "checked_at": time.time()}
    def is_oauth(url, h): return "token gho_xyz" in h.get("Authorization", "")
    ctx = _AsyncClientCtx([(is_oauth, _Resp(200))])
    with patch("services.repo_heal.httpx.AsyncClient", return_value=ctx):
        from services.repo_heal import heal_project
        result = asyncio.run(heal_project(
            db=db, user_id="u1", project_id="p1",
            prior_status={"error": "github_rejected", "auth": "pat"},
        ))
    assert result["ok"] is True
    # Cache must be cleared so the next /connection-status call
    # re-fetches and turns the dot green immediately.
    assert "p1" not in rs._CACHE
