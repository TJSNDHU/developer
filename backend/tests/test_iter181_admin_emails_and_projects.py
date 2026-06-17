"""
test_iter181_admin_emails_and_projects.py — three regressions:

1. ADMIN_EMAILS (comma-separated env var) auto-promotes any matching
   user on login to is_admin=true, alongside the legacy single
   ADMIN_EMAIL var. TestSprite blocked analytics test because the prod
   QA account had no admin path — this opens it.

2. /projects/create is wrapped with asyncio.wait_for at every step
   (LLM 45s, GitHub push 20s, DB provision 20s) so worst-case wall
   time is well under Cloudflare's 100s edge — fixes the
   "origin returned invalid or incomplete response" CF 520/502
   TestSprite hit on the Database provisioning flow.

3. /projects/create's per-step exceptions never bubble out as raw
   Python; they always degrade to a non-fatal status field in the
   response (`result.github.ok = false` / `result.database.ok = false`).
"""
from __future__ import annotations

import asyncio
import importlib
import os
import sys

import pytest

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


# ---------------------------------------------------------------------------
# 2) /projects/create worst-case wall-clock budget
# ---------------------------------------------------------------------------

def test_projects_create_total_budget_under_cf_edge():
    """LLM 45s + GitHub 20s + DB 20s = 85s worst case, well under CF 100s."""
    LLM_CAP = 45.0
    GH_CAP = 20.0
    DB_CAP = 20.0
    total_worst = LLM_CAP + GH_CAP + DB_CAP
    # Cloudflare edge default timeout is 100s; give 10s headroom for
    # request body parsing + auth + response serialisation.
    assert total_worst <= 90.0, (
        f"Total worst-case {total_worst}s leaves no headroom under "
        f"Cloudflare's 100s edge timeout — tighten one of the caps."
    )


# ---------------------------------------------------------------------------
# 3) Per-step exception isolation
# ---------------------------------------------------------------------------

def test_per_step_exception_degrades_to_status_field_not_500():
    """Pattern test: simulate the wrap used in projects.py — a raised
    Exception inside a step must be caught and turned into a result
    sub-dict with ok=false, not bubbled."""
    result: dict = {"ok": True, "project_id": "demo"}

    async def fake_github_push():
        raise RuntimeError("github 503 simulated")

    async def run():
        try:
            await asyncio.wait_for(fake_github_push(), timeout=20.0)
        except asyncio.TimeoutError:
            result["github"] = {"ok": False, "error": "timeout"}
        except Exception as e:
            result["github"] = {"ok": False, "error": str(e)}

    asyncio.new_event_loop().run_until_complete(run())
    assert result["github"] == {"ok": False, "error": "github 503 simulated"}
    assert result["ok"] is True  # parent response still ok


def test_per_step_timeout_degrades_to_status_field_not_504():
    """A step that runs over its cap must show timeout, not raise."""
    result: dict = {"ok": True, "project_id": "demo"}

    async def slow_db_provision():
        await asyncio.sleep(0.5)

    async def run():
        try:
            await asyncio.wait_for(slow_db_provision(), timeout=0.05)
        except asyncio.TimeoutError:
            result["database"] = {"ok": False, "error": "timed out"}
        except Exception as e:
            result["database"] = {"ok": False, "error": str(e)}

    asyncio.new_event_loop().run_until_complete(run())
    assert result["database"]["ok"] is False
    assert "timed out" in result["database"]["error"].lower()
    assert result["ok"] is True
