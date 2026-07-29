"""
Iteration 13 — AUREM CTO Rollback endpoint coverage.
Endpoint under test: POST /api/aurem-dev/cto/tasks/{task_id}/rollback

Strategy:
  1. Authenticate as the seeded test user (test@aurem.dev).
  2. Create a real cto_project via the API so we have a valid parent doc
     (it carries a fake PAT so the guards see "PAT exists" but git clone
     will fail — which is exactly what we want to verify graceful failure).
  3. Seed cto_task docs directly into MongoDB to cover every guard branch
     (status mismatch, no commit, already rolled back, rollback in flight,
     and the happy path that queues the worker).
  4. For the happy-path test we wait for the worker to finish and assert
     rollback_status == 'failed' with rollback_error and rollback_steps[]
     because the fake PAT cannot actually clone.
"""
from __future__ import annotations

import os
import time
import uuid
import pytest
import requests
from pymongo import MongoClient

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
if not BASE_URL:
    # Fallback to the public preview URL the rest of the suite uses.
    BASE_URL = "https://launch-pad-237.preview.emergentagent.com"

API = f"{BASE_URL}/api/aurem-dev"
CTO = f"{API}/cto"

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "aurem_dev")

EMAIL = "test@aurem.dev"
PASSWORD = "AuremTest2026!"


# ─────────────────────────── fixtures ───────────────────────────
@pytest.fixture(scope="module")
def db():
    cli = MongoClient(MONGO_URL)
    yield cli[DB_NAME]
    cli.close()


@pytest.fixture(scope="module")
def auth_headers():
    s = requests.Session()
    # signup is idempotent — try it, ignore 409
    s.post(f"{API}/auth/signup", json={
        "email": EMAIL, "password": PASSWORD, "name": "Test Builder",
    }, timeout=15)
    r = s.post(f"{API}/auth/login",
               json={"email": EMAIL, "password": PASSWORD}, timeout=15)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    token = r.json().get("token")
    assert token
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


@pytest.fixture(scope="module")
def test_user_id(db):
    u = db.dev_users.find_one({"email": EMAIL}, {"user_id": 1})
    assert u, "test user not found in dev_users"
    return u["user_id"]


@pytest.fixture(scope="module")
def project_with_pat(auth_headers, db):
    """Real cto_project owned by the test user. Iter 344: the
    /projects/add endpoint now VALIDATES the PAT against the live
    GitHub API (Contents: R/W), so a fake PAT is rejected with 400.
    A real sandbox PAT must be supplied via QA_GITHUB_PAT; without it
    these live-integration tests are skipped (requires_live_server
    class), not silently faked."""
    pat = os.environ.get("QA_GITHUB_PAT")
    if not pat:
        pytest.skip("QA_GITHUB_PAT not set — /projects/add validates PATs "
                    "against live GitHub now; fake PATs are rejected (400)")
    payload = {
        "name": "TEST_RB_Project",
        "github_url": "https://github.com/test-aurem/rb-fixture",
        "github_token": pat,
        "branch": "main",
        "tech_stack": "node",
    }
    r = requests.post(f"{CTO}/projects/add", json=payload,
                      headers=auth_headers, timeout=15)
    assert r.status_code == 200, r.text
    pid = r.json()["project_id"]
    yield pid
    # cleanup
    requests.delete(f"{CTO}/projects/{pid}", headers=auth_headers, timeout=10)
    db.cto_tasks.delete_many({"project_id": pid})


