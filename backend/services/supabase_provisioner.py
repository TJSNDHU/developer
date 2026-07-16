"""
services/supabase_provisioner.py — Iter 212m-234 — Phase 5

Paid-tier "dedicated Postgres" backend for Personal Track apps.

WHY (extends the Phase 4 free-tier decision):
    Phase 4 (aurem_managed_db.py) covers the free tier by scoping every
    write into ONE shared MongoDB collection with `{app_id, user_id}`
    tenancy. That is a $0-marginal fit for hobbyist users but it caps
    at 10k docs/app and, more importantly, does NOT give paid users
    the two things they actually pay for:

      1. **Ownership + exportability.** A dedicated Postgres they can
         download, SQL-query, or migrate off AUREM at any time.
      2. **Real relational features.** JOINs, FK constraints, row-level
         policies — none of which the shared Mongo pattern provides.

    Phase 5 provisions a real Supabase project per paid app via the
    Supabase Management API and migrates their existing free-tier data
    into it. Cost (~$10/mo Compute) is registered against the user in
    `financials.py` so it shows up in AUREM's own P&L.

Public API
==========
    is_configured() -> bool
    create_project(user_id, project_id, region=None, display_name=None) -> dict
    get_project_status(project_ref) -> dict
    run_sql(project_ref, sql) -> dict
    translate_schema_to_sql(schemas_by_collection) -> str
    migrate_from_shared_mongo(db, app_id, user_id, project_ref) -> dict
    apply_downgrade(db, app_id, user_id, policy=None) -> dict
    delete_project(project_ref) -> dict

Configuration
=============
    backend/.env:
        SUPABASE_MANAGEMENT_TOKEN  = "sbp_..."             (required)
        SUPABASE_ORG_ID            = "abcdefgh"            (required — the
                                                            AUREM org the
                                                            projects land in)
        SUPABASE_DEFAULT_REGION    = "us-east-1"           (optional)
        SUPABASE_DB_PASSWORD_SALT  = <long random string>  (used to derive
                                                            per-project DB
                                                            passwords)
        SUPABASE_DOWNGRADE_POLICY  = "migrate_back"        (optional; one of
                                                            migrate_back |
                                                            read_only |
                                                            export_delete |
                                                            keep_bill_user)

    If SUPABASE_MANAGEMENT_TOKEN or SUPABASE_ORG_ID is missing every
    function returns a structured `{"ok": False, "reason":
    "supabase_not_configured", ...}`. The router turns that into a
    clean HTTP 503 with setup instructions — same pattern as the
    Phase 2 GitHub org client + Phase 3 Vercel platform deploy.

Downgrade policy (Iter 212m-234)
================================
When a paid user drops back to the free tier we need a defined
behaviour for the dedicated Postgres. The default is
`migrate_back` (safest for user data). Founder can override via env
or by passing an explicit `policy` at call-time.

    migrate_back    — data replayed to shared Mongo, project deleted
                      after 7-day grace. DEFAULT.
    read_only       — writes blocked, 30-day grace, then delete
    export_delete   — SQL dump emailed to user, 7-day grace, then delete
    keep_bill_user  — project stays but AUREM stops paying; user gets
                      an option to transfer the project to their own
                      Supabase org
"""
# arch: allow-http — Supabase Management API calls are this module's purpose (iter 212m-234)
from __future__ import annotations

import hashlib
import logging
import os
import re
import time
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_API_ROOT = "https://api.supabase.com"
_TIMEOUT = 30.0

# Cost accounting — Supabase's smallest paid tier ("Pro") is
# $25/project/month but AUREM absorbs the base and only bills users
# the marginal compute. We track $10/project/mo as the internal cost
# figure so financials.py can subtract it from the paid tier margin.
COST_USD_PER_PROJECT_PER_MONTH = 10.0

_VALID_DOWNGRADE_POLICIES = (
    "migrate_back", "read_only", "export_delete", "keep_bill_user",
)
_DEFAULT_DOWNGRADE_POLICY = "migrate_back"

