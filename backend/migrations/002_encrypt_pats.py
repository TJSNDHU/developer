"""
migrations/002_encrypt_pats.py
==============================
Iter 43 — One-shot migration. Encrypts plaintext `github_token` values in
the `cto_projects` collection using the per-customer vault.

Idempotent — rows already encrypted (token starts with "v1:") are skipped.
Rows with no token are skipped. Audits every encrypt via vault._audit.

Run with:
    python -m migrations.002_encrypt_pats

Requires: AUREM_MASTER_KEY env var (>= 32 chars).
"""
import asyncio
import os
import sys

from motor.motor_asyncio import AsyncIOMotorClient

# Ensure backend root on path so `services.*` imports work when invoked
# from the repo root via `python -m`.
_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

from services.vault import encrypt, is_vault_available  # noqa: E402


MONGO_URI = os.getenv("MONGO_URL") or os.getenv("MONGODB_URI", "mongodb://localhost:27017")
DB_NAME   = os.getenv("DB_NAME")   or os.getenv("MONGODB_DB", "aurem_dev")


async def migrate():
    if not is_vault_available():
        print("✗ AUREM_MASTER_KEY missing or too short — refusing to run.")
        sys.exit(1)

    # Iter 212m-227 — production-grade pool config for migration runs.
    client = AsyncIOMotorClient(
        MONGO_URI,
        maxPoolSize=10, minPoolSize=1, maxIdleTimeMS=30_000,
        connectTimeoutMS=10_000,
    )
    db = client[DB_NAME]

    # cto_projects.github_token is where PATs actually live in our schema.
    # The original spec also mentions dev_users.github_pat, but that field
    # doesn't exist here — dev_users.github.access_token is the OAuth token
    # path (separate concern, OAuth-managed). Only the per-project PATs
    # need encrypting.
    cursor = db.cto_projects.find(
        {"github_token": {"$exists": True, "$ne": None, "$ne": ""}},
        {"project_id": 1, "user_id": 1, "github_token": 1, "_id": 1},
    )
    docs = await cursor.to_list(length=10_000)

    print(f"Scanned {len(docs)} project rows with a stored github_token.")
    migrated = 0
    already  = 0
    skipped  = 0

    for d in docs:
        tok  = (d.get("github_token") or "").strip()
        uid  = d.get("user_id") or ""
        if not tok:
            skipped += 1
            continue
        if tok.startswith("v1:"):
            already += 1
            continue
        if not uid:
            print(f"  ! skipping {d.get('project_id')}: no user_id")
            skipped += 1
            continue
        try:
            ct = await encrypt(uid, tok, kind="github_token")
        except Exception as e:
            print(f"  ! encrypt failed for {d.get('project_id')}: {e}")
            skipped += 1
            continue
        await db.cto_projects.update_one(
            {"_id": d["_id"]},
            {"$set": {"github_token": ct, "pat_encrypted": True,
                      "pat_encrypted_at": __import__("time").time()}},
        )
        migrated += 1

    print(f"\n✓ Migrated {migrated} PATs.")
    print(f"✓ Already encrypted (skipped): {already}")
    print(f"✓ Skipped (no token or missing user_id): {skipped}")
    client.close()


if __name__ == "__main__":
    asyncio.run(migrate())
