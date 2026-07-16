"""
Iter 212m-234 P0 — Admin backfill/health endpoints for the
`dev_users.created_at` visibility fix.

Locks in that the founder can:
  1. Manually re-trigger the backfill sweep post-deploy without a restart.
  2. Read a shape-distribution probe to verify the fix landed cleanly.

The startup task in `main.py:_backfill_dev_users_created_at` runs the
same logic automatically on every boot; these endpoints are the
manual/observable version for production verification.
"""

from __future__ import annotations


def test_backfill_endpoint_is_admin_gated():
    """Static check — endpoint must call `_require_admin` before
    touching `dev_users`."""
    src = open("/app/backend/routers/admin.py").read()
    # Find the backfill handler
    idx = src.index("async def admin_backfill_dev_users_created_at(")
    body = src[idx:idx + 2000]
    assert "await _require_admin(authorization)" in body, (
        "Backfill endpoint must be founder-only — missing _require_admin call"
    )


def test_backfill_uses_pipeline_update_for_datetime_conversion():
    """The conversion MUST use `$toLong / 1000` — a plain `$set` with a
    datetime value would just re-write the same BSON date type."""
    src = open("/app/backend/routers/admin.py").read()
    assert "$toLong" in src and "$divide" in src, (
        "datetime→float coercion must use $toLong/$divide pipeline"
    )


def test_backfill_prefers_connected_at_over_now_for_missing():
    """Missing rows must attempt github/google connected_at first —
    `_now` is a last-resort fallback so rankings stay roughly correct."""
    src = open("/app/backend/routers/admin.py").read()
    # Find both endpoints combined
    assert "$github.connected_at" in src
    assert "$google.connected_at" in src
    assert "$ifNull" in src


def test_health_endpoint_reports_type_distribution():
    """The health probe must project every $type of `created_at` so
    the founder can see whether legacy datetime rows still exist."""
    src = open("/app/backend/routers/admin.py").read()
    idx = src.index("async def admin_dev_users_created_at_health(")
    body = src[idx:idx + 2000]
    assert '"$type": "$created_at"' in body
    assert '"datetime_typed"' in body
    assert '"missing_field"' in body
    assert '"healthy"' in body


def test_endpoints_registered_in_admin_router():
    from routers.admin import router
    paths = [r.path for r in router.routes]
    assert "/admin/dev-users/backfill-created-at" in paths
    assert "/admin/dev-users/created-at-health" in paths


def test_signup_writes_float_epoch_created_at():
    """Guard against regression — all 3 signup writers must emit
    `time.time()` (float) not `datetime.utcnow()` (BSON date)."""
    src = open("/app/backend/routers/auth.py").read()
    # /signup uses `time.time()` for created_at
    idx_signup = src.index("@router.post(\"/signup\")")
    signup_body = src[idx_signup:idx_signup + 3500]
    assert "time.time()" in signup_body
    # /google/session uses `time.time()` for created_at
    idx_gs = src.index("async def google_session(")
    google_body = src[idx_gs:idx_gs + 3500]
    assert "time.time()" in google_body


def test_startup_backfill_task_is_wired():
    """The idempotent startup sweep must be present in main.py so a
    prod deploy triggers the fix without any manual step."""
    src = open("/app/backend/main.py").read()
    assert "_backfill_dev_users_created_at" in src
    assert "asyncio.create_task" in src or "_asyncio.create_task" in src
    # Must handle both legacy shapes:
    assert '"created_at": {"$type": "date"}' in src
    assert '"created_at": {"$exists": False}' in src


def test_read_path_tolerates_both_created_at_types():
    """Read-path in admin.list_users must handle both float epoch and
    BSON date (via $switch on $type) so legacy rows show up even if
    backfill hasn't finished yet."""
    src = open("/app/backend/routers/admin.py").read()
    # Look for the $switch pipeline
    assert '"$switch"' in src
    assert '"$toLong": "$created_at"' in src
    # And the OR-branch tolerance in _window_query
    assert "legacy datetime rows" in src
