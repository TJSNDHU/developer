import asyncio
import json
import os
from datetime import datetime, timedelta, timezone

import httpx
from motor.motor_asyncio import AsyncIOMotorClient

from services.loop_engine import resume_stale, STALE_AFTER_S


BASE = "https://launch-pad-237.preview.emergentagent.com/api/aurem-dev"
EMAIL = "test@aurem.dev"
PASSWORD = "AuremTest2026!"


async def main():
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ.get("DB_NAME", "aurem_dev")]
    user = await db.dev_users.find_one({"email": EMAIL}, {"_id": 0, "user_id": 1})
    loop_id = "loop_iter308_sse_reaper_probe"
    await db.loop_sessions.delete_many({"loop_id": loop_id})
    stale_event = {
        "loop_id": loop_id,
        "state": "executing",
        "phase": "execute",
        "message": "EXECUTE START stale event should not remain visible after reaper",
        "data": {"sub_step": "start"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    await db.loop_sessions.insert_one({
        "loop_id": loop_id,
        "user_id": user["user_id"],
        "project_id": "p_norepotest",
        "state": "executing",
        "phase": "execute",
        "context": {},
        "updated_at": datetime.now(timezone.utc) - timedelta(seconds=STALE_AFTER_S + 30),
        "last_event": stale_event,
    })
    try:
        rescued = await resume_stale(db)
        doc = await db.loop_sessions.find_one({"loop_id": loop_id}, {"_id": 0, "state": 1, "resume_reason": 1, "last_event": 1})
        print("resume_stale rescued", rescued)
        print("doc after reaper", json.dumps(doc, default=str))

        async with httpx.AsyncClient(timeout=20) as client:
            login = await client.post(f"{BASE}/auth/login", json={"email": EMAIL, "password": PASSWORD})
            token = login.json()["token"]
            async with client.stream(
                "GET",
                f"{BASE}/loop/{loop_id}/stream",
                headers={"Authorization": f"Bearer {token}", "Accept": "text/event-stream"},
            ) as resp:
                print("stream status", resp.status_code)
                chunks = []
                async for line in resp.aiter_lines():
                    if line.startswith("data:"):
                        chunks.append(line[5:].strip())
                        break
                print("first stream data", chunks[0] if chunks else "<none>")
    finally:
        await db.loop_sessions.delete_many({"loop_id": loop_id})


asyncio.run(main())