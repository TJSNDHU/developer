"""
tests/test_ora_chat_pin_login.py — Iter 212m-248 regression

Production bug: PIN login was returning
    HTTP 503 "Founder identity not configured"
because the endpoint only trusted the `is_founder=True` DB flag,
which isn't reliably backfilled on prod Mongo. The authoritative
signal is `FOUNDER_EMAILS` env + hardcoded default in
`services.usage.founder_emails()`.

Fix (routers/ora_chat.py::pin_login):
  1. Look up any `dev_users` row whose email is in the trusted set.
  2. Fall back to the legacy `is_founder=True` flag.
  3. Backfill the flag idempotently once resolved.

These tests are STATIC — they inspect the source, not a live server,
so they run in the fast pytest suite alongside the rest of ora_chat.
"""
from __future__ import annotations

from pathlib import Path


_ROUTER_SRC = Path("/app/backend/routers/ora_chat.py").read_text()


class TestFounderResolution:
    def test_uses_founder_emails_helper(self):
        assert "founder_emails" in _ROUTER_SRC, (
            "pin_login MUST resolve via services.usage.founder_emails() so "
            "prod Mongo without a backfilled `is_founder` flag still works."
        )

    def test_falls_back_to_is_founder_flag(self):
        # The legacy code-path is still present so users who WERE
        # migrated with the flag continue to work.
        assert 'find_one(\n            {"is_founder": True}' in _ROUTER_SRC or \
               '{"is_founder": True}' in _ROUTER_SRC

    def test_backfills_is_founder_flag(self):
        # Idempotent self-heal: once we know who the founder is, we
        # keep the DB flag in sync so downstream code stays consistent.
        assert '"is_founder": True, "is_admin": True' in _ROUTER_SRC

    def test_still_refuses_when_no_founder_row_found(self):
        # NEVER auto-privilege-escalate to a random admin.
        assert 'Founder identity not configured' in _ROUTER_SRC


class TestPinRateLimitStillActive:
    """PIN rate-limit (5 wrong / hour) must NOT be affected by the fix."""

    def test_rate_limit_check_present(self):
        assert "if n_fail >= 5:" in _ROUTER_SRC
        assert '"too_many_attempts"' in _ROUTER_SRC

    def test_hmac_compare_used(self):
        # Timing-safe compare — critical for the PIN.
        assert "hmac.compare_digest(body.pin.strip(), expected)" in _ROUTER_SRC

    def test_missing_env_var_returns_503(self):
        assert '"PIN login not configured"' in _ROUTER_SRC
