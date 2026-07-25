"""Synthetic proof for iter308 loop-stuck rescue visibility.

Logs in as preview test user, inserts a stale executing loop_sessions doc for that
user, runs resume_stale(db), then opens the real /loop/{id}/stream endpoint with
curl and asserts the Mongo-persisted rescue last_event is yielded to an SSE client.
"""
from __future__ import annotations

import asyncio
import json
import os
import secrets
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import requests
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, "/app/backend")
from services.loop_engine import LoopState, STALE_AFTER_S, resume_stale  # noqa: E402

API_BASE = os.getenv("API_BASE", "http://localhost:8001/api/aurem-dev")
EMAIL = os.getenv("AUREM_TEST_EMAIL", "test@aurem.dev")
PASSWORD = os.getenv("AUREM_TEST_PASSWORD", "AuremTest2026!")


def login() -> tuple[str, str]:
    r = requests.post(
        f"{API_BASE}/auth/login",
        json={"email": EMAIL, "password": PASSWORD},
        timeout=15,
    )
    r.raise_for_status()
    data = r.json()
    return data["token"], data["user_id"]


async def main() -> None:
    token, user_id = login()
    mongo_url = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
    db_name = os.environ.get("DB_NAME", "aurem_dev")
    db = AsyncIOMotorClient(mongo_url)[db_name]
    loop_id = f"loop_iter308_sse_{secrets.token_hex(5)}"
    stale_ts = datetime.now(timezone.utc) - timedelta(seconds=STALE_AFTER_S + 90)
    try:
        await db.loop_sessions.insert_one({
            "loop_id": loop_id,
            "user_id": user_id,
            "project_id": "iter308_probe_project",
            "state": LoopState.EXECUTING.value,
            "phase": "execute",
            "context": {"errors_encountered": []},
            "updated_at": stale_ts,
            "last_event": {
                "loop_id": loop_id,
                "state": LoopState.EXECUTING.value,
                "phase": "execute",
                "message": "EXECUTE START stale event (should be replaced)",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        })
        rescued = await resume_stale(db)
        assert rescued >= 1, f"resume_stale rescued={rescued}, expected >=1"
        doc = await db.loop_sessions.find_one({"loop_id": loop_id}, {"_id": 0})
        le = (doc or {}).get("last_event") or {}
        assert (doc or {}).get("state") == "paused_for_user", doc
        assert le.get("state") == "paused_for_user", le
        assert le.get("requires_user_action") is True, le
        assert (le.get("data") or {}).get("rescued") is True, le

        proc = subprocess.run(
            [
                "timeout", "8", "curl", "-N", "-sS",
                "-H", f"Authorization: Bearer {token}",
                "-H", "Accept: text/event-stream",
                f"{API_BASE}/loop/{loop_id}/stream",
            ],
            capture_output=True,
            text=True,
            timeout=12,
            check=False,
        )
        stdout = proc.stdout or ""
        frames = []
        for frame in stdout.split("\n\n"):
            data_lines = [line[5:].strip() for line in frame.splitlines() if line.startswith("data:")]
            if data_lines:
                frames.append(json.loads("\n".join(data_lines)))
        assert frames, f"SSE stream yielded no data frames. stdout={stdout!r} stderr={proc.stderr!r}"
        ev = frames[0]
        assert ev.get("state") == "paused_for_user", ev
        assert ev.get("requires_user_action") is True, ev
        assert (ev.get("data") or {}).get("rescued") is True, ev
        print(json.dumps({
            "ok": True,
            "loop_id": loop_id,
            "rescued_count": rescued,
            "persisted_last_event": {
                "state": le.get("state"),
                "phase": le.get("phase"),
                "requires_user_action": le.get("requires_user_action"),
                "rescued": (le.get("data") or {}).get("rescued"),
            },
            "stream_event": {
                "state": ev.get("state"),
                "phase": ev.get("phase"),
                "requires_user_action": ev.get("requires_user_action"),
                "rescued": (ev.get("data") or {}).get("rescued"),
                "message": ev.get("message"),
            },
        }, indent=2))
    finally:
        await db.loop_sessions.delete_many({"loop_id": loop_id})
        await db.loop_failures.delete_many({"loop_id": loop_id})


if __name__ == "__main__":
    asyncio.run(main())
