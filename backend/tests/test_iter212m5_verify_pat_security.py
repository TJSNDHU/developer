"""Iter 212m-5 — verify-pat per-repo security gate tests.

The `/cto/projects/verify-pat` endpoint must:
  • Return ok=True with `full_name`, `private`, `scopes`,
    `total_accessible_repos`, `warning`, `fine_grained` when PAT grants
    access to the requested repo.
  • Surface a `warning` when a classic PAT can access > 1 repo (so the
    UI can show an amber over-scoped pill — multi-project security).
  • Skip the warning for fine-grained PATs (no scopes header) since
    those are explicitly created per-repo.
  • Fail loudly for invalid token, missing scope, repo not found.

All I/O is mocked via `httpx.AsyncClient.get` patch.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from routers import cto_projects as cto_mod


# ── Helpers ───────────────────────────────────────────────────────────


class _MockResp:
    """Minimal stand-in for httpx.Response."""
    def __init__(self, status_code=200, json_body=None, headers=None):
        self.status_code = status_code
        self._json = json_body or {}
        self.headers = headers or {}

    def json(self):
        return self._json


class _MockClient:
    """Stand-in for httpx.AsyncClient. Routes by URL:
       /repos/{owner}/{repo}  → access-check response
       /user/repos            → over-scope probe response
    A shared dict is passed in so each `async with` block sees the
    same responses (the real code uses two separate `async with`
    contexts and would otherwise reset the response queue)."""
    def __init__(self, routes):
        self._routes = routes

    async def __aenter__(self): return self
    async def __aexit__(self, *_): return False

    async def get(self, url, **_kwargs):
        if "/user/repos" in url:
            return self._routes.get("user_repos") or _MockResp(500)
        return self._routes.get("repo") or _MockResp(500)


def _patch_client(routes):
    """Patch httpx.AsyncClient inside cto_projects to return our URL-routed
    responses. Same `routes` dict is reused across both `async with`
    blocks the endpoint creates."""
    import httpx
    return patch.object(
        httpx, "AsyncClient",
        new=lambda *a, **k: _MockClient(routes),
    )


# Bypass auth — verify-pat only needs a logged-in user, not admin.
async def _mock_current_dev(_authz):
    return {"user_id": "u_test", "email": "t@test.com"}


# Pydantic body builder.
def _body(repo: str, pat: str):
    return cto_mod.VerifyPatBody(repo=repo, pat=pat)


# ──────────────────────────────────────────────────────────────────
# Test 1 — Fine-grained PAT, single-repo access.
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_verify_pat_finegrained_single_repo(monkeypatch):
    monkeypatch.setattr(cto_mod, "current_dev", _mock_current_dev)
    routes = {
        "repo":       _MockResp(200, {"full_name": "alice/myrepo", "private": False}, {}),
        "user_repos": _MockResp(200, [{"full_name": "alice/myrepo"}], {}),
    }
    with _patch_client(routes):
        res = await cto_mod.verify_pat(
            _body("alice/myrepo", "github_pat_abc123ABC123abc123ABC"),
            authorization="Bearer x",
        )

    assert res["ok"] is True
    assert res["full_name"] == "alice/myrepo"
    assert res["private"] is False
    assert res["scopes"] == []
    assert res["fine_grained"] is True
    assert res["total_accessible_repos"] == 1
    assert res.get("warning") is None


# ──────────────────────────────────────────────────────────────────
# Test 2 — Classic PAT with `repo` scope + 47 accessible repos
#          → warning surfaces.
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_verify_pat_classic_overscoped_warning(monkeypatch):
    monkeypatch.setattr(cto_mod, "current_dev", _mock_current_dev)
    link = (
        '<https://api.github.com/user/repos?per_page=1&page=2>; rel="next", '
        '<https://api.github.com/user/repos?per_page=1&page=47>; rel="last"'
    )
    routes = {
        "repo": _MockResp(
            200, {"full_name": "alice/myrepo", "private": True},
            {"X-OAuth-Scopes": "repo, read:org"},
        ),
        "user_repos": _MockResp(200, [{"full_name": "alice/repo1"}], {"Link": link}),
    }
    with _patch_client(routes):
        res = await cto_mod.verify_pat(
            _body("alice/myrepo", "ghp_classicABC123ABC123abc123"),
            authorization="Bearer x",
        )

    assert res["ok"] is True
    assert res["scopes"] == ["repo", "read:org"]
    assert res["fine_grained"] is False
    assert res["total_accessible_repos"] == 47
    assert res["warning"] is not None
    assert "47" in res["warning"]
    assert "fine-grained" in res["warning"].lower()


# ──────────────────────────────────────────────────────────────────
# Test 3 — Classic PAT scoped to ONE repo via fine-grained → no warning.
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_verify_pat_classic_single_repo_no_warning(monkeypatch):
    monkeypatch.setattr(cto_mod, "current_dev", _mock_current_dev)
    routes = {
        "repo": _MockResp(
            200, {"full_name": "alice/single", "private": False},
            {"X-OAuth-Scopes": "repo"},
        ),
        "user_repos": _MockResp(200, [{"full_name": "alice/single"}], {}),
    }
    with _patch_client(routes):
        res = await cto_mod.verify_pat(
            _body("alice/single", "ghp_singleABC123ABC123abc123"),
            authorization="Bearer x",
        )

    assert res["ok"] is True
    assert res["total_accessible_repos"] == 1
    assert res.get("warning") is None


# ──────────────────────────────────────────────────────────────────
# Test 4 — 401 → invalid_token (over-scope probe never runs).
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_verify_pat_invalid_token(monkeypatch):
    monkeypatch.setattr(cto_mod, "current_dev", _mock_current_dev)
    with _patch_client({"repo": _MockResp(401, {}, {})}):
        res = await cto_mod.verify_pat(
            _body("alice/myrepo", "ghp_invalidABC123ABC123ABCDEF"),
            authorization="Bearer x",
        )

    assert res["ok"] is False
    assert res["error"] == "invalid_token"
    assert "invalid" in res["detail"].lower()


# ──────────────────────────────────────────────────────────────────
# Test 5 — 404 → repo_not_found.
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_verify_pat_repo_not_found(monkeypatch):
    monkeypatch.setattr(cto_mod, "current_dev", _mock_current_dev)
    with _patch_client({"repo": _MockResp(404, {}, {})}):
        res = await cto_mod.verify_pat(
            _body("alice/ghost", "github_pat_xxxABC123ABC123ABC"),
            authorization="Bearer x",
        )

    assert res["ok"] is False
    assert res["error"] == "repo_not_found"
    assert "alice/ghost" in res["detail"]


# ──────────────────────────────────────────────────────────────────
# Test 6 — Classic PAT WITHOUT `repo` scope → missing_scope.
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_verify_pat_missing_scope(monkeypatch):
    monkeypatch.setattr(cto_mod, "current_dev", _mock_current_dev)
    routes = {"repo": _MockResp(
        200, {"full_name": "alice/myrepo"},
        {"X-OAuth-Scopes": "read:user, gist"},
    )}
    with _patch_client(routes):
        res = await cto_mod.verify_pat(
            _body("alice/myrepo", "ghp_noscopeABC123ABC123abc"),
            authorization="Bearer x",
        )

    assert res["ok"] is False
    assert res["error"] == "missing_scope"
    assert res["has_scopes"] == ["read:user", "gist"]


# ──────────────────────────────────────────────────────────────────
# Test 7 — Bad format / bad repo string → caught before any HTTP.
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_verify_pat_bad_repo_format(monkeypatch):
    monkeypatch.setattr(cto_mod, "current_dev", _mock_current_dev)
    res = await cto_mod.verify_pat(
        _body("not-a-repo", "ghp_validABC123ABC123abc"),
        authorization="Bearer x",
    )
    assert res["ok"] is False
    assert res["error"] == "bad_repo"


@pytest.mark.asyncio
async def test_verify_pat_bad_pat_format(monkeypatch):
    monkeypatch.setattr(cto_mod, "current_dev", _mock_current_dev)
    res = await cto_mod.verify_pat(
        _body("alice/myrepo", "not_a_pat"),
        authorization="Bearer x",
    )
    assert res["ok"] is False
    assert res["error"] == "bad_format"


# ──────────────────────────────────────────────────────────────────
# Test 8 — Over-scope probe network failure must NOT fail the verify.
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_verify_pat_overscope_probe_resilient(monkeypatch):
    """If the /user/repos probe blows up, the primary access verification
    must still return ok with total_accessible_repos=None."""
    monkeypatch.setattr(cto_mod, "current_dev", _mock_current_dev)

    class _PartialClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *_): return False

        async def get(self, url, **_):
            if url.endswith("/repos/alice/myrepo"):
                return _MockResp(
                    200,
                    {"full_name": "alice/myrepo"},
                    {"X-OAuth-Scopes": "repo"},
                )
            # 2nd call (user/repos) — simulate an httpx.RequestError.
            import httpx
            raise httpx.ConnectError("dns failed")

    import httpx
    with patch.object(httpx, "AsyncClient", new=lambda *a, **k: _PartialClient()):
        res = await cto_mod.verify_pat(
            _body("alice/myrepo", "ghp_okABC123ABC123abc123ABC"),
            authorization="Bearer x",
        )

    assert res["ok"] is True
    assert res["total_accessible_repos"] is None
    assert res.get("warning") is None
