"""
tests/test_iter122_memory_diag.py

Iter 122 — /api/_diag/memory admin diagnostic endpoint.

Validates:
  - 401 without auth
  - 403 for non-admin
  - 200 + shape for admin
  - Returns rss_mb, uptime_s, route_cache_size, top[]
"""
import os
import pytest

os.environ.setdefault("JWT_SECRET", os.environ.get("JWT_SECRET", "test-secret"))

from fastapi.testclient import TestClient  # noqa: E402
from main import app  # noqa: E402
from cto_services.auth import create_token  # noqa: E402


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_memory_diag_requires_auth(client):
    r = client.get("/api/_diag/memory")
    assert r.status_code == 401


def test_memory_diag_rejects_non_admin(client):
    tok = create_token("regular-uid", "user@aurem.test", is_admin=False)
    r = client.get("/api/_diag/memory", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 403


def test_memory_diag_admin_payload_shape(client):
    tok = create_token("admin-iter122", "admin@aurem.test", is_admin=True)
    r = client.get("/api/_diag/memory", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200, r.text
    body = r.json()
    # Shape lock
    for key in ("rss_mb", "uptime_s", "tracemalloc_active", "route_cache_size", "top"):
        assert key in body, f"missing {key}"
    # Sanity bounds
    assert body["uptime_s"] >= 0
    assert isinstance(body["top"], list)
    if body["top"]:
        for row in body["top"][:3]:
            assert "file" in row
            assert "size_kb" in row
            assert "count" in row
            assert row["size_kb"] >= 0
            assert row["count"] >= 0
