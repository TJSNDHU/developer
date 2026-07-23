"""
Iter 282 follow-up regression — _ensure_indexes must resolve
IndexOptionsConflict when adding TTL over a pre-existing plain
single-field index. This was the real bug surfaced on prod by
the diagnostics endpoint: 2 of 6 loop collections
(loop_verification_log, loop_run_log) had prior plain `created_at_1`
indexes that blocked TTL creation.
"""
from __future__ import annotations
import os
import time
import pytest
import pytest_asyncio
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv("/app/backend/.env")


@pytest_asyncio.fixture
async def db():
    c = AsyncIOMotorClient(os.environ["MONGO_URL"],
                           serverSelectionTimeoutMS=3000)
    yield c[os.environ["DB_NAME"]]
    c.close()


@pytest.mark.asyncio
async def test_regression_iter282_ttl_bootstrap_resolves_index_conflict(db):
    """
    Prod repro scenario:
      1. A collection exists with a plain single-field index on
         `created_at` (default name `created_at_1`, no expireAfterSeconds).
      2. init_prod_collections.py tries to add a TTL index on the
         same field.
      3. MongoDB raises IndexOptionsConflict.
      4. The fixed _ensure_indexes MUST drop the plain index and
         recreate as TTL — no manual intervention required.
    """
    from scripts.init_prod_collections import _ensure_indexes

    coll = f"regress282_bootstrap_{int(time.time()*1000)}"
    # Seed collection + pre-existing plain index that will conflict.
    await db[coll].insert_one({"created_at": time.time(), "probe": 1})
    await db[coll].create_index([("created_at", 1)])   # plain, no TTL

    # Verify precondition: index exists, no TTL yet.
    idxs = await db[coll].index_information()
    plain_name = "created_at_1"
    assert plain_name in idxs
    assert "expireAfterSeconds" not in idxs[plain_name]

    # Now run _ensure_indexes with the exact TTL spec init_prod_collections uses.
    ttl_seconds = 90 * 24 * 3600
    created = await _ensure_indexes(
        db, coll,
        [([("created_at", 1)], {"expireAfterSeconds": ttl_seconds})],
    )
    assert created == 1, "TTL index MUST be created after conflict resolution"

    # Post-condition: the index on created_at now carries expireAfterSeconds.
    idxs2 = await db[coll].index_information()
    ttl_matches = [
        (n, info) for n, info in idxs2.items()
        if info.get("expireAfterSeconds") == ttl_seconds
        and any(k == "created_at" for k, _ in info.get("key", []))
    ]
    assert ttl_matches, (
        f"TTL index missing after conflict resolution. Indexes: {idxs2!r}"
    )

    # cleanup
    await db[coll].drop()
