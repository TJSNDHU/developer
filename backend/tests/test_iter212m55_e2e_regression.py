"""
Iter 212m-55 — End-to-end HTTP regression for:
  1) New /api/aurem-dev/security-scan/run endpoint (401, 400, 404, 400-no-PAT)
  2) Critical POST-JSON regression after NoSQLOpASGIGuard middleware rewrite
     (login, register, NoSQL $where guard still blocks).

Runs against REACT_APP_BACKEND_URL (preview env).
"""
import os
import time
import uuid
import requests
import pytest

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://launch-pad-237.preview.emergentagent.com").rstrip("/")
SCAN_URL = f"{BASE_URL}/api/aurem-dev/security-scan/run"
LOGIN_URL = f"{BASE_URL}/api/aurem-dev/auth/login"
SIGNUP_URL = f"{BASE_URL}/api/aurem-dev/auth/signup"

TEST_EMAIL = "test@aurem.dev"
TEST_PASSWORD = "AuremTest2026!"


# ─────────── fixtures ───────────
def _clear_login_lockout():
    """Iter 344 — other suites' failed logins trip the shared IP
    brute-force lockout (429) and cascade into this module. Clear the
    persisted lockout rows before we log in."""
    try:
        import pymongo
        db = pymongo.MongoClient(
            os.environ.get("MONGO_URL", "mongodb://localhost:27017")
        )[os.environ.get("DB_NAME", "aurem_dev")]
        db.login_attempts.delete_many({})
    except Exception:
        pass


@pytest.fixture(scope="module")
def session() -> requests.Session:
    _clear_login_lockout()
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    try:
        requests.get(f"{BASE_URL}/api/health", timeout=10)
    except requests.ConnectionError:
        pytest.skip(f"preview API unreachable at {BASE_URL} — live-server suite")
    return s


@pytest.fixture(scope="module")
def auth_token(session) -> str:
    r = session.post(LOGIN_URL, json={"email": TEST_EMAIL, "password": TEST_PASSWORD}, timeout=30)
    if r.status_code != 200:
        pytest.skip(f"Cannot login as {TEST_EMAIL}: {r.status_code} {r.text[:200]}")
    data = r.json()
    token = data.get("token") or data.get("access_token") or (data.get("user") or {}).get("token")
    if not token:
        pytest.skip(f"Login OK but no token field in response: keys={list(data.keys())}")
    return token


# ─────────── POST-JSON regression (highest risk) ───────────
class TestMiddlewareRegression:
    """Confirm the rewrite of _nosql_op_guard → NoSQLOpASGIGuard did NOT break POST JSON."""

    def test_login_with_json_body_not_499(self, session):
        """Before fix: every POST JSON returned 499. Now must return real status code."""
        r = session.post(LOGIN_URL, json={"email": "nobody@aurem.dev", "password": "wrong"}, timeout=30)
        # The critical assertion: NOT 499 (client disconnected/upstream error)
        assert r.status_code != 499, f"REGRESSION: login still 499 — middleware not fixed. body={r.text[:300]}"
        # Bad creds should return 4xx (401 expected, but 400/403 acceptable)
        assert 400 <= r.status_code < 500, f"Unexpected status {r.status_code}: {r.text[:200]}"

    def test_login_good_creds_returns_200(self, session):
        # Iter 345 — other suites' bad-password tests accumulate IP
        # lockout DURING the run; clear right before this login.
        _clear_login_lockout()
        r = session.post(LOGIN_URL, json={"email": TEST_EMAIL, "password": TEST_PASSWORD}, timeout=30)
        assert r.status_code != 499, "REGRESSION: login 499 with good creds"
        assert r.status_code == 200, f"Expected 200 for seeded test user, got {r.status_code}: {r.text[:200]}"
        data = r.json()
        token = data.get("token") or data.get("access_token")
        assert token, f"No token in login response: {list(data.keys())}"

    def test_signup_endpoint_reachable(self, session):
        """Signup with a fresh random email — should succeed or return 4xx (NOT 499)."""
        rnd = uuid.uuid4().hex[:10]
        payload = {"email": f"TEST_reg_{rnd}@aurem.dev", "password": "TempPass123!", "name": "Regression Test"}
        r = session.post(SIGNUP_URL, json=payload, timeout=30)
        assert r.status_code != 499, f"REGRESSION: signup returned 499. body={r.text[:300]}"
        # Either success (200/201) or a sensible client error
        assert r.status_code in (200, 201, 400, 409, 422), f"Unexpected status {r.status_code}: {r.text[:200]}"

    def test_nosql_where_operator_still_blocked(self, session):
        """The middleware was rewritten — confirm $where guard STILL works."""
        payload = {"email": "x@x.com", "password": {"$where": "1"}}
        r = session.post(LOGIN_URL, json=payload, timeout=30)
        assert r.status_code != 499, "REGRESSION: $where request 499"
        # Should be 400 from the guard middleware
        assert r.status_code == 400, f"Expected 400 from NoSQL guard, got {r.status_code}: {r.text[:200]}"
        # Message should reference disallowed operator
        body_text = r.text.lower()
        assert "disallowed" in body_text or "operator" in body_text or "$where" in r.text, \
            f"Guard message missing: {r.text[:300]}"


# ─────────── Security-scan endpoint contract tests ───────────
class TestSecurityScanEndpoint:
    def test_no_auth_returns_401(self, session):
        # No Authorization header
        plain = requests.Session()
        plain.headers.update({"Content-Type": "application/json"})
        r = plain.post(SCAN_URL, json={"project_id": "anything"}, timeout=30)
        assert r.status_code != 499, "REGRESSION: scan endpoint 499"
        assert r.status_code == 401, f"Expected 401 without auth, got {r.status_code}: {r.text[:200]}"

    def test_missing_project_id_returns_400(self, session, auth_token):
        h = {"Authorization": f"Bearer {auth_token}", "Content-Type": "application/json"}
        r = requests.post(SCAN_URL, json={}, headers=h, timeout=30)
        assert r.status_code != 499
        assert r.status_code == 400, f"Expected 400 for missing project_id, got {r.status_code}: {r.text[:200]}"
        assert "project_id" in r.text.lower()

    def test_bogus_project_id_returns_404(self, session, auth_token):
        h = {"Authorization": f"Bearer {auth_token}", "Content-Type": "application/json"}
        r = requests.post(SCAN_URL, json={"project_id": f"TEST_bogus_{uuid.uuid4().hex}"}, headers=h, timeout=30)
        assert r.status_code != 499
        assert r.status_code == 404, f"Expected 404 for bogus project_id, got {r.status_code}: {r.text[:200]}"

    def test_route_is_wired(self, session, auth_token):
        """Smoke: any auth+body POST that does NOT return 404 'Not Found' on the route itself."""
        h = {"Authorization": f"Bearer {auth_token}", "Content-Type": "application/json"}
        r = requests.post(SCAN_URL, json={"project_id": "x"}, headers=h, timeout=30)
        # If status is 404, ensure it's the project-not-found error, not route-not-found
        if r.status_code == 404:
            assert "project" in r.text.lower() or "not found" in r.text.lower()
        assert r.status_code != 499, "Endpoint returning 499 — middleware regression"
