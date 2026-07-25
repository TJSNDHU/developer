import asyncio
import os
from datetime import datetime, timedelta, timezone

import httpx
from motor.motor_asyncio import AsyncIOMotorClient


BASE = os.environ.get("PREVIEW_BASE", "https://launch-pad-237.preview.emergentagent.com") + "/api/aurem-dev"
EMAIL = "test@aurem.dev"
PASSWORD = "AuremTest2026!"


async def login():
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(f"{BASE}/auth/login", json={"email": EMAIL, "password": PASSWORD})
        print("login", r.status_code, r.text[:300])
        r.raise_for_status()
        return r.json()["token"]


async def probe_loop_start(token):
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            f"{BASE}/loop/start",
            headers={"Authorization": f"Bearer {token}"},
            json={"project_id": "p_norepotest", "user_message": "Add a small health check comment to README.md"},
        )
        print("loop/start", r.status_code, r.text[:800])
        return r


async def synthetic_reaper_probe(token):
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ.get("DB_NAME", "aurem_dev")]
    user = await db.dev_users.find_one({"email": EMAIL}, {"_id": 0, "user_id": 1})
    loop_id = "loop_iter308_periodic_probe"
    await db.loop_sessions.delete_many({"loop_id": loop_id})
    await db.loop_sessions.insert_one({
        "loop_id": loop_id,
        "user_id": user["user_id"],
        "project_id": "p_norepotest",
        "state": "executing",
        "phase": "execute",
        "context": {},
        "updated_at": datetime.now(timezone.utc) - timedelta(seconds=900),
        "last_event": {
            "loop_id": loop_id,
            "state": "executing",
            "phase": "execute",
            "message": "EXECUTE START synthetic stale probe",
            "data": {},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    })
    async with httpx.AsyncClient(timeout=20) as h:
        before = await h.get(f"{BASE}/loop/{loop_id}/status", headers={"Authorization": f"Bearer {token}"})
        print("synthetic status before", before.status_code, before.text[:300])
        # Background reaper runs every 60s. Poll for up to 75s.
        for i in range(16):
            await asyncio.sleep(5)
            doc = await db.loop_sessions.find_one({"loop_id": loop_id}, {"_id": 0, "state": 1, "resume_reason": 1, "updated_at": 1})
            print("poll", i, doc)
            if doc and doc.get("state") == "paused_for_user":
                break
        after = await h.get(f"{BASE}/loop/{loop_id}/status", headers={"Authorization": f"Bearer {token}"})
        print("synthetic status after", after.status_code, after.text[:500])
    await db.loop_sessions.delete_many({"loop_id": loop_id})


async def main():
    token = await login()
    await probe_loop_start(token)
    await synthetic_reaper_probe(token)


asyncio.run(main())