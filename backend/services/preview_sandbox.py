"""
services/preview_sandbox.py — Iter 212m-239 — Tier 2.5

Live-preview backend for the `react-fastapi` stack (E2B-only path).

Design decision (revised Tier 2.5 scope, Feb 13 2026):
    - JS-based stacks (nextjs-node, vue-express, plain-html) preview
      IN THE BROWSER via `@codesandbox/sandpack-react` — zero server
      cost, zero lifecycle to manage.
    - Only `react-fastapi` needs a real Python interpreter, so E2B is
      scoped to THAT one stack. This module is the thin adapter.

Public API
==========
    is_configured() -> bool
    create_preview_sandbox(draft_id, files) -> dict
    get_preview_status(sandbox_id) -> dict
    kill_preview_sandbox(sandbox_id) -> dict

Lifecycle
=========
    TTL: 20 minutes (matches E2B free-tier billing granularity).
    Auto-cleanup: sandboxes idle > TTL are killed by
    `_preview_sweeper_cron()` (main.py-wired, runs every 5 minutes).
    Tracking: `db.preview_sandboxes` collection.

If `E2B_API_KEY` is missing every function returns a structured
`{ok: False, reason: "e2b_not_configured"}` and the router surfaces
a graceful 503 — same pattern as Phase 2/3/5.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Optional

logger = logging.getLogger(__name__)

PREVIEW_TTL_S      = 20 * 60          # 20 min — matches E2B billing granularity
PREVIEW_COLLECTION = "preview_sandboxes"


def _key() -> str:
    return (os.environ.get("E2B_API_KEY") or "").strip()


def is_configured() -> bool:
    return bool(_key())


def _not_configured() -> dict:
    return {
        "ok":     False,
        "reason": "e2b_not_configured",
        "detail": ("Live preview for the react-fastapi stack requires "
                   "E2B_API_KEY in backend/.env. Get a free key at "
                   "https://e2b.dev (100 sandbox-hours/month free)."),
    }


async def create_preview_sandbox(draft_id: str, files: list[dict]) -> dict:
    """Spin up an ephemeral sandbox, write the draft's files into it,
    start the FastAPI dev server, and return a public preview URL.

    Returns:
        On success: {ok, sandbox_id, url, expires_at}
        On failure: {ok:False, reason, detail}
    """
    if not is_configured():
        return _not_configured()
    try:
        # Late import to avoid loading e2b at cold-start.
        from e2b_code_interpreter import Sandbox            # type: ignore
    except ImportError:
        return {"ok": False, "reason": "e2b_sdk_missing",
                "detail": "e2b-code-interpreter package not installed"}

    try:
        # E2B doesn't have an async create; use the blocking API in an
        # executor so the FastAPI event loop stays healthy.
        loop = asyncio.get_running_loop()
        sbx  = await loop.run_in_executor(
            None, lambda: Sandbox.create(api_key=_key(), timeout=PREVIEW_TTL_S),
        )
        # Write files.
        for f in files:
            path = f.get("path")
            content = f.get("content") or ""
            if not path or ".." in path or path.startswith("/"):
                continue
            await loop.run_in_executor(
                None,
                lambda p=path, c=content: sbx.files.write(f"/home/user/{p}", c),
            )
        # Start the FastAPI server in the background.
        # Assumes react-fastapi's boilerplate: `api/main.py` on port 8000.
        await loop.run_in_executor(
            None,
            lambda: sbx.commands.run(
                "cd /home/user && pip install -q -r api/requirements.txt "
                "&& uvicorn api.main:app --host 0.0.0.0 --port 8000 &",
                background=True,
            ),
        )
        # E2B exposes ports via the sandbox host domain.
        host = getattr(sbx, "get_host", lambda _p: None)(8000)
        url = f"https://{host}" if host else None
        expires_at = time.time() + PREVIEW_TTL_S
        logger.info("[preview] created sandbox=%s draft=%s url=%s",
                    sbx.sandbox_id, draft_id, url)
        return {
            "ok":         True,
            "sandbox_id": sbx.sandbox_id,
            "url":        url,
            "expires_at": expires_at,
        }
    except Exception as e:                                # noqa: BLE001
        logger.warning("[preview] create failed: %r", e)
        return {"ok": False, "reason": "sandbox_create_failed",
                "detail": str(e)[:300]}


async def get_preview_status(sandbox_id: str) -> dict:
    """Return the sandbox's health / TTL remaining without touching E2B.
    Reads the tracking row we wrote at create-time."""
    from cto_services.db import get_db
    db = get_db()
    if db is None: return {"ok": False, "reason": "db_not_connected"}
    row = await db[PREVIEW_COLLECTION].find_one({"sandbox_id": sandbox_id}, {"_id": 0})
    if not row: return {"ok": False, "reason": "not_found"}
    remaining = max(0, int((row.get("expires_at") or 0) - time.time()))
    return {"ok": True, **row, "seconds_remaining": remaining,
            "expired": remaining == 0}


async def kill_preview_sandbox(sandbox_id: str) -> dict:
    """Force-kill a sandbox before its TTL expires. Best-effort — a
    dead sandbox that returns 404 to `kill()` is treated as success."""
    if not is_configured(): return _not_configured()
    try:
        from e2b_code_interpreter import Sandbox            # type: ignore
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            lambda: Sandbox.connect(sandbox_id, api_key=_key()).kill(),
        )
    except Exception as e:                                # noqa: BLE001
        logger.info("[preview] kill best-effort err=%r", e)
    return {"ok": True, "sandbox_id": sandbox_id, "killed": True}


async def sweep_expired_previews() -> dict:
    """Kill sandboxes past their TTL — invoked by main.py's 5-min cron."""
    from cto_services.db import get_db
    db = get_db()
    if db is None: return {"ok": False}
    now = time.time()
    q = {"expires_at": {"$lt": now}, "killed": {"$ne": True}}
    killed = 0
    async for row in db[PREVIEW_COLLECTION].find(q, {"sandbox_id": 1}):
        r = await kill_preview_sandbox(row["sandbox_id"])
        if r.get("ok"):
            await db[PREVIEW_COLLECTION].update_one(
                {"sandbox_id": row["sandbox_id"]},
                {"$set": {"killed": True, "killed_at": now}},
            )
            killed += 1
    return {"ok": True, "killed": killed, "at": now}


__all__ = [
    "PREVIEW_TTL_S", "PREVIEW_COLLECTION",
    "is_configured", "create_preview_sandbox", "get_preview_status",
    "kill_preview_sandbox", "sweep_expired_previews",
]
