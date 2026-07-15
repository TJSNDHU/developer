"""
backend/scripts/cleanup_orphans.py
==================================
Iter 121 — Optional one-shot maintenance script. Removes rows that
reference deleted parents.

USAGE (production safe — dry-run by default):

    python -m scripts.cleanup_orphans            # report only
    python -m scripts.cleanup_orphans --apply    # actually delete

What it touches:
  - chat_sessions where user_id ∉ dev_users
  - cto_tasks    where user_id ∉ dev_users
  - cto_tasks    where project_id is set but ∉ cto_projects
  - cto_projects where user_id ∉ dev_users

Why this matters:
  Foreign-key violations bloat the auditor's "FEW INDEXES" report,
  inflate /admin/users aggregations, and confuse the admin tab counts.

This script is NOT wired into lifespan — orphans are a sign of a
manual cleanup or seed gone wrong, so the founder should run it
explicitly when needed.
"""
from __future__ import annotations
import asyncio
import logging
import os
import sys
from typing import Iterable
from pathlib import Path

logger = logging.getLogger(__name__)


def _load_env() -> None:
    env_file = Path(__file__).resolve().parents[1] / ".env"
    if not env_file.is_file():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        if k.strip() and k.strip() not in os.environ:
            os.environ[k.strip()] = v.strip().strip('"').strip("'")


async def _ids(coll, field: str) -> set[str]:
    out = set()
    async for d in coll.find({}, {field: 1}):
        v = d.get(field)
        if isinstance(v, str):
            out.add(v)
    return out


async def cleanup_orphans(db, apply: bool = False) -> dict:
    """Returns {table: count_to_delete (or deleted)}.
    If `apply` is False we only count; no writes."""
    user_ids = await _ids(db.dev_users,    "user_id")
    proj_ids = await _ids(db.cto_projects, "project_id")
    report: dict[str, int] = {}

    async def _scan(coll, field: str, allowed: Iterable[str], label: str,
                    extra_match: dict | None = None) -> None:
        q: dict = {field: {"$nin": list(allowed)}}
        if extra_match:
            q.update(extra_match)
        n = await coll.count_documents(q)
        report[label] = n
        if apply and n:
            res = await coll.delete_many(q)
            report[label] = res.deleted_count

    await _scan(db.chat_sessions, "user_id",    user_ids,         "chat_sessions:no_user")
    await _scan(db.cto_tasks,     "user_id",    user_ids,         "cto_tasks:no_user")
    await _scan(db.cto_projects,  "user_id",    user_ids,         "cto_projects:no_user")
    # Tasks with a project_id field set that points nowhere. We
    # explicitly require the field to exist + be non-null to avoid
    # nuking "free-floating" tasks that never had a project.
    await _scan(
        db.cto_tasks, "project_id", proj_ids,   "cto_tasks:no_project",
        extra_match={"project_id": {"$exists": True, "$ne": None}},
    )
    return report


async def _main(apply: bool) -> int:
    _load_env()
    from motor.motor_asyncio import AsyncIOMotorClient
    # Iter 212m-227 — production-grade pool config so the cleanup
    # script never starves connections under Atlas M10 traffic.
    client = AsyncIOMotorClient(
        os.environ["MONGO_URL"],
        maxPoolSize=10, minPoolSize=1, maxIdleTimeMS=30_000,
        connectTimeoutMS=10_000,
    )
    db = client[os.environ.get("DB_NAME", "aurem_dev")]
    report = await cleanup_orphans(db, apply=apply)
    mode = "DELETED" if apply else "would-delete"
    print(f"\nOrphan cleanup ({mode}):")
    total = 0
    for k, v in report.items():
        print(f"  {k:<32} {v}")
        total += v
    print(f"  {'TOTAL':<32} {total}")
    if not apply:
        print("\nDry-run only. Re-run with --apply to delete.")
    client.close()
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    apply_flag = "--apply" in sys.argv
    sys.exit(asyncio.run(_main(apply_flag)))
