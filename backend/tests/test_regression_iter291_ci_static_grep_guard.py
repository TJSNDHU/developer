"""
tests/test_regression_iter291_ci_static_grep_guard.py — Iter 291

# static-grep-ok: this suite mostly reads scripts/ci_check_test_style.py
# to lock its behaviour + fixture files, which is deliberate. The
# behavioural sub-tests exercise the real CLI end-to-end via git
# fixtures.

Locks the CI guard's behaviour:
  1. Fails when a new/changed test file exceeds 60% STATIC_GREP.
  2. Passes when the file carries a `# static-grep-ok: <reason>`
     magic comment.
  3. Ignores non-test files whose basename happens to start with
     `test_` (the `services/test_style_analyzer.py` false-positive
     class).
  4. Ignores files with < 3 tests (sample size too small).

Sub-tests are BEHAVIOURAL — they invoke the script via subprocess
against a real ephemeral git repo, then assert on exit code + stdout.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile


_SCRIPT = "/app/backend/scripts/ci_check_test_style.py"


def _git(cwd: str, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-c", "user.email=x", "-c", "user.name=x", *args],
        cwd=cwd, text=True,
    )


def _make_repo(tmpdir: str, files: dict[str, str]) -> str:
    """Create a fresh git repo with a single base commit containing
    an empty README, then a second commit adding all `files`. Returns
    the head SHA of the added commit; use HEAD~1 as base."""
    _git(tmpdir, "init", "-q")
    with open(os.path.join(tmpdir, "README.md"), "w") as f:
        f.write("# fixture\n")
    _git(tmpdir, "add", ".")
    _git(tmpdir, "commit", "-q", "-m", "base")
    for rel, content in files.items():
        full = os.path.join(tmpdir, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as f:
            f.write(content)
    _git(tmpdir, "add", ".")
    _git(tmpdir, "commit", "-q", "-m", "add tests")
    return _git(tmpdir, "rev-parse", "HEAD").strip()


def _run_guard(cwd: str, base: str, head: str) -> tuple[int, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = "/app/backend"
    proc = subprocess.run(
        [sys.executable, _SCRIPT, base, head],
        cwd=cwd, env=env, capture_output=True, text=True,
    )
    return proc.returncode, proc.stdout + proc.stderr


_WEAK_SRC = """
def _read(p): return open(p).read()

def test_g1():
    s = _read('/tmp/x')
    assert 'A' in s

def test_g2():
    s = _read('/tmp/x')
    assert 'B' in s

def test_g3():
    s = _read('/tmp/x')
    assert 'C' in s

def test_g4():
    s = _read('/tmp/x')
    assert 'D' in s

def test_b1():
    import asyncio
    from services.qa_matrix import load_matrix
    r = load_matrix()
    assert r
"""


def test_regression_iter291_weak_file_is_blocked():
    with tempfile.TemporaryDirectory() as td:
        head = _make_repo(td, {"backend/tests/test_weak.py": _WEAK_SRC})
        base = _git(td, "rev-parse", "HEAD~1").strip()
        rc, out = _run_guard(td, base, head)
        assert rc == 1, out
        assert "static-grep 80.0%" in out
        assert "test_weak.py" in out


_EXEMPT_SRC = "# static-grep-ok: mutation suite\n" + _WEAK_SRC


def test_regression_iter291_exempt_marker_is_respected():
    with tempfile.TemporaryDirectory() as td:
        head = _make_repo(td,
                          {"backend/tests/test_exempt.py": _EXEMPT_SRC})
        base = _git(td, "rev-parse", "HEAD~1").strip()
        rc, out = _run_guard(td, base, head)
        assert rc == 0, out
        assert "[EXEMPT ]" in out
        assert "mutation suite" in out


_HEALTHY_SRC = """
def test_b1():
    import asyncio
    from services.qa_matrix import load_matrix
    r = load_matrix()
    assert r

def test_b2():
    from services.qa_matrix import matrix_summary
    r = matrix_summary()
    assert r

def test_b3():
    from services.qa_matrix import open_gaps
    r = open_gaps()
    assert isinstance(r, list)

def test_g1():
    s = open('/tmp/x').read()
    assert 'X' in s
"""


def test_regression_iter291_healthy_file_passes():
    """3 behavioural + 1 grep = 25% grep, well under threshold."""
    with tempfile.TemporaryDirectory() as td:
        head = _make_repo(td,
                          {"backend/tests/test_ok.py": _HEALTHY_SRC})
        base = _git(td, "rev-parse", "HEAD~1").strip()
        rc, out = _run_guard(td, base, head)
        assert rc == 0, out
        assert "[PASS   ]" in out


def test_regression_iter291_tiny_file_is_skipped_below_min_size():
    """< 3 tests is statistical noise → skipped, not blocked."""
    tiny = "def _read(p): return open(p).read()\n\ndef test_a():\n    s = _read('/tmp/x')\n    assert 'A' in s\n"
    with tempfile.TemporaryDirectory() as td:
        head = _make_repo(td, {"backend/tests/test_tiny.py": tiny})
        base = _git(td, "rev-parse", "HEAD~1").strip()
        rc, out = _run_guard(td, base, head)
        assert rc == 0, out
        assert "test_tiny.py" in out


def test_regression_iter291_ignores_non_tests_dir_files():
    """A file under services/ whose basename starts with test_ MUST
    NOT be treated as a test file — this was a real false-positive
    seen during iter291 development (`services/test_style_analyzer.py`
    was picked up by an earlier version of the glob)."""
    with tempfile.TemporaryDirectory() as td:
        head = _make_repo(td, {"backend/services/test_helper.py": _WEAK_SRC})
        base = _git(td, "rev-parse", "HEAD~1").strip()
        rc, out = _run_guard(td, base, head)
        assert rc == 0, out
        assert "No changed test files" in out or "0 pass" in out


def test_regression_iter291_script_exists_and_is_executable():
    assert os.path.isfile(_SCRIPT)
    assert os.access(_SCRIPT, os.X_OK), \
        "ci_check_test_style.py must be executable (chmod +x)"


def test_regression_iter291_workflow_wires_the_guard():
    """The .github/workflows/quality-gate.yml MUST invoke the guard
    on every PR — otherwise the file exists but never runs."""
    with open("/app/.github/workflows/quality-gate.yml") as f:
        wf = f.read()
    assert "test-style-guard" in wf, \
        "quality-gate.yml must declare the test-style-guard job"
    assert "ci_check_test_style.py" in wf, \
        "the workflow must invoke ci_check_test_style.py"
