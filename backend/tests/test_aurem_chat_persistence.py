"""
Backend tests for AUREM Dev chat session persistence + SSE streaming.

Covers (iteration 2):
  * POST /api/aurem-dev/chat/send persists to db.chat_sessions
  * GET  /api/aurem-dev/chat/history
  * GET  /api/aurem-dev/chat/sessions
  * DEL  /api/aurem-dev/chat/sessions/{id}
  * POST /api/aurem-dev/chat/stream (SSE)
  * Auth boundaries + cross-user isolation
"""
import json
import os
import time
import uuid

import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL")
if not BASE_URL:
    # Iter 309 · Phase 0.2 · Round 4 — wrap the .env fallback in
    # try/except FileNotFoundError. In CI there is no /app/frontend/.env,
    # and a bare open() there raises at collection time and aborts
    # pytest for the whole run (masking every subsequent test, including
    # the CI canary). Matches the guard already in test_aurem_backend.py.
    try:
        with open("/app/frontend/.env") as fh:
            for line in fh:
                if line.startswith("REACT_APP_BACKEND_URL"):
                    BASE_URL = line.split("=", 1)[1].strip()
                    break
    except FileNotFoundError:
        pass
if not BASE_URL:
    pytest.skip("REACT_APP_BACKEND_URL not set — skipping live-URL smoke tests",
                allow_module_level=True)
BASE_URL = BASE_URL.rstrip("/")
AUREM = f"{BASE_URL}/api/aurem-dev"

SEEDED_EMAIL = "test@aurem.dev"
SEEDED_PASS = "testpass123"


@pytest.fixture(scope="session")
def session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


def _login_or_signup(s, email, password, name="Test"):
    r = s.post(f"{AUREM}/auth/login", json={"email": email, "password": password}, timeout=15)
    if r.status_code == 200:
        return r.json()["token"]
    r = s.post(f"{AUREM}/auth/signup",
               json={"email": email, "password": password, "name": name}, timeout=15)
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture(scope="session")
def token_a(session):
    return _login_or_signup(session, SEEDED_EMAIL, SEEDED_PASS, "Seeded")


@pytest.fixture(scope="session")
def token_b(session):
    email = f"e2e-userb-{int(time.time())}@aurem.dev"
    return _login_or_signup(session, email, "testpass123", "User B")


def auth(t):
    return {"Authorization": f"Bearer {t}"}


# ─── Auth boundary: 401 without token ────────────────────────────────────
@pytest.mark.parametrize("method,path,body", [
    ("POST", "/chat/send", {"prompt": "hi", "session_id": "x"}),
    ("POST", "/chat/stream", {"prompt": "hi", "session_id": "x"}),
    ("GET", "/chat/history?session_id=x", None),
    ("GET", "/chat/sessions", None),
    ("DELETE", "/chat/sessions/x", None),
])
def test_chat_endpoints_require_auth(session, method, path, body):
    url = f"{AUREM}{path}"
    if method == "POST":
        r = session.post(url, json=body, timeout=15)
    elif method == "GET":
        r = session.get(url, timeout=15)
    else:
        r = session.delete(url, timeout=15)
    assert r.status_code == 401, f"{method} {path} → {r.status_code} {r.text}"


# ─── /chat/send persists + /chat/history returns turns ──────────────────
def test_chat_send_persists_and_history_returns(session, token_a):
    sid = f"sess-{uuid.uuid4()}"
    prompt = "Reply with the single word: PONG"
    r = session.post(f"{AUREM}/chat/send",
                     headers=auth(token_a),
                     json={"prompt": prompt, "session_id": sid, "max_tool_iters": 1},
                     timeout=60)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["ok"] is True
    assert isinstance(data["content"], str) and len(data["content"]) > 0
    assert data["session_id"] == sid
    provider_send = data.get("provider", "")

    # history should now have user + assistant turns
    h = session.get(f"{AUREM}/chat/history",
                    headers=auth(token_a),
                    params={"session_id": sid}, timeout=15)
    assert h.status_code == 200, h.text
    hd = h.json()
    assert hd["ok"] is True
    msgs = hd["messages"]
    assert isinstance(msgs, list)
    assert len(msgs) >= 2
    # last 2 are user + assistant
    assert msgs[-2]["role"] == "user"
    assert msgs[-2]["content"] == prompt
    assert msgs[-1]["role"] == "assistant"
    assert msgs[-1]["content"] == data["content"]
    assert "ts" in msgs[-1]
    assert msgs[-1].get("provider") == provider_send


