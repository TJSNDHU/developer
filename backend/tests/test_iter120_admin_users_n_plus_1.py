"""
tests/test_iter120_admin_users_n_plus_1.py

Iter 120 — `/admin/users` was doing 3 `count_documents` per user
(3N round-trips). Replaced with 3 grouped aggregations + dict
lookup. Test asserts the counts are still correct end-to-end.

Seeding uses the sync `pymongo` client (avoids motor cross-loop
issues with the TestClient fixture).
"""
import os
import time
import uuid
import pytest
from pymongo import MongoClient

os.environ.setdefault("JWT_SECRET", os.environ.get("JWT_SECRET", "test-secret"))

from fastapi.testclient import TestClient  # noqa: E402
from main import app  # noqa: E402
from cto_services.auth import create_token  # noqa: E402


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def sync_db():
    url = os.environ["MONGO_URL"]
    name = os.environ.get("DB_NAME", "aurem_dev")
    return MongoClient(url)[name]


def _admin_token() -> str:
    return create_token("admin-uid-iter120", "admin-iter120@aurem.test", is_admin=True)


def test_admin_users_returns_correct_counts(client, sync_db):
    """Three seeded users each have 2 projects, 4 tasks, 1 session.
    The aggregation rewrite must preserve those exact counts."""
    seeded = []
    try:
        for i in range(3):
            uid = f"n1-fix-user-{uuid.uuid4().hex[:8]}"
            seeded.append(uid)
            sync_db.dev_users.insert_one({
                "user_id": uid, "email": f"{uid}@aurem.test",
                "name": f"N1 Fix User {i}", "tier": "free", "is_admin": False,
                "created_at": time.time() - (3 - i),
            })
            for _ in range(2):
                sync_db.cto_projects.insert_one({
                    "project_id": f"p_{uuid.uuid4().hex[:8]}",
                    "user_id": uid, "name": "p",
                })
            for _ in range(4):
                sync_db.cto_tasks.insert_one({
                    "task_id": f"t_{uuid.uuid4().hex[:8]}",
                    "user_id": uid, "status": "done",
                })
            sync_db.chat_sessions.insert_one({
                "session_id": f"s_{uuid.uuid4().hex[:8]}",
                "user_id": uid, "updated_at": time.time(),
            })

        r = client.get(
            "/api/aurem-dev/admin/users",
            headers={"Authorization": f"Bearer {_admin_token()}"},
            params={"search": "n1-fix-user"},
        )
        assert r.status_code == 200, r.text
        users = {u["user_id"]: u for u in r.json()["users"]}
        for uid in seeded:
            assert uid in users, f"seeded user {uid} not returned"
            assert users[uid]["project_count"] == 2, users[uid]
            assert users[uid]["task_count"]    == 4, users[uid]
            assert users[uid]["session_count"] == 1, users[uid]
    finally:
        for uid in seeded:
            sync_db.dev_users.delete_one({"user_id": uid})
            sync_db.cto_projects.delete_many({"user_id": uid})
            sync_db.cto_tasks.delete_many({"user_id": uid})
            sync_db.chat_sessions.delete_many({"user_id": uid})


def test_admin_users_handles_empty_result(client):
    """No matching users → no aggregation queries, returns empty list."""
    r = client.get(
        "/api/aurem-dev/admin/users",
        headers={"Authorization": f"Bearer {_admin_token()}"},
        params={"search": "no-such-user-zzzzz"},
    )
    assert r.status_code == 200
    assert r.json()["users"] == []


def test_healthz_no_db_dependency(client):
    """K8s probe path — must answer 200 even if DB is wonky."""
    r = client.get("/api/healthz")
    assert r.status_code == 200
    assert r.json() == {"ok": True}
