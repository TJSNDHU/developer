"""
routers/advisor_context.py — Iter 212m-209

Read-only aggregation endpoint that powers the Ask Advisor panel's
project-scoped answers.  Returns a single JSON blob per project_id
combining four live signals:

    1. `findings`         — cto_open_findings row counts by severity
    2. `council`          — latest council_health_probes row
    3. `deploy_sync`      — self /version vs prod /version comparison
    4. `quota`            — the current user's monthly token quota row

Design contract
---------------
• Pure read.  No side effects, no LLM calls, no tool loop.
• Project scoped — 404 if the caller doesn't own the project.
• EVERY field can be `None`; consumers (LLM + morning brief) must
  handle missing data by saying "yeh data abhi available nahi hai"
  and MUST NOT guess.
• Sub-second by design: 4 count/find operations, no aggregation
  pipelines.
"""

from __future__ import annotations
import os
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, Header, HTTPException

from routers.auth import current_dev
from cto_services.db import require_db

router = APIRouter(prefix="/api/aurem-dev", tags=["advisor"])

# Same PROD_ORIGIN AdminSystemHealth uses for deploy-sync detection.
PROD_ORIGIN = os.environ.get("AUREM_PROD_ORIGIN", "https://auremcto.com")
_SELF_COMMIT = None  # lazy — filled from local /version on first call


async def _self_commit() -> Optional[str]:
    """Read our own commit_sha via the version router state.

    Kept lazy + best-effort — if the version router isn't mounted (very
    early boot) we just return None and the deploy-sync card degrades
    gracefully.
    """
    global _SELF_COMMIT
    if _SELF_COMMIT is not None:
        return _SELF_COMMIT
    try:
        from routers.version import _COMMIT_SHA as _local
        _SELF_COMMIT = _local
    except Exception:
        _SELF_COMMIT = None
    return _SELF_COMMIT


@router.get("/advisor/context")
async def get_advisor_context(
    project_id: str,
    authorization: Optional[str] = Header(None),
) -> dict:
    me = await current_dev(authorization)
    user_id = me["user_id"]
    db = require_db()

    # ── Project ownership + name ────────────────────────────────
    proj = await db["cto_projects"].find_one(
        {"project_id": project_id, "user_id": user_id},
        {"_id": 0, "project_id": 1, "name": 1, "github_owner": 1, "github_repo": 1},
    )
    if not proj:
        raise HTTPException(status_code=404, detail="Project not found")
    proj_name = (
        proj.get("name")
        or (f"{proj.get('github_owner')}/{proj.get('github_repo')}"
            if proj.get("github_owner") else project_id)
    )

    # ── 1. Findings ─── project_id + user_id BOTH filtered, hard limit
    findings = {"total": 0, "p0": 0, "p1": 0, "p2": 0, "error": None}
    try:
        _filter_base = {"project_id": project_id, "user_id": user_id, "status": {"$ne": "resolved"}}
        findings["total"] = await db["cto_open_findings"].count_documents(
            _filter_base, maxTimeMS=1500, limit=5000,
        )
        for sev, key in [("P0", "p0"), ("P1", "p1"), ("P2", "p2")]:
            findings[key] = await db["cto_open_findings"].count_documents(
                {**_filter_base, "severity": sev}, maxTimeMS=1500, limit=5000,
            )
    except Exception as e:
        findings["error"] = str(e)[:80]

    # ── 2. Council A latest probe — ALLOWLIST projection ───────
    council = {"live": None, "primary_actual": None, "primary_intended": None,
               "last_probe": None, "error": None}
    try:
        row = await db["council_health_probes"].find_one(
            {"council": "A"},
            projection={"_id": 0, "live": 1, "primary_actual": 1,
                        "primary_intended": 1, "last_probe": 1, "created_at": 1},
            sort=[("_id", -1)],
            max_time_ms=1500,
        )
        if row:
            council["live"] = row.get("live")
            council["primary_actual"] = row.get("primary_actual")
            council["primary_intended"] = row.get("primary_intended")
            lp = row.get("last_probe") or row.get("created_at")
            if lp:
                council["last_probe"] = lp.isoformat() if hasattr(lp, "isoformat") else str(lp)
    except Exception as e:
        council["error"] = str(e)[:80]

    # ── 3. Deploy sync — same as before ────────────────────────
    deploy_sync = {"self_sha": None, "prod_sha": None, "in_sync": None, "error": None}
    try:
        deploy_sync["self_sha"] = await _self_commit()
        try:
            async with httpx.AsyncClient(timeout=4.0) as cx:
                r = await cx.get(f"{PROD_ORIGIN}/api/aurem-dev/version")
                if r.status_code == 200:
                    deploy_sync["prod_sha"] = (r.json() or {}).get("commit_sha")
        except Exception as _e:
            deploy_sync["error"] = f"prod fetch failed: {str(_e)[:60]}"
        if deploy_sync["self_sha"] and deploy_sync["prod_sha"]:
            if deploy_sync["self_sha"] == "unknown" or deploy_sync["prod_sha"] == "unknown":
                deploy_sync["in_sync"] = None
            else:
                deploy_sync["in_sync"] = (deploy_sync["self_sha"] == deploy_sync["prod_sha"])
    except Exception as e:
        deploy_sync["error"] = str(e)[:80]

    # ── 4. Quota — ALLOWLIST projection (no api_key, no secrets) ─
    quota = {"tokens_used": None, "tokens_limit": None, "period": None, "error": None}
    try:
        q = await db["dev_user_quota"].find_one(
            {"user_id": user_id},
            projection={"_id": 0, "tokens_used": 1, "tokens_limit": 1,
                        "monthly_limit": 1, "period": 1, "month": 1},
            max_time_ms=1500,
        )
        if q:
            quota["tokens_used"] = q.get("tokens_used")
            quota["tokens_limit"] = q.get("tokens_limit") or q.get("monthly_limit")
            quota["period"] = q.get("period") or q.get("month")
    except Exception as e:
        quota["error"] = str(e)[:80]

    return {
        "project_id":   project_id,
        "project_name": proj_name,
        "findings":     findings,
        "council":      council,
        "deploy_sync":  deploy_sync,
        "quota":        quota,
        "checked_at":   datetime.now(timezone.utc).isoformat(),
    }
