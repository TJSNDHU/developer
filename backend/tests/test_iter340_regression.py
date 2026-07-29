"""Iter 340 pre-deploy regression - backend API checks."""
import os
import json
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://launch-pad-237.preview.emergentagent.com").rstrip("/")
EMAIL = "test@aurem.dev"
PASSWORD = "AuremTest2026!"
SESSION_ID = "e2e-collapse-339d"
PROJECT_ID = "p_demo_a"


@pytest.fixture(scope="module")
def token():
    r = requests.post(f"{BASE_URL}/api/aurem-dev/auth/login",
                      json={"email": EMAIL, "password": PASSWORD}, timeout=15)
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture(scope="module")
def auth_headers(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def test_health():
    r = requests.get(f"{BASE_URL}/api/health", timeout=15)
    assert r.status_code == 200


def test_auth_me_no_sensitive_keys(auth_headers):
    r = requests.get(f"{BASE_URL}/api/aurem-dev/auth/me", headers=auth_headers, timeout=15)
    assert r.status_code == 200, r.text
    body_text = r.text.lower()
    # Check no sensitive keys in body
    forbidden = ["access_token", "mfa_secret", "backup_codes"]
    leaks = [k for k in forbidden if k in body_text]
    assert not leaks, f"Sensitive keys leaked in /auth/me: {leaks} body={r.text[:500]}"


def test_chat_history_8_turns(auth_headers):
    r = requests.get(f"{BASE_URL}/api/aurem-dev/chat/history",
                     params={"session_id": SESSION_ID}, headers=auth_headers, timeout=15)
    assert r.status_code == 200, r.text
    data = r.json()
    # Could be list or dict with turns/messages key
    turns = data if isinstance(data, list) else (data.get("turns") or data.get("messages") or data.get("history") or [])
    assert len(turns) >= 8, f"Expected >=8 turns, got {len(turns)}: {json.dumps(data)[:500]}"
    providers = [str(t.get("provider", "")).lower() for t in turns]
    assert any("loop" in p for p in providers), f"No provider:'loop' turn found. providers={providers}"


def test_loop_start_accepts_session_id_schema(auth_headers):
    """POST /api/aurem-dev/loop/start must accept session_id field (no 422)."""
    payload = {"project_id": PROJECT_ID, "user_message": "test", "session_id": "x"}
    r = requests.post(f"{BASE_URL}/api/aurem-dev/loop/start",
                      json=payload, headers=auth_headers, timeout=20)
    # 422 = schema rejection = FAIL. Anything else acceptable.
    assert r.status_code != 422, f"session_id rejected as schema-invalid: {r.text}"
    # Cleanup: if loop actually started, cancel it
    if r.status_code in (200, 201, 202):
        try:
            body = r.json()
            loop_id = body.get("loop_id") or body.get("id") or (body.get("loop") or {}).get("id")
            if loop_id:
                requests.post(f"{BASE_URL}/api/aurem-dev/loop/{loop_id}/cancel",
                              headers=auth_headers, timeout=10)
        except Exception as e:
            print(f"cleanup err: {e}")
    print(f"loop/start status={r.status_code} body={r.text[:300]}")
