#!/usr/bin/env python3
"""Focused API/DB probe for Iter 311 /admin/scope-drift-audit.

Checks admin auth, response contract, and absence of observed DB count changes
around the read-only GET. Token values are intentionally never persisted.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient


BASE_URL = os.getenv("ITER311_BASE_URL", "http://127.0.0.1:8001")
ARTIFACT_DIR = Path("/app/test_reports/bug_verification_artifacts")
ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)


async def db_counts() -> dict[str, int]:
    load_dotenv("/app/backend/.env")
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    try:
        db = client[os.environ.get("DB_NAME", "aurem_dev")]
        names = sorted(await db.list_collection_names())
        counts = {}
        for name in names:
            counts[name] = await db[name].count_documents({})
        return counts
    finally:
        client.close()


async def main() -> int:
    email = "test@aurem.dev"
    password = "AuremTest2026!"
    endpoint = f"{BASE_URL}/api/aurem-dev/admin/scope-drift-audit?days=30&limit=5"
    required_fields = {
        "ok",
        "total_drift_events",
        "distinct_loops",
        "avg_extras_per_drift",
        "most_frequent_extra_paths",
        "samples",
        "notes",
    }

    async with httpx.AsyncClient(timeout=20.0) as client:
        unauth = await client.get(endpoint)
        login = await client.post(
            f"{BASE_URL}/api/aurem-dev/auth/login",
            json={"email": email, "password": password},
        )
        login.raise_for_status()
        login_body = login.json()
        token = login_body.get("token")
        assert token, "login did not return token"
        assert login_body.get("is_admin") is True, login_body

        redacted_login = dict(login_body)
        redacted_login["token"] = "<redacted-present>"
        (ARTIFACT_DIR / "iter311_login_response.json").write_text(
            json.dumps(redacted_login, indent=2, sort_keys=True) + "\n"
        )

        before = await db_counts()
        authed = await client.get(endpoint, headers={"Authorization": f"Bearer {token}"})
        after = await db_counts()

    assert unauth.status_code == 401, unauth.text
    assert authed.status_code == 200, authed.text
    body = authed.json()
    missing = sorted(required_fields - set(body))
    assert not missing, missing
    assert body["ok"] is True, body
    assert isinstance(body["most_frequent_extra_paths"], list), body
    assert isinstance(body["samples"], list), body
    assert isinstance(body["notes"], list), body

    count_deltas = {
        name: [before.get(name, 0), after.get(name, 0)]
        for name in sorted(set(before) | set(after))
        if before.get(name, 0) != after.get(name, 0)
    }
    assert not count_deltas, count_deltas

    result = {
        "ok": True,
        "unauth_status": unauth.status_code,
        "authed_status": authed.status_code,
        "response_fields_present": sorted(required_fields),
        "total_drift_events": body["total_drift_events"],
        "distinct_loops": body["distinct_loops"],
        "db_collection_count_deltas_after_read_only_get": count_deltas,
        "note": "No DB collection count changes observed; endpoint code review shows only find/sort/limit reads.",
    }
    (ARTIFACT_DIR / "iter311_scope_drift_audit_probe_result.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n"
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))