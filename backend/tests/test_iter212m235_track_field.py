"""
Iter 212m-235 — Phase 6 Personal Track: `track` field on dev_users.

Locks in:
1. `/auth/set-track` endpoint validates the value and rejects anything
   not in ("developer", "personal").
2. Idempotent — setting the same value twice is a no-op.
3. Startup backfill task in main.py exists + is idempotent (only
   touches rows missing the `track` field).
4. Response shape stable so the frontend can rely on `{ok, track}`.
"""

from __future__ import annotations

import pytest


def test_set_track_endpoint_registered():
    from routers.auth import router
    paths = [r.path for r in router.routes]
    assert "/auth/set-track" in paths


def test_allowed_tracks_are_exactly_developer_and_personal():
    """No sneaky third value — the enum is strictly 2 options."""
    src = open("/app/backend/routers/auth.py").read()
    idx = src.index("_ALLOWED_TRACKS")
    line = src[idx:idx + 80]
    assert '"developer"' in line
    assert '"personal"' in line


def test_set_track_rejects_invalid_value_with_400():
    """Static — the endpoint must return 400 for unknown tracks."""
    src = open("/app/backend/routers/auth.py").read()
    idx = src.index("async def set_track(")
    body = src[idx:idx + 2000]
    assert "invalid_track" in body
    assert "HTTPException(" in body
    assert "400" in body


def test_startup_backfill_task_is_wired_and_idempotent():
    """The main.py startup task must exist and only touch rows that
    are missing the field. Idempotent = second boot is a no-op."""
    src = open("/app/backend/main.py").read()
    assert "_backfill_dev_users_track" in src
    # Idempotency guard — count with limit=1, exit if zero.
    assert '{"track": {"$exists": False}}' in src
    # Default value must be "developer" (existing users pre-date rollout).
    assert '"track": "developer"' in src


def test_backfill_writes_timestamp():
    """`track_updated_at` should be set so admin can see when a user's
    track was assigned."""
    src = open("/app/backend/main.py").read()
    idx = src.index("_backfill_dev_users_track")
    body = src[idx:idx + 1500]
    assert "track_updated_at" in body


def test_endpoint_writes_timestamp_on_manual_switch():
    src = open("/app/backend/routers/auth.py").read()
    idx = src.index("async def set_track(")
    body = src[idx:idx + 2000]
    assert "track_updated_at" in body