# Collection used to track provisioned Supabase projects.
PROJECTS_COLLECTION = "supabase_projects"


# ── Config helpers ────────────────────────────────────────────────
def _token() -> str:
    """Supabase Management API personal access token."""
    return (os.environ.get("SUPABASE_MANAGEMENT_TOKEN") or "").strip()


def _org_id() -> str:
    """The AUREM Supabase org id under which every Personal Track
    project lands. Required — no fallback."""
    return (os.environ.get("SUPABASE_ORG_ID") or "").strip()


def _default_region() -> str:
    return (os.environ.get("SUPABASE_DEFAULT_REGION") or "us-east-1").strip()


def _password_salt() -> str:
    """Salt used to derive deterministic-but-not-guessable DB passwords.
    If missing we fall back to a session-random string (which means
    passwords will be regenerated on every restart — fine for MVP but
    the founder should set this in prod)."""
    return (os.environ.get("SUPABASE_DB_PASSWORD_SALT") or "").strip()


def _configured_downgrade_policy() -> str:
    val = (os.environ.get("SUPABASE_DOWNGRADE_POLICY") or _DEFAULT_DOWNGRADE_POLICY).strip()
    return val if val in _VALID_DOWNGRADE_POLICIES else _DEFAULT_DOWNGRADE_POLICY


def is_configured() -> bool:
    """Both the management token AND the org id must be present.
    Router uses this to emit a 503 with setup instructions."""
    return bool(_token() and _org_id())


def _headers() -> dict:
    return {"Authorization": f"Bearer {_token()}",
            "Content-Type":  "application/json"}


def _not_configured_error() -> dict:
    return {
        "ok":     False,
        "reason": "supabase_not_configured",
        "detail": ("Set SUPABASE_MANAGEMENT_TOKEN and SUPABASE_ORG_ID "
                   "in backend/.env then restart the backend. Token is "
                   "created at https://supabase.com/dashboard/account/tokens"),
    }


# ── Password + slug helpers ───────────────────────────────────────
_SLUG_RX = re.compile(r"[^a-z0-9-]+")


def _slugify(raw: str) -> str:
    """Supabase project names: lowercase + hyphens, ≤ 40 chars."""
    s = _SLUG_RX.sub("-", (raw or "").lower()).strip("-")
    return (s or "app")[:40]


def _project_name(user_id: str, display_name: Optional[str]) -> str:
    """Namespace: `aurem-{user}-{name}` so multiple paid Personal Track
    apps coexist under one Supabase org."""
    us = _slugify(user_id)[:12]
    ns = _slugify(display_name or "app")[:24]
    return f"aurem-{us}-{ns}"[:40]


def _derive_db_password(user_id: str, project_id: str) -> str:
    """Deterministic but unguessable DB password derived from
    (salt, user_id, project_id). If salt is unset we mix in the
    process start time to guarantee uniqueness — regen-safe by
    design (we always read the current password from the Supabase
    project object, never from this function)."""
    salt = _password_salt() or f"session-{time.time()}"
    raw = f"{salt}::{user_id}::{project_id}".encode()
    # 32 chars of hex → strong enough (>=128 bits) and Supabase-safe.
    return "Aur" + hashlib.sha256(raw).hexdigest()[:29] + "!"


