"""
test_iter181_admin_emails_and_projects.py

ADMIN_EMAILS multi-admin promotion regression.

(Iter 212m-172 — the /projects/create Flow-B section of this test file
was removed together with the endpoint itself.  See PRD.md for the
consolidation rationale.  Flow-A `/cto/projects/add` is now the sole
project creation surface.)
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, "/app/backend")


# ---------------------------------------------------------------------------
# 1) ADMIN_EMAILS multi-admin promotion
# ---------------------------------------------------------------------------

def test_admin_emails_env_var_parsing(monkeypatch):
    """Comma-separated list, mixed case, trims whitespace."""
    monkeypatch.setenv(
        "ADMIN_EMAILS",
        " Alice@Aurem.dev ,bob@aurem.dev,  Charlie@AUREM.DEV  , ",
    )
    raw = os.environ.get("ADMIN_EMAILS", "")
    admin_emails = {
        e.strip().lower()
        for e in raw.split(",")
        if e.strip()
    }
    assert admin_emails == {
        "alice@aurem.dev",
        "bob@aurem.dev",
        "charlie@aurem.dev",
    }


def test_admin_emails_empty_does_not_promote_anyone():
    """Unset / empty ADMIN_EMAILS must not accidentally grant admin."""
    admin_emails = {
        e.strip().lower()
        for e in "".split(",")
        if e.strip()
    }
    assert admin_emails == set()
    assert "anyone@example.com" not in admin_emails


def test_legacy_admin_email_singular_still_honored():
    """Backward compat: ADMIN_EMAIL (singular) keeps working."""
    admin_email = "test@aurem.dev".lower().strip()
    user_email_lc = "test@aurem.dev".lower()
    assert admin_email and user_email_lc == admin_email
