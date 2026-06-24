"""Iter 212m-9 — Live HTTP integration tests for BYOH Deploy UI endpoints.

Hits the preview backend through REACT_APP_BACKEND_URL to confirm
authentication, hybrid fallback contract, runs filter and logs alias.
"""
from __future__ import annotations

import os
import pytest
import requests

BASE_URL = "https://launch-pad-237.preview.emergentagent.com"
EMAIL = "test@aurem.dev"
PASSWORD = "AuremTest2026!"


@pytest.fixture(scope="module")
def token():
    r = requests.post(
        f"{BASE_URL}/api/aurem-dev/auth/login",
        json={"email": EMAIL, "password": PASSWORD},
        timeout=30,
    )
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text[:300]}"
    return r.json()["token"]


@pytest.fixture(scope="module")
def auth_headers(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


# ── Auth gate ───────────────────────────────────────────────────────

def test_config_get_requires_auth():
    r = requests.get(f"{BASE_URL}/api/aurem-dev/deploy/config", timeout=20)
    assert r.status_code == 401

def test_config_for_project_requires_auth():
    r = requests.get(f"{BASE_URL}/api/aurem-dev/deploy/config/anyproj", timeout=20)
    assert r.status_code == 401

def test_runs_requires_auth():
    r = requests.get(f"{BASE_URL}/api/aurem-dev/deploy/runs", timeout=20)
    assert r.status_code == 401

def test_runs_logs_requires_auth():
    r = requests.get(f"{BASE_URL}/api/aurem-dev/deploy/runs/anything/logs", timeout=20)
    assert r.status_code == 401


# ── Config endpoints (hybrid fallback) ──────────────────────────────

def test_config_returns_configured_flag(auth_headers):
    r = requests.get(f"{BASE_URL}/api/aurem-dev/deploy/config",
                     headers=auth_headers, timeout=20)
    assert r.status_code == 200
    d = r.json()
    assert "configured" in d
    # If configured, private_key MUST be masked
    if d.get("configured"):
        assert "private_key_enc" not in d
        assert d.get("private_key", "").startswith("•")
        assert d.get("scope") in ("project", "user")


def test_config_for_project_hybrid_contract(auth_headers):
    # Use a clearly non-existing project_id; expected to either
    # fall back to user-level cfg (scope=user) or return not configured.
    r = requests.get(
        f"{BASE_URL}/api/aurem-dev/deploy/config/__nonexistent_proj_xyz__",
        headers=auth_headers, timeout=20)
    assert r.status_code == 200
    d = r.json()
    if d.get("configured"):
        # Hybrid fallback - must be scope=user since project doesn't exist
        assert d.get("scope") == "user"
        assert d.get("project_id") is None
        assert "private_key_enc" not in d
    else:
        assert d == {"configured": False}


# ── Runs alias ──────────────────────────────────────────────────────

def test_runs_returns_runs_and_project_id_null(auth_headers):
    r = requests.get(f"{BASE_URL}/api/aurem-dev/deploy/runs",
                     headers=auth_headers, timeout=20)
    assert r.status_code == 200
    d = r.json()
    assert "runs" in d and isinstance(d["runs"], list)
    assert d.get("project_id") is None


def test_runs_filters_by_project_id(auth_headers):
    r = requests.get(
        f"{BASE_URL}/api/aurem-dev/deploy/runs?project_id=p_no_such_proj",
        headers=auth_headers, timeout=20)
    assert r.status_code == 200
    d = r.json()
    assert d.get("project_id") == "p_no_such_proj"
    assert isinstance(d.get("runs"), list)


def test_runs_clamps_limit(auth_headers):
    r = requests.get(
        f"{BASE_URL}/api/aurem-dev/deploy/runs?limit=999999",
        headers=auth_headers, timeout=20)
    assert r.status_code == 200
    d = r.json()
    assert len(d["runs"]) <= 100


# ── Logs alias ──────────────────────────────────────────────────────

def test_runs_logs_returns_404_for_unknown_run(auth_headers):
    r = requests.get(
        f"{BASE_URL}/api/aurem-dev/deploy/runs/__nope__/logs",
        headers=auth_headers, timeout=20)
    assert r.status_code == 404


# ── POST /run with no config ────────────────────────────────────────

def test_run_400_when_not_configured(auth_headers):
    # If the test user has no config saved, POST /run should yield 400.
    cfg = requests.get(f"{BASE_URL}/api/aurem-dev/deploy/config",
                       headers=auth_headers, timeout=20).json()
    if cfg.get("configured"):
        pytest.skip("test user has cfg saved - skipping not-configured path")
    r = requests.post(
        f"{BASE_URL}/api/aurem-dev/deploy/run",
        headers=auth_headers,
        json={"mode": "dry_run", "project_id": "__p_no_cfg__"},
        timeout=20)
    assert r.status_code == 400
    body = r.json()
    detail = body.get("detail") if isinstance(body, dict) else body
    assert detail == "deploy_not_configured" or "deploy_not_configured" in str(body)
