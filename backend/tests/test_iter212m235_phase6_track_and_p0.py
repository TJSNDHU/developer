"""
Phase 6 tests: Personal Track (set-track/me) + P0 admin panel endpoints
Iter 212m235
"""
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://launch-pad-237.preview.emergentagent.com").rstrip("/")
EMAIL = "test@aurem.dev"
PASSWORD = "AuremTest2026!"


@pytest.fixture(scope="module")
def session():
    s = requests.Session()
    # Try aurem-dev login
    r = s.post(f"{BASE_URL}/api/aurem-dev/auth/login", json={"email": EMAIL, "password": PASSWORD}, timeout=30)
    if r.status_code != 200:
        pytest.skip(f"Login failed: {r.status_code} {r.text[:200]}")
    tok = r.json().get("token") or r.json().get("access_token")
    if tok:
        s.headers.update({"Authorization": f"Bearer {tok}"})
    return s


# ---- Track endpoints ----
class TestSetTrack:
    def test_set_track_personal(self, session):
        r = session.post(f"{BASE_URL}/api/aurem-dev/auth/set-track", json={"track": "personal"}, timeout=15)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("ok") is True
        assert body.get("track") == "personal"
        assert "updated_at" in body

    def test_me_reflects_personal(self, session):
        r = session.get(f"{BASE_URL}/api/aurem-dev/auth/me", timeout=15)
        assert r.status_code == 200
        user = r.json().get("user") or r.json()
        assert user.get("track") == "personal"

    def test_set_track_developer(self, session):
        r = session.post(f"{BASE_URL}/api/aurem-dev/auth/set-track", json={"track": "developer"}, timeout=15)
        assert r.status_code == 200
        body = r.json()
        assert body.get("track") == "developer"

    def test_me_reflects_developer(self, session):
        r = session.get(f"{BASE_URL}/api/aurem-dev/auth/me", timeout=15)
        assert r.status_code == 200
        user = r.json().get("user") or r.json()
        assert user.get("track") == "developer"

    def test_set_track_invalid(self, session):
        r = session.post(f"{BASE_URL}/api/aurem-dev/auth/set-track", json={"track": "foobar"}, timeout=15)
        assert r.status_code == 400
        detail = r.json().get("detail")
        # detail may be dict or str
        if isinstance(detail, dict):
            assert detail.get("reason") == "invalid_track"
            assert "developer" in detail.get("allowed", [])
            assert "personal" in detail.get("allowed", [])


# ---- P0 admin created-at health ----
class TestCreatedAtHealth:
    def test_health(self, session):
        r = session.get(f"{BASE_URL}/api/aurem-dev/admin/dev-users/created-at-health", timeout=30)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("ok") is True
        assert body.get("healthy") is True
        assert body.get("total_users", 0) >= 1
        assert body.get("datetime_typed", -1) == 0
        assert body.get("missing_field", -1) == 0
        assert body.get("by_type", {}).get("double", 0) >= 1

    def test_backfill_idempotent(self, session):
        r = session.post(f"{BASE_URL}/api/aurem-dev/admin/dev-users/backfill-created-at", timeout=30)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("ok") is True
        assert body.get("datetime_fixed") == 0
        assert body.get("missing_filled") == 0
        assert body.get("still_pending") == 0


# ---- Supabase admin ----
class TestSupabaseAdmin:
    def test_pending_downgrades(self, session):
        r = session.get(f"{BASE_URL}/api/aurem-dev/supabase/admin/pending-downgrades", timeout=30)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("ok") is True
        assert body.get("count") == 0
        assert body.get("escalated") == []
        assert body.get("rows") == []

    def test_sweep_now(self, session):
        r = session.post(f"{BASE_URL}/api/aurem-dev/supabase/admin/sweep-now", timeout=30)
        assert r.status_code == 200, r.text
        body = r.json()
        assert "stats" in body
        assert body["stats"].get("processed") == 0
        assert body["stats"].get("deleted") == 0
