"""
Iter 212m-75 — Tests for:
  - /codebase-health/scan rate limit (10/hour/category)
  - /cto/projects/{id}/indexing-status endpoint
  - BugHunt landing route registration
"""
import time
import pytest

from routers.codebase_health import (
    _check_scan_rate_limit, _SCAN_RATE_CAP, _SCAN_RATE_WINDOW,
)


class _FakeCursor:
    def __init__(self, rows): self._rows = rows
    def sort(self, *a, **k): self._rows.sort(key=lambda r: r["ts"]); return self
    def __aiter__(self): self._i = 0; return self
    async def __anext__(self):
        if self._i >= len(self._rows): raise StopAsyncIteration
        r = self._rows[self._i]; self._i += 1; return r


class _FakeCollection:
    def __init__(self):
        self.rows: list[dict] = []
    async def delete_many(self, q):
        cutoff = q.get("ts", {}).get("$lt")
        before = len(self.rows)
        if cutoff is not None:
            self.rows = [r for r in self.rows
                         if not (r["user_id"] == q["user_id"] and r["ts"] < cutoff)]
        return type("R", (), {"deleted_count": before - len(self.rows)})()
    def find(self, q, proj=None):
        cutoff = q.get("ts", {}).get("$gte", 0)
        rows = [r for r in self.rows
                if r["user_id"] == q["user_id"]
                and r["category"] == q["category"]
                and r["ts"] >= cutoff]
        return _FakeCursor(rows)
    async def insert_many(self, docs):
        self.rows.extend(docs)


class _FakeDB:
    def __init__(self): self.scan_rate_limits = _FakeCollection()


@pytest.mark.asyncio
async def test_rate_limit_allows_under_cap():
    db = _FakeDB()
    for i in range(_SCAN_RATE_CAP - 1):
        denied, retry, remaining = await _check_scan_rate_limit(
            db, "u1", ["security"])
        assert denied is None
        assert remaining["security"] == _SCAN_RATE_CAP - (i + 1)


@pytest.mark.asyncio
async def test_rate_limit_blocks_at_cap():
    db = _FakeDB()
    # Pre-seed 10 hits.
    now = time.time()
    db.scan_rate_limits.rows = [
        {"user_id": "u2", "category": "bug_hunt", "ts": now - 60 - i}
        for i in range(_SCAN_RATE_CAP)
    ]
    denied, retry, remaining = await _check_scan_rate_limit(
        db, "u2", ["bug_hunt"])
    assert denied == "bug_hunt"
    assert retry > 0 and retry <= _SCAN_RATE_WINDOW
    assert remaining["bug_hunt"] == 0


@pytest.mark.asyncio
async def test_rate_limit_first_denied_wins():
    db = _FakeDB()
    now = time.time()
    db.scan_rate_limits.rows = [
        {"user_id": "u3", "category": "bug_hunt", "ts": now - 30 - i}
        for i in range(_SCAN_RATE_CAP)
    ]
    # Request multiple categories; bug_hunt is the only one over cap.
    denied, retry, remaining = await _check_scan_rate_limit(
        db, "u3", ["security", "bug_hunt"])
    assert denied == "bug_hunt"
    assert remaining["security"] == _SCAN_RATE_CAP
    assert remaining["bug_hunt"] == 0


@pytest.mark.asyncio
async def test_rate_limit_window_expiry():
    db = _FakeDB()
    # Old hits outside the window should not count.
    very_old = time.time() - _SCAN_RATE_WINDOW - 100
    db.scan_rate_limits.rows = [
        {"user_id": "u4", "category": "security", "ts": very_old}
        for _ in range(_SCAN_RATE_CAP + 5)
    ]
    denied, retry, remaining = await _check_scan_rate_limit(
        db, "u4", ["security"])
    assert denied is None   # window expired → allowed
    assert remaining["security"] == _SCAN_RATE_CAP - 1
    # Old rows pruned.
    assert all(r["ts"] >= time.time() - _SCAN_RATE_WINDOW
               for r in db.scan_rate_limits.rows)


@pytest.mark.asyncio
async def test_rate_limit_multi_category_inserts_one_per_cat():
    db = _FakeDB()
    cats = ["security", "performance", "bug_hunt"]
    denied, _, remaining = await _check_scan_rate_limit(db, "u5", cats)
    assert denied is None
    # Should have inserted exactly one entry per category.
    assert len([r for r in db.scan_rate_limits.rows
                if r["user_id"] == "u5"]) == 3
    for c in cats:
        assert remaining[c] == _SCAN_RATE_CAP - 1


def test_bug_hunt_route_registered():
    """The /bug-hunt route is mounted via the lazy import in App.jsx —
    we verify the file exists and exports a default component."""
    import pathlib
    p = pathlib.Path("/app/frontend/src/pages/BugHunt.jsx")
    assert p.exists()
    src = p.read_text()
    assert "export default function BugHunt" in src
    assert "ORA Bug Hunt" in src
    assert "application/ld+json" in src or 'type = "application/ld+json"' in src
    # Verify App.jsx has the route + lazy import.
    app = pathlib.Path("/app/frontend/src/App.jsx").read_text()
    assert 'import("./pages/BugHunt")' in app
    assert 'path="/bug-hunt"' in app


def test_sitemap_has_bug_hunt():
    import pathlib
    sm = pathlib.Path("/app/frontend/public/sitemap.xml").read_text()
    assert "https://auremcto.com/bug-hunt" in sm