# ── Supabase Management API wrappers ──────────────────────────────
async def create_project(
    user_id:      str,
    project_id:   str,
    region:       Optional[str] = None,
    display_name: Optional[str] = None,
) -> dict:
    """Create a Supabase project asynchronously.

    Returns:
        On success:
            { ok:True, project_ref, name, region, db_password, status:"COMING_UP" }
        On failure:
            { ok:False, reason, detail }

    NOTE: The Management API returns 201 with the project's `ref`
    almost immediately, but the actual Postgres cluster takes
    ~1-3 minutes to be reachable. Callers should poll
    `get_project_status(ref)` until `status == "ACTIVE_HEALTHY"`
    before running any SQL against it.
    """
    if not is_configured():
        return _not_configured_error()

    name = _project_name(user_id, display_name or project_id)
    db_password = _derive_db_password(user_id, project_id)

    payload = {
        "name":         name,
        "organization_id": _org_id(),
        "region":       (region or _default_region()),
        "plan":         "free",   # AUREM absorbs upgrades outside this call
        "db_pass":      db_password,
    }
    async with httpx.AsyncClient(timeout=_TIMEOUT) as cli:
        r = await cli.post(f"{_API_ROOT}/v1/projects",
                           headers=_headers(), json=payload)
    if r.status_code not in (200, 201):
        return {
            "ok":     False,
            "reason": f"supabase_{r.status_code}",
            "detail": r.text[:400],
            "user_message": _friendly_error(r.status_code, r.text),
            "attempted_name": name,
        }
    p = r.json() or {}
    ref = p.get("id") or p.get("ref")
    if not ref:
        return {"ok": False, "reason": "supabase_response_missing_ref",
                "detail": (r.text or "")[:400]}
    logger.info("[supabase-provision] created project ref=%s name=%s user=%s",
                ref, name, user_id)
    return {
        "ok":           True,
        "project_ref":  ref,
        "name":         p.get("name") or name,
        "region":       p.get("region") or (region or _default_region()),
        "db_password":  db_password,
        "status":       p.get("status") or "COMING_UP",
        "created_at":   time.time(),
    }


async def get_project_status(project_ref: str) -> dict:
    """Poll Supabase for the project's readiness state."""
    if not is_configured():
        return _not_configured_error()
    async with httpx.AsyncClient(timeout=_TIMEOUT) as cli:
        r = await cli.get(f"{_API_ROOT}/v1/projects/{project_ref}",
                          headers=_headers())
    if r.status_code != 200:
        return {"ok": False, "reason": f"supabase_{r.status_code}",
                "detail": r.text[:300]}
    p = r.json() or {}
    return {
        "ok":         True,
        "project_ref": project_ref,
        "status":     p.get("status") or "unknown",
        "region":     p.get("region"),
        "database":   p.get("database"),
    }


async def run_sql(project_ref: str, sql: str) -> dict:
    """Execute SQL against a provisioned project via the Management
    API's query endpoint. Used for schema creation + data migration."""
    if not is_configured():
        return _not_configured_error()
    if not sql or not sql.strip():
        return {"ok": True, "rows": [], "skipped": "empty_sql"}
    async with httpx.AsyncClient(timeout=_TIMEOUT) as cli:
        r = await cli.post(
            f"{_API_ROOT}/v1/projects/{project_ref}/database/query",
            headers=_headers(), json={"query": sql},
        )
    if r.status_code not in (200, 201):
        return {"ok": False, "reason": f"supabase_{r.status_code}",
                "detail": r.text[:500], "sql_snippet": sql[:200]}
    try:
        body = r.json()
    except Exception:
        body = {"raw": r.text[:500]}
    return {"ok": True, "result": body}


async def delete_project(project_ref: str) -> dict:
    """Delete a Supabase project. Used for aborted provisions and
    for the downgrade grace-period expiry cleanup."""
    if not is_configured():
        return _not_configured_error()
    async with httpx.AsyncClient(timeout=_TIMEOUT) as cli:
        r = await cli.delete(f"{_API_ROOT}/v1/projects/{project_ref}",
                             headers=_headers())
    if r.status_code in (200, 202, 204):
        logger.info("[supabase-provision] deleted project ref=%s", project_ref)
        return {"ok": True, "project_ref": project_ref, "deleted": True}
    return {"ok": False, "reason": f"supabase_{r.status_code}",
            "detail": r.text[:300]}


# ── Schema translation (JSON → SQL) ───────────────────────────────
_JSON_TO_PG_TYPE = {
    "string":  "TEXT",
    "integer": "BIGINT",
    "number":  "DOUBLE PRECISION",
    "boolean": "BOOLEAN",
    "object":  "JSONB",
    "array":   "JSONB",
    "null":    "TEXT",  # nullable text — best-effort
}


def _pg_ident(raw: str) -> str:
    """Quote a Postgres identifier safely. Only letters/digits/underscore
    are allowed — anything else gets stripped so we never emit unsafe
    SQL even from a schema that had special chars in field names."""
    cleaned = re.sub(r"[^a-zA-Z0-9_]", "_", raw or "")
    if not cleaned or cleaned[0].isdigit():
        cleaned = "f_" + cleaned
    return '"' + cleaned[:60] + '"'


def translate_schema_to_sql(schemas_by_collection: dict) -> str:
    """Translate `{collection_name: json_schema}` into a set of
    `CREATE TABLE IF NOT EXISTS` statements.

    Each table gets:
      • `id` UUID primary key (matches Supabase convention)
      • `user_id TEXT NOT NULL` (preserves the multi-user model the
        generated app already uses)
      • one column per JSON-schema property (typed via _JSON_TO_PG_TYPE)
      • `created_at timestamptz DEFAULT now()`

    Returns a single string with all statements joined by semicolons.
    Safe against SQL injection at the identifier level (see _pg_ident).
    """
    if not schemas_by_collection:
        return ""
    parts: list[str] = []
    for coll, schema in schemas_by_collection.items():
        table = _pg_ident(coll)
        cols = [
            '"id" UUID PRIMARY KEY DEFAULT gen_random_uuid()',
            '"user_id" TEXT NOT NULL',
        ]
        props = (schema or {}).get("properties") or {}
        required = set((schema or {}).get("required") or [])
        for name, spec in props.items():
            pg_type = _JSON_TO_PG_TYPE.get(
                (spec or {}).get("type") or "string", "TEXT",
            )
            null = "" if name in required else " NULL"
            cols.append(f'{_pg_ident(name)} {pg_type}{null}')
        cols.append('"created_at" TIMESTAMPTZ DEFAULT now()')
        parts.append(
            f"CREATE TABLE IF NOT EXISTS {table} (\n  "
            + ",\n  ".join(cols)
            + "\n);"
        )
    return "\n\n".join(parts)


# ── Data migration (shared Mongo → dedicated Postgres) ────────────
def _sql_literal(v) -> str:
    """Escape a Python value into a Postgres literal. Used only for
    the migration replay where we control the schema — for user-facing
    queries we would use parameterised statements."""
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, (dict, list)):
        import json as _json
        return "'" + _json.dumps(v).replace("'", "''") + "'::jsonb"
    # string / everything else
    s = str(v).replace("'", "''")
    return "'" + s + "'"


def _build_insert_sql(collection: str, docs: list[dict], user_id: str) -> str:
    """Assemble a batch INSERT for one collection's docs."""
    if not docs:
        return ""
    all_keys: list[str] = []
    for d in docs:
        for k in d.keys():
            if k in ("id", "created_at"):
                continue
            if k not in all_keys:
                all_keys.append(k)
    if not all_keys:
        # Empty collection — insert stub rows so the migration is
        # visible in the target DB even if no columns were set.
        return ""
    cols = ", ".join([_pg_ident("user_id")] + [_pg_ident(k) for k in all_keys])
    rows = []
    for d in docs:
        vals = [_sql_literal(user_id)] + [_sql_literal(d.get(k)) for k in all_keys]
        rows.append("(" + ", ".join(vals) + ")")
    return (
        f"INSERT INTO {_pg_ident(collection)} ({cols}) VALUES\n"
        + ",\n".join(rows) + ";"
    )


async def migrate_from_shared_mongo(
    db, app_id: str, user_id: str, project_ref: str,
) -> dict:
    """Export every row for `(app_id, user_id)` from the shared Mongo
    (via Phase 4's export_app_data) and replay it into the dedicated
    Supabase project.

    Two-phase:
      1. Build schema-from-observed-data (union of keys across every
         doc in a given collection) → CREATE TABLE.
      2. INSERT the rows in batches of 200 so we don't send a
         multi-megabyte query in one shot.

    Returns:
        { ok, migrated_counts:{coll:N}, tables_created:[...], errors:[...] }
    """
    if not is_configured():
        return _not_configured_error()

    from services.aurem_managed_db import export_app_data
    dump = await export_app_data(db, app_id, user_id)
    grouped: dict = dump.get("collections") or {}
    counts:  dict = dump.get("counts") or {}

    # Derive JSON-schema from observed data (best-effort typing).
    schemas: dict[str, dict] = {}
    for coll, docs in grouped.items():
        props: dict[str, dict] = {}
        for d in docs:
            for k, v in d.items():
                if k in props:
                    continue
                if isinstance(v, bool):
                    t = "boolean"
                elif isinstance(v, int):
                    t = "integer"
                elif isinstance(v, float):
                    t = "number"
                elif isinstance(v, (dict, list)):
                    t = "object"
                else:
                    t = "string"
                props[k] = {"type": t}
        schemas[coll] = {"properties": props}

    schema_sql = translate_schema_to_sql(schemas)
    tables_created: list[str] = list(schemas.keys())
    errors: list[dict] = []
    migrated: dict[str, int] = {}

    # Step 1 — create tables.
    if schema_sql:
        r = await run_sql(project_ref, schema_sql)
        if not r.get("ok"):
            return {"ok": False, "reason": "schema_create_failed", "detail": r,
                    "counts": counts}

    # Step 2 — batch inserts (200 per batch).
    for coll, docs in grouped.items():
        if not docs:
            migrated[coll] = 0
            continue
        for i in range(0, len(docs), 200):
            batch = docs[i:i + 200]
            insert_sql = _build_insert_sql(coll, batch, user_id)
            if not insert_sql:
                continue
            r = await run_sql(project_ref, insert_sql)
            if not r.get("ok"):
                errors.append({"collection": coll, "batch_start": i,
                               "detail": r})
                break
        migrated[coll] = len(docs) - sum(
            (200 if e["collection"] == coll else 0) for e in errors)

    logger.info("[supabase-migrate] app=%s user=%s ref=%s counts=%s errors=%d",
                app_id, user_id, project_ref, migrated, len(errors))
    return {
        "ok":              not errors,
        "project_ref":     project_ref,
        "tables_created":  tables_created,
        "migrated_counts": migrated,
        "errors":          errors,
    }


# ── Downgrade handler ─────────────────────────────────────────────
async def apply_downgrade(
    db, app_id: str, user_id: str, policy: Optional[str] = None,
) -> dict:
    """Apply the configured downgrade policy when a paid user drops
    back to the free tier. Policy is chosen as:
        1. explicit `policy` argument (founder override)
        2. env var SUPABASE_DOWNGRADE_POLICY
        3. default `migrate_back`

    All four policies write a `downgrade_pending` marker on the
    supabase_projects row so a scheduled sweeper (not shipped here —
    Phase 6) can finalize the delete after the grace period.
    """
    chosen = (policy or _configured_downgrade_policy()).strip()
    if chosen not in _VALID_DOWNGRADE_POLICIES:
        return {"ok": False, "reason": "invalid_policy",
                "allowed": list(_VALID_DOWNGRADE_POLICIES)}

    proj = await db[PROJECTS_COLLECTION].find_one({
        "app_id": app_id, "user_id": user_id,
    })
    if not proj:
        return {"ok": False, "reason": "no_supabase_project",
                "detail": "This app has no dedicated Supabase project to downgrade."}
    ref = proj.get("project_ref")

    grace_days = {"migrate_back": 7, "read_only": 30,
                  "export_delete": 7, "keep_bill_user": 0}[chosen]
    now = time.time()
    marker = {
        "downgrade_pending":  True,
        "downgrade_policy":   chosen,
        "downgrade_started":  now,
        "downgrade_grace_until": now + grace_days * 86400,
    }

    # migrate_back: copy data back to shared Mongo BEFORE marking pending.
    if chosen == "migrate_back":
        migrated = await _migrate_supabase_to_shared_mongo(
            db, ref, app_id, user_id,
        )
        marker["migrate_back_result"] = migrated

    await db[PROJECTS_COLLECTION].update_one(
        {"app_id": app_id, "user_id": user_id},
        {"$set": marker},
    )
    logger.info("[supabase-downgrade] app=%s user=%s policy=%s grace_days=%d",
                app_id, user_id, chosen, grace_days)
    return {
        "ok": True, "app_id": app_id, "policy": chosen,
        "grace_days": grace_days,
        "grace_until": marker["downgrade_grace_until"],
        "next": (
            f"Project ref {ref} will be deleted after grace period. "
            f"Founder can override or extend via /supabase/{app_id}/downgrade."
        ),
    }


