"""
test_iter212b_verify_pat_endpoint.py

Iter 212 follow-up — Debounced PAT verification before project connect.

Contract for POST /api/aurem-dev/cto/projects/verify-pat:
  • Stateless — no DB write, no project lookup.
  • Auth required (logged-in builder).
  • Uniform JSON shape; HTTP 200 always; error encoded in `ok` + `error`.
  • Maps GitHub status codes to typed errors:
      bad_format / bad_repo  → 4xx-like local validation
      invalid_token          → GitHub 401
      missing_scope          → GitHub 403 or scope-missing on 200
      repo_not_found         → GitHub 404
      network_error          → httpx.RequestError
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest


def _mk_response(status_code: int, json_data=None, scopes_hdr: str = ""):
    class _Resp:
        def __init__(self, sc, j, sh):
            self.status_code = sc
            self._json = j or {}
            self.headers = {"X-OAuth-Scopes": sh} if sh else {}

        def json(self):
            return self._json

    return _Resp(status_code, json_data, scopes_hdr)


# ── Local format gates ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_verify_pat_rejects_bad_repo_format():
    from routers.cto_projects import verify_pat, VerifyPatBody
    with patch("routers.cto_projects.current_dev",
               new=AsyncMock(return_value={"user_id": "u1"})):
        out = await verify_pat(
            VerifyPatBody(repo="badformat", pat="ghp_abc12345"),
            authorization="Bearer x",
        )
    assert out["ok"] is False
    assert out["error"] == "bad_repo"


@pytest.mark.asyncio
async def test_verify_pat_rejects_bad_token_format():
    from routers.cto_projects import verify_pat, VerifyPatBody
    with patch("routers.cto_projects.current_dev",
               new=AsyncMock(return_value={"user_id": "u1"})):
        out = await verify_pat(
            VerifyPatBody(repo="octocat/Hello-World", pat="not_a_token"),
            authorization="Bearer x",
        )
    assert out["ok"] is False
    assert out["error"] == "bad_format"


# ── GitHub status code mapping ────────────────────────────────────

@pytest.mark.asyncio
async def test_verify_pat_success_with_repo_scope():
    from routers import cto_projects
    fake_resp = _mk_response(
        200, json_data={"full_name": "octocat/Hello-World", "private": False},
        scopes_hdr="repo, read:org",
    )

    class _C:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, *a, **k): return fake_resp

    with patch("routers.cto_projects.current_dev",
               new=AsyncMock(return_value={"user_id": "u1"})), \
         patch("httpx.AsyncClient", return_value=_C()):
        out = await cto_projects.verify_pat(
            cto_projects.VerifyPatBody(repo="octocat/Hello-World",
                                       pat="ghp_abc12345678901234567890"),
            authorization="Bearer x",
        )
    assert out["ok"] is True
    assert "repo" in out["scopes"]
    assert out["full_name"] == "octocat/Hello-World"


@pytest.mark.asyncio
async def test_verify_pat_200_but_missing_repo_scope():
    from routers import cto_projects
    fake_resp = _mk_response(
        200, json_data={"full_name": "octocat/Hello-World"},
        scopes_hdr="read:user, gist",  # `repo` is NOT in scopes
    )

    class _C:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, *a, **k): return fake_resp

    with patch("routers.cto_projects.current_dev",
               new=AsyncMock(return_value={"user_id": "u1"})), \
         patch("httpx.AsyncClient", return_value=_C()):
        out = await cto_projects.verify_pat(
            cto_projects.VerifyPatBody(repo="octocat/Hello-World",
                                       pat="ghp_abc12345678901234567890"),
            authorization="Bearer x",
        )
    assert out["ok"] is False
    assert out["error"] == "missing_scope"
    assert out["has_scopes"] == ["read:user", "gist"]


@pytest.mark.asyncio
async def test_verify_pat_200_fine_grained_no_scope_header():
    """Fine-grained PATs don't send X-OAuth-Scopes. Treat 200 as proof
    of access and report empty scopes list."""
    from routers import cto_projects
    fake_resp = _mk_response(
        200, json_data={"full_name": "octocat/Hello-World", "private": True},
        scopes_hdr="",
    )

    class _C:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, *a, **k): return fake_resp

    with patch("routers.cto_projects.current_dev",
               new=AsyncMock(return_value={"user_id": "u1"})), \
         patch("httpx.AsyncClient", return_value=_C()):
        out = await cto_projects.verify_pat(
            cto_projects.VerifyPatBody(
                repo="octocat/Hello-World",
                pat="github_pat_11AABBCCDD_finegrained1234567890",
            ),
            authorization="Bearer x",
        )
    assert out["ok"] is True
    assert out["scopes"] == []
    assert out["private"] is True


@pytest.mark.asyncio
async def test_verify_pat_401_invalid_token():
    from routers import cto_projects
    fake_resp = _mk_response(401)

    class _C:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, *a, **k): return fake_resp

    with patch("routers.cto_projects.current_dev",
               new=AsyncMock(return_value={"user_id": "u1"})), \
         patch("httpx.AsyncClient", return_value=_C()):
        out = await cto_projects.verify_pat(
            cto_projects.VerifyPatBody(repo="octocat/Hello-World",
                                       pat="ghp_abc12345678901234567890"),
            authorization="Bearer x",
        )
    assert out["ok"] is False
    assert out["error"] == "invalid_token"


@pytest.mark.asyncio
async def test_verify_pat_403_missing_scope():
    from routers import cto_projects
    fake_resp = _mk_response(403, scopes_hdr="read:user")

    class _C:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, *a, **k): return fake_resp

    with patch("routers.cto_projects.current_dev",
               new=AsyncMock(return_value={"user_id": "u1"})), \
         patch("httpx.AsyncClient", return_value=_C()):
        out = await cto_projects.verify_pat(
            cto_projects.VerifyPatBody(repo="octocat/Hello-World",
                                       pat="ghp_abc12345678901234567890"),
            authorization="Bearer x",
        )
    assert out["ok"] is False
    assert out["error"] == "missing_scope"


@pytest.mark.asyncio
async def test_verify_pat_404_repo_not_found():
    from routers import cto_projects
    fake_resp = _mk_response(404)

    class _C:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, *a, **k): return fake_resp

    with patch("routers.cto_projects.current_dev",
               new=AsyncMock(return_value={"user_id": "u1"})), \
         patch("httpx.AsyncClient", return_value=_C()):
        out = await cto_projects.verify_pat(
            cto_projects.VerifyPatBody(repo="ghost/missing",
                                       pat="ghp_abc12345678901234567890"),
            authorization="Bearer x",
        )
    assert out["ok"] is False
    assert out["error"] == "repo_not_found"


@pytest.mark.asyncio
async def test_verify_pat_network_error_returns_typed_error():
    import httpx
    from routers import cto_projects

    class _C:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, *a, **k):
            raise httpx.ConnectError("boom")

    with patch("routers.cto_projects.current_dev",
               new=AsyncMock(return_value={"user_id": "u1"})), \
         patch("httpx.AsyncClient", return_value=_C()):
        out = await cto_projects.verify_pat(
            cto_projects.VerifyPatBody(repo="octocat/Hello-World",
                                       pat="ghp_abc12345678901234567890"),
            authorization="Bearer x",
        )
    assert out["ok"] is False
    assert out["error"] == "network_error"


# ── Frontend wiring lock-in ───────────────────────────────────────

PROJECTS_JSX = Path("/app/frontend/src/pages/Projects.jsx").read_text(encoding="utf-8")


def test_frontend_has_debounced_verification_effect():
    """useEffect debounces by 800ms and POSTs to /cto/projects/verify-pat."""
    assert "setTimeout" in PROJECTS_JSX
    assert "800" in PROJECTS_JSX
    assert "/cto/projects/verify-pat" in PROJECTS_JSX


def test_frontend_renders_three_status_pills():
    """Loading / OK / Error pills, each with a dedicated data-testid."""
    for tid in (
        "proj-pat-verify-loading",
        "proj-pat-verify-ok",
        "proj-pat-verify-error",
    ):
        assert f'data-testid="{tid}"' in PROJECTS_JSX, f"missing pill {tid}"


def test_connect_button_gated_on_verified_pat():
    """The Connect button must be disabled until patCheck.status === 'ok'."""
    assert 'patCheck.status !== "ok"' in PROJECTS_JSX
    # Pre-Iter-212 gate was `!repoPat.trim()`. Make sure the disabled
    # check no longer relies on raw text presence alone.
    # (The verify effect itself still references repoPat.trim, so we
    # only check the Connect button's disabled clause.)
    assert "disabled={!selectedRepo || patCheck.status !== \"ok\" || busy}" in PROJECTS_JSX


def test_robot_guide_stagec_only_after_verification():
    """Stage-C copy ('Token verified ✓') must be gated on patCheck.status === 'ok'."""
    assert 'patCheck.status === "ok"' in PROJECTS_JSX
    assert "Token verified" in PROJECTS_JSX


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
