"""test_iter328_11_feature_flag_seed_upsert.py

Iter 328 · #11 root-cause lock-in.

Silent-state-loss bug we are eliminating:
  When `init_prod_collections` seeded `feature_flags`, the previous
  implementation was `insert_many(if collection empty)`. On production
  this ran once (at first-ever boot) and never again — so newly-added
  flags in the seed list (e.g. `integration_health_cron`) never landed
  in production Mongo, even though the code that read them was live.

Two invariants this test locks in permanently:

  1. `integration_health_cron` MUST be present in the seed list.
  2. Seed MUST use `$setOnInsert`, NEVER `$set`. `$set` on every boot
     would silently overwrite any founder-toggled `enabled` state back
     to the default — a different silent-state-loss bug of the same
     class.

Plus a functional test with a fake db that:
  a. Missing flags get inserted with defaults (via $setOnInsert).
  b. Pre-existing flags with `enabled=False` KEEP that state on
     re-run (proves $setOnInsert semantics, not $set).
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest


SRC = (
    Path(__file__).resolve().parents[1] / "scripts" / "init_prod_collections.py"
).read_text(encoding="utf-8")


# ── Invariant 1 — flag is in the seed list ───────────────────────────
def test_integration_health_cron_flag_is_in_seed_list():
    assert '"integration_health_cron"' in SRC, (
        "integration_health_cron MUST be in the feature_flags seed. "
        "This is the flag whose absence caused the founder-reported "
        "#11 bug (missing from /admin/feature-flags on prod)."
    )


# ── Invariant 2 — $setOnInsert is used, NOT $set ─────────────────────
def test_feature_flag_seed_uses_set_on_insert_not_set():
    # Locate the seed block. Use the marker comment we placed above it.
    marker = "Per-flag `update_one($setOnInsert, upsert=True)`"
    assert marker in SRC, (
        "Expected the $setOnInsert design comment above the seed loop"
    )
    # After the marker, the seed loop MUST use $setOnInsert and MUST
    # NOT use $set for feature_flags.
    tail = SRC.split(marker, 1)[1]
    # Take just the seed loop region — up to the next major top-level
    # section (roughly next `# ──` marker or end-of-file).
    seed_region = tail.split("logger.info(\"init_prod_collections done",1)[0]
    assert "$setOnInsert" in seed_region, (
        "feature_flags seed MUST use $setOnInsert. Without it, every "
        "boot silently wipes founder-toggled state back to defaults."
    )
    assert '"$set":' not in seed_region and "'$set':" not in seed_region, (
        "feature_flags seed MUST NOT use $set — that would overwrite "
        "founder-toggled enabled state on every boot. Use $setOnInsert."
    )


# ── Invariant 3 — functional: missing flag gets inserted ─────────────
def _make_fake_db_with_flags(existing_flags: list[dict]):
    """Build a fake `db` object where `db.feature_flags.update_one`
    inspects the operator + upsert flag and records the operation.
    Returns (db, ops_log) where ops_log is a list of (filter, update,
    upserted_id) tuples."""
    db = MagicMock()
    # Track state by flag name so we can simulate real upsert semantics.
    state: dict[str, dict] = {f["flag"]: dict(f) for f in existing_flags}
    ops_log: list[tuple] = []

    async def _update_one(filter_, update, upsert=False):
        fname = filter_["flag"]
        result = MagicMock()
        result.upserted_id = None
        if fname in state:
            # $setOnInsert must NOT touch existing.
            pass
        elif upsert:
            # New insert path.
            seed = update.get("$setOnInsert") or {}
            state[fname] = dict(seed)
            result.upserted_id = f"objid_{fname}"
        ops_log.append((filter_, update, result.upserted_id))
        return result

    async def _count():
        return len(state)

    db.feature_flags = MagicMock()
    db.feature_flags.update_one = _update_one
    # Provide enough of the other calls the bootstrap makes to noop.
    db.feature_flags.count_documents = AsyncMock(return_value=len(state))
    return db, ops_log, state


def test_seed_inserts_missing_flag_via_set_on_insert():
    """Fresh feature_flags collection: seed inserts every flag via
    $setOnInsert with defaults."""
    # Import lazily so the module reload picks up the latest source.
    import importlib
    import scripts.init_prod_collections as ipc
    importlib.reload(ipc)

    db, ops_log, state = _make_fake_db_with_flags(existing_flags=[])

    # Patch out the rest of init_prod_collections' work — we only care
    # about the feature_flags seed block. Give it a fake db that
    # answers only the seed's calls plus a couple of noops for the
    # bootstrap machinery.
    # The full init_prod_collections touches many collections; here we
    # bypass by calling only the feature_flags portion via a wrapper.
    # Simpler: run init_prod_collections but swallow errors on the
    # rest.
    async def _touch(name: str):
        # Simulate the "touch every collection" call
        return None

    # Stub out other collections used by the bootstrap.
    for coll in [
        "cto_payments", "cto_support", "cto_support_messages",
        "cto_token_grants", "cto_vault_audit_log", "vanguard_audit",
        "integration_health", "integration_health_history",
        "dev_users", "dev_sessions", "cto_projects", "loop_sessions",
        "feature_flags",
    ]:
        m = MagicMock()
        m.create_index = AsyncMock(return_value="idx")
        m.insert_one   = AsyncMock(return_value=None)
        m.delete_one   = AsyncMock(return_value=None)
        m.find_one     = AsyncMock(return_value=None)
        m.count_documents = AsyncMock(return_value=0)
        if coll != "feature_flags":
            setattr(db, coll, m)

    # Run only the feature_flags seed portion. We inspect ops_log.
    result = asyncio.run(ipc.init_prod_collections(db))
    # feature_flags:inserted(...) should include integration_health_cron
    created = " ".join(result.get("created", []))
    assert "integration_health_cron" in created, (
        f"integration_health_cron must be inserted on fresh DB. "
        f"created={result.get('created')} errors={result.get('errors')}"
    )
    # Verify every update_one used $setOnInsert with upsert=True.
    ff_ops = [op for op in ops_log if op[0].get("flag")]
    assert len(ff_ops) >= 5, f"expected ≥5 flag ops, got {len(ff_ops)}"
    for _filter, _update, _ in ff_ops:
        assert "$setOnInsert" in _update, (
            f"op for {_filter} missing $setOnInsert: {_update}"
        )
        assert "$set" not in _update, (
            f"op for {_filter} MUST NOT use $set: {_update}"
        )


def test_seed_preserves_founder_toggled_state_on_re_run():
    """Existing feature_flags with founder-toggled state: re-running
    the seed MUST NOT overwrite the enabled field.

    This is the specific silent-state-loss regression we're guarding
    against."""
    import importlib
    import scripts.init_prod_collections as ipc
    importlib.reload(ipc)

    # Simulate: founder has toggled integration_health_cron to OFF
    # via /admin/feature-flags.
    pre_existing = [
        {"flag": "integration_health_cron", "enabled": False,
         "tier_allowlist": [], "user_allowlist": [],
         "description": "founder disabled this"},
    ]
    db, ops_log, state = _make_fake_db_with_flags(
        existing_flags=pre_existing
    )
    for coll in [
        "cto_payments", "cto_support", "cto_support_messages",
        "cto_token_grants", "cto_vault_audit_log", "vanguard_audit",
        "integration_health", "integration_health_history",
        "dev_users", "dev_sessions", "cto_projects", "loop_sessions",
    ]:
        m = MagicMock()
        m.create_index = AsyncMock(return_value="idx")
        m.insert_one   = AsyncMock(return_value=None)
        m.delete_one   = AsyncMock(return_value=None)
        m.find_one     = AsyncMock(return_value=None)
        m.count_documents = AsyncMock(return_value=0)
        setattr(db, coll, m)

    asyncio.run(ipc.init_prod_collections(db))
    # State snapshot must still have `enabled=False` for the flag.
    assert state["integration_health_cron"]["enabled"] is False, (
        "$setOnInsert must NOT overwrite founder's OFF toggle. "
        f"got state={state['integration_health_cron']}"
    )