# ─── /chat/sessions returns recent sessions ─────────────────────────────
def test_chat_sessions_lists_recent(session, token_a):
    sid = f"sess-{uuid.uuid4()}"
    session.post(f"{AUREM}/chat/send", headers=auth(token_a),
                 json={"prompt": "Say hi briefly", "session_id": sid, "max_tool_iters": 1},
                 timeout=60)
    r = session.get(f"{AUREM}/chat/sessions", headers=auth(token_a), timeout=15)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["ok"] is True
    sessions = data["sessions"]
    assert isinstance(sessions, list) and len(sessions) >= 1
    ids = [s["session_id"] for s in sessions]
    assert sid in ids
    # First entry should have the required fields
    first = sessions[0]
    assert "session_id" in first
    assert "last_message" in first
    assert "updated_at" in first
    assert "created_at" in first
    # ordered desc by updated_at
    updates = [s["updated_at"] for s in sessions]
    assert updates == sorted(updates, reverse=True)


# ─── Cross-user isolation ───────────────────────────────────────────────
def test_cross_user_isolation(session, token_a, token_b):
    sid = f"sess-{uuid.uuid4()}"
    r = session.post(f"{AUREM}/chat/send", headers=auth(token_a),
                     json={"prompt": "User A only", "session_id": sid, "max_tool_iters": 1},
                     timeout=60)
    assert r.status_code == 200, r.text

    # User B sees no messages for that session
    h = session.get(f"{AUREM}/chat/history", headers=auth(token_b),
                    params={"session_id": sid}, timeout=15)
    assert h.status_code == 200
    assert h.json()["messages"] == []

    # User B cannot delete it (deleted=0)
    d = session.delete(f"{AUREM}/chat/sessions/{sid}", headers=auth(token_b), timeout=15)
    assert d.status_code == 200, d.text
    assert d.json()["deleted"] == 0

    # User A can still see it
    h2 = session.get(f"{AUREM}/chat/history", headers=auth(token_a),
                     params={"session_id": sid}, timeout=15)
    assert len(h2.json()["messages"]) >= 2


# ─── DELETE removes session ─────────────────────────────────────────────
def test_delete_session_removes_it(session, token_a):
    sid = f"sess-{uuid.uuid4()}"
    session.post(f"{AUREM}/chat/send", headers=auth(token_a),
                 json={"prompt": "to be deleted", "session_id": sid, "max_tool_iters": 1},
                 timeout=60)
    d = session.delete(f"{AUREM}/chat/sessions/{sid}", headers=auth(token_a), timeout=15)
    assert d.status_code == 200, d.text
    body = d.json()
    assert body["ok"] is True
    assert body["deleted"] == 1

    # history empty after delete
    h = session.get(f"{AUREM}/chat/history", headers=auth(token_a),
                    params={"session_id": sid}, timeout=15)
    assert h.json()["messages"] == []

    # not in sessions list
    s = session.get(f"{AUREM}/chat/sessions", headers=auth(token_a), timeout=15)
    ids = [x["session_id"] for x in s.json()["sessions"]]
    assert sid not in ids


# ─── /chat/stream SSE happy-path ────────────────────────────────────────
def test_chat_stream_sse(token_a):
    sid = f"sess-{uuid.uuid4()}"
    url = f"{AUREM}/chat/stream"
    with requests.post(
        url,
        headers={"Content-Type": "application/json", **auth(token_a)},
        json={"prompt": "Reply with: STREAM_OK", "session_id": sid, "max_tool_iters": 1},
        stream=True,
        timeout=90,
    ) as r:
        assert r.status_code == 200, r.text
        ctype = r.headers.get("content-type", "")
        assert "text/event-stream" in ctype, f"bad content-type {ctype}"

        meta_seen = False
        tokens_seen = 0
        done_payload = None
        full = ""
        buf = ""
        for chunk in r.iter_content(chunk_size=None, decode_unicode=True):
            if not chunk:
                continue
            buf += chunk
            while "\n\n" in buf:
                frame, buf = buf.split("\n\n", 1)
                line = next((ln for ln in frame.split("\n") if ln.startswith("data:")), None)
                if not line:
                    continue
                payload = json.loads(line[5:].strip())
                if payload.get("meta"):
                    meta_seen = True
                    assert payload["session_id"] == sid
                elif "token" in payload:
                    tokens_seen += 1
                    full += payload["token"]
                elif payload.get("done"):
                    done_payload = payload
                    break
            if done_payload:
                break

        assert meta_seen, "no meta frame received"
        assert tokens_seen >= 1, f"no token frames received (got {tokens_seen})"
        assert done_payload is not None, "no done frame"
        assert done_payload["session_id"] == sid
        assert len(full) > 0

    # After stream, session should be persisted in /history
    s = requests.Session()
    h = s.get(f"{AUREM}/chat/history",
              headers={"Content-Type": "application/json", **auth(token_a)},
              params={"session_id": sid}, timeout=15)
    assert h.status_code == 200
    msgs = h.json()["messages"]
    assert len(msgs) >= 2
    assert msgs[-1]["role"] == "assistant"
    assert msgs[-1]["content"] == full
