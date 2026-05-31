"""
scripts/migrate_iter34.py — one-time cleanup for sparse `turns[]` entries.

Background: before Iter 34, the front-end sent `turn_index` based on the
rendered messages array position. That included a WELCOME system message
at index 0 which was NEVER persisted to MongoDB. Result: shipping the
first assistant reply caused `db.chat_sessions.turns[2].shipped_task_id`
to be written when the array only had 2 elements, and MongoDB silently
created a sparse `turns[2]` entry containing ONLY {shipped_task_id} — no
role, no content, no ts.

Those sparse entries make the Ship button reappear for the affected
turns even after Iter 34 ships. This script removes them.

Safe to run anytime. Idempotent. Two-phase:
  • PHASE 1: scan every chat_sessions doc, count + report sparse turns
  • PHASE 2: rewrite `turns` arrays, dropping entries that lack a `role`

Use:
  cd /app/backend && python3 -m scripts.migrate_iter34            # dry run
  cd /app/backend && python3 -m scripts.migrate_iter34 --apply    # commit

Exit codes: 0 ok, 1 connection/setup error.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import Any

from motor.motor_asyncio import AsyncIOMotorClient

REQUIRED_ENV = ("MONGO_URL", "DB_NAME")


def _env_or_die() -> tuple[str, str]:
    missing = [k for k in REQUIRED_ENV if not os.environ.get(k)]
    if missing:
        sys.stderr.write(
            f"missing env vars: {missing} — "
            "run from /app/backend after sourcing .env\n"
        )
        sys.exit(1)
    return os.environ["MONGO_URL"], os.environ["DB_NAME"]


def _is_sparse(turn: Any) -> bool:
    """Sparse = not a dict, OR no `role` key. Legitimate turns always
    carry role='user' or role='assistant' (see _persist_turn in chat.py)."""
    if not isinstance(turn, dict):
        return True
    role = turn.get("role")
    return role not in ("user", "assistant")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="commit the cleanup (default: dry-run, report only)")
    args = ap.parse_args()

    mongo_url, db_name = _env_or_die()
    db = AsyncIOMotorClient(mongo_url)[db_name]

    cursor = db.chat_sessions.find(
        {}, {"_id": 1, "session_id": 1, "user_id": 1, "turns": 1},
    )
    total_sessions = 0
    affected_sessions = 0
    total_sparse_turns = 0
    examples: list[dict] = []

    async for doc in cursor:
        total_sessions += 1
        turns = doc.get("turns") or []
        sparse = [t for t in turns if _is_sparse(t)]
        if not sparse:
            continue
        affected_sessions += 1
        total_sparse_turns += len(sparse)
        if len(examples) < 5:
            examples.append({
                "session_id": (doc.get("session_id") or "")[:24],
                "user_id":    (doc.get("user_id") or "")[:24],
                "len_before": len(turns),
                "sparse_count": len(sparse),
                "sample": sparse[0] if sparse else None,
            })
        if args.apply:
            clean = [t for t in turns if not _is_sparse(t)]
            await db.chat_sessions.update_one(
                {"_id": doc["_id"]},
                {"$set": {"turns": clean}},
            )

    print("─" * 60)
    print(f"Scanned sessions:    {total_sessions}")
    print(f"Affected sessions:   {affected_sessions}")
    print(f"Sparse turn entries: {total_sparse_turns}")
    if examples:
        print("\nFirst 5 examples:")
        for ex in examples:
            print(f"  • {ex['session_id']!r}  (user {ex['user_id']!r}) — "
                  f"{ex['sparse_count']} sparse / {ex['len_before']} total — "
                  f"sample: {ex['sample']}")
    if args.apply:
        print(f"\n✅ APPLIED — cleaned {total_sparse_turns} sparse entries "
              f"from {affected_sessions} sessions.")
    elif affected_sessions:
        print(f"\nℹ️  dry-run — run with --apply to commit the cleanup.")
    else:
        print("\n✅ no sparse entries found, nothing to do.")


if __name__ == "__main__":
    asyncio.run(main())
