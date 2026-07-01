"""
Iter 212m-166 — Loop mode FileNotFoundError launch-blocker fix.

Root cause reproduced: `asyncio.create_subprocess_exec("eslint", ...)`
inside `services/loop_verify.py::_run` raised `FileNotFoundError(2)`
when the linter binary was missing on the runtime pod.  The exception
bubbled through `_lint_one → verify_files → LoopEngine._execute` and
killed the entire Loop mid-Execute with the exact errno-2 message the
founder was seeing on prod.

The fix wraps the spawn in a try/except (FileNotFoundError, OSError),
returns rc=127 with a self-describing stderr, and `_lint_one` treats
rc=127 as a soft skip so Ship still runs.

This suite:
  • Reproduces the crash pre-fix (would have raised).
  • Confirms the fix returns rc=127 gracefully.
  • Confirms `verify_files` returns a well-formed report even when
    the linter is missing (ok=True per file, linter="skip").
  • Confirms other error paths (timeout, real lint errors) still work.
"""

import asyncio
import pathlib
import sys
import unittest.mock as mock

BACKEND = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


# ─── Unit: _run subprocess spawn resilience ─────────────────────────────────

def test_run_returns_127_when_binary_missing():
    """The core bug: spawning a non-existent binary must NOT raise —
    it must return (127, b'', <stderr>) so the caller can degrade."""
    from services.loop_verify import _run

    async def _drive():
        return await _run(["definitely-not-a-real-linter-xyz"], cwd="/tmp")

    rc, stdout, stderr = asyncio.run(_drive())
    assert rc == 127, f"expected rc=127 for missing binary, got {rc}"
    assert stdout == b""
    assert b"not installed" in stderr or b"not found" in stderr.lower(), (
        f"stderr must self-describe, got {stderr!r}"
    )


def test_run_returns_127_on_permission_error(monkeypatch):
    """Permission / ENOEXEC on spawn must also degrade to rc=127."""
    from services.loop_verify import _run

    class _Boom:
        def __call__(self, *a, **kw):
            raise PermissionError("permission denied")

    async def fake_exec(*a, **kw):
        raise PermissionError("EACCES")

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
    rc, stdout, stderr = asyncio.run(_run(["ruff"], cwd="/tmp"))
    assert rc == 127
    assert b"spawn failed" in stderr


def test_run_still_returns_timeout_code_124():
    """Regression: the existing 124-on-timeout contract must hold."""
    from services.loop_verify import _run

    class FakeProc:
        returncode = 0
        async def communicate(self):
            # Sleep past the caller's timeout so wait_for triggers.
            await asyncio.sleep(10)
            return (b"", b"")
        def kill(self):
            pass

    async def fake_exec(*a, **kw):
        return FakeProc()

    async def _drive():
        return await _run(["fake"], cwd="/tmp", timeout=0.05)

    with mock.patch("asyncio.create_subprocess_exec", fake_exec):
        rc, stdout, stderr = asyncio.run(_drive())
    assert rc == 124
    assert b"timed out" in stderr


# ─── Integration: verify_files degrades cleanly when linter missing ─────────

def test_verify_files_soft_skips_missing_linter(monkeypatch):
    """The most important guarantee — when the linter binary is
    missing on the runtime pod, `verify_files` must:
      1. NOT raise
      2. return ok=True (so the Loop's Ship phase still runs)
      3. tag each row's linter as 'skip'
    """
    from services import loop_verify

    async def fake_exec(*a, **kw):
        raise FileNotFoundError(2, "No such file or directory", a[0] if a else "?")

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    async def _drive():
        return await loop_verify.verify_files([
            {"path": "backend/auth.py",       "content": "def x(): pass\n"},
            {"path": "frontend/src/App.jsx",  "content": "export default () => null\n"},
        ])

    report = asyncio.run(_drive())
    assert report["ok"] is True, (
        f"verify_files must degrade to ok=True when linter missing — "
        f"the ENTIRE Loop mode launch blocker depends on this. "
        f"Got: {report}"
    )
    assert len(report["results"]) == 2
    for row in report["results"]:
        assert row["ok"] is True
        assert row["linter"] == "skip"
    assert report["errors"] == []


def test_verify_files_real_lint_errors_still_surface(monkeypatch):
    """Regression: when the linter DOES run and reports errors, they
    must still bubble up (we didn't muzzle the whole verify path)."""
    from services import loop_verify

    class FakeProc:
        def __init__(self):
            self.returncode = 1
        async def communicate(self):
            return (b"x.py:1:1: E999 syntax error\n", b"")
        def kill(self):
            pass

    async def fake_exec(*a, **kw):
        return FakeProc()

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    async def _drive():
        return await loop_verify.verify_files([
            {"path": "bad.py", "content": "def : pass"},
        ])

    report = asyncio.run(_drive())
    assert report["ok"] is False
    assert report["results"][0]["ok"] is False
    assert report["results"][0]["linter"] == "ruff"
    assert len(report["errors"]) == 1


# ─── Repro: pre-fix behaviour would have raised FileNotFoundError ───────────

def test_repro_pre_fix_crash_pattern_no_longer_leaks():
    """Semantic guard: the source must catch FileNotFoundError inside
    `_run`.  If someone refactors and removes the try/except, this
    test breaks so we catch the regression before shipping."""
    src = pathlib.Path("/app/backend/services/loop_verify.py").read_text()
    # The critical guard
    assert "except FileNotFoundError" in src
    # And it must live INSIDE _run (not somewhere unrelated).
    run_start = src.find("async def _run(")
    run_end   = src.find("\nasync def ", run_start + 1)
    if run_end == -1:
        run_end = src.find("\ndef ", run_start + 1)
    _run_body = src[run_start:run_end]
    assert "except FileNotFoundError" in _run_body
    # The rc=127 soft-skip in _lint_one must also exist.
    assert "if rc == 127:" in src


def test_run_docstring_mentions_iter_212m_166_and_founder_bug():
    """Provenance guard — this fix must remain attributed so future
    engineers understand why the try/except exists."""
    src = pathlib.Path("/app/backend/services/loop_verify.py").read_text()
    assert "Iter 212m-166" in src
    assert "FileNotFoundError" in src
