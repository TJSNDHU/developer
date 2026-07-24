"""
Iter 294 — Frontend Layer 1 (LoopStepBar) + CI-guard JSX extension.

# static-grep-ok: this file locks the analyzer's JS/TS parsing rules
# via file-shape assertions. The behavioural coverage of the analyzer
# itself lives in test_regression_iter290_test_style_analyzer.py.

Locks:
  1. `.test.jsx` / `.test.js` / `.test.tsx` / `.test.ts` are now
     recognised by the analyzer and by ci_check_test_style.py.
  2. The JS classifier obeys the same contract as the Python one:
     BEHAVIOURAL when RTL/userEvent tokens present, STATIC_GREP when
     readFileSync-family present and no behavioural, HYBRID both,
     UNKNOWN neither.
  3. LoopStepBar's real RTL test file exists AND classifies all
     three tests as BEHAVIOURAL. If iter294's test regresses to
     source-string grep, this test screams.
  4. The `// static-grep-ok:` marker (JS/TS variant) is respected
     by ci_check_test_style.py's exempt-parsing regex.
"""
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
import os


ANALYZER = "/app/backend/services/test_style_analyzer.py"
CI_GUARD = "/app/backend/scripts/ci_check_test_style.py"
LOOP_STEP_TEST = "/app/frontend/src/components/__tests__/LoopStepBar.test.jsx"


def _read(p: str) -> str:
    with open(p, "r", encoding="utf-8") as f:
        return f.read()


def _git(cwd, *args):
    return subprocess.check_output(
        ["git", "-c", "user.email=x", "-c", "user.name=x", *args],
        cwd=cwd, text=True,
    )


# ── (1) Analyzer recognises JS/TS extensions ─────────────────────────

def test_analyzer_recognises_jsx_test_extension():
    from services.test_style_analyzer import analyze_file
    r = analyze_file(LOOP_STEP_TEST)
    assert r["ok"] is True
    assert isinstance(r["tests"], list)
    assert len(r["tests"]) == 3, (
        f"iter294 LoopStepBar test file must have 3 tests; got "
        f"{len(r['tests'])}. If the JS test-block regex broke, this "
        f"assertion is the tripwire."
    )


def test_all_three_loopstepbar_tests_classified_behavioural():
    from services.test_style_analyzer import analyze_file
    r = analyze_file(LOOP_STEP_TEST)
    kinds = [t["kind"] for t in r["tests"]]
    assert kinds == ["BEHAVIOURAL", "BEHAVIOURAL", "BEHAVIOURAL"], (
        f"expected 3 BEHAVIOURAL, got {kinds}. The frontend layer 1 "
        f"pattern MUST self-verify as behavioural — otherwise it is "
        f"failing at the exact discipline it exists to enforce."
    )


# ── (2) JS classifier contract ───────────────────────────────────────

def test_js_classifier_flags_readfilesync_as_static_grep():
    from services.test_style_analyzer import _analyze_js_file
    with tempfile.NamedTemporaryFile(mode="w", suffix=".test.js",
                                       delete=False) as f:
        f.write("""
import fs from 'node:fs';
it('greps source', () => {
  const s = fs.readFileSync('/tmp/x', 'utf-8');
  expect(s.includes('MCP')).toBe(true);
});
""")
        p = f.name
    try:
        r = _analyze_js_file(p)
        assert r["ok"] is True
        assert r["tests"], "no tests parsed"
        assert all(t["kind"] == "STATIC_GREP" for t in r["tests"])
    finally:
        os.unlink(p)


def test_js_classifier_flags_rtl_render_as_behavioural():
    from services.test_style_analyzer import _analyze_js_file
    with tempfile.NamedTemporaryFile(mode="w", suffix=".test.jsx",
                                       delete=False) as f:
        f.write("""
import { render, screen } from '@testing-library/react';
it('renders component', () => {
  render(<div>hello</div>);
  expect(screen.getByText('hello')).toBeInTheDocument();
});
""")
        p = f.name
    try:
        r = _analyze_js_file(p)
        assert r["ok"] is True
        assert r["tests"]
        assert all(t["kind"] == "BEHAVIOURAL" for t in r["tests"])
    finally:
        os.unlink(p)


