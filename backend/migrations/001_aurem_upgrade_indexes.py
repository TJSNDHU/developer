"""
migrations/001_aurem_upgrade_indexes.py
========================================
Run ONCE after deploying new services.
Creates all indexes needed for the 5-feature upgrade.

How to run:
    python -m migrations.001_aurem_upgrade_indexes

Or add to your startup script (idempotent — safe to run multiple times).
"""

import asyncio
import os
from motor.motor_asyncio import AsyncIOMotorClient


MONGO_URI = os.getenv("MONGO_URL") or os.getenv("MONGODB_URI", "mongodb://localhost:27017")
DB_NAME   = os.getenv("DB_NAME")   or os.getenv("MONGODB_DB", "aurem_dev")


async def run_migrations():
    client = AsyncIOMotorClient(MONGO_URI)
    db     = client[DB_NAME]

    print("Running AUREM upgrade migrations...")

    # ── project_brains ────────────────────────────────────────────
    await db["project_brains"].create_index("project_id", unique=True)
    await db["project_brains"].create_index("repo_full_name")
    await db["project_brains"].create_index("updated_at")
    print("✓ project_brains indexes")

    # ── ora_council_logs ──────────────────────────────────────────
    await db["ora_council_logs"].create_index([("timestamp", -1)])
    await db["ora_council_logs"].create_index("mode")
    await db["ora_council_logs"].create_index("exported_for_training")
    await db["ora_council_logs"].create_index("project_id")
    await db["ora_council_logs"].create_index("user_id")
    print("✓ ora_council_logs indexes")

    # ── issues_cache ──────────────────────────────────────────────
    await db["issues_cache"].create_index("repo", unique=True)
    await db["issues_cache"].create_index([("fetched_at", -1)])
    # TTL index: auto-delete cache entries older than 1 hour
    await db["issues_cache"].create_index(
        "fetched_at",
        expireAfterSeconds=3600,
        name="issues_cache_ttl",
    )
    print("✓ issues_cache indexes + TTL")

    # ── existing cto_review_logs (if exists — upsert safe) ────────
    await db["cto_review_logs"].create_index([("timestamp", -1)])
    await db["cto_review_logs"].create_index("task_id")
    print("✓ cto_review_logs indexes")

    print("\n✅ All migrations complete.")
    client.close()


if __name__ == "__main__":
    asyncio.run(run_migrations())
