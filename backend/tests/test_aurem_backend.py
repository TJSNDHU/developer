"""
Backend tests for AUREM Dev.

Covers:
  * /api/health
  * /api/aurem-dev/auth/signup, /login, /me
  * /api/aurem-dev/chat/send  (Emergent LLM)
  * /api/aurem-dev/stacks
"""
import os
import time
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL")
if not BASE_URL:
    # Fallback to frontend .env when running pytest from CLI without env
    try:
        with open("/app/frontend/.env") as fh:
            for line in fh:
                if line.startswith("REACT_APP_BACKEND_URL"):
                    BASE_URL = line.split("=", 1)[1].strip()
                    break
    except FileNotFoundError:
        pass

assert BASE_URL, "REACT_APP_BACKEND_URL must be set"
BASE_URL = BASE_URL.rstrip("/")
API = f"{BASE_URL}/api"
AUREM = f"{API}/aurem-dev"

SEEDED_EMAIL = "test@aurem.dev"
SEEDED_PASS = "testpass123"


@pytest.fixture(scope="session")
def session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


@pytest.fixture(scope="session")
def unique_email() -> str:
    return f"e2e-{int(time.time())}@aurem.dev"


# ─── Health ───────────────────────────────────────────────────────────────
def test_health(session):
    r = session.get(f"{API}/health", timeout=15)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["ok"] is True
    assert data["service"] == "aurem-dev"
    assert data["db"] is True


# ─── Stacks ───────────────────────────────────────────────────────────────
def test_stacks_list(session):
    r = session.get(f"{AUREM}/stacks", timeout=15)
    assert r.status_code == 200, r.text
    data = r.json()
    assert "stacks" in data
    assert isinstance(data["stacks"], list)
    assert len(data["stacks"]) >= 4
    # Required stack ids
    ids = {s["id"] for s in data["stacks"]}
    assert "react-fastapi" in ids


# ─── Auth Signup ──────────────────────────────────────────────────────────
@pytest.fixture(scope="session")
def new_user_token(session, unique_email):
    r = session.post(
        f"{AUREM}/auth/signup",
        json={"email": unique_email, "password": "testpass123", "name": "E2E User"},
        timeout=15,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["ok"] is True
    assert data["email"] == unique_email
    assert isinstance(data["token"], str) and len(data["token"]) > 10
    return data["token"]


def test_signup_creates_user(new_user_token):
    assert new_user_token


def test_signup_duplicate_returns_409(session, unique_email):
    r = session.post(
        f"{AUREM}/auth/signup",
        json={"email": unique_email, "password": "x", "name": "dup"},
        timeout=15,
    )
    assert r.status_code == 409, f"expected 409 got {r.status_code}: {r.text}"


# ─── Auth Login ───────────────────────────────────────────────────────────
def _ensure_seeded(session):
    """Create seeded user if not already (idempotent)."""
    r = session.post(
        f"{AUREM}/auth/login",
        json={"email": SEEDED_EMAIL, "password": SEEDED_PASS},
        timeout=15,
    )
    if r.status_code == 200:
        return r.json()["token"]
    if r.status_code == 401:
        sr = session.post(
            f"{AUREM}/auth/signup",
            json={"email": SEEDED_EMAIL, "password": SEEDED_PASS, "name": "Seeded"},
            timeout=15,
        )
        assert sr.status_code == 200, sr.text
        return sr.json()["token"]
    raise AssertionError(f"Unexpected login status {r.status_code}: {r.text}")


@pytest.fixture(scope="session")
def seeded_token(session):
    return _ensure_seeded(session)


def test_login_success(session, seeded_token):
    r = session.post(
        f"{AUREM}/auth/login",
        json={"email": SEEDED_EMAIL, "password": SEEDED_PASS},
        timeout=15,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["ok"] is True
    assert data["email"] == SEEDED_EMAIL
    assert "token" in data


def test_login_bad_password(session, seeded_token):
    r = session.post(
        f"{AUREM}/auth/login",
        json={"email": SEEDED_EMAIL, "password": "wrong-pass-xyz"},
        timeout=15,
    )
    assert r.status_code == 401, r.text


# ─── Auth /me ─────────────────────────────────────────────────────────────
def test_me_with_token(session, seeded_token):
    r = session.get(
        f"{AUREM}/auth/me",
        headers={"Authorization": f"Bearer {seeded_token}"},
        timeout=15,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["ok"] is True
    assert data["user"]["email"] == SEEDED_EMAIL


def test_me_without_token(session):
    r = session.get(f"{AUREM}/auth/me", timeout=15)
    assert r.status_code == 401, r.text


# ─── Chat ─────────────────────────────────────────────────────────────────
def test_chat_send_with_auth(session, seeded_token):
    r = session.post(
        f"{AUREM}/chat/send",
        headers={"Authorization": f"Bearer {seeded_token}"},
        json={"prompt": "Say hello in 5 words", "max_tool_iters": 1},
        timeout=60,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["ok"] is True
    assert isinstance(data.get("content"), str)
    assert len(data["content"]) > 0
    # provider should hint emergent
    provider = (data.get("provider") or "").lower()
    assert "emergent" in provider or provider != "", f"provider missing: {data}"


def test_chat_send_without_auth(session):
    r = session.post(
        f"{AUREM}/chat/send",
        json={"prompt": "hi", "max_tool_iters": 1},
        timeout=15,
    )
    assert r.status_code == 401, r.text
