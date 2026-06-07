"""
test_iter95_e2b_sandbox_live.py — locks in E2B Code Interpreter wiring.

Offline checks always run. Live network checks (opt-in via
RUN_LIVE_NETWORK_TESTS=1) actually spin up a real e2b sandbox and run
Python in it — proves the founder's key is valid and the SDK migration
to `Sandbox.create(api_key=…)` is sound.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
from dotenv import load_dotenv


ENV_PATH = Path(__file__).resolve().parents[1] / ".env"


@pytest.fixture(autouse=True)
def _load_env():
    load_dotenv(str(ENV_PATH), override=True)
    yield


def test_e2b_api_key_configured():
    """Key must be present and `e2b_` prefixed."""
    k = os.environ.get("E2B_API_KEY", "")
    assert k, "E2B_API_KEY missing from env"
    assert k.startswith("e2b_"), f"E2B keys start with `e2b_`, got {k[:5]!r}"
    assert len(k) >= 40, f"E2B key suspiciously short ({len(k)} chars)"


def test_e2b_sdk_installed():
    """The `e2b_code_interpreter` SDK must be importable — otherwise
    `sandbox_runner.py` silently no-ops in prod."""
    import e2b_code_interpreter
    assert hasattr(e2b_code_interpreter, "Sandbox")


def test_sandbox_runner_uses_create_factory():
    """sandbox_runner.py must use the new `Sandbox.create(...)` factory
    (e2b SDK 2.x+) instead of the deprecated `Sandbox(api_key=...)`
    constructor (which raised TypeError on 2.8.0)."""
    src = (Path(__file__).resolve().parents[1] / "services" / "sandbox_runner.py").read_text()
    assert "Sandbox.create(" in src, (
        "sandbox_runner.py must call Sandbox.create(...) (SDK 2.x+ API)"
    )
    # Negative guard — direct constructor form is broken.
    assert "Sandbox(api_key=" not in src, (
        "sandbox_runner.py still uses the deprecated Sandbox(api_key=...) "
        "constructor — that raises TypeError on e2b SDK 2.8.0+"
    )


@pytest.mark.skipif(
    os.environ.get("RUN_LIVE_NETWORK_TESTS") != "1",
    reason="Live E2B sandbox spin-up — opt-in via RUN_LIVE_NETWORK_TESTS=1",
)
def test_live_sandbox_executes_python():
    """End-to-end: spin a real sandbox, run a 1-line Python program,
    assert the output matches. Proves the founder's key + the SDK
    integration both work in prod."""
    from services.sandbox_runner import run_python_check
    result = asyncio.run(run_python_check("print(7 * 6)"))
    assert result["ok"] is True, f"sandbox refused valid code: {result}"
    assert result["skipped"] is False, "live run should not skip"
    assert "42" in result["stdout"], (
        f"expected '42' in stdout, got {result.get('stdout')!r}"
    )


@pytest.mark.skipif(
    os.environ.get("RUN_LIVE_NETWORK_TESTS") != "1",
    reason="Live E2B sandbox — opt-in",
)
def test_live_sandbox_catches_syntax_error():
    """Syntax errors must bubble up via the `ok: False` + `stderr`
    path — otherwise broken code would silently pass validation."""
    from services.sandbox_runner import run_python_check
    result = asyncio.run(run_python_check("def broken( :\n  pass"))
    assert result["ok"] is False, "syntax-error code must fail validation"
    assert "SyntaxError" in (result.get("stderr") or ""), (
        f"expected SyntaxError in stderr, got {result.get('stderr')!r}"
    )
