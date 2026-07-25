import asyncio
import os
from motor.motor_asyncio import AsyncIOMotorClient


async def main():
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ.get("DB_NAME", "aurem_dev")]
    user = await db.dev_users.find_one({"email": "test@aurem.dev"}, {"_id": 0, "user_id": 1, "email": 1, "is_admin": 1, "tier": 1, "is_unlimited": 1, "github": 1})
    print({k: ("<present>" if k == "github" and v else v) for k, v in (user or {}).items()})
    if user:
        projects = []
        async for p in db.cto_projects.find({"user_id": user["user_id"]}, {"_id": 0, "project_id": 1, "name": 1, "github_owner": 1, "github_repo": 1, "branch": 1, "github_branch": 1, "github_token": 1}).limit(10):
            if p.get("github_token"):
                p["github_token"] = "<present>"
            projects.append(p)
        print("projects", projects)
        active = []
        async for s in db.loop_sessions.find({"user_id": user["user_id"]}, {"_id": 0, "loop_id": 1, "state": 1, "phase": 1, "updated_at": 1, "resume_reason": 1, "last_event": 1}).sort("updated_at", -1).limit(10):
            if s.get("last_event"):
                s["last_event"] = {k: s["last_event"].get(k) for k in ["state", "phase", "message", "timestamp", "data"]}
            print("session", s)


asyncio.run(main())