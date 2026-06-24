"""Iter 212m-16 — Production-audit fixes (Feb 2026).

Three issues found while auditing the live admin panel at auremcto.com
against the founder account:

  1. SECURITY — `/admin/users` and `/admin/users/{user_id}` were leaking
     bcrypt password hashes. The projection excluded `password_hash`
     (which doesn't exist in `dev_users`) but the real field is
     `password` (set in `routers/auth.py::signup`). Even though hashes
     are bcrypt, exposing them via an API is unacceptable.

  2. `/admin/integrations/refresh` returned `None` (no body) — the admin
     UI had to follow up with a GET to /integrations/health to read the
     fresh snapshot. Now returns the full snap dict on success.

  3. The daily 06:00 UTC integration health cron was marking 7/11
     probes as broken with the message "Probe timed out after 12.0s",
     even though the same probes return ok with 2-4s latency on manual
     refresh. The cause was event-loop contention under parallel
     `asyncio.gather` plus a too-tight 12s ceiling. Bumped PROBE_TIMEOUT
     to 20s.
"""
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def test_admin_list_users_projects_password_field():
    src = (BACKEND / "routers" / "admin.py").read_text(encoding="utf-8")
    # find the `list_users` endpoint and assert its projection includes
    # the actual `password` field (not just the phantom `password_hash`).
    idx = src.find("@router.get(\"/users\")")
    end = src.find("@router", idx + 10)
    body = src[idx:end]
    assert '"password": 0' in body, (
        "/admin/users must project out the real `password` field "
        "(legacy `password_hash` projection was a no-op)"
    )


def test_admin_get_user_projects_password_field():
    src = (BACKEND / "routers" / "admin.py").read_text(encoding="utf-8")
    idx = src.find("@router.get(\"/users/{user_id}\")")
    end = src.find("@router", idx + 10)
    body = src[idx:end]
    assert '"password": 0' in body, (
        "/admin/users/{user_id} must project out the real `password` "
        "field"
    )


def test_admin_dashboard_projects_password_field():
    src = (BACKEND / "routers" / "admin.py").read_text(encoding="utf-8")
    idx = src.find("@router.get(\"/dashboard\")")
    end = src.find("@router", idx + 10)
    body = src[idx:end]
    # Dashboard returns a `recent_users` list; same projection bug
    # was present there.
    assert '"password": 0' in body, (
        "/admin/dashboard's recent_users must project out the real "
        "`password` field"
    )


def test_integrations_refresh_returns_snap():
    src = (BACKEND / "routers" / "admin.py").read_text(encoding="utf-8")
    # find the refresh endpoint
    idx = src.find("@router.post(\"/integrations/refresh\")")
    end = src.find("@router", idx + 10)
    body = src[idx:end]
    # The handler must `return snap` after the upsert, not fall off
    # the end (which would default to None).
    assert "return snap" in body, (
        "/admin/integrations/refresh must return the fresh snapshot so "
        "the admin UI can render it without a second roundtrip"
    )


def test_probe_timeout_bumped_to_20s():
    src = (BACKEND / "services" / "integration_health.py").read_text(
        encoding="utf-8"
    )
    # The old 12s ceiling was firing on the daily cron under
    # event-loop contention. 20s gives the parallel gather more
    # headroom on cold DNS / TLS hosts.
    assert "PROBE_TIMEOUT = 20.0" in src
    assert "PROBE_TIMEOUT = 12.0" not in src
