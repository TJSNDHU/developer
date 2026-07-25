"""
test_ci_canary_MUST_FAIL_iter309.py — Iter 309 · Phase 0.2 · TEMPORARY

⚠️  THIS FILE IS DELIBERATELY BROKEN. ⚠️
    It exists ONLY to verify that CI failure propagation actually works
    on GitHub Actions. The audit found 13 loop-related tests were
    silently failing without CI catching them. Before we can trust
    "add pytest -k loop to quality-gate.yml" as a real safety net, we
    have to prove that a single failing test in `backend/tests/` DOES
    turn the Actions tab red.

    Procedure:
    1. Save-to-GitHub this file on a NEW branch (e.g. `phase-0.2-canary`).
       Do NOT merge into main.
    2. Open the repo Actions tab. Locate the run for that branch.
    3. Expected: the workflow shows FAILURE, and the failing job's
       log contains "test_ci_canary_MUST_FAIL" with an AssertionError.
    4. If the run is GREEN or the test does not appear in the log,
       CI failure propagation is broken — do NOT proceed to add the
       loop-tests glob. Fix the CI first.
    5. After confirming the red run, this file gets deleted in the
       next commit that also adds `pytest -k loop` to quality-gate.yml.

    This test is INTENTIONALLY simple so any dropped CI signal is
    obvious. No imports of the app, no fixtures, no Mongo — just an
    assertion that will always fail. If someone accidentally "fixes"
    this test by making it pass, they defeated the whole purpose.
"""


def test_ci_failure_propagation_canary_iter309():
    """This test MUST fail. Do not fix. See file docstring."""
    # Iter 309 · Phase 0.2 · Founder-approved canary. Fails on purpose
    # to prove GitHub Actions actually surfaces test failures.
    assert False, (
        "CANARY: This test is deliberately failing to verify that "
        "CI turns the Actions tab red on any test failure. If you "
        "see this in a GitHub Actions log, the CI signal is WORKING. "
        "If the workflow shows green despite this test running, CI "
        "is broken and needs to be fixed BEFORE adding pytest -k loop "
        "to quality-gate.yml (see /app/memory/CHANGELOG.md iter 309)."
    )
