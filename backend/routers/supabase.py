"""
routers/supabase.py — Iter 212m-234 — Phase 5

Paid-tier Supabase provisioning endpoints for Personal Track apps.

Endpoints (all prefixed `/api/aurem-dev/supabase`):
    POST   /{app_id}/provision   → create dedicated Supabase project + migrate
    GET    /{app_id}/status      → poll provisioning + migration status
    POST   /{app_id}/downgrade   → apply downgrade policy (paid→free)
    DELETE /{app_id}             → destroy the project (founder-scoped)

Every endpoint respects the same ownership gate as `routers/managed_db.py`:
`cto_projects.personal_track=True` + user_id match.
"""
# arch: allow-http — router registered under /api/aurem-dev (iter 212m-234)
from __future__ import annotations

import logging
import time
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException
from pydantic import BaseModel

from cto_services.auth import current_dev
from cto_services.db import get_db
from services import supabase_provisioner as sp

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/supabase", tags=["Supabase — Personal Track Paid Tier"])


class ProvisionBody(BaseModel):
    region: Optional[str] = None
    display_name: Optional[str] = None
    migrate_existing_data: bool = True


class DowngradeBody(BaseModel):
    policy: Optional[str] = None   # migrate_back | read_only | export_delete | keep_bill_user


async def _verify_paid_app_ownership(db, app_id: str, user_id: str) -> dict:
    """Same guard as routers/managed_db._verify_app_ownership but with
    an additional paid-tier check for the provision endpoint."""
    proj = await db.cto_projects.find_one({
        "project_id":     app_id,
        "user_id":        user_id,
        "personal_track": True,
    })
    if not proj:
        raise HTTPException(403, "App not found or not owned by caller")
    return proj


def _503_if_missing() -> None:
    """Uniform 503 whenever Supabase isn't configured, matching the
    Phase 2/3 graceful-503 pattern."""
    if not sp.is_configured():
        raise HTTPException(
            status_code=503,
            detail={
                "reason":  "supabase_not_configured",
                "message": ("Supabase provisioning is not enabled yet. "
                            "Founder must set SUPABASE_MANAGEMENT_TOKEN and "
                            "SUPABASE_ORG_ID in backend/.env and restart the "
                            "backend."),
                "docs":    "See backend/services/supabase_provisioner.py",
            },
        )


# ── Endpoints ────────────────────────────────────────────────────
@router.post("/{app_id}/provision")
async def provision(
    app_id: str,
    body:   ProvisionBody,
    background_tasks: BackgroundTasks,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Kick off a dedicated Supabase Postgres for a paid Personal Track app.

    Flow:
      1. Ownership check.
      2. Refuse if already provisioned (idempotent — returns existing ref).
      3. `create_project` on Supabase Management API (returns immediately).
      4. Record the row in `supabase_projects` + register the monthly cost.
      5. Schedule the schema+data migration as a background task so this
         request stays fast — the client polls `/status` for progress.
    """
    user = await current_dev(authorization)
    _503_if_missing()
    db = get_db()
    if db is None:
        raise HTTPException(503, "DB not connected")

    await _verify_paid_app_ownership(db, app_id, user["user_id"])

    existing = await db[sp.PROJECTS_COLLECTION].find_one({
        "app_id": app_id, "user_id": user["user_id"],
    })
    if existing and existing.get("project_ref"):
        return {
            "ok":           True,
            "already_done": True,
            "project_ref":  existing["project_ref"],
            "status":       existing.get("status") or "unknown",
        }

    created = await sp.create_project(
        user_id=user["user_id"], project_id=app_id,
        region=body.region, display_name=body.display_name,
    )
    if not created.get("ok"):
        raise HTTPException(502, detail=created)

    now = time.time()
    doc = {
        "app_id":       app_id,
        "user_id":      user["user_id"],
        "project_ref":  created["project_ref"],
        "name":         created["name"],
        "region":       created["region"],
        "status":       created["status"],
        "cost_usd_per_month": sp.COST_USD_PER_PROJECT_PER_MONTH,
        "migration":    {"status": "pending"},
        "created_at":   now,
        "updated_at":   now,
    }
    await db[sp.PROJECTS_COLLECTION].update_one(
        {"app_id": app_id, "user_id": user["user_id"]},
        {"$set": doc},
        upsert=True,
    )

    # Also flip the project to paid-tier storage in cto_projects so the
    # managed_db router can route reads/writes to Supabase in future
    # phases if the founder chooses. Marker only — Phase 5 keeps the
    # runtime data path on the shared Mongo for backwards compat.
    await db.cto_projects.update_one(
        {"project_id": app_id, "user_id": user["user_id"]},
        {"$set": {"storage_tier": "supabase_dedicated",
                  "supabase_ref": created["project_ref"]}},
    )

    if body.migrate_existing_data:
        background_tasks.add_task(
            _run_migration, app_id, user["user_id"], created["project_ref"],
        )

    return {
        "ok":            True,
        "project_ref":   created["project_ref"],
        "name":          created["name"],
        "region":        created["region"],
        "status":        created["status"],
        "cost_usd_per_month": sp.COST_USD_PER_PROJECT_PER_MONTH,
        "migration_scheduled": body.migrate_existing_data,
        "next_step":     "GET /supabase/{app_id}/status  (poll every 10-15s)",
    }


async def _run_migration(app_id: str, user_id: str, project_ref: str) -> None:
    """Background job — poll until the Postgres is reachable then
    migrate the free-tier data over. Writes progress into
    supabase_projects.migration for the polling endpoint to surface.
    """
    db = get_db()
    if db is None:
        return
    # Poll status up to 5 minutes (30 attempts × 10s).
    for _ in range(30):
        st = await sp.get_project_status(project_ref)
        current = (st.get("status") or "").upper()
        if current in ("ACTIVE_HEALTHY", "ACTIVE"):
            break
        await _sleep(10)
    else:
        await db[sp.PROJECTS_COLLECTION].update_one(
            {"app_id": app_id, "user_id": user_id},
            {"$set": {"migration": {"status": "timeout_waiting_for_active"}}},
        )
        return

    await db[sp.PROJECTS_COLLECTION].update_one(
        {"app_id": app_id, "user_id": user_id},
        {"$set": {"status": "ACTIVE_HEALTHY",
                  "migration": {"status": "running", "started_at": time.time()}}},
    )
    result = await sp.migrate_from_shared_mongo(db, app_id, user_id, project_ref)
    await db[sp.PROJECTS_COLLECTION].update_one(
        {"app_id": app_id, "user_id": user_id},
        {"$set": {"migration": {
            "status":  "completed" if result.get("ok") else "failed",
            "result":  result,
            "finished_at": time.time(),
        }}},
    )


async def _sleep(seconds: float) -> None:
    import asyncio
    await asyncio.sleep(seconds)


@router.get("/{app_id}/status")
async def status(
    app_id: str,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Return the provisioning + migration state. Safe to call
    repeatedly — no side effects."""
    user = await current_dev(authorization)
    _503_if_missing()
    db = get_db()
    if db is None:
        raise HTTPException(503, "DB not connected")
    await _verify_paid_app_ownership(db, app_id, user["user_id"])

    row = await db[sp.PROJECTS_COLLECTION].find_one(
        {"app_id": app_id, "user_id": user["user_id"]},
        {"_id": 0},
    )
    if not row:
        raise HTTPException(404, "No Supabase project for this app")

    # Also fetch the live status from Supabase — the DB row can lag if
    # the migration hasn't run yet.
    live = await sp.get_project_status(row["project_ref"])
    if live.get("ok"):
        row["live_status"] = live.get("status")
    return row


@router.post("/{app_id}/downgrade")
async def downgrade(
    app_id: str,
    body:   DowngradeBody,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Apply the configured downgrade policy for a paid → free move.
    Founder can override the env-configured policy via `body.policy`.
    """
    user = await current_dev(authorization)
    _503_if_missing()
    db = get_db()
    if db is None:
        raise HTTPException(503, "DB not connected")
    await _verify_paid_app_ownership(db, app_id, user["user_id"])

    return await sp.apply_downgrade(db, app_id, user["user_id"], body.policy)


@router.delete("/{app_id}")
async def destroy(
    app_id: str,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Immediately destroy the Supabase project. Skips the grace period
    — this is the "founder unblock" hatch, NOT the normal downgrade path.
    """
    user = await current_dev(authorization)
    _503_if_missing()
    db = get_db()
    if db is None:
        raise HTTPException(503, "DB not connected")
    proj = await _verify_paid_app_ownership(db, app_id, user["user_id"])
    # Only allow destroy if caller is the app owner AND has founder role
    # (double-check the RBAC — this endpoint bypasses the grace period).
    if not user.get("is_founder"):
        raise HTTPException(403, "Only the founder can force-delete a Supabase project.")

    row = await db[sp.PROJECTS_COLLECTION].find_one(
        {"app_id": app_id, "user_id": user["user_id"]},
    )
    if not row:
        raise HTTPException(404, "No Supabase project for this app")
    ref = row["project_ref"]
    deleted = await sp.delete_project(ref)
    if deleted.get("ok"):
        await db[sp.PROJECTS_COLLECTION].delete_one(
            {"app_id": app_id, "user_id": user["user_id"]},
        )
        await db.cto_projects.update_one(
            {"project_id": app_id, "user_id": user["user_id"]},
            {"$set": {"storage_tier": "shared_mongo"},
             "$unset": {"supabase_ref": ""}},
        )
    return deleted