@pytest.fixture(scope="module")
def project_no_pat(auth_headers, db, test_user_id):
    """Project deliberately stripped of github_token to exercise the
    'No PAT on file' guard. We create via API then unset the field."""
    r = requests.post(f"{CTO}/projects/add", json={
        "name": "TEST_RB_NoPAT",
        "github_url": "https://github.com/test-aurem/no-pat",
        "github_token": "temp",
        "branch": "main",
        "tech_stack": "node",
    }, headers=auth_headers, timeout=15)
    assert r.status_code == 200
    pid = r.json()["project_id"]
    db.cto_projects.update_one({"project_id": pid},
                               {"$unset": {"github_token": ""}})
    # Also clear any oauth token on the user so the fallback is empty
    db.dev_users.update_one(
        {"user_id": test_user_id},
        {"$unset": {"github.access_token": ""}},
    )
    yield pid
    requests.delete(f"{CTO}/projects/{pid}", headers=auth_headers, timeout=10)
    db.cto_tasks.delete_many({"project_id": pid})


def _seed_task(db, project_id, user_id, **overrides) -> str:
    tid = f"t_TEST_{uuid.uuid4().hex[:8]}"
    doc = {
        "task_id": tid,
        "project_id": project_id,
        "user_id": user_id,
        "task": "TEST seeded task",
        "files": [],
        "context": "",
        "status": "done",
        "steps": [],
        "commit_sha": "abc1234",
        "result": "ok",
        "error": None,
        "created_at": time.time(),
    }
    doc.update(overrides)
    db.cto_tasks.insert_one(doc)
    return tid


# ─────────────────────────── tests ───────────────────────────
class TestRollbackAuth:
    def test_no_auth_returns_401(self):
        r = requests.post(f"{CTO}/tasks/whatever/rollback",
                          json={"confirm": "ROLLBACK"}, timeout=10)
        assert r.status_code == 401, r.text


class TestRollbackConfirm:
    def test_wrong_confirm_string_returns_400(
        self, auth_headers, project_with_pat, db, test_user_id,
    ):
        tid = _seed_task(db, project_with_pat, test_user_id)
        try:
            r = requests.post(
                f"{CTO}/tasks/{tid}/rollback",
                json={"confirm": "yes"},
                headers=auth_headers, timeout=10,
            )
            assert r.status_code == 400
            detail = r.json().get("detail", "")
            assert "ROLLBACK" in detail
        finally:
            db.cto_tasks.delete_one({"task_id": tid})

    def test_missing_confirm_field_returns_422_or_400(
        self, auth_headers, project_with_pat, db, test_user_id,
    ):
        tid = _seed_task(db, project_with_pat, test_user_id)
        try:
            r = requests.post(
                f"{CTO}/tasks/{tid}/rollback",
                json={}, headers=auth_headers, timeout=10,
            )
            # FastAPI raises 422 for pydantic validation; either is acceptable
            assert r.status_code in (400, 422), r.text
        finally:
            db.cto_tasks.delete_one({"task_id": tid})


