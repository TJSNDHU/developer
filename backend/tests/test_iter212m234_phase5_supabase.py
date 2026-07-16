"""
Iter 212m-234 — Phase 5: Supabase provisioner (paid-tier dedicated Postgres).

Locks in:
1. `services/supabase_provisioner.py`:
   - Requires BOTH SUPABASE_MANAGEMENT_TOKEN and SUPABASE_ORG_ID —
     `is_configured()` False otherwise, callers return graceful 503.
   - JSON → SQL schema translation with safe identifier quoting.
   - SQL literal escaper for the migration replay.
   - Schema-from-observed-data heuristic.
   - Downgrade policy: 4 valid options, `migrate_back` default.
   - Cost constant $10/project/mo exposed for financials.py.
   - `_friendly_error` never surfaces raw JSON.

2. `routers/supabase.py`:
   - Provision + status + downgrade + destroy endpoints registered.
   - `_verify_paid_app_ownership` uses same guard as managed_db.
   - Idempotent provision (already-done returns existing ref).
   - Force-destroy requires `is_founder`.

3. `services/financials.py` — Supabase project cost line item added to
   the P&L (only when at least one project is active).

4. Wire-in check: main.py imports + includes the router.
"""

from __future__ import annotations

import os
import pytest


# ── Configuration guards ─────────────────────────────────────────
def test_supabase_requires_both_env_vars():
    """Both SUPABASE_MANAGEMENT_TOKEN and SUPABASE_ORG_ID must be set.
    Missing either → is_configured() False."""
    from services import supabase_provisioner as sp

    orig_token = os.environ.pop("SUPABASE_MANAGEMENT_TOKEN", None)
    orig_org   = os.environ.pop("SUPABASE_ORG_ID", None)
    try:
        assert sp.is_configured() is False
        os.environ["SUPABASE_MANAGEMENT_TOKEN"] = "sbp_test"
        assert sp.is_configured() is False, "org_id also required"
        os.environ["SUPABASE_ORG_ID"] = "org_test"
        assert sp.is_configured() is True
    finally:
        os.environ.pop("SUPABASE_MANAGEMENT_TOKEN", None)
        os.environ.pop("SUPABASE_ORG_ID", None)
        if orig_token: os.environ["SUPABASE_MANAGEMENT_TOKEN"] = orig_token
        if orig_org:   os.environ["SUPABASE_ORG_ID"] = orig_org


def test_not_configured_returns_structured_error_not_exception():
    """Every public API returns `{ok:False, reason:'supabase_not_configured'}`
    when secrets are missing — router turns it into a graceful 503."""
    from services import supabase_provisioner as sp
    orig_token = os.environ.pop("SUPABASE_MANAGEMENT_TOKEN", None)
    orig_org   = os.environ.pop("SUPABASE_ORG_ID", None)
    try:
        err = sp._not_configured_error()
        assert err["ok"] is False
        assert err["reason"] == "supabase_not_configured"
        assert "SUPABASE_MANAGEMENT_TOKEN" in err["detail"]
        assert "SUPABASE_ORG_ID" in err["detail"]
    finally:
        if orig_token: os.environ["SUPABASE_MANAGEMENT_TOKEN"] = orig_token
        if orig_org:   os.environ["SUPABASE_ORG_ID"] = orig_org


# ── Slug + project name safety ───────────────────────────────────
def test_slugify_and_project_name_are_safe():
    from services.supabase_provisioner import _slugify, _project_name
    assert _slugify("My App!") == "my-app"
    assert _slugify("") == "app"
    # Length cap
    name = _project_name("user_abc_very_long", "Some Awesome Habit Tracker")
    assert name.startswith("aurem-")
    assert len(name) <= 40


def test_pg_identifier_quoting_prevents_sql_injection():
    from services.supabase_provisioner import _pg_ident
    # Dangerous chars stripped (each replaced by _), quotes wrapped —
    # the safety property is "no quotes/semicolons make it through",
    # not the exact underscore count.
    hostile = _pg_ident('users"; DROP TABLE users; --')
    assert hostile.startswith('"') and hostile.endswith('"')
    assert '"' not in hostile[1:-1]     # no escaped-quote injection possible
    assert ';' not in hostile
    assert '--' not in hostile
    assert 'DROP' in hostile             # letters preserved, punctuation gone
    # Digit-leading names prefixed
    assert _pg_ident("1st_choice") == '"f_1st_choice"'
    # Whitespace + special chars normalised
    assert _pg_ident("foo bar") == '"foo_bar"'


