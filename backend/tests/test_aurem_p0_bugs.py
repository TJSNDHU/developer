"""
Backend tests for AUREM Dev P0 bug fixes (iteration 3).

Covers:
  * BUG 1: POST /cto/projects/add stores github_token; /list excludes it
  * BUG 2: PATCH /cto/projects/{id} updates branch/tech_stack/pat, filters empties
  * BUG 4: POST /chat/feedback persists vote into turns[idx].feedback
  * BUG 5: chat persistence works (no $setOnInsert/$set conflict); session
           filtering by project_id (home / specific / null)
"""
import os
import time
import uuid

import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL")
if not BASE_URL:
    with open("/app/frontend/.env") as fh:
        for line in fh:
            if line.startswith("REACT_APP_BACKEND_URL"):
                BASE_URL = line.split("=", 1)[1].strip()
                break
assert BASE_URL
BASE_URL = BASE_URL.rstrip("/")
AUREM = f"{BASE_URL}/api/aurem-dev"

SEEDED_EMAIL = "test@aurem.dev"
SEEDED_PASS = "testpass123"


@pytest.fixture(scope="session")
def session():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


@pytest.fixture(scope="session")
def token(session):
    r = session.post(f"{AUREM}/auth/login",
                     json={"email": SEEDED_EMAIL, "password": SEEDED_PASS}, timeout=15)
    if r.status_code == 200:
        return r.json()["token"]
    r2 = session.post(f"{AUREM}/auth/signup",
                      json={"email": SEEDED_EMAIL, "password": SEEDED_PASS, "name": "Seeded"},
                      timeout=15)
    assert r2.status_code == 200, r2.text
    return r2.json()["token"]


def H(t):
    return {"Authorization": f"Bearer {t}"}


# ─────────────────────────────────────────────────────────────
# BUG 1: PAT stored on add, hidden on list
# ─────────────────────────────────────────────────────────────
@pytest.fixture(scope="session")
def created_project(session, token):
    payload = {
        "name": f"TEST_Proj_{int(time.time())}",
        "github_url": "https://github.com/owner/test-repo",
        "github_token": "github_pat_TEST_VALUE_BUG1",
        "branch": "main",
        "tech_stack": "react-fastapi",
    }
    r = session.post(f"{AUREM}/cto/projects/add",
                     headers=H(token), json=payload, timeout=15)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert "project_id" in body
    assert body["owner"] == "owner"
    assert body["repo"] == "test-repo"
    return {"id": body["project_id"], "payload": payload}


def test_bug1_add_project_returns_ok(created_project):
    assert created_project["id"].startswith("p_")


def test_bug1_list_excludes_github_token(session, token, created_project):
    r = session.get(f"{AUREM}/cto/projects/list", headers=H(token), timeout=15)
    assert r.status_code == 200, r.text
    projs = r.json()["projects"]
    me = next((p for p in projs if p["project_id"] == created_project["id"]), None)
    assert me is not None, "created project not in list"
    # Critical: github_token must NOT leak through list endpoint
    assert "github_token" not in me, f"PAT leaked in list: {me}"
    # Other fields must be intact
    assert me["name"] == created_project["payload"]["name"]
    assert me["branch"] == "main"
    assert me["tech_stack"] == "react-fastapi"
    assert me["github_url"] == created_project["payload"]["github_url"]


# ─────────────────────────────────────────────────────────────
# BUG 2: PATCH updates branch/tech_stack/pat & filters empties
# ─────────────────────────────────────────────────────────────
def test_bug2_patch_updates_branch_and_tech(session, token, created_project):
    pid = created_project["id"]
    r = session.patch(f"{AUREM}/cto/projects/{pid}",
                      headers=H(token),
                      json={"branch": "develop", "tech_stack": "vite-fastapi",
                            "github_token": "github_pat_UPDATED_BUG2"},
                      timeout=15)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert set(body["updated_fields"]) == {"branch", "tech_stack", "github_token"}

    # Verify via list that update is persisted
    lr = session.get(f"{AUREM}/cto/projects/list", headers=H(token), timeout=15)
    me = next(p for p in lr.json()["projects"] if p["project_id"] == pid)
    assert me["branch"] == "develop"
    assert me["tech_stack"] == "vite-fastapi"
    assert "github_token" not in me  # still hidden


def test_bug2_patch_filters_empty_fields(session, token, created_project):
    pid = created_project["id"]
    # Send empty strings & None — none should be applied
    r = session.patch(f"{AUREM}/cto/projects/{pid}",
                      headers=H(token),
                      json={"branch": "", "tech_stack": None, "github_token": ""},
                      timeout=15)
    # Nothing to update → 400 expected
    assert r.status_code == 400, r.text


def test_bug2_patch_partial_only_one_field(session, token, created_project):
    pid = created_project["id"]
    r = session.patch(f"{AUREM}/cto/projects/{pid}",
                      headers=H(token),
                      json={"branch": "release"},
                      timeout=15)
    assert r.status_code == 200, r.text
    assert r.json()["updated_fields"] == ["branch"]
    # tech_stack should remain from previous patch
    lr = session.get(f"{AUREM}/cto/projects/list", headers=H(token), timeout=15)
    me = next(p for p in lr.json()["projects"] if p["project_id"] == pid)
    assert me["branch"] == "release"
    assert me["tech_stack"] == "vite-fastapi"