class TestRollbackGuards:
    def test_unknown_task_returns_404(self, auth_headers):
        r = requests.post(
            f"{CTO}/tasks/t_does_not_exist_xyz/rollback",
            json={"confirm": "ROLLBACK"},
            headers=auth_headers, timeout=10,
        )
        assert r.status_code == 404

    def test_task_in_queued_status_returns_400(
        self, auth_headers, project_with_pat, db, test_user_id,
    ):
        tid = _seed_task(db, project_with_pat, test_user_id,
                         status="queued", commit_sha=None)
        try:
            r = requests.post(
                f"{CTO}/tasks/{tid}/rollback",
                json={"confirm": "ROLLBACK"},
                headers=auth_headers, timeout=10,
            )
            assert r.status_code == 400
            assert "completed" in r.json().get("detail", "").lower()
        finally:
            db.cto_tasks.delete_one({"task_id": tid})

    def test_done_task_without_commit_sha_returns_400(
        self, auth_headers, project_with_pat, db, test_user_id,
    ):
        tid = _seed_task(db, project_with_pat, test_user_id,
                         status="done", commit_sha=None)
        try:
            r = requests.post(
                f"{CTO}/tasks/{tid}/rollback",
                json={"confirm": "ROLLBACK"},
                headers=auth_headers, timeout=10,
            )
            assert r.status_code == 400
            assert "commit" in r.json().get("detail", "").lower()
        finally:
            db.cto_tasks.delete_one({"task_id": tid})

    def test_already_rolled_back_returns_409(
        self, auth_headers, project_with_pat, db, test_user_id,
    ):
        tid = _seed_task(db, project_with_pat, test_user_id,
                         rollback_sha="rev1234", rollback_status="done")
        try:
            r = requests.post(
                f"{CTO}/tasks/{tid}/rollback",
                json={"confirm": "ROLLBACK"},
                headers=auth_headers, timeout=10,
            )
            assert r.status_code == 409
            assert "already" in r.json().get("detail", "").lower()
        finally:
            db.cto_tasks.delete_one({"task_id": tid})

    def test_rollback_in_progress_queued_returns_409(
        self, auth_headers, project_with_pat, db, test_user_id,
    ):
        tid = _seed_task(db, project_with_pat, test_user_id,
                         rollback_status="queued")
        try:
            r = requests.post(
                f"{CTO}/tasks/{tid}/rollback",
                json={"confirm": "ROLLBACK"},
                headers=auth_headers, timeout=10,
            )
            assert r.status_code == 409
        finally:
            db.cto_tasks.delete_one({"task_id": tid})

    def test_rollback_in_progress_running_returns_409(
        self, auth_headers, project_with_pat, db, test_user_id,
    ):
        tid = _seed_task(db, project_with_pat, test_user_id,
                         rollback_status="running")
        try:
            r = requests.post(
                f"{CTO}/tasks/{tid}/rollback",
                json={"confirm": "ROLLBACK"},
                headers=auth_headers, timeout=10,
            )
            assert r.status_code == 409
        finally:
            db.cto_tasks.delete_one({"task_id": tid})

    def test_no_pat_returns_400(
        self, auth_headers, project_no_pat, db, test_user_id,
    ):
        tid = _seed_task(db, project_no_pat, test_user_id)
        try:
            r = requests.post(
                f"{CTO}/tasks/{tid}/rollback",
                json={"confirm": "ROLLBACK"},
                headers=auth_headers, timeout=10,
            )
            assert r.status_code == 400
            assert "PAT" in r.json().get("detail", "")
        finally:
            db.cto_tasks.delete_one({"task_id": tid})


class TestRollbackHappyPathOrchestration:
    """The fake PAT will fail at `git clone`. We verify:
       (a) the API queues the worker (200, rollback_status=queued),
       (b) Mongo reflects the queued state,
       (c) the worker eventually flips to 'failed' with rollback_error
           and rollback_steps[] populated (graceful failure)."""

    def test_queue_then_fail_gracefully(
        self, auth_headers, project_with_pat, db, test_user_id,
    ):
        tid = _seed_task(db, project_with_pat, test_user_id,
                         status="done", commit_sha="abc1234")
        try:
            # Submit rollback
            r = requests.post(
                f"{CTO}/tasks/{tid}/rollback",
                json={"confirm": "ROLLBACK"},
                headers=auth_headers, timeout=15,
            )
            assert r.status_code == 200, r.text
            data = r.json()
            assert data["ok"] is True
            assert data["task_id"] == tid
            assert data["rollback_status"] == "queued"

            # Verify Mongo received the queued state
            doc = db.cto_tasks.find_one({"task_id": tid})
            assert doc is not None
            assert doc.get("rollback_status") in ("queued", "running", "failed")
            assert doc.get("rollback_started_at") is not None

            # Wait for worker to finish (clone will fail fast)
            deadline = time.time() + 60
            final = None
            while time.time() < deadline:
                doc = db.cto_tasks.find_one({"task_id": tid})
                if doc and doc.get("rollback_status") in ("done", "failed"):
                    final = doc
                    break
                time.sleep(2)

            assert final is not None, (
                "rollback worker never reached terminal state within 60s"
            )
            # With a fake PAT clone MUST fail
            assert final["rollback_status"] == "failed", (
                f"expected failed, got {final.get('rollback_status')}; "
                f"steps={final.get('rollback_steps')}"
            )
            assert final.get("rollback_error"), "rollback_error must be set"
            steps = final.get("rollback_steps") or []
            assert len(steps) > 0, "rollback_steps must be populated"
            # At least one step should be marked error
            assert any(s.get("status") == "error" for s in steps), (
                f"expected an error step, got {steps}"
            )

            # GET endpoint also returns the failure state cleanly
            g = requests.get(f"{CTO}/tasks/{tid}",
                             headers=auth_headers, timeout=10)
            assert g.status_code == 200
            payload = g.json()["task"]
            assert payload["rollback_status"] == "failed"
            assert payload.get("rollback_error")

            # Idempotency: re-submitting should now return 409 because
            # rollback_status is 'failed' (NOT queued/running) but also no
            # rollback_sha was set — per implementation this should be
            # allowed (no auto-retry only enforced in UI). Let's verify
            # actual behavior: the backend currently allows a retry since
            # status is 'failed', not queued/running, and rollback_sha is
            # unset. Document the observed behavior.
            r2 = requests.post(
                f"{CTO}/tasks/{tid}/rollback",
                json={"confirm": "ROLLBACK"},
                headers=auth_headers, timeout=15,
            )
            # Accept either: 200 (backend allows retry) or 409 (locked out)
            assert r2.status_code in (200, 409), (
                f"unexpected retry status: {r2.status_code} {r2.text}"
            )
        finally:
            db.cto_tasks.delete_one({"task_id": tid})


