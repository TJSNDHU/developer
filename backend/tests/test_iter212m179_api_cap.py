"""
Iter 212m-179 — API-level tests for fix-pipeline preview/bulk cap enforcement
and summary 404. Uses the live preview backend via REACT_APP_BACKEND_URL.
"""
import os
import requests
import pytest

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
if not BASE_URL:
    # Fallback: read from frontend .env
    try:
        with open("/app/frontend/.env") as f:
            for line in f:
                if line.startswith("REACT_APP_BACKEND_URL"):
                    BASE_URL = line.split("=", 1)[1].strip().strip('"').rstrip("/")
    except Exception:
        pass

CREDS = {"email": "test@aurem.dev", "password": "AuremTest2026!"}


@pytest.fixture(scope="module")
def auth_token():
    r = requests.post(f"{BASE_URL}/api/aurem-dev/auth/login", json=CREDS, timeout=30)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text[:200]}"
    tok = r.json().get("token")
    assert tok, "no token in login response"
    return tok


@pytest.fixture
def headers(auth_token):
    return {"Authorization": f"Bearer {auth_token}", "Content-Type": "application/json"}


def _mk_findings(n: int):
    return [
        {
            "id": f"f{i}",
            "rule_id": "x",
            "file": "a.py",
            "category": "vanguard",
            "severity": "high",
        }
        for i in range(n)
    ]


# Preview endpoint tests --------------------------------------------------

def test_preview_caps_at_20_when_over(headers):
    findings = _mk_findings(25)
    r = requests.post(
        f"{BASE_URL}/api/aurem-dev/fix-pipeline/preview",
        json={"findings": findings, "project_id": "nonexistent"},
        headers=headers, timeout=30,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["count"] == 20
    assert data["bulk_max"] == 20
    assert data["total_requested"] == 25


def test_preview_under_cap_returns_normal(headers):
    findings = _mk_findings(5)
    r = requests.post(
        f"{BASE_URL}/api/aurem-dev/fix-pipeline/preview",
        json={"findings": findings, "project_id": "nonexistent"},
        headers=headers, timeout=30,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["count"] == 5
    assert data["bulk_max"] == 20
    assert data["total_requested"] == 5


# Bulk endpoint cap tests ------------------------------------------------

def test_bulk_over_cap_returns_400(headers):
    findings = _mk_findings(21)
    r = requests.post(
        f"{BASE_URL}/api/aurem-dev/fix-pipeline/bulk",
        json={"project_id": "nonexistent", "findings": findings},
        headers=headers, timeout=30,
    )
    assert r.status_code == 400, r.text
    body = r.json()
    # FastAPI wraps HTTPException detail in {"detail": <detail>}
    detail = body.get("detail", body)
    assert detail.get("error") == "bulk_limit_exceeded", detail
    assert detail.get("max") == 20
    assert detail.get("requested") == 21


def test_bulk_under_cap_passes_cap_check(headers):
    # <=20 findings on an invalid project — should proceed past cap check
    # but fail later for project_not_found or similar. NOT 400 with bulk_limit_exceeded.
    findings = _mk_findings(3)
    r = requests.post(
        f"{BASE_URL}/api/aurem-dev/fix-pipeline/bulk",
        json={"project_id": "definitely_nonexistent_xyz", "findings": findings},
        headers=headers, timeout=30,
    )
    # Should NOT be a bulk_limit_exceeded error
    if r.status_code == 400:
        detail = r.json().get("detail", {})
        if isinstance(detail, dict):
            assert detail.get("error") != "bulk_limit_exceeded", \
                f"unexpectedly hit cap with 3 findings: {detail}"
    # Any status other than the cap error is acceptable (project_not_found, etc.)


# Summary 404 ------------------------------------------------------------

def test_summary_nonexistent_returns_404(headers):
    r = requests.get(
        f"{BASE_URL}/api/aurem-dev/fix-pipeline/summary/does_not_exist_xyz",
        headers=headers, timeout=30,
    )
    assert r.status_code == 404, f"expected 404, got {r.status_code}: {r.text[:200]}"
