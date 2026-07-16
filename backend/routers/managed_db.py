"""
routers/managed_db.py — Iter 212m-233 — Phase 4

REST API that Personal Track generated apps talk to when they want to
persist data. Sits in front of AUREM's shared MongoDB with strict
`app_id` + `user_id` scoping so cross-app leaks are impossible.

The scaffolded app's own bcrypt+JWT auth (from Phase 2's `api/auth.py`)
mints tokens with `user_id`. This router accepts those tokens on
behalf of the generated app by re-using AUREM's own `current_dev`
(JWT decode) — Phase 4 assumes AUREM and the generated apps share the
same JWT signing secret at scaffold time. Isolation between apps is
enforced via `app_id`, which is stamped on every document.

Endpoints (all prefixed `/api/aurem-dev/managed-db`):
    POST   /{app_id}/{collection}/find       — filter → list of docs
    POST   /{app_id}/{collection}/find-one   — filter → single doc
    POST   /{app_id}/{collection}/insert     — payload → inserted doc
    PATCH  /{app_id}/{collection}/update     — filter+patch → count
    DELETE /{app_id}/{collection}/{doc_id}   — cascade-safe delete
    GET    /{app_id}/quota                   — current usage

Every operation:
  • Verifies the caller owns `app_id` (via `cto_projects.personal_track`
    lookup keyed on user_id).
  • Wraps the client filter with the mandatory scope filter.
  • Rejects writes when the app is over quota.
"""
# arch: allow-http — router registered under /api/aurem-dev (iter 212m-233)
from __future__ import annotations

import logging
import time
from typing import Any, Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel, Field

from cto_services.auth import current_dev
from cto_services.db import get_db
from services.aurem_managed_db import (
    SHARED_COLLECTION,
    build_scoped_filter,
    check_quota,
    validate_against_schema,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/managed-db", tags=["Managed DB — Personal Track"])


class FindBody(BaseModel):
    filter: dict = Field(default_factory=dict)
    limit:  int  = Field(default=50, ge=1, le=500)
    sort:   Optional[list[list]] = None  # [["field", -1], ...]


class InsertBody(BaseModel):
    data:   dict = Field(default_factory=dict)
    schema: Optional[dict] = None


class UpdateBody(BaseModel):
    filter: dict = Field(default_factory=dict)
    patch:  dict = Field(default_factory=dict)


# ── Ownership check ─────────────────────────────────────────────
async def _verify_app_ownership(db, app_id: str, user_id: str) -> dict:
    """Confirm the caller owns this Personal Track app via cto_projects.
    Returns the project doc on success, raises 403 otherwise."""
    proj = await db.cto_projects.find_one({
        "project_id":       app_id,
        "user_id":          user_id,
        "personal_track":   True,
    })
    if not proj:
        raise HTTPException(403, "App not found or not owned by caller")
    return proj


def _sanitize_doc(doc: dict) -> dict:
    """Strip Mongo internals + rename `_id` to `id` for the response."""
    out = {k: v for k, v in doc.items() if k != "_id"}
    if "_id" in doc:
        out["id"] = str(doc["_id"])
    return out


# ── Endpoints ───────────────────────────────────────────────────
@router.post("/{app_id}/{collection}/find")
async def find_docs(
    app_id: str,
    collection: str,
    body: FindBody,
    authorization: Optional[str] = Header(None),
) -> dict:
    user = await current_dev(authorization)
    db = get_db()
    if db is None:
        raise HTTPException(503, "DB not connected")
    await _verify_app_ownership(db, app_id, user["user_id"])

    filt = build_scoped_filter(app_id, user["user_id"], body.filter)
    filt["_collection"] = collection    # namespaced by user-chosen collection

    cur = db[SHARED_COLLECTION].find(filt).limit(body.limit)
    if body.sort:
        cur = cur.sort([(s[0], int(s[1])) for s in body.sort])

    return {"docs": [_sanitize_doc(d) async for d in cur]}


@router.post("/{app_id}/{collection}/find-one")
async def find_one_doc(
    app_id: str,
    collection: str,
    body: FindBody,
    authorization: Optional[str] = Header(None),
) -> dict:
    user = await current_dev(authorization)
    db = get_db()
    if db is None:
        raise HTTPException(503, "DB not connected")
    await _verify_app_ownership(db, app_id, user["user_id"])

    filt = build_scoped_filter(app_id, user["user_id"], body.filter)
    filt["_collection"] = collection
    doc = await db[SHARED_COLLECTION].find_one(filt)
    return {"doc": _sanitize_doc(doc) if doc else None}


@router.post("/{app_id}/{collection}/insert")
async def insert_doc(
    app_id: str,
    collection: str,
    body: InsertBody,
    authorization: Optional[str] = Header(None),
) -> dict:
    user = await current_dev(authorization)
    db = get_db()
    if db is None:
        raise HTTPException(503, "DB not connected")
    await _verify_app_ownership(db, app_id, user["user_id"])

    # Quota check.
    q = await check_quota(db, app_id)
    if q["over"]:
        raise HTTPException(
            status_code=429,
            detail={"reason": "app_over_quota",
                    "count": q["count"], "limit": q["limit"],
                    "upgrade_path": "Phase 5 — dedicated Supabase (paid tier)"},
        )

    # Schema validation (optional — the generated app's SDK passes
    # the schema when it defined one at scaffold time).
    if body.schema:
        v = validate_against_schema(body.data, body.schema)
        if not v["ok"]:
            raise HTTPException(400,
                {"reason": "schema_violation", "errors": v["errors"]})

    doc = {
        **body.data,
        "app_id":       app_id,
        "user_id":      user["user_id"],
        "_collection":  collection,
        "created_at":   time.time(),
    }
    res = await db[SHARED_COLLECTION].insert_one(doc)
    return {"id": str(res.inserted_id), "ok": True}


@router.patch("/{app_id}/{collection}/update")
async def update_docs(
    app_id: str,
    collection: str,
    body: UpdateBody,
    authorization: Optional[str] = Header(None),
) -> dict:
    user = await current_dev(authorization)
    db = get_db()
    if db is None:
        raise HTTPException(503, "DB not connected")
    await _verify_app_ownership(db, app_id, user["user_id"])

    filt = build_scoped_filter(app_id, user["user_id"], body.filter)
    filt["_collection"] = collection
    # Prevent client from `$set`-ing the ownership fields.
    patch = {k: v for k, v in (body.patch or {}).items()
             if k not in ("app_id", "user_id", "_collection")}
    if not patch:
        return {"matched": 0, "modified": 0}
    res = await db[SHARED_COLLECTION].update_many(filt, {"$set": patch})
    return {"matched": res.matched_count, "modified": res.modified_count}


@router.delete("/{app_id}/{collection}/{doc_id}")
async def delete_doc(
    app_id: str,
    collection: str,
    doc_id: str,
    authorization: Optional[str] = Header(None),
) -> dict:
    user = await current_dev(authorization)
    db = get_db()
    if db is None:
        raise HTTPException(503, "DB not connected")
    await _verify_app_ownership(db, app_id, user["user_id"])

    try:
        oid = ObjectId(doc_id)
    except Exception:
        raise HTTPException(400, "Invalid doc id")
    filt = build_scoped_filter(app_id, user["user_id"], {"_id": oid})
    filt["_collection"] = collection
    res = await db[SHARED_COLLECTION].delete_one(filt)
    return {"deleted": res.deleted_count}


@router.get("/{app_id}/quota")
async def get_app_quota(
    app_id: str,
    authorization: Optional[str] = Header(None),
) -> dict:
    user = await current_dev(authorization)
    db = get_db()
    if db is None:
        raise HTTPException(503, "DB not connected")
    await _verify_app_ownership(db, app_id, user["user_id"])
    q = await check_quota(db, app_id)
    return q
