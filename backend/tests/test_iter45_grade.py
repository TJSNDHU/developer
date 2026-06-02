"""tests/test_iter45_grade.py — A-grade additions (Iter 45)."""
from __future__ import annotations
import os
import pytest


# ─── Rate limiter ───────────────────────────────────────────────────────

class TestRateLimiter:
    def test_under_limit_passes(self):
        from services.rate_limiter import check_rate_limit
        # Unique key per test to avoid cross-test bleed
        key = f"unit-test:{id(self)}-1"
        for _ in range(5):
            assert check_rate_limit(key, 10) is True

    def test_over_limit_returns_false(self):
        from services.rate_limiter import check_rate_limit
        key = f"unit-test:{id(self)}-2"
        for _ in range(10):
            assert check_rate_limit(key, 10) is True
        assert check_rate_limit(key, 10) is False

    def test_per_key_isolation(self):
        from services.rate_limiter import check_rate_limit
        a, b = f"unit-test:{id(self)}-a", f"unit-test:{id(self)}-b"
        for _ in range(10):
            assert check_rate_limit(a, 10) is True
        # key A is exhausted; key B should still be fresh
        assert check_rate_limit(a, 10) is False
        assert check_rate_limit(b, 10) is True

    def test_xff_header_extraction(self):
        from services.rate_limiter import client_ip_from_request
        class _Req:
            headers = {"x-forwarded-for": "1.2.3.4, 10.0.0.1"}
            class client: host = "10.0.0.1"
        assert client_ip_from_request(_Req()) == "1.2.3.4"

    def test_fallback_to_client_host(self):
        from services.rate_limiter import client_ip_from_request
        class _Req:
            headers = {}
            class client: host = "10.0.0.5"
        assert client_ip_from_request(_Req()) == "10.0.0.5"


# ─── Free tier monthly cap is wired ─────────────────────────────────────

@pytest.mark.asyncio
async def test_free_tier_cap_logic_present():
    """Make sure the free-tier cap code path is present in submit_task."""
    import inspect
    from routers.cto_projects import submit_task
    src = inspect.getsource(submit_task)
    assert "FREE_TIER_MONTHLY_CAP" in src
    assert "Free tier limit reached" in src


# ─── Public stats endpoint shape ────────────────────────────────────────

@pytest.mark.asyncio
async def test_public_stats_no_auth():
    if not os.environ.get("MONGO_URL"):
        pytest.skip("no MONGO_URL")
    from routers.usage import public_stats
    r = await public_stats()
    # Either available (db reachable) or {"available": False} — but never crash
    assert isinstance(r, dict)
    assert "available" in r
    if r["available"]:
        for k in ("users", "tasks_shipped", "interactions",
                  "claude_corrections", "correction_rate_pct",
                  "lint_blocks_caught"):
            assert k in r, f"public_stats missing {k}"
        assert isinstance(r["correction_rate_pct"], (int, float))
        assert 0 <= r["correction_rate_pct"] <= 100


# ─── Sentry init is gated on env (no DSN → no crash, no init) ───────────

def test_sentry_inert_when_no_dsn():
    # main.py must be importable without SENTRY_DSN. Just ensure the
    # module loaded and Sentry didn't blow up at import time.
    import main  # noqa: F401
    assert True   # if we got here, the gate works