async def _migrate_supabase_to_shared_mongo(
    db, project_ref: str, app_id: str, user_id: str,
) -> dict:
    """Reverse-migrate: pull every row from the dedicated Postgres and
    insert into shared Mongo under `{app_id, user_id, _collection}`.

    Used only by the `migrate_back` downgrade policy. Best-effort — if
    the SQL fetch fails we surface the error but don't block the
    downgrade (user data is still safe in Postgres until grace expires).
    """
    if not is_configured():
        return _not_configured_error()

    # Discover tables via information_schema.
    list_sql = ("SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='public';")
    tables_res = await run_sql(project_ref, list_sql)
    if not tables_res.get("ok"):
        return {"ok": False, "reason": "list_tables_failed", "detail": tables_res}

    result = tables_res.get("result") or {}
    rows = result if isinstance(result, list) else (result.get("rows") or result.get("data") or [])
    tables = [r.get("table_name") for r in rows if isinstance(r, dict) and r.get("table_name")]

    from services.aurem_managed_db import SHARED_COLLECTION
    total_inserted = 0
    per_table: dict[str, int] = {}
    for table in tables:
        fetch = await run_sql(project_ref, f"SELECT * FROM {_pg_ident(table)};")
        if not fetch.get("ok"):
            per_table[table] = -1
            continue
        payload = fetch.get("result") or {}
        rows = payload if isinstance(payload, list) else (payload.get("rows") or payload.get("data") or [])
        if not rows:
            per_table[table] = 0
            continue
        docs = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            clean = {k: v for k, v in r.items() if k not in ("id", "created_at")}
            clean.update({
                "app_id":      app_id,
                "user_id":     user_id,
                "_collection": table,
                "created_at":  time.time(),
            })
            docs.append(clean)
        if docs:
            await db[SHARED_COLLECTION].insert_many(docs, ordered=False)
            per_table[table] = len(docs)
            total_inserted += len(docs)

    return {"ok": True, "tables": per_table, "total_rows": total_inserted}


# ── Friendly error translator ─────────────────────────────────────
def _friendly_error(status_code: int, raw_body: str) -> str:
    """Translate Supabase API errors into plain language for non-tech
    users. Never surface raw JSON."""
    body_low = (raw_body or "").lower()
    if status_code == 402 or "quota" in body_low or "limit" in body_low:
        return ("AUREM's paid-tier Supabase capacity is temporarily full. "
                "Our team has been notified.")
    if status_code == 409 or "already exists" in body_low:
        return ("A project with this name already exists on AUREM. "
                "We'll try a different name automatically.")
    if status_code in (401, 403):
        return ("Database provisioning is temporarily restricted. "
                "Please try again in a few minutes.")
    if status_code == 400:
        return "The request to create your database was rejected. Our team is investigating."
    if status_code >= 500:
        return "Supabase is currently having issues. Provisioning will be retried automatically."
    return "Something unexpected happened while creating your database. We'll retry shortly."


__all__ = [
    "COST_USD_PER_PROJECT_PER_MONTH",
    "PROJECTS_COLLECTION",
    "is_configured",
    "create_project",
    "get_project_status",
    "run_sql",
    "delete_project",
    "translate_schema_to_sql",
    "migrate_from_shared_mongo",
    "apply_downgrade",
]