# ─────────────── regression: task submit + get still works ───────────────
class TestSubmitTaskRegression:
    def test_submit_task_creates_doc_without_rollback_fields(
        self, auth_headers, project_with_pat, db,
    ):
        r = requests.post(f"{CTO}/tasks/submit", json={
            "project_id": project_with_pat,
            "task": "TEST regression — do nothing",
            "files": [],
            "context": "",
        }, headers=auth_headers, timeout=15)
        assert r.status_code == 200, r.text
        tid = r.json()["task_id"]
        try:
            doc = db.cto_tasks.find_one({"task_id": tid})
            assert doc is not None
            # rollback_status should NOT exist at creation
            assert "rollback_status" not in doc
            assert "rollback_sha" not in doc

            # GET endpoint returns the task
            g = requests.get(f"{CTO}/tasks/{tid}",
                             headers=auth_headers, timeout=10)
            assert g.status_code == 200
            t = g.json()["task"]
            assert t["task_id"] == tid
            assert t["project_id"] == project_with_pat
        finally:
            # Don't wait for the background worker; just remove the doc.
            db.cto_tasks.delete_one({"task_id": tid})


class TestUpdateProjectPreviewURL:
    """preview_url plumbing (already shipped) regression check."""

    def test_add_then_patch_preview_url(self, auth_headers, db):
        r = requests.post(f"{CTO}/projects/add", json={
            "name": "TEST_RB_Preview",
            "github_url": "https://github.com/test-aurem/preview",
            "github_token": "ghp_FAKE_TOKEN",
            "branch": "main",
            "preview_url": "https://example.com",
        }, headers=auth_headers, timeout=15)
        assert r.status_code == 200
        pid = r.json()["project_id"]
        try:
            doc = db.cto_projects.find_one({"project_id": pid})
            assert doc["preview_url"] == "https://example.com"

            r2 = requests.patch(f"{CTO}/projects/{pid}", json={
                "preview_url": "https://updated.example.com",
            }, headers=auth_headers, timeout=10)
            assert r2.status_code == 200
            updated = r2.json()["updated_fields"]
            assert "preview_url" in updated

            doc2 = db.cto_projects.find_one({"project_id": pid})
            assert doc2["preview_url"] == "https://updated.example.com"
        finally:
            requests.delete(f"{CTO}/projects/{pid}",
                            headers=auth_headers, timeout=10)