def test_js_classifier_flags_hybrid_when_both_signals():
    from services.test_style_analyzer import _analyze_js_file
    with tempfile.NamedTemporaryFile(mode="w", suffix=".test.jsx",
                                       delete=False) as f:
        f.write("""
import fs from 'node:fs';
import { render, screen } from '@testing-library/react';
it('mixed', () => {
  render(<div>hi</div>);
  fs.readFileSync('/tmp/x');
});
""")
        p = f.name
    try:
        r = _analyze_js_file(p)
        assert r["ok"] is True
        assert all(t["kind"] == "HYBRID" for t in r["tests"])
    finally:
        os.unlink(p)


# ── (3) CI-guard: end-to-end JSX diff ────────────────────────────────

def test_ci_guard_picks_up_jsx_files_in_diff():
    """Behavioural — create a synthetic git repo containing a .test.jsx
    file with a weak grep pattern; the guard must PICK IT UP (i.e.
    it must not be silently ignored the way Python-only globs did)."""
    with tempfile.TemporaryDirectory() as td:
        _git(td, "init", "-q")
        with open(os.path.join(td, "README.md"), "w") as f:
            f.write("# fixture\n")
        _git(td, "add", ".")
        _git(td, "commit", "-q", "-m", "base")
        # A JSX file that reads a source file (STATIC_GREP by JS
        # classifier) with enough test blocks to trip the min-size.
        os.makedirs(os.path.join(td, "src/components/__tests__"))
        weak = """
import fs from 'node:fs';
it('a', () => { fs.readFileSync('/tmp/x'); });
it('b', () => { fs.readFileSync('/tmp/x'); });
it('c', () => { fs.readFileSync('/tmp/x'); });
it('d', () => { fs.readFileSync('/tmp/x'); });
"""
        path = "src/components/__tests__/Foo.test.jsx"
        with open(os.path.join(td, path), "w") as f:
            f.write(weak)
        _git(td, "add", ".")
        _git(td, "commit", "-q", "-m", "add jsx test")
        head = _git(td, "rev-parse", "HEAD").strip()
        base = _git(td, "rev-parse", "HEAD~1").strip()
        env = os.environ.copy()
        env["PYTHONPATH"] = "/app/backend"
        proc = subprocess.run(
            [sys.executable, CI_GUARD, base, head],
            cwd=td, env=env, capture_output=True, text=True,
        )
        # It should have PICKED UP the file (i.e. not "No changed
        # test files"). Whether it BLOCKS depends on the guard's
        # STATIC_GREP fraction — 4/4=100% so must block.
        assert proc.returncode == 1, proc.stdout + proc.stderr
        assert "Foo.test.jsx" in (proc.stdout + proc.stderr)
        assert "static-grep 100.0%" in (proc.stdout + proc.stderr)


def test_ci_guard_respects_js_style_exempt_marker():
    """// static-grep-ok: ... on a JS test file must also work."""
    with tempfile.TemporaryDirectory() as td:
        _git(td, "init", "-q")
        with open(os.path.join(td, "README.md"), "w") as f:
            f.write("# fixture\n")
        _git(td, "add", ".")
        _git(td, "commit", "-q", "-m", "base")
        os.makedirs(os.path.join(td, "src/components/__tests__"))
        exempt = """// static-grep-ok: intentional mutation-style tests
import fs from 'node:fs';
it('a', () => { fs.readFileSync('/tmp/x'); });
it('b', () => { fs.readFileSync('/tmp/x'); });
it('c', () => { fs.readFileSync('/tmp/x'); });
"""
        path = "src/components/__tests__/Bar.test.jsx"
        with open(os.path.join(td, path), "w") as f:
            f.write(exempt)
        _git(td, "add", ".")
        _git(td, "commit", "-q", "-m", "add exempt jsx")
        head = _git(td, "rev-parse", "HEAD").strip()
        base = _git(td, "rev-parse", "HEAD~1").strip()
        env = os.environ.copy()
        env["PYTHONPATH"] = "/app/backend"
        proc = subprocess.run(
            [sys.executable, CI_GUARD, base, head],
            cwd=td, env=env, capture_output=True, text=True,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "[EXEMPT ]" in proc.stdout
        assert "intentional" in proc.stdout


# ── (4) Sanity: LoopStepBar test file itself exists ──────────────────

def test_loopstepbar_test_file_exists_with_data_testid_assertions():
    src = _read(LOOP_STEP_TEST)
    # RTL entry points must be present.
    assert "from \"@testing-library/react\"" in src or \
           "from '@testing-library/react'" in src
    assert "screen.getByTestId" in src
    # No fs.readFileSync or path.resolve in the RTL file (would flip
    # to HYBRID and defeat the purpose).
    assert "readFileSync" not in src
    assert "fs.readFile" not in src
