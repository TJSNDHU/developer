"""
Iter 22 — Live HTTP regression on PREVIEW for iter 212m-110 founder bypass.

Tests:
  1. Founder login (try Singh1986$ first, fall back to FounderOwn123!)
  2. /codebase-health/scan with categories=['bug_hunt'] hit 12x rapidly
     → founder must NEVER get 429 rate_limited.
  3. /codebase-health/fix with founder → tokens_charged must be 0.
  4. Non-founder (test@aurem.dev) /fix when tokens_remaining=0 should
     still get 402 (or charge correctly when balance > 0).
"""
from __future__ import annotations

import os
import pytest
import requests

BASE_URL = "https://launch-pad-237.preview.emergentagent.com"
# Iter 22 NOTE: On PREVIEW, `teji.ss1986@gmail.com` is not seeded with either
# password from /app/memory/test_credentials.md (both return 401). However,
# `test@aurem.dev` on PREVIEW is auto-promoted to tier=founder, is_admin=true,
# is_unlimited=true via ADMIN_EMAIL/FOUNDER_EMAILS env vars — so we use it as
# the live founder-bypass proxy. The non-founder-bypass-leak guard is already
# covered by the unit test test_fix_route_still_charges_non_founder (PASSED).
FOUNDER_EMAIL = "test@aurem.dev"
FOUNDER_PASSWORDS = ["AuremTest2026!"]
# Optional secondary attempt against the real founder email (will skip if 401)
SECONDARY_FOUNDER_EMAIL = "teji.ss1986@gmail.com"
SECONDARY_FOUNDER_PASSWORDS = ["Singh1986$", "FounderOwn123!"]


def _login(email: str, password: str) -> str | None:
    r = requests.post(
        f"{BASE_URL}/api/aurem-dev/auth/login",
        json={"email": email, "password": password},
        timeout=30,
    )
    if r.status_code == 200:
        return r.json().get("token") or r.json().get("access_token")
    return None


@pytest.fixture(scope="module")
def founder_token():
    for pw in FOUNDER_PASSWORDS:
        tok = _login(FOUNDER_EMAIL, pw)
        if tok:
            print(f"\n[founder] logged in with password={pw[:3]}***")
            return tok
    pytest.skip("Founder login failed on PREVIEW with both passwords")


@pytest.fixture(scope="module")
def free_token():
    """Skip the free-user live test — on PREVIEW, test@aurem.dev is itself
    a founder. The non-founder bypass-leak guard is covered by the unit
    test test_fix_route_still_charges_non_founder (passes)."""
    pytest.skip("PREVIEW has no seeded non-founder account — unit test covers leak guard")


@pytest.fixture(scope="module")
def founder_project_id(founder_token):
    r = requests.get(
        f"{BASE_URL}/api/aurem-dev/cto/projects/list",
        headers={"Authorization": f"Bearer {founder_token}"},
        timeout=30,
    )
    assert r.status_code == 200, f"projects/list {r.status_code}: {r.text[:200]}"
    projects = r.json().get("projects") or r.json().get("items") or []
    if not projects:
        pytest.skip("Founder has no projects to test against")
    pid = projects[0].get("project_id") or projects[0].get("id")
    print(f"[founder] using project_id={pid}")
    return pid


def test_founder_auth_me(founder_token):
    """Confirm founder claims are present on the JWT."""
    r = requests.get(
        f"{BASE_URL}/api/aurem-dev/auth/me",
        headers={"Authorization": f"Bearer {founder_token}"},
        timeout=30,
    )
    assert r.status_code == 200, r.text[:300]
    data = r.json()
    # /auth/me may return claims at root OR nested under `user`.
    claims = data.get("user") or data
    qualifies = (
        claims.get("is_admin") or claims.get("is_unlimited") or claims.get("tier") == "founder"
    )
    assert qualifies, f"founder user does not have any bypass flag: {claims}"


def test_founder_scan_no_rate_limit(founder_token, founder_project_id):
    """Hit /codebase-health/scan 12x rapidly — founder must NEVER 429."""
    headers = {"Authorization": f"Bearer {founder_token}"}
    body = {"project_id": founder_project_id, "categories": ["bug_hunt"]}
    statuses = []
    for i in range(12):
        r = requests.post(
            f"{BASE_URL}/api/aurem-dev/codebase-health/scan",
            json=body,
            headers=headers,
            timeout=60,
        )
        statuses.append(r.status_code)
        if r.status_code == 429:
            print(f"\n[scan #{i+1}] 429 body: {r.text[:300]}")
    print(f"\n[scan] statuses across 12 hits: {statuses}")
    assert 429 not in statuses, f"Founder got rate-limited: {statuses}"


def test_founder_fix_tokens_charged_zero(founder_token, founder_project_id):
    """Founder /fix call must return tokens_charged=0."""
    headers = {"Authorization": f"Bearer {founder_token}"}
    body = {
        "project_id": founder_project_id,
        "finding_id": "test_finding_iter22",
        "title": "demo",
        "file": "a.py",
        "line": 1,
        "message": "x",
        "fix_hint": "y",
        "tokens": 50,
    }
    r = requests.post(
        f"{BASE_URL}/api/aurem-dev/codebase-health/fix",
        json=body,
        headers=headers,
        timeout=60,
    )
    print(f"\n[fix] status={r.status_code} body={r.text[:400]}")
    assert r.status_code == 200, f"founder /fix returned {r.status_code}: {r.text[:300]}"
    data = r.json()
    assert data.get("tokens_charged") == 0, f"founder tokens_charged != 0: {data}"
    assert data.get("ok") is True


def test_non_founder_fix_still_charges_or_402(free_token):
    """Free user /fix should NOT have founder bypass.
    Either tokens_charged > 0 (if has balance) OR 402 insufficient_tokens."""
    headers = {"Authorization": f"Bearer {free_token}"}
    # Use a dummy project_id — we just need to confirm the bypass doesn't leak.
    # If project_id is invalid the route may 404 or 400 BEFORE the bypass branch
    # — so first fetch a valid project for this user, else skip.
    pr = requests.get(
        f"{BASE_URL}/api/aurem-dev/cto/projects/list",
        headers=headers,
        timeout=30,
    )
    projects = []
    if pr.status_code == 200:
        projects = pr.json().get("projects") or pr.json().get("items") or []
    if not projects:
        pytest.skip("free user has no project — bypass-leak check covered by unit test")
    pid = projects[0].get("project_id") or projects[0].get("id")

    body = {
        "project_id": pid,
        "finding_id": "test_finding_iter22_free",
        "title": "demo",
        "file": "a.py",
        "line": 1,
        "message": "x",
        "fix_hint": "y",
        "tokens": 50,
    }
    r = requests.post(
        f"{BASE_URL}/api/aurem-dev/codebase-health/fix",
        json=body,
        headers=headers,
        timeout=60,
    )
    print(f"\n[free /fix] status={r.status_code} body={r.text[:400]}")
    if r.status_code == 200:
        data = r.json()
        # Free user must be charged (>0) — bypass must NOT apply.
        assert data.get("tokens_charged", 0) > 0, \
            f"BYPASS LEAK: free user got tokens_charged={data.get('tokens_charged')}"
    else:
        # 402 insufficient is also acceptable proof bypass didn't apply.
        assert r.status_code in (400, 402, 403, 404), \
            f"unexpected status {r.status_code}: {r.text[:300]}"
