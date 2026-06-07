"""
test_iter96_sentry_live.py — locks in Sentry DSN wiring + the bug fix
that moved `_sentry_filter` definition above `sentry_sdk.init()` (the
old order caused `NameError("name '_sentry_filter' is not defined")`
at module-load time, silently disabling Sentry in production).
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from dotenv import load_dotenv


ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
MAIN_PATH = Path(__file__).resolve().parents[1] / "main.py"


@pytest.fixture(autouse=True)
def _load_env():
    load_dotenv(str(ENV_PATH), override=True)
    yield


def test_sentry_dsn_configured():
    """DSN must be present and look like a real Sentry ingest URL."""
    dsn = os.environ.get("SENTRY_DSN", "")
    assert dsn, "SENTRY_DSN missing from env"
    assert dsn.startswith("https://"), f"SENTRY_DSN must be https, got {dsn[:10]!r}"
    assert ".ingest." in dsn and ".sentry.io/" in dsn, (
        f"SENTRY_DSN doesn't look like a Sentry ingest URL: {dsn[:60]}..."
    )


def test_sentry_filter_defined_before_init():
    """The `_sentry_filter` function MUST be defined BEFORE the
    `sentry_sdk.init(...)` call references it. Otherwise the init hits
    `NameError` at module load and Sentry silently never activates —
    we got bit by this on first deploy (see Iter 96 PRD entry)."""
    src = MAIN_PATH.read_text()
    filter_def_pos = src.find("def _sentry_filter(")
    init_pos = src.find("sentry_sdk.init(")
    assert filter_def_pos > 0, "_sentry_filter function not found in main.py"
    assert init_pos > 0, "sentry_sdk.init not found in main.py"
    assert filter_def_pos < init_pos, (
        f"_sentry_filter is defined at offset {filter_def_pos} but referenced "
        f"by sentry_sdk.init at offset {init_pos} — init will NameError. "
        "Move the function definition above the init block."
    )


def test_sentry_active_after_module_import():
    """When DSN is set, importing main must set SENTRY_ACTIVE=True with
    no exceptions raised during init."""
    # Force-reimport main with current env
    import importlib
    import sys
    if "main" in sys.modules:
        del sys.modules["main"]
    import main  # noqa: F401  — import is the test
    assert main.SENTRY_ACTIVE is True, (
        f"Sentry should be active when SENTRY_DSN is set "
        f"(dsn present={bool(os.environ.get('SENTRY_DSN'))}, "
        f"active={main.SENTRY_ACTIVE})"
    )


def test_sentry_test_admin_endpoint_registered():
    """Founder-only `/admin/sentry/test` endpoint must exist so the
    founder can validate Sentry from prod via curl."""
    from routers.admin import router
    paths = {r.path for r in router.routes}
    # Admin router has prefix=/admin already applied to each route path.
    assert "/admin/sentry/test" in paths, (
        f"/admin/sentry/test missing. Routes: {sorted(paths)}"
    )
