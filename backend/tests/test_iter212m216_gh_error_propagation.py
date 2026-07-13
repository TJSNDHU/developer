"""
Iter 212m-216 — GitHub error propagation regression.

Before this iter, any GitHub non-2xx that wasn't 401/404/5xx (notably
403 rate-limits and 422 empty-repo errors) got caught by
`codebase_health.scan()`'s outer wrap → HTTPException(502) →
Cloudflare's 5xx intercept replaced the JSON body with a branded
"Bad gateway" HTML page.  Users saw a raw 1.3s 502 with zero clue
what actually broke.

This suite locks the new behaviour of `_gh_get`:

  1. GitHub 401  →  our 401 with detail = "github_pat_invalid: <gh msg>"
  2. GitHub 403 (rate limited: x-ratelimit-remaining == 0)
       →  our 429 with body {error, message, retry_after_seconds,
          github_message}
  3. GitHub 403 (other) → our 403 with "github_forbidden: <gh msg>"
  4. GitHub 404 → our 404 with "github_repo_not_found: <gh msg>"
  5. GitHub 409 (empty repo) → our 422 with "github_repo_empty: <gh msg>"
  6. GitHub 422 (bad ref)   → our 422 with "github_bad_ref: <gh msg>"
  7. GitHub 5xx → our 502 with "github_upstream_<sc>: <gh msg>"
  8. httpx.TimeoutException → our 504 with "github_upstream_timeout: ..."
  9. httpx.RequestError → our 502 with "github_transport_<ClsName>: ..."

Each branch is exercised by monkey-patching an httpx.AsyncClient
stub so the test never hits GitHub — deterministic, offline.
"""

from __future__ import annotations

import pytest
import httpx

from routers.security_scan import _gh_get
from fastapi import HTTPException


class _FakeResp:
    """Minimal httpx.Response stand-in for `_gh_get`."""
    def __init__(self, status_code=200, json_body=None, text_body="",
                  headers=None):
        self.status_code = status_code
        self._json = json_body
        self.text = text_body
        self.headers = headers or {}
    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json


class _FakeClient:
    def __init__(self, resp=None, exc=None):
        self._resp = resp
        self._exc  = exc
    async def get(self, url, *, headers=None, timeout=None):
        if self._exc is not None:
            raise self._exc
        return self._resp


pytestmark = pytest.mark.asyncio


async def test_401_carries_github_reason():
    c = _FakeClient(_FakeResp(401, {"message": "Bad credentials"}))
    with pytest.raises(HTTPException) as ei:
        await _gh_get(c, "https://api.github.com/x", "ghp_bad")
    assert ei.value.status_code == 401
    assert "github_pat_invalid" in str(ei.value.detail)
    assert "Bad credentials" in str(ei.value.detail)


async def test_403_rate_limited_becomes_429_with_retry_after():
    """Primary rate limit: remaining=0 + reset header → our 429."""
    import time
    reset_at = int(time.time()) + 42
    c = _FakeClient(_FakeResp(
        403,
        json_body={"message": "API rate limit exceeded for user."},
        headers={"x-ratelimit-remaining": "0",
                 "x-ratelimit-reset":     str(reset_at)},
    ))
    with pytest.raises(HTTPException) as ei:
        await _gh_get(c, "https://api.github.com/x", "ghp_ok")
    assert ei.value.status_code == 429
    body = ei.value.detail
    assert isinstance(body, dict)
    assert body["error"] == "github_rate_limited"
    # ~42 s ±2 s tolerance for wall-clock drift
    assert 30 <= body["retry_after_seconds"] <= 50, body
    assert "API rate limit exceeded" in body["github_message"]


async def test_403_generic_forbidden_stays_403():
    c = _FakeClient(_FakeResp(
        403,
        json_body={"message": "Resource protected by organization SAML enforcement."},
        headers={},   # no rate-limit header
    ))
    with pytest.raises(HTTPException) as ei:
        await _gh_get(c, "https://api.github.com/x", "ghp_ok")
    assert ei.value.status_code == 403
    assert "github_forbidden" in str(ei.value.detail)


async def test_404_carries_reason():
    c = _FakeClient(_FakeResp(404, {"message": "Not Found"}))
    with pytest.raises(HTTPException) as ei:
        await _gh_get(c, "https://api.github.com/x", "ghp_ok")
    assert ei.value.status_code == 404
    assert "github_repo_not_found" in str(ei.value.detail)


async def test_409_empty_repo_becomes_422():
    c = _FakeClient(_FakeResp(409, {"message": "Git Repository is empty."}))
    with pytest.raises(HTTPException) as ei:
        await _gh_get(c, "https://api.github.com/x", "ghp_ok")
    assert ei.value.status_code == 422
    assert "github_repo_empty" in str(ei.value.detail)


async def test_422_bad_ref():
    c = _FakeClient(_FakeResp(422, {"message": "Reference does not exist"}))
    with pytest.raises(HTTPException) as ei:
        await _gh_get(c, "https://api.github.com/x", "ghp_ok")
    assert ei.value.status_code == 422
    assert "github_bad_ref" in str(ei.value.detail)


async def test_5xx_stays_502_with_upstream_code():
    c = _FakeClient(_FakeResp(503, {"message": "GitHub is down"}))
    with pytest.raises(HTTPException) as ei:
        await _gh_get(c, "https://api.github.com/x", "ghp_ok")
    assert ei.value.status_code == 502
    assert "github_upstream_503" in str(ei.value.detail)


async def test_timeout_becomes_504():
    c = _FakeClient(exc=httpx.ConnectTimeout("timed out"))
    with pytest.raises(HTTPException) as ei:
        await _gh_get(c, "https://api.github.com/x", "ghp_ok")
    assert ei.value.status_code == 504
    assert "github_upstream_timeout" in str(ei.value.detail)


async def test_transport_error_becomes_502_with_class_name():
    c = _FakeClient(exc=httpx.ConnectError("dns failed"))
    with pytest.raises(HTTPException) as ei:
        await _gh_get(c, "https://api.github.com/x", "ghp_ok")
    assert ei.value.status_code == 502
    # The class name MUST appear so a founder can grep prod logs.
    assert "github_transport_ConnectError" in str(ei.value.detail)


async def test_200_returns_body():
    c = _FakeClient(_FakeResp(200, {"default_branch": "main"}))
    body = await _gh_get(c, "https://api.github.com/x", "ghp_ok")
    assert body == {"default_branch": "main"}


async def test_200_with_bad_json_becomes_502():
    c = _FakeClient(_FakeResp(200, json_body=None, text_body="<html>proxy err</html>"))
    with pytest.raises(HTTPException) as ei:
        await _gh_get(c, "https://api.github.com/x", "ghp_ok")
    assert ei.value.status_code == 502
    assert "github_bad_json" in str(ei.value.detail)


def test_source_no_more_blanket_502_wrap():
    """Lock: the outer wrap in codebase_health.scan MUST NOT re-throw
    every HTTPException as 502.  A future refactor that reverts to
    `except Exception → HTTPException(502, ...)` without the
    `except HTTPException: raise` clause would silently break error
    surfacing again."""
    src = open("/app/backend/routers/codebase_health.py").read()
    assert "except HTTPException:" in src, (
        "codebase_health.scan lost its `except HTTPException: raise` "
        "clause — meaningful GH statuses will be re-wrapped as 502 again"
    )
    assert "github_fetch_crashed" in src, (
        "codebase_health.scan lost the tagged 502 fallback — a real "
        "crash would revert to a nondescript 'GitHub fetch failed'"
    )
