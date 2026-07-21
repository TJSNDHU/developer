"""
services/loop_task_specs.py — Iter 272 Feature 1.1

Frozen task spec: at plan-approval time, snapshot the user's original
task description + the plan's acceptance criteria into a WORM
(write-once-read-many) collection. The fixing agent must not be able
to see or modify this snapshot for the rest of the run — it exists
solely to be the ground truth the independent verifier judges against.

Public surface:
    freeze(...) → dict          — write the snapshot; idempotent per run
    get(run_id) → dict | None   — read-only lookup for the verifier

Deliberate design choices:
  * No `update`/`delete` function is exported. The collection has no
    admin-facing router. Anyone with DB access can still mutate rows
    manually — WORM is enforced at the code layer, not at Mongo.
  * `acceptance_criteria` is a `list[str]`. If the plan is short/
    unparseable we still snapshot the raw user message as a single
    criterion — never leaves the list empty.
  * Idempotent per (run_id): re-freezing a run is a no-op that returns
    the existing row. Prevents accidental double-writes.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

_COLL = "loop_task_specs"

# Bullet / numbered / checkbox line extractor. Kept intentionally
# permissive — over-collection is better than missing a criterion.
_CRITERION_RE = re.compile(
    r"^\s*(?:[-*+•]|\d+[.)]|\[[ xX]\])\s+(.+?)\s*$",
    re.MULTILINE,
)


def _extract_criteria(plan_text: str, fallback: str) -> list[str]:
    """Pull actionable bullets/steps out of the plan. Falls back to
    a single-item list containing the raw user message when the plan
    has no structure."""
    text = (plan_text or "").strip()
    if text:
        found = [m.group(1).strip() for m in _CRITERION_RE.finditer(text)]
        # Drop trivial-length noise ("ok", "done", …).
        found = [c for c in found if len(c) >= 8]
        if found:
            # Cap so a runaway plan can't blow the doc size.
            return found[:40]
    fb = (fallback or "").strip()
    return [fb] if fb else ["(no criteria captured)"]


async def freeze(db,
                 *,
                 loop_id: str,
                 task_id: Optional[str],
                 user_id: str,
                 project_id: Optional[str],
                 user_message: str,
                 plan: Any) -> dict:
    """Snapshot the task + plan into `loop_task_specs`. Idempotent
    per `loop_id`. Returns the (possibly-existing) row."""
    existing = await db[_COLL].find_one({"loop_id": loop_id})
    if existing:
        existing.pop("_id", None)
        return existing

    plan_text = plan if isinstance(plan, str) else str(plan or "")
    criteria = _extract_criteria(plan_text, user_message)

    row = {
        "loop_id":              loop_id,
        "task_id":              task_id or loop_id,
        "user_id":              user_id,
        "project_id":           project_id,
        "frozen_at":            datetime.now(timezone.utc).isoformat(),
        "original_task":        (user_message or "")[:8000],
        "plan_snapshot":        plan_text[:16000],
        "acceptance_criteria":  criteria,
        "created_by":           user_id,
        "worm":                 True,          # explicit marker, docs-only
    }
    try:
        await db[_COLL].insert_one(dict(row))
        logger.info("[loop %s] task spec frozen with %d criteria",
                    loop_id, len(criteria))
    except Exception as e:                                    # noqa: BLE001
        # Race: another worker inserted concurrently. Re-read.
        logger.warning("[loop %s] freeze insert failed (%r) — reading",
                       loop_id, e)
        again = await db[_COLL].find_one({"loop_id": loop_id})
        if again:
            again.pop("_id", None)
            return again
        raise
    return row


async def get(db, loop_id: str) -> Optional[dict]:
    """Read-only fetch for the independent verifier. Never call
    `find_one_and_update` on this collection."""
    doc = await db[_COLL].find_one({"loop_id": loop_id})
    if doc:
        doc.pop("_id", None)
    return doc


async def ensure_indexes(db) -> None:
    """Called from main.py on boot. Unique index enforces WORM at
    the DB layer for the (loop_id) key."""
    await db[_COLL].create_index("loop_id", unique=True)
    await db[_COLL].create_index("user_id")
    await db[_COLL].create_index("frozen_at")
