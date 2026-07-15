"""
qa/simulated-user/seed_qa_user.py
==================================
One-shot seeder for the simulated-user QA suite.

Creates:
  • dev_users     — email qa+bot@aurem.dev, tier=free (so quota tests
                    trigger)
  • cto_projects  — TWO projects owned by that user:
                       p_qa_project_a  (has a wired repo dummy)
                       p_qa_project_b  (has a wired repo dummy)
                    Used by scenario 2 (project-scoping confusion).
  • cto_open_findings — one critical finding on Project A so the
                        "review a pending finding" scenario has
                        something concrete to talk about.
  • Prints on stdout:
      AUREM_QA_TOKEN=<probe token>
      AUREM_QA_JWT=<bearer JWT for the seeded user>
      AUREM_QA_PROJECT_A=p_qa_project_a
      AUREM_QA_PROJECT_B=p_qa_project_b
    …so run.sh can `eval $(python3 seed_qa_user.py)`.

Idempotent — running it twice does not duplicate rows.
"""
from __future__ import annotations

import asyncio
import os
import secrets
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "backend"),
))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", "backend", ".env"))

from motor.motor_asyncio import AsyncIOMotorClient


QA_EMAIL       = "qa+bot@aurem.dev"
QA_USER_ID     = "qa-bot-user-01"
QA_PROJECT_A   = "p_qa_project_a"
QA_PROJECT_B   = "p_qa_project_b"


async def _seed():
    # Iter 212m-227 — production-grade pool config even for QA seed.
    client = AsyncIOMotorClient(
        os.environ["MONGO_URL"],
        maxPoolSize=5, minPoolSize=1, maxIdleTimeMS=30_000,
        connectTimeoutMS=10_000,
    )
    db     = client[os.environ["DB_NAME"]]
    now    = datetime.now(timezone.utc)

    # 1. Test user — tier=free forces quota tests to fire.
    await db.dev_users.update_one(
        {"email": QA_EMAIL},
        {
            "$setOnInsert": {
                "user_id":       QA_USER_ID,
                "email":         QA_EMAIL,
                "created_at":    now,
                "password_hash": "$2b$12$" + "x" * 53,   # unusable, JWT only
                "tier":          "free",
            },
            "$set": {
                "name":          "QA Simulated User",
                "quota_used":    0,
            },
        },
        upsert=True,
    )

    # 2. Two projects — b intentionally identical shape to a so a
    # confused agent could accidentally reach for it.
    for pid in (QA_PROJECT_A, QA_PROJECT_B):
        await db.cto_projects.update_one(
            {"project_id": pid},
            {
                "$setOnInsert": {
                    "project_id":  pid,
                    "user_id":     QA_USER_ID,
                    "created_at":  now,
                },
                "$set": {
                    "name":         f"QA {pid[-1].upper()}",
                    "github_owner": "aurem-qa",
                    "github_repo":  f"probe-{pid[-1]}",
                    "default_branch": "main",
                },
            },
            upsert=True,
        )

    # 3. One critical finding on Project A → gives the persona a real
    # "pending SQL finding" to reference in Scenario 2.
    await db.cto_open_findings.update_one(
        {"finding_id": "qa::app/user_controller.py:22:sql_string_format"},
        {
            "$setOnInsert": {
                "user_id":       QA_USER_ID,
                "project_id":    QA_PROJECT_A,
                "finding_id":    "qa::app/user_controller.py:22:sql_string_format",
                "first_seen_at": now - timedelta(days=45),
                "status":        "open",
                "source":        "qa_seed",
            },
            "$set": {
                "scanner":      "bug_hunt",
                "rule_id":      "sql_string_format",
                "severity":     "critical",
                "file":         "app/user_controller.py",
                "line":         22,
                "title":        "f-string SQL query",
                "message":      "user_controller.py uses an f-string SQL "
                                "call — parameterise it.",
                "fix_hint":     "Use parameterised query with placeholders.",
                "last_seen_at": now - timedelta(days=45),
                "exposure_count": 0,
            },
        },
        upsert=True,
    )

    # 4. Mint a JWT for the seeded user matching the app's usual key
    # + algorithm so authorised requests succeed against the real
    # backend without going through /auth/login.
    import jwt as _jwt
    jwt_secret = os.environ.get("JWT_SECRET") or os.environ.get("AUREM_JWT_SECRET")
    if not jwt_secret:
        # Fallback — mirror the default the app derives when unset.
        jwt_secret = "aurem-dev-fallback-key-32-chars-minimum!"
    token = _jwt.encode(
        {
            "sub":       QA_USER_ID,
            "user_id":   QA_USER_ID,
            "email":     QA_EMAIL,
            "tier":      "free",
            "iat":       int(now.timestamp()),
            "exp":       int((now + timedelta(hours=6)).timestamp()),
        },
        jwt_secret, algorithm="HS256",
    )

    # 5. Mint the probe-token — a random per-run secret written to
    # the QA env for both the CLI and the backend to share.
    probe_token = "qa-" + secrets.token_urlsafe(24)

    # Report on stdout — run.sh consumes these as env exports.
    print(f"AUREM_QA_TOKEN={probe_token}")
    print(f"AUREM_QA_JWT={token}")
    print(f"AUREM_QA_PROJECT_A={QA_PROJECT_A}")
    print(f"AUREM_QA_PROJECT_B={QA_PROJECT_B}")
    print(f"AUREM_QA_USER_ID={QA_USER_ID}")


if __name__ == "__main__":
    asyncio.run(_seed())
