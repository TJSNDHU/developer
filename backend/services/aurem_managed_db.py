"""
services/aurem_managed_db.py — Iter 212m-233 — Phase 4

Free-tier "shared MongoDB" backend for Personal Track apps.

WHY (from the founder's cost-analysis note):
    Supabase organisations get only 2 free active projects — beyond
    that, ~$10/month per additional project. Provisioning a
    dedicated per-user Supabase does not scale for free-tier users.

    AUREM already runs a MongoDB cluster; the same isolation pattern
    AUREM's own routers use (`{"user_id": <uid>}` filtering) extends
    cleanly to per-generated-app isolation via
    `{"app_id": <app_id>, "user_id": <uid>}`.

    Free tier → this shared collection (cost: $0 marginal).
    Paid tier → dedicated Supabase (Phase 5, deferred).

How generated apps use it
=========================
Every Personal Track project ships with an SDK helper (in the
scaffolded boilerplate) that talks to
`POST /api/aurem-dev/managed-db/{app_id}/{collection}/find` (etc.)
using the app's JWT. The API here enforces:
  • collection-level scoping via `app_id` + `user_id` — no user can
    read another app's data even by guessing app_id
  • hard per-app document count quota (default 10,000) — prevents
    one runaway free-tier app from ballooning our shared cluster
  • simple JSON-schema validation (from Phase 2's scaffolded schema)

Public API (backend-internal)
=============================
    build_scoped_filter(app_id, user_id, extra=None) -> dict
    check_quota(db, app_id) -> {"count": int, "over": bool}
    validate_against_schema(doc, schema) -> {"ok": bool, "errors": list}
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Per-app document count cap. Hit this → writes get rejected until the
# user either deletes older docs OR upgrades to paid tier (Phase 5).
DEFAULT_APP_QUOTA_DOCS = 10_000

# Collection name in AUREM's MongoDB for all shared data.
# Naming: prefixed so it never collides with AUREM's own collections.
SHARED_COLLECTION = "aurem_managed_app_data"


# ── Scoped filter helpers ────────────────────────────────────────
def build_scoped_filter(
    app_id:   str,
    user_id:  str,
    extra:    Optional[dict] = None,
) -> dict:
    """Wrap a user-supplied filter with the mandatory `app_id`+`user_id`
    scope. Applied server-side on every read/write so a crafted client
    request can't escape its own app namespace.

    Merge policy: extra keys win EXCEPT `app_id` / `user_id` which are
    always overwritten by the trusted values.
    """
    filt = dict(extra or {})
    filt["app_id"]  = app_id
    filt["user_id"] = user_id
    return filt


# ── Quota enforcement ────────────────────────────────────────────
async def check_quota(db, app_id: str) -> dict:
    """Count documents for a given app_id across ALL its users.
    Returns whether the app is over the free-tier quota."""
    count = await db[SHARED_COLLECTION].count_documents({"app_id": app_id})
    return {"count": int(count), "over": count >= DEFAULT_APP_QUOTA_DOCS,
            "limit": DEFAULT_APP_QUOTA_DOCS}


# ── Schema validation (lightweight) ─────────────────────────────
_ALLOWED_TYPES = {"string", "integer", "number", "boolean",
                  "object", "array", "null"}


def validate_against_schema(doc: dict, schema: Optional[dict]) -> dict:
    """Very small JSON-schema-ish validator that covers the fields
    the scaffolded model definitions actually emit:
      - required fields present
      - basic type checking (string / integer / boolean / number)

    Deliberately NOT a full JSON Schema implementation — we don't want
    the generated app's SDK to depend on jsonschema. Everything here is
    a stdlib-only check.
    """
    if not schema:
        return {"ok": True, "errors": []}
    errs: list[str] = []
    fields = (schema.get("properties") or {})
    required = schema.get("required") or []
    for name in required:
        if name not in doc:
            errs.append(f"missing_required_field: {name}")

    for name, spec in fields.items():
        if name not in doc:
            continue
        expected = spec.get("type")
        if expected and expected in _ALLOWED_TYPES:
            v = doc[name]
            if not _matches_type(v, expected):
                errs.append(f"type_mismatch: {name} expected {expected}")

    return {"ok": not errs, "errors": errs}


def _matches_type(v: Any, expected: str) -> bool:
    if expected == "string":  return isinstance(v, str)
    if expected == "integer": return isinstance(v, int) and not isinstance(v, bool)
    if expected == "number":  return isinstance(v, (int, float)) and not isinstance(v, bool)
    if expected == "boolean": return isinstance(v, bool)
    if expected == "object":  return isinstance(v, dict)
    if expected == "array":   return isinstance(v, list)
    if expected == "null":    return v is None
    return True


__all__ = [
    "SHARED_COLLECTION", "DEFAULT_APP_QUOTA_DOCS",
    "build_scoped_filter", "check_quota", "validate_against_schema",
    "export_app_data",
]


# ── Export routine (Phase 5 migration helper) ────────────────────
async def export_app_data(db, app_id: str, user_id: str) -> dict:
    """Export ALL documents for a given `(app_id, user_id)` scope.
    Returns a JSON-serialisable dict grouped by `_collection` so Phase 5
    (dedicated Supabase provisioning) can replay it into a real Postgres
    schema without a second scan.

    Kept intentionally minimal — the Phase-5 import side owns the
    actual schema translation. This function's contract:
        `{ "app_id", "exported_at", "counts": {coll: N}, "collections": {coll: [docs]} }`
    """
    import time as _t
    filt = {"app_id": app_id, "user_id": user_id}
    cur = db[SHARED_COLLECTION].find(filt)

    grouped: dict[str, list[dict]] = {}
    counts:  dict[str, int] = {}
    async for row in cur:
        coll = row.get("_collection") or "default"
        # Strip Mongo internals — Phase 5 import assigns new PKs anyway.
        clean = {k: v for k, v in row.items()
                 if k not in ("_id", "app_id", "user_id", "_collection")}
        # Convert ObjectId-in-value cases to strings for JSON safety.
        for k, v in list(clean.items()):
            try:
                from bson import ObjectId as _OID
                if isinstance(v, _OID):
                    clean[k] = str(v)
            except Exception:
                pass
        grouped.setdefault(coll, []).append(clean)
        counts[coll] = counts.get(coll, 0) + 1

    return {
        "app_id":      app_id,
        "user_id":     user_id,
        "exported_at": _t.time(),
        "counts":      counts,
        "collections": grouped,
    }