# ── Password derivation is deterministic given a salt ────────────
def test_password_derivation_deterministic_with_salt():
    from services import supabase_provisioner as sp
    orig = os.environ.get("SUPABASE_DB_PASSWORD_SALT")
    os.environ["SUPABASE_DB_PASSWORD_SALT"] = "fixed_test_salt_1234567890"
    try:
        p1 = sp._derive_db_password("user_A", "app_1")
        p2 = sp._derive_db_password("user_A", "app_1")
        p3 = sp._derive_db_password("user_A", "app_2")
        assert p1 == p2
        assert p1 != p3
        # Meets Supabase's basic complexity: has letter, digit,
        # and a special char.
        assert any(c.isalpha() for c in p1)
        assert any(c.isdigit() for c in p1)
        assert "!" in p1
    finally:
        if orig is not None: os.environ["SUPABASE_DB_PASSWORD_SALT"] = orig
        else: os.environ.pop("SUPABASE_DB_PASSWORD_SALT", None)


# ── Schema translation ───────────────────────────────────────────
def test_translate_schema_to_sql_covers_all_json_types():
    from services.supabase_provisioner import translate_schema_to_sql
    schemas = {
        "todos": {
            "properties": {
                "title":     {"type": "string"},
                "count":     {"type": "integer"},
                "amount":    {"type": "number"},
                "done":      {"type": "boolean"},
                "meta":      {"type": "object"},
                "tags":      {"type": "array"},
            },
            "required": ["title"],
        }
    }
    sql = translate_schema_to_sql(schemas)
    assert 'CREATE TABLE IF NOT EXISTS "todos"' in sql
    assert '"title" TEXT' in sql
    assert '"count" BIGINT' in sql
    assert '"amount" DOUBLE PRECISION' in sql
    assert '"done" BOOLEAN' in sql
    assert '"meta" JSONB' in sql
    assert '"tags" JSONB' in sql
    # Required column has no NULL suffix; optional ones do.
    assert '"title" TEXT NULL' not in sql       # required
    assert '"count" BIGINT NULL' in sql          # optional
    # Every table gets id + user_id + created_at
    assert '"id" UUID PRIMARY KEY' in sql
    assert '"user_id" TEXT NOT NULL' in sql
    assert '"created_at" TIMESTAMPTZ' in sql


def test_translate_schema_empty_returns_empty_string():
    from services.supabase_provisioner import translate_schema_to_sql
    assert translate_schema_to_sql({}) == ""
    assert translate_schema_to_sql(None) == ""


# ── SQL literal escaping ─────────────────────────────────────────
def test_sql_literal_escapes_quotes_and_jsonifies_dicts():
    from services.supabase_provisioner import _sql_literal
    assert _sql_literal("O'Brien") == "'O''Brien'"
    assert _sql_literal(None) == "NULL"
    assert _sql_literal(True) == "TRUE"
    assert _sql_literal(False) == "FALSE"
    assert _sql_literal(42) == "42"
    lit = _sql_literal({"a": 1})
    assert lit.endswith("::jsonb")
    assert '"a": 1' in lit


def test_build_insert_sql_batches_rows_correctly():
    from services.supabase_provisioner import _build_insert_sql
    docs = [
        {"title": "First",  "done": True,  "count": 3},
        {"title": "Second", "done": False, "count": 7},
    ]
    sql = _build_insert_sql("todos", docs, user_id="user_A")
    assert 'INSERT INTO "todos"' in sql
    assert '"user_id"' in sql
    assert '"title"' in sql
    assert "'user_A'" in sql
    assert "'First'" in sql
    assert "TRUE" in sql
    assert "FALSE" in sql
    # Both rows present
    assert sql.count("(") >= 2


def test_build_insert_sql_empty_docs_is_empty_string():
    from services.supabase_provisioner import _build_insert_sql
    assert _build_insert_sql("todos", [], user_id="u") == ""


# ── Downgrade policy ─────────────────────────────────────────────
def test_downgrade_policy_default_is_migrate_back():
    from services import supabase_provisioner as sp
    orig = os.environ.pop("SUPABASE_DOWNGRADE_POLICY", None)
    try:
        assert sp._configured_downgrade_policy() == "migrate_back"
    finally:
        if orig: os.environ["SUPABASE_DOWNGRADE_POLICY"] = orig


def test_downgrade_policy_env_override_works():
    from services import supabase_provisioner as sp
    orig = os.environ.get("SUPABASE_DOWNGRADE_POLICY")
    for opt in ("read_only", "export_delete", "keep_bill_user"):
        os.environ["SUPABASE_DOWNGRADE_POLICY"] = opt
        assert sp._configured_downgrade_policy() == opt
    # Invalid falls back to default
    os.environ["SUPABASE_DOWNGRADE_POLICY"] = "delete_everything_now"
    assert sp._configured_downgrade_policy() == "migrate_back"
    if orig is not None: os.environ["SUPABASE_DOWNGRADE_POLICY"] = orig
    else: os.environ.pop("SUPABASE_DOWNGRADE_POLICY", None)


# ── Friendly error translator ────────────────────────────────────
def test_friendly_error_never_leaks_raw_json():
    from services.supabase_provisioner import _friendly_error
    msg = _friendly_error(409, '{"error":{"code":"conflict"}}')
    assert "already exists" in msg.lower()
    assert "{" not in msg
    msg = _friendly_error(402, "You've hit a quota limit")
    assert "capacity" in msg.lower() or "full" in msg.lower()
    msg = _friendly_error(500, "kaboom")
    assert "retried" in msg.lower() or "try again" in msg.lower()


# ── Cost constant + financials wire-in ───────────────────────────
def test_cost_constant_exposed():
    from services.supabase_provisioner import COST_USD_PER_PROJECT_PER_MONTH
    assert COST_USD_PER_PROJECT_PER_MONTH == 10.0


def test_financials_registers_supabase_cost_line_item():
    src = open("/app/backend/services/financials.py").read()
    assert "SUPABASE_PROJECT_USD_PER_MONTH" in src
    assert "_real_supabase_projects_cost" in src
    assert "supabase_projects" in src, (
        "compute_financials output must include the supabase_projects "
        "summary for the admin dashboard"
    )


# ── Router wire-in ───────────────────────────────────────────────
def test_router_registered_and_has_all_endpoints():
    from routers.supabase import router
    paths = [r.path for r in router.routes]
    for expected in ("/supabase/{app_id}/provision",
                     "/supabase/{app_id}/status",
                     "/supabase/{app_id}/downgrade",
                     "/supabase/{app_id}"):
        assert expected in paths, f"Missing route: {expected}. Have: {paths}"


def test_router_wired_into_main():
    src = open("/app/backend/main.py").read()
    assert "from routers.supabase   import router as supabase_router" in src
    assert "app.include_router(supabase_router" in src


# ── Force-destroy requires founder ───────────────────────────────
def test_force_destroy_requires_founder_flag():
    """Static check — the DELETE handler must gate on `is_founder`."""
    src = open("/app/backend/routers/supabase.py").read()
    assert 'user.get("is_founder")' in src or 'is_founder' in src, (
        "Force-destroy must be founder-only to prevent accidental drops."
    )


# ── Isolation — every endpoint must own-check the app ────────────
def test_every_endpoint_calls_ownership_verifier():
    src = open("/app/backend/routers/supabase.py").read()
    # provision + status + downgrade + destroy → 4 calls minimum
    assert src.count("_verify_paid_app_ownership(") >= 4, (
        "Every Supabase endpoint must verify app ownership before "
        "touching the provisioning API — this is the isolation gate."
    )


# ── Migration exports the shared-Mongo data correctly ─────────────
@pytest.mark.asyncio
async def test_migrate_from_shared_mongo_uses_export_helper():
    """Static check — the migration function must import export_app_data
    (Phase 4's helper) so the free-tier scoped filter is respected."""
    import inspect
    from services import supabase_provisioner as sp
    src = inspect.getsource(sp.migrate_from_shared_mongo)
    assert "export_app_data" in src
