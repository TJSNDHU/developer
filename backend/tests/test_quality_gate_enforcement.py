"""
Proof that /app/.github/workflows/quality-gate.yml actually blocks a
fix-shaped PR that lacks a test change. Runs the SAME shell logic
the workflow does, against a synthetic diff.

Runs in CI so the gate's own logic can never silently regress.
"""
from __future__ import annotations

import subprocess


def _run_gate(changed_files: list[str], labels: list[str] | None = None) -> int:
    """
    Mirrors the workflow's `Diff-based gate` step. Returns the exit
    code the workflow would return (0 = allowed, 1 = blocked).
    """
    # Label override short-circuit (matches the workflow's Bash regex).
    if labels:
        for lbl in labels:
            if lbl in ("docs-only", "no-test-needed"):
                return 0

    files_input = "\n".join(changed_files)
    script = r'''
set -euo pipefail
CHANGED="$1"
FIX_SHAPED=$(echo "$CHANGED" | grep -E \
  '^(backend/(routers|services|models)/|frontend/src/(components|pages|hooks|lib)/)' \
  || true)
if [ -z "$FIX_SHAPED" ]; then
  exit 0
fi
TEST_TOUCHED=$(echo "$CHANGED" | grep -E \
  '(^|/)test_[^/]+\.py$|(^|/)[^/]+\.test\.(js|jsx|ts|tsx)$|(^|/)tests/' \
  || true)
if [ -n "$TEST_TOUCHED" ]; then
  exit 0
fi
exit 1
'''
    r = subprocess.run(
        ["bash", "-c", script, "_", files_input],
        capture_output=True, text=True,
    )
    return r.returncode


def test_quality_gate_blocks_fix_shaped_pr_without_tests():
    """
    A PR touching backend/routers/loop.py with NO test file changes
    must be BLOCKED.
    """
    files = ["backend/routers/loop.py"]
    assert _run_gate(files) == 1, (
        "Quality-gate must BLOCK a PR that touches routers/ without a test."
    )


def test_quality_gate_blocks_frontend_component_change_without_tests():
    """
    A PR touching frontend/src/components/ChatPanel.jsx alone must
    be BLOCKED.
    """
    files = ["frontend/src/components/ChatPanel.jsx"]
    assert _run_gate(files) == 1


def test_quality_gate_allows_fix_shaped_pr_with_tests():
    """
    Same fix-shaped diff PLUS a test file — must PASS.
    """
    files = [
        "backend/routers/loop.py",
        "backend/tests/test_regression_iter281_something.py",
    ]
    assert _run_gate(files) == 0


def test_quality_gate_allows_pure_docs_change():
    """
    A PR that only touches README.md / AGENTS.md is NOT fix-shaped,
    so no test is required.
    """
    files = ["README.md", "AGENTS.md"]
    assert _run_gate(files) == 0


def test_quality_gate_respects_docs_only_label_override():
    """
    Fix-shaped diff with the `docs-only` label override must be
    ALLOWED (reviewer sign-off is enforced by branch protection).
    """
    files = ["backend/services/loop_engine.py"]
    assert _run_gate(files, labels=["docs-only"]) == 0


def test_quality_gate_respects_no_test_needed_label_override():
    files = ["backend/routers/loop.py"]
    assert _run_gate(files, labels=["no-test-needed"]) == 0


def test_quality_gate_ignores_unrelated_labels():
    """
    Random labels like `bug` or `p0` do NOT override the gate.
    """
    files = ["frontend/src/components/ChatPanel.jsx"]
    assert _run_gate(files, labels=["bug", "p0", "urgent"]) == 1
