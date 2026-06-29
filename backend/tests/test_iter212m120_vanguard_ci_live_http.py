"""
Iter 212m-120 — Phase 1 live HTTP smoke tests for the Vanguard CI ingest
endpoint.  These tests hit the public preview URL and only verify the
fail-closed paths (POST 503 when AUREM_CI_INGEST_TOKEN is unset, GET 401
when no JWT) since the live backend has the env var intentionally unset.
"""
from __future__ import annotations

import os
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
if not BASE_URL:
    # Live URL needed; fall back to reading frontend/.env directly.
    with open("/app/frontend/.env") as f:
        for line in f:
            if line.startswith("REACT_APP_BACKEND_URL"):
                BASE_URL = line.split("=", 1)[1].strip().rstrip("/")
                break

INGEST_URL = f"{BASE_URL}/api/aurem-dev/vanguard/ci-findings"


def test_live_post_no_auth_returns_503_or_401():
    """Live backend has AUREM_CI_INGEST_TOKEN unset → 503 fail-closed
    regardless of whether a bearer is provided."""
    r = requests.post(INGEST_URL, json={
        "repo": "a/b", "commit": "deadbeef", "scanner": "trufflehog",
        "findings": [],
    }, timeout=15)
    # Token unset → 503 fail-closed.
    assert r.status_code == 503, f"expected 503 fail-closed, got {r.status_code}: {r.text}"
    body = r.json()
    assert "AUREM_CI_INGEST_TOKEN" in (body.get("detail") or "")


def test_live_post_with_wrong_bearer_still_503_when_token_unset():
    r = requests.post(INGEST_URL, json={
        "repo": "a/b", "commit": "deadbeef", "scanner": "trufflehog",
        "findings": [],
    }, headers={"Authorization": "Bearer guessing-game"}, timeout=15)
    assert r.status_code == 503, f"expected 503 fail-closed, got {r.status_code}: {r.text}"


def test_live_get_requires_jwt():
    """GET endpoint is JWT-protected; no auth → 401 (or 403)."""
    r = requests.get(f"{INGEST_URL}?limit=5", timeout=15)
    # current_dev() raises 401 when no Authorization header.
    assert r.status_code in (401, 403), f"expected 401/403, got {r.status_code}: {r.text}"


def test_live_get_with_invalid_jwt_rejected():
    r = requests.get(
        f"{INGEST_URL}?limit=5",
        headers={"Authorization": "Bearer not-a-real-jwt"},
        timeout=15,
    )
    assert r.status_code in (401, 403), f"expected 401/403, got {r.status_code}: {r.text}"


def test_live_get_with_valid_jwt_returns_runs_list():
    """Login as preview founder, hit the GET endpoint, expect ok=True and a runs list."""
    login = requests.post(
        f"{BASE_URL}/api/aurem-dev/auth/login",
        json={"email": "test@aurem.dev", "password": "AuremTest2026!"},
        timeout=15,
    )
    if login.status_code != 200:
        # Don't fail the suite if preview seed is missing; just note it.
        import pytest
        pytest.skip(f"login failed: {login.status_code} {login.text[:200]}")
    token = login.json().get("access_token") or login.json().get("token")
    assert token, f"no token in login response: {login.json()}"
    r = requests.get(
        f"{INGEST_URL}?limit=5",
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    assert r.status_code == 200, f"expected 200, got {r.status_code}: {r.text}"
    body = r.json()
    assert body.get("ok") is True
    assert "runs" in body
    assert isinstance(body["runs"], list)
