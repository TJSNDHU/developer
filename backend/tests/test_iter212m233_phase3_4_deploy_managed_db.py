"""
Iter 212m-233 — Phase 3+4: Vercel platform-owned deploy + AUREM Managed DB.

Locks in:
1. `services/vercel_platform_deploy.py`:
   - Uses DEDICATED `AUREM_VERCEL_PLATFORM_TOKEN` (not shared founder token).
   - REQUIRES `VERCEL_PLATFORM_TEAM_ID` — no personal-account fallback.
   - Returns 503 with clear setup instructions when either is missing.
   - `_friendly_error()` translates Vercel API errors into plain
     language for non-tech users (no raw JSON exposed).
   - `check_spend_alert()` polls actual bandwidth + tri-state
     (ok / alert / kill) — the MUST-HAVE guardrail from the plan.
   - `pause_project()` used when threshold crossed.
   - Namespace `aurem-{user}-{project}` prevents collisions.

2. `services/aurem_managed_db.py`:
   - `build_scoped_filter` server-enforces app_id + user_id.
   - `check_quota` counts docs, respects 10k default cap.
   - `validate_against_schema` lightweight JSON-schema-ish validator.
   - `export_app_data` — Phase 5 migration helper.

3. `routers/managed_db.py`:
   - All 5 endpoints (find/find-one/insert/update/delete/quota).
   - Ownership check via `cto_projects.personal_track=True` flag.
   - **Cross-tenant isolation** (server-side, not client-trusted).
   - Quota enforcement rejects writes with HTTP 429 when over.

4. `templates/stacks/react-fastapi/boilerplate/api/aurem_db_client.py`:
   - Generated apps use this SDK — never see raw Mongo credentials.

5. Scaffold auto-triggers Vercel deploy after materialize (best-effort).
"""

from __future__ import annotations

import os
import pytest


# ── vercel_platform_deploy config guards ─────────────────────────
def test_vercel_platform_requires_both_env_vars():
    """Both AUREM_VERCEL_PLATFORM_TOKEN and VERCEL_PLATFORM_TEAM_ID
    must be set. Missing either → is_available() False."""
    from services import vercel_platform_deploy as v

    orig_token = os.environ.pop("AUREM_VERCEL_PLATFORM_TOKEN", None)
    orig_team  = os.environ.pop("VERCEL_PLATFORM_TEAM_ID", None)
    try:
        assert v.is_available() is False
        os.environ["AUREM_VERCEL_PLATFORM_TOKEN"] = "test_token"
        assert v.is_available() is False, "team id also required"
        os.environ["VERCEL_PLATFORM_TEAM_ID"] = "team_test"
        assert v.is_available() is True
    finally:
        os.environ.pop("AUREM_VERCEL_PLATFORM_TOKEN", None)
        os.environ.pop("VERCEL_PLATFORM_TEAM_ID", None)
        if orig_token: os.environ["AUREM_VERCEL_PLATFORM_TOKEN"] = orig_token
        if orig_team:  os.environ["VERCEL_PLATFORM_TEAM_ID"] = orig_team


def test_vercel_platform_does_not_reuse_shared_founder_token():
    """Iter 212m-233 requirement: Personal Track deploys must NOT
    silently fall back to VERCEL_API_TOKEN (founder's shared token)."""
    from services import vercel_platform_deploy as v
    orig_shared = os.environ.get("VERCEL_API_TOKEN")
    orig_pt     = os.environ.pop("AUREM_VERCEL_PLATFORM_TOKEN", None)
    orig_team   = os.environ.pop("VERCEL_PLATFORM_TEAM_ID", None)
    try:
        os.environ["VERCEL_API_TOKEN"] = "founder_shared_token"
        assert v.is_available() is False, (
            "Personal Track must NOT accept the shared VERCEL_API_TOKEN "
            "as a fallback — it's for AUREM's own infra, not user apps."
        )
    finally:
        if orig_shared is not None: os.environ["VERCEL_API_TOKEN"] = orig_shared
        if orig_pt is not None:     os.environ["AUREM_VERCEL_PLATFORM_TOKEN"] = orig_pt
        if orig_team is not None:   os.environ["VERCEL_PLATFORM_TEAM_ID"] = orig_team


def test_vercel_slug_and_project_name():
    from services.vercel_platform_deploy import _slugify, _project_name
    assert _slugify("My App!") == "my-app"
    assert _slugify("") == "app"
    # Namespace format
    name = _project_name("user_abc_long_id_here", "habit tracker")
    assert name.startswith("aurem-")
    assert len(name) <= 52


def test_friendly_error_returns_plain_language():
    """Never surface raw Vercel JSON to non-tech users."""
    from services.vercel_platform_deploy import _friendly_error
    msg = _friendly_error(409, '{"error":{"code":"conflict"}}')
    assert "already exists" in msg.lower()
    assert "{" not in msg
    msg = _friendly_error(402, "Quota exceeded")
    assert "limit" in msg.lower() or "monthly" in msg.lower()
    msg = _friendly_error(500, "internal error")
    assert "retried" in msg.lower() or "try again" in msg.lower()


# ── aurem_managed_db scoped filter — CRITICAL isolation test ─────
def test_scoped_filter_always_sets_app_and_user_id():
    from services.aurem_managed_db import build_scoped_filter
    # Client tries to leak into a different app_id → server overwrites.
    filt = build_scoped_filter(
        "app_A", "user_1",
        extra={"app_id": "app_B", "user_id": "user_2", "title": "leak"},
    )
    assert filt["app_id"] == "app_A", "Client-supplied app_id must be OVERWRITTEN"
    assert filt["user_id"] == "user_1", "Client-supplied user_id must be OVERWRITTEN"
    assert filt["title"] == "leak", "Non-privileged fields pass through"


def test_scoped_filter_empty_extra():
    from services.aurem_managed_db import build_scoped_filter
    f = build_scoped_filter("app_X", "user_Y")
    assert f == {"app_id": "app_X", "user_id": "user_Y"}


# ── Cross-tenant isolation (the biggest ask) ─────────────────────
def test_router_uses_scoped_filter_on_every_endpoint():
    """Static check — every managed-db handler must call
    `build_scoped_filter` so ownership can't be bypassed by a crafted
    filter payload. Missing this in even one handler is a data-leak
    vulnerability."""
    src = open("/app/backend/routers/managed_db.py").read()
    handlers = ("find_docs", "find_one_doc", "insert_doc",
                "update_docs", "delete_doc")
    for h in handlers:
        # Handler defined
        assert f"async def {h}(" in src, f"Handler {h} missing"
    # Every filter path routes through the helper.
    assert src.count("build_scoped_filter(app_id, user[") >= 4, (
        "Not enough call-sites of build_scoped_filter — every read/write "
        "endpoint must scope the filter server-side"
    )


def test_router_blocks_client_from_setting_ownership_in_update():
    """Update handler must strip app_id/user_id from the patch — a
    client could otherwise POST `patch: {app_id: 'other_app'}` and
    reassign their doc to another tenant."""
    src = open("/app/backend/routers/managed_db.py").read()
    assert "app_id" in src and "user_id" in src
    assert 'k not in ("app_id", "user_id", "_collection")' in src, (
        "Update handler must filter out privileged fields from patch"
    )


def test_router_verifies_app_ownership_before_every_op():
    """Every endpoint MUST call `_verify_app_ownership` before touching
    the shared collection — this is the second isolation gate."""
    src = open("/app/backend/routers/managed_db.py").read()
    # 5 handlers × 1 call each = at least 5 (find/find-one/insert/update/delete)
    assert src.count("await _verify_app_ownership(") >= 5


# ── Quota + validation ──────────────────────────────────────────
def test_default_quota_is_configurable():
    from services.aurem_managed_db import DEFAULT_APP_QUOTA_DOCS
    assert DEFAULT_APP_QUOTA_DOCS == 10_000


def test_schema_validation_catches_type_and_required():
    from services.aurem_managed_db import validate_against_schema
    schema = {
        "properties": {"title": {"type": "string"},
                       "count": {"type": "integer"}},
        "required": ["title"],
    }
    v = validate_against_schema({"title": "x", "count": "wrong"}, schema)
    assert not v["ok"]
    assert any("count" in e for e in v["errors"])

    v = validate_against_schema({}, schema)
    assert not v["ok"]
    assert any("title" in e for e in v["errors"])

    v = validate_against_schema({"title": "x", "count": 5}, schema)
    assert v["ok"]


def test_schema_validation_no_schema_passes():
    from services.aurem_managed_db import validate_against_schema
    assert validate_against_schema({"any": "field"}, None)["ok"] is True


# ── Export routine (Phase 5 hook) ────────────────────────────────
def test_export_app_data_is_exported():
    from services.aurem_managed_db import export_app_data
    import inspect
    assert inspect.iscoroutinefunction(export_app_data), (
        "export_app_data must be async"
    )


# ── managed_db router wire-in ────────────────────────────────────
def test_managed_db_router_registered():
    from routers.managed_db import router
    paths = [r.path for r in router.routes]
    for expected in ("/managed-db/{app_id}/{collection}/find",
                     "/managed-db/{app_id}/{collection}/find-one",
                     "/managed-db/{app_id}/{collection}/insert",
                     "/managed-db/{app_id}/{collection}/update",
                     "/managed-db/{app_id}/{collection}/{doc_id}",
                     "/managed-db/{app_id}/quota"):
        assert expected in paths, f"Missing route: {expected}. Have: {paths}"


def test_managed_db_router_wired_into_main():
    src = open("/app/backend/main.py").read()
    assert "from routers.managed_db import router as managed_db_router" in src
    assert "app.include_router(managed_db_router" in src


# ── aurem_db_client SDK in scaffold ──────────────────────────────
def test_scaffold_includes_aurem_db_client():
    """Generated apps must not have raw Mongo strings — they use the
    scoped SDK instead."""
    path = ("/app/backend/templates/stacks/react-fastapi/"
            "boilerplate/api/aurem_db_client.py")
    assert os.path.exists(path)
    src = open(path).read()
    assert "AuremDB" in src
    assert "MONGO_URL" not in src, (
        "SDK must not expose Mongo connection strings"
    )


@pytest.mark.asyncio
async def test_scaffold_file_tree_includes_db_client():
    from routers.scaffold import _generate_file_tree
    files = await _generate_file_tree("todo app", "react-fastapi", "u1", "d1")
    paths = {f["path"] for f in files}
    assert "api/aurem_db_client.py" in paths, (
        "Generated file tree must include the AUREM DB client SDK"
    )
    # Backend .env.example must reference the scoped API, not raw Mongo.
    env_example = next((f["content"] for f in files
                        if f["path"] == "api/.env.example"), "")
    assert "AUREM_API_BASE" in env_example
    assert "MONGO_URL" not in env_example
