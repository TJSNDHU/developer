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
