"""
test_iter309_phase02_ci_exit_propagation.py — Iter 309 · Phase 0.2 · Round 6

Regression guard against the ci.yml exit-code-swallow bug found in
production Round 6: my own Round 5 fix inadvertently clobbered
${PIPESTATUS[0]} by adding a `grep` line AFTER the pytest pipe and
BEFORE `exit ${PIPESTATUS[0]}`. Bash's PIPESTATUS array only refers
to the MOST RECENT pipeline — so the final `exit` was returning the
grep's exit code (0 when canary present) instead of pytest's.

Result: CI showed the job GREEN despite ~20 test failures visible in
the log. Defeated the whole point of Phase 0.2.

This static test parses the ci.yml `Run tests` step and asserts:
  1. The pytest exit code is captured into a variable IMMEDIATELY
     after the pipe (no intervening pipelines).
  2. `set -o pipefail` is enabled OR PIPESTATUS is used explicitly.
  3. The final `exit` line uses the captured variable, NOT
     `${PIPESTATUS[0]}` (which is fragile).

If ANY of these three invariants is violated, this test fails —
before another CI-silence bug can hide in production.
"""
from __future__ import annotations

import re
from pathlib import Path


def test_ci_yml_run_tests_step_propagates_pytest_exit_code():
    ci_yml = (Path(__file__).resolve().parents[2]
              / ".github" / "workflows" / "ci.yml").read_text()

    # Extract the `Run tests` step's shell body.
    m = re.search(
        r"- name: Run tests\n(?:.*\n){1,4}?\s+run: \|\n((?:            .*\n|            \n|            $)+)",
        ci_yml,
    )
    if not m:
        # Looser match — the run: | block starts, capture until the
        # next top-level `- name:` at the same indent.
        m2 = re.search(
            r"- name: Run tests[\s\S]*?run: \|\n([\s\S]*?)(?=\n      - name:|\Z)",
            ci_yml,
        )
        assert m2, "ci.yml `Run tests` step body not found"
        body = m2.group(1)
    else:
        body = m.group(1)

    # Invariant 1: pipefail OR PIPESTATUS captured to a variable
    # BEFORE any later command runs.
    has_pipefail = "set -o pipefail" in body
    # Regex: some assignment like `X=${PIPESTATUS[0]}` right after
    # the pytest pipeline line ending with `tee /tmp/pytest_output.txt`.
    captured = re.search(
        r"tee /tmp/pytest_output\.txt\s*\n\s*[A-Z_]+=\$\{PIPESTATUS\[0\]\}",
        body,
    )
    assert has_pipefail or captured, (
        "ci.yml Run-tests step neither enables `set -o pipefail` nor "
        "captures $PIPESTATUS[0] into a variable IMMEDIATELY after the "
        "pytest pipe. This was the iter309 Round 6 bug: the grep line "
        "added between pytest and `exit ${PIPESTATUS[0]}` clobbered "
        "PIPESTATUS, so failing pytest runs showed the CI job green."
    )

    # Invariant 2: the final `exit` in the step (ignoring comments)
    # must NOT reference `${PIPESTATUS[0]}` directly.
    body_no_comments = "\n".join(
        line for line in body.splitlines()
        if not line.lstrip().startswith("#")
    )
    exit_matches = re.findall(r'\bexit\s+"?\$\{?[A-Z_]+', body_no_comments)
    for exit_line in exit_matches:
        assert "PIPESTATUS" not in exit_line, (
            f"ci.yml final `{exit_line}...` uses PIPESTATUS directly. "
            "This is the exact iter309 Round 6 bug — PIPESTATUS refers "
            "to the LAST pipeline, and if ANY command runs between "
            "pytest and this exit line, PIPESTATUS is clobbered. "
            "Capture into a variable immediately after the pipe and "
            "use that variable here."
        )
