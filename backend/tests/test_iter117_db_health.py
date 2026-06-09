"""Iter 117 — /admin/db-health endpoint."""
import asyncio
import pytest

from scripts import init_prod_collections as ipc


class _FakeColl:
    def __init__(self, idx_count=2):
        self._idx = [{"key": {"_id": 1}}] + [{"key": {f"f{i}": 1}} for i in range(idx_count - 1)]
    async def insert_one(self, _): return None
    async def delete_one(self, _):
        class R: deleted_count = 1
        return R()
    async def create_index(self, *_a, **_kw): return "ok"
    def list_indexes(self):
        cur = self
        class _L:
            async def to_list(_s, length=50): return cur._idx
        return _L()


class _FakeDB:
    def __init__(self):
        self._colls = {}
    async def list_collection_names(self):
        return list(self._colls)
    def __getitem__(self, n):
        if n not in self._colls:
            self._colls[n] = _FakeColl()
        return self._colls[n]


@pytest.mark.asyncio
async def test_bootstrap_persists_summary_for_db_health():
    db = _FakeDB()
    result = await ipc.init_prod_collections(db)
    last = ipc.get_last_bootstrap()
    assert last is not None
    assert last["ts"] == result["ts"]
    assert last["created"] == result["created"]
    assert last["indexed"] == result["indexed"]


@pytest.mark.asyncio
async def test_bootstrap_summary_includes_iso_timestamp():
    await ipc.init_prod_collections(_FakeDB())
    last = ipc.get_last_bootstrap()
    # ISO8601-ish — starts with 4-digit year + T separator
    assert last["ts"][:4].isdigit()
    assert "T" in last["ts"]


def test_get_last_bootstrap_returns_dict_or_none():
    # After a real run from earlier tests, returns dict; module reload
    # tests this contract.
    res = ipc.get_last_bootstrap()
    assert res is None or isinstance(res, dict)
