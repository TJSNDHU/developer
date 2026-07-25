import asyncio
import os
from motor.motor_asyncio import AsyncIOMotorClient


async def main():
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ.get("DB_NAME", "aurem_dev")]
    res = await db.loop_locks.delete_many({
        "user_id": "test_admin_001",
        "project_id": {"$in": ["p_demo_a", "p_demo_b"]},
        "loop_id": {"$in": ["loop_bebaf2643d0441", "loop_5af11808c8c24f"]},
    })
    sess = await db.loop_sessions.delete_many({
        "loop_id": {"$in": ["loop_5bcc168504b641", "loop_657727bef0ac4f"]},
        "user_id": "test_admin_001",
    })
    print({"deleted_locks": res.deleted_count, "deleted_test_sessions": sess.deleted_count})


asyncio.run(main())