"""Iter 116 — bootstrap all required collections on startup.

Tests cover:
  - All 10 collections in the spec are materialised
  - Indexes are created with the documented shape
  - Idempotency: running twice doesn't duplicate or error
  - Safe with db=None (must NOT raise)
  - Safe when a single collection's create_index fails (continues with rest)
"""
import asyncio
import pytest

from scripts.init_prod_collections import (
    init_prod_collections,
    _BOOTSTRAP_SPEC,
)


# ── Stub Motor DB ───────────────────────────────────────────────
class _FakeColl:
    def __init__(self):
        self.docs = []
        self.indexes = []
    async def insert_one(self, doc):
        self.docs.append(doc); return None
    async def delete_one(self, q):
        before = len(self.docs)
        self.docs = [d for d in self.docs if not all(d.get(k) == v for k, v in q.items())]
        return type("R", (), {"deleted_count": before - len(self.docs)})()
    async def create_index(self, keys, **opts):
        self.indexes.append((tuple(tuple(k) for k in keys), tuple(sorted(opts.items()))))
        return "ok"


class _FakeDB:
    def __init__(self):
        self.collections: dict[str, _FakeColl] = {}
    async def list_collection_names(self):
        return list(self.collections)
    def __getitem__(self, name):
        if name not in self.collections:
            self.collections[name] = _FakeColl()
        return self.collections[name]
    def __getattr__(self, name):
        return self[name]


@pytest.mark.asyncio
async def test_creates_all_ten_collections():
    db = _FakeDB()
    out = await init_prod_collections(db)
    expected = {name for name, _ in _BOOTSTRAP_SPEC}
    actual = set(out["created"])
    assert expected == actual, f"missing: {expected - actual}"


@pytest.mark.asyncio
async def test_each_collection_has_indexes_created():
    db = _FakeDB()
    await init_prod_collections(db)
    for name, specs in _BOOTSTRAP_SPEC:
        n_made = len(db.collections[name].indexes)
        n_want = len(specs)
        assert n_made == n_want, f"{name}: made {n_made}, expected {n_want}"


@pytest.mark.asyncio
async def test_idempotent_second_run_does_NOT_recreate():
    db = _FakeDB()
    first  = await init_prod_collections(db)
    second = await init_prod_collections(db)
    # Second run sees them already existing → "created" list empty
    assert first["created"] != []
    assert second["created"] == []
    # But indexes still get re-ensured (create_index is idempotent in Mongo)
    assert second["indexed"] != []


@pytest.mark.asyncio
async def test_safe_with_none_db():
    out = await init_prod_collections(None)
    assert "db is None" in out["errors"]


@pytest.mark.asyncio
async def test_continues_when_one_collection_indexer_fails():
    db = _FakeDB()
    # Inject a failure on a specific collection's create_index
    target = "vanguard_audit"
    orig_create = db[target].create_index
    calls = {"n": 0}
    async def boom(*a, **kw):
        calls["n"] += 1
        raise RuntimeError("simulated index failure")
    db[target].create_index = boom
    out = await init_prod_collections(db)
    # Other collections must still have indexes
    assert any(name.startswith("cto_payments:") for name in out["indexed"]) \
        or "cto_payments:3" in out["indexed"]
    # Caller can see the failure was tolerated (no raise above)
    assert calls["n"] >= 1


def test_spec_includes_all_user_requested_collections():
    """User explicitly listed these collections — lock them in.

    Iter 212m-172 — `project_plans` removed together with Flow-B
    /projects/plan endpoint.
    """
    required = {
        "cto_payments", "cto_support", "cto_support_messages",
        "cto_token_grants", "cto_vault_audit_log", "referrals",
        "vanguard_audit", "cto_automations",
        "aurem_cto_unlock_requests",
    }
    have = {name for name, _ in _BOOTSTRAP_SPEC}
    assert required.issubset(have), f"missing: {required - have}"
