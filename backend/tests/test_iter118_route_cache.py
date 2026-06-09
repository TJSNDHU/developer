"""
tests/test_iter118_route_cache.py

Iter 118 — In-memory route cache middleware.

Covers:
  1. Public endpoints: 1st MISS, 2nd HIT, identical body.
  2. Admin endpoints: cache HIT for non-admin caller returns 401, not
     leaked admin data.
  3. Admin endpoints: admin caller sees HIT after MISS.
  4. Non-cached endpoints (e.g. /api/health) do NOT get X-Cache header.
  5. TTL expiry purges the entry.
  6. Cache key is normalised across query-string ordering.

Uses FastAPI TestClient so the app lifespan runs (DB connection set up
before the handler executes). Each test flushes the cache.
"""

import os
import time
import pytest

os.environ.setdefault("JWT_SECRET", os.environ.get("JWT_SECRET", "test-secret"))

from fastapi.testclient import TestClient  # noqa: E402
from main import app  # noqa: E402
from services import route_cache  # noqa: E402
from cto_services.auth import create_token  # noqa: E402


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _flush_cache():
    route_cache.clear()
    yield
    route_cache.clear()


def _admin_token() -> str:
    # Mint a token directly — middleware only checks `is_admin` on the
    # JWT payload, so we don't need a matching DB row.
    return create_token("test-admin-uid", "ci-admin@aurem.dev", is_admin=True)


def test_public_endpoint_miss_then_hit(client):
    r1 = client.get("/api/aurem-dev/usage/public/stats")
    r2 = client.get("/api/aurem-dev/usage/public/stats")
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.headers.get("x-cache") == "MISS"
    assert r2.headers.get("x-cache") == "HIT"
    assert r1.content == r2.content


def test_admin_endpoint_blocks_anon_on_warm_cache(client):
    """Security: warm the cache as admin, then verify an anon request
    gets 401, NOT the cached admin payload."""
    r_admin = client.get(
        "/api/aurem-dev/admin/council/stats",
        headers={"Authorization": f"Bearer {_admin_token()}"},
    )
    assert r_admin.status_code == 200
    assert r_admin.headers.get("x-cache") == "MISS"

    r_anon = client.get("/api/aurem-dev/admin/council/stats")
    assert r_anon.status_code == 401
    # The cached admin body MUST NOT have leaked into the anon response.
    assert b"mode_a" not in r_anon.content
    assert b"total" not in r_anon.content


def test_admin_endpoint_hit_for_admin(client):
    h = {"Authorization": f"Bearer {_admin_token()}"}
    r1 = client.get("/api/aurem-dev/admin/council/stats", headers=h)
    r2 = client.get("/api/aurem-dev/admin/council/stats", headers=h)
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.headers.get("x-cache") == "MISS"
    assert r2.headers.get("x-cache") == "HIT"
    assert r1.content == r2.content


def test_non_cached_endpoint_has_no_x_cache_header(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    headers_lower = {k.lower() for k in r.headers.keys()}
    assert "x-cache" not in headers_lower


def test_ttl_expiry_purges_entry():
    route_cache.put("k", ttl=1, status=200, body=b"x", content_type="application/json")
    assert route_cache.get("k") is not None
    # Force expiry by stomping the timestamp
    import services.route_cache as rc
    rc._CACHE["k"] = (time.time() - 1, 200, b"x", "application/json")
    assert route_cache.get("k") is None
    assert route_cache.size() == 0


def test_cache_key_sensitive_to_query_ordering():
    assert route_cache.make_key("/p", "a=1&b=2") == route_cache.make_key("/p", "b=2&a=1")
    assert route_cache.make_key("/p", "a=1&b=2") != route_cache.make_key("/p", "a=1&b=3")
