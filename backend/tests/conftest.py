"""
backend/tests/conftest.py

Loads /app/backend/.env into the test process before any test imports.

Why this exists: tests that talk to Mongo / Redis / external services
read connection strings from `os.environ`. The backend server (run by
supervisor) gets these via the supervisor environment; pytest does
not. Without this file, tests like `test_token_enforcement.py` fail
with `KeyError: 'MONGO_URL'` even though the backend itself works
fine.

Placed at the `tests/` directory level so it applies to every test
file but does not affect runtime imports of the app.
"""

import os
from pathlib import Path

# Load /app/backend/.env into the current process. We do this manually
# (no dotenv dep needed) so it works even on a stripped CI image.
_env_file = Path(__file__).resolve().parents[1] / ".env"
if _env_file.is_file():
    for line in _env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip()
        # Don't override anything already set (CI / supervisor wins).
        if k and k not in os.environ:
            # Strip surrounding quotes if any
            if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
                v = v[1:-1]
            os.environ[k] = v


# ── Iter 345 — legacy quarantine (founder ruling 2026-07-29) ─────────
# The 259 pre-existing failures (238 FAILED + 21 ERRORS across ~114
# iter36–iter267-era files) are DEFERRED, not fixed and not deleted.
# Exact nodeids live in tests/legacy_quarantine.txt; this hook tags
# each with @pytest.mark.legacy at collection time so no test file
# needs editing and the list stays reviewable in one place.
import pytest as _pytest

_LEGACY_LIST = Path(__file__).parent / "legacy_quarantine.txt"


def _load_legacy_nodeids():
    try:
        return {
            ln.strip() for ln in _LEGACY_LIST.read_text().splitlines()
            if ln.strip() and not ln.strip().startswith("#")
        }
    except FileNotFoundError:
        return set()


def pytest_collection_modifyitems(config, items):
    legacy = _load_legacy_nodeids()
    if not legacy:
        return
    for item in items:
        if item.nodeid in legacy:
            item.add_marker(_pytest.mark.legacy)
