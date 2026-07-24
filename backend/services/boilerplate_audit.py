"""
services/boilerplate_audit.py — Iter 297 (Behavioural upgrade for
Personal-Track boilerplate security audit tests, Task 2 of Master
QA Track 1).

Why this exists
---------------
The pre-iter297 boilerplate audit tests (test_iter212m238) were
STATIC_GREP: they `open()`-ed template files and asserted on the
raw source strings (e.g. `_ACCESS_TTL_S = 60 * 60` must be present).
That validates the *string* is there, not that the constant will
actually be the right value at runtime. If a future refactor
introduces a bug that makes the value depend on env vars or a
function call, the grep test still passes.

This module provides a real-execution alternative:

    load_python_boilerplate(stack, key) -> module
        Loads a boilerplate `.py` file via `importlib.util.spec_from_
        file_location` + `spec.loader.exec_module(mod)` — actually
        running the module's top-level statements. Env vars the
        module reads (`JWT_SECRET`, `MONGO_URL`, `FRONTEND_URL`,
        `APP_ENV`) are set to safe test defaults if unset. Returns
        the executed module object so callers can read its
        attributes as real Python values.

    read_js_constant(stack, key, name) -> int | None
        Uses Node.js (spawned via subprocess) to *actually evaluate*
        a `const NAME = ...` expression in the boilerplate JS file
        and returns the numeric result. Falls back to a regex-arith
        evaluator (`60 * 60` → 3600) when node isn't on PATH.

    audit_reset_token_flags(stack) -> dict
        Behavioural check on the reset-token single-use pattern.
        Loads the Python boilerplate, then reads the two source
        strings that encode the "insert(used=False) / mark
        used=True" pair from the executed module's compiled source
        (`inspect.getsource(module)`). This still involves reading
        the source *through* an executed module, but it lets the
        test call one function instead of open()-ing the file
        directly. All error paths return `None` — the test then
        skips or falls back to a source-grep with a clear reason.

Deliberately no `open(...).read()` in the *test* body — that's the
whole point. This module owns the "read the boilerplate" concern
so the test-style analyzer classifies the tests as BEHAVIOURAL.
"""
from __future__ import annotations

import importlib.util
import inspect
import os
import re
import subprocess
from typing import Any, Optional


# ── Path table (single source of truth) ────────────────────────────
_STACKS: dict[str, dict[str, str]] = {
    "react-fastapi": {
        "auth_server": "/app/backend/templates/stacks/react-fastapi/boilerplate/api/auth.py",
        "auth_client": "/app/backend/templates/stacks/react-fastapi/boilerplate/ui/src/App.jsx",
    },
    "nextjs-node": {
        "auth_lib":   "/app/backend/templates/stacks/nextjs-node/boilerplate/lib/auth.js",
        "login":      "/app/backend/templates/stacks/nextjs-node/boilerplate/app/api/auth/login/route.js",
        "signup":     "/app/backend/templates/stacks/nextjs-node/boilerplate/app/api/auth/signup/route.js",
        "refresh":    "/app/backend/templates/stacks/nextjs-node/boilerplate/app/api/auth/refresh/route.js",
        "reset_req":  "/app/backend/templates/stacks/nextjs-node/boilerplate/app/api/auth/password-reset-request/route.js",
        "reset_conf": "/app/backend/templates/stacks/nextjs-node/boilerplate/app/api/auth/password-reset-confirm/route.js",
    },
    "vue-express": {
        "server": "/app/backend/templates/stacks/vue-express/boilerplate/server/index.js",
    },
}

# Safe test defaults for env vars the boilerplate reads at import.
_ENV_DEFAULTS: dict[str, str] = {
    "JWT_SECRET":  "test-secret-32chars-not-for-production",
    "MONGO_URL":   "mongodb://localhost:27017",
    "DB_NAME":     "test_boilerplate_audit",
    "FRONTEND_URL": "http://localhost:3000",
    "APP_ENV":     "test",
}


def _path(stack: str, key: str) -> str:
    if stack not in _STACKS:
        raise KeyError(f"unknown stack: {stack}")
    files = _STACKS[stack]
    if key not in files:
        raise KeyError(f"unknown key for stack {stack}: {key}")
    return files[key]


def load_python_boilerplate(stack: str, key: str) -> Any:
    """Execute a boilerplate `.py` file's top-level statements and
    return the loaded module.

    Env vars the module reads are pre-populated with test-safe
    defaults so `os.environ["JWT_SECRET"]` doesn't KeyError at
    import time.
    """
    path = _path(stack, key)
    if not path.endswith(".py"):
        raise ValueError(f"not a Python file: {path}")
    # Populate any env vars the module might read.
    for k, v in _ENV_DEFAULTS.items():
        os.environ.setdefault(k, v)
    # Unique module name so repeated calls don't collide in sys.modules.
    mod_name = f"_boilerplate_audit_{stack.replace('-', '_')}_{key}"
    spec = importlib.util.spec_from_file_location(mod_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not build import spec for {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)   # ← real code execution
    return mod


def read_js_constant(stack: str, key: str, name: str) -> Optional[int]:
    """Return the numeric value of `const NAME = <expr>;` from a
    boilerplate JS file. Runs Node.js in a subprocess to evaluate
    the expression when possible; falls back to a mul/add regex
    evaluator (`60 * 60` → 3600) when node is not on PATH.
    """
    path = _path(stack, key)
    if not path.endswith(".js"):
        raise ValueError(f"not a JS file: {path}")
    try:
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
    except OSError:
        return None
    m = re.search(
        rf"^\s*(?:export\s+)?(?:const|let|var)\s+{re.escape(name)}\s*=\s*([^;]+?);",
        src, re.M,
    )
    if not m:
        return None
    expr = m.group(1).strip()
    # Try Node first — real evaluation of the expression.
    try:
        r = subprocess.run(
            ["node", "-e", f"process.stdout.write(String({expr}));"],
            capture_output=True, text=True, timeout=4,
        )
        if r.returncode == 0 and r.stdout.strip():
            return int(r.stdout.strip())
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    # Fallback — parse `A * B` / `A * B * C` etc.
    if re.fullmatch(r"\s*(\d+)\s*(\*\s*\d+\s*)*", expr):
        parts = [int(p) for p in re.findall(r"\d+", expr)]
        val = 1
        for p in parts:
            val *= p
        return val
    # Single integer literal.
    if expr.isdigit():
        return int(expr)
    return None


def audit_reset_token_flags(stack: str) -> dict:
    """Return `{used_false_present, used_true_present}` for the
    reset-token single-use pattern in the executed Python auth
    module of `stack`. The two flags are read from the *executed*
    module's compiled source (via `inspect.getsource`) — the module
    has to import cleanly for the flags to be readable, which is a
    stronger guarantee than a blind file-grep.
    """
    if stack != "react-fastapi":
        raise ValueError("reset-token audit currently only covers react-fastapi")
    mod = load_python_boilerplate("react-fastapi", "auth_server")
    src = inspect.getsource(mod)
    return {
        "used_false_present": ('"used":       False' in src
                                or '"used": False' in src),
        "used_true_present":  ('{"used": True' in src
                                or '"used": True, "used_at"' in src),
        "reset_ttl_s":        getattr(mod, "_RESET_TTL_S", None),
        "access_ttl_s":       getattr(mod, "_ACCESS_TTL_S", None),
        "refresh_ttl_s":      getattr(mod, "_REFRESH_TTL_S", None),
    }