def test_bug2_patch_404_for_unknown_project(session, token):
    r = session.patch(f"{AUREM}/cto/projects/p_doesnotexist123",
                      headers=H(token),
                      json={"branch": "main"},
                      timeout=15)
    assert r.status_code == 404


# ─────────────────────────────────────────────────────────────
# BUG 5: Chat persistence — no MongoDB conflict + project_id filter
# ─────────────────────────────────────────────────────────────
def test_bug5_chat_send_persists_with_project_id(session, token):
    pid = "p_test_bug5"
    sid = f"sess-{uuid.uuid4()}"
    r = session.post(f"{AUREM}/chat/send", headers=H(token),
                     json={"prompt": "Reply: PERSIST_OK", "session_id": sid,
                           "project_id": pid, "max_tool_iters": 1},
                     timeout=60)
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True

    # history should have 2 turns (proves _persist_turn worked; bug 5 fixed)
    h = session.get(f"{AUREM}/chat/history", headers=H(token),
                    params={"session_id": sid}, timeout=15)
    assert h.status_code == 200
    msgs = h.json()["messages"]
    assert len(msgs) >= 2, f"persistence failed — bug 5 NOT fixed: {msgs}"
    assert msgs[-2]["role"] == "user"
    assert msgs[-1]["role"] == "assistant"
    return sid, pid


def test_bug5_sessions_filtered_by_project_id(session, token):
    # Create a session under a specific project
    pid = f"p_filt_{int(time.time())}"
    sid_proj = f"sess-{uuid.uuid4()}"
    session.post(f"{AUREM}/chat/send", headers=H(token),
                 json={"prompt": "proj-scoped", "session_id": sid_proj,
                       "project_id": pid, "max_tool_iters": 1},
                 timeout=60)

    # Create a home session (no project_id)
    sid_home = f"sess-{uuid.uuid4()}"
    session.post(f"{AUREM}/chat/send", headers=H(token),
                 json={"prompt": "home-scoped", "session_id": sid_home,
                       "max_tool_iters": 1},
                 timeout=60)

    # Filter by specific project
    r = session.get(f"{AUREM}/chat/sessions", headers=H(token),
                    params={"project_id": pid}, timeout=15)
    assert r.status_code == 200
    ids = [s["session_id"] for s in r.json()["sessions"]]
    assert sid_proj in ids
    assert sid_home not in ids

    # Filter by home (null/missing project_id)
    r2 = session.get(f"{AUREM}/chat/sessions", headers=H(token),
                     params={"project_id": "home"}, timeout=15)
    assert r2.status_code == 200
    ids2 = [s["session_id"] for s in r2.json()["sessions"]]
    assert sid_home in ids2
    assert sid_proj not in ids2

    # No filter → both visible
    r3 = session.get(f"{AUREM}/chat/sessions", headers=H(token), timeout=15)
    ids3 = [s["session_id"] for s in r3.json()["sessions"]]
    assert sid_proj in ids3
    assert sid_home in ids3


# ─────────────────────────────────────────────────────────────
# BUG 4: /chat/feedback persists vote
# ─────────────────────────────────────────────────────────────
def test_bug4_chat_feedback_up(session, token):
    sid = f"sess-{uuid.uuid4()}"
    r = session.post(f"{AUREM}/chat/send", headers=H(token),
                     json={"prompt": "feedback test", "session_id": sid,
                           "max_tool_iters": 1}, timeout=60)
    assert r.status_code == 200

    # assistant is at index 1 of turns array (user=0, assistant=1)
    fb = session.post(f"{AUREM}/chat/feedback", headers=H(token),
                      json={"session_id": sid, "turn_index": 1, "vote": "up"},
                      timeout=15)
    assert fb.status_code == 200, fb.text
    assert fb.json()["ok"] is True

    h = session.get(f"{AUREM}/chat/history", headers=H(token),
                    params={"session_id": sid}, timeout=15)
    msgs = h.json()["messages"]
    # Find assistant turn
    asst = msgs[1]
    assert asst["role"] == "assistant"
    assert "feedback" in asst, f"feedback not persisted: {asst}"
    assert asst["feedback"]["vote"] == "up"


def test_bug4_chat_feedback_invalid_vote_rejected(session, token):
    sid = f"sess-{uuid.uuid4()}"
    session.post(f"{AUREM}/chat/send", headers=H(token),
                 json={"prompt": "x", "session_id": sid, "max_tool_iters": 1},
                 timeout=60)
    r = session.post(f"{AUREM}/chat/feedback", headers=H(token),
                     json={"session_id": sid, "turn_index": 1, "vote": "love"},
                     timeout=15)
    assert r.status_code == 400


def test_bug4_chat_feedback_requires_auth(session):
    r = session.post(f"{AUREM}/chat/feedback",
                     json={"session_id": "x", "turn_index": 1, "vote": "up"},
                     timeout=15)
    assert r.status_code == 401


# ─────────────────────────────────────────────────────────────
# Cleanup: delete the created project
# ─────────────────────────────────────────────────────────────
def test_zzz_cleanup_delete_project(session, token, created_project):
    r = session.delete(f"{AUREM}/cto/projects/{created_project['id']}",
                       headers=H(token), timeout=15)
    assert r.status_code == 200
    assert r.json()["deleted"] == 1
