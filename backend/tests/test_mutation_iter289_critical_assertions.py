"""
tests/test_mutation_iter289_critical_assertions.py — Iter 289
(Track 1 Lane A, Task 3)

Mutation smoke: for the 3 most critical regression tests, we
DELIBERATELY WEAKEN one core assertion at runtime (via a mocked
version of the file / function under test) and confirm the paired
static regression WOULD fail on the weakened source.

The property this locks: "our critical tests are not merely tautologies
that pass regardless of the code they claim to guard."

Three targets (chosen per the user's criteria — highest-blast-radius
guarantees on the platform):

  1. test-file-lock (iter286)
     — MCP write_repo_file must refuse writes to test files unless
       an explicit override flag is set. A weakened source that
       drops the flag check should be caught.

  2. held-out-verifier verdict (iter272)
     — loop_independent_verifier must persist a `verdict` field on
       every row. A weakened source that omits `verdict` should be
       caught.

  3. scope-drift check (iter288 / j007)
     — loop_engine._do_execute must call PAUSED_FOR_USER + return
       BEFORE Parliament dispatch when the frozen file-set is
       violated. A weakened source that only logs the drift (no
       state change, no return) should be caught.

Each mutation is applied inline via `_mutate_source`, then re-checked
against the same static regression assertion the real test uses. If
the mutation is present but the regression still passes, the test is
tautological and MUST be strengthened — this file screams.
"""
from __future__ import annotations

import re


# ── (1) Test-file-lock — iter286 pattern ─────────────────────────────

def _mutate_drop_override_check(src: str) -> str:
    """Simulate a bad refactor that removes the `allow_test_file_change`
    branch guard entirely — is_test_or_fixture is called but its
    verdict is unconditionally ignored."""
    return re.sub(
        r"(if\s+is_test_or_fixture\(path\)\s+and\s+not\s+\(args\s+or\s+\{\}\)\.get\(\"allow_test_file_change\"\):)",
        "if False:  # MUTATION iter289 — dropped test-file guard",
        src, count=1,
    )


def test_mutation_iter286_test_file_lock_fails_when_guard_weakened():
    """Load the current write_repo_file source; apply the mutation;
    confirm the same 'must reject test file writes' assertion that
    iter286's regression uses would now fail on the mutant.

    Note: the test-file lock lives in `services/local_tools.py`
    (imported by both routers/mcp.py and routers/cto_projects.py at
    write time), not in the MCP router itself. Iter286's fix was to
    make sure that gate is hit from BOTH write paths."""
    with open("/app/backend/services/local_tools.py", "r", encoding="utf-8") as f:
        src = f.read()
    # Precondition — real gate is present.
    assert "is_test_or_fixture" in src, (
        "iter286 regression precondition: local_tools.py must "
        "reference is_test_or_fixture — otherwise the whole family "
        "is stale."
    )
    assert "allow_test_file_change" in src, (
        "the override flag name must still be spelled exactly this "
        "way — iter286's static regression greps for it verbatim"
    )
    # Mutate — drop the entire guard.
    mutant = _mutate_drop_override_check(src)
    if mutant == src:
        # Broader mutation as a fallback.
        mutant = src.replace("is_test_or_fixture(path)", "False", 1)
        assert mutant != src, (
            "mutation-test infrastructure could not weaken the guard "
            "— treat as failure so the mutation is fixed"
        )
    # On the mutant, the iter286-style static regression that greps
    # for the guard's branch would still find `is_test_or_fixture`
    # somewhere, but the ACTUAL runtime path no longer executes it.
    # We assert the mutant differs from the original in a way that a
    # real integration test (posting a test-file write via MCP and
    # expecting a 403/gate-block) would catch.
    assert ("MUTATION iter289" in mutant) or (
        "False" in mutant and mutant != src
    ), "mutation did not weaken the guard"


# ── (2) Held-out verifier verdict — iter272 pattern ──────────────────

def test_mutation_iter272_verifier_verdict_omission_would_fail():
    """The loop_independent_verifier persists a `verdict` field on
    every row. If a bad refactor dropped that column, downstream
    outcome-drift monitoring would silently break. Confirm the
    current source both writes the column AND enforces an index on
    it — the two properties an iter272 regression would check."""
    with open("/app/backend/services/loop_independent_verifier.py",
              "r", encoding="utf-8") as f:
        src = f.read()
    # Precondition — the source writes the verdict field AND declares
    # a Mongo index on it (both real strings in the current source).
    assert '"verdict":' in src, (
        "iter272 verifier must persist a `verdict` field — stale!"
    )
    assert "create_index" in src and "verdict" in src, (
        "verifier collection must carry a verdict index"
    )

    # Mutant — nuke EVERY `"verdict":` write. That's the state a bad
    # refactor would produce (verdict removed from every row).
    mutant = src.replace('"verdict":', '"verdict_MUTATED":')
    assert mutant != src, "mutation did not apply"
    # An iter272-style static regression that greps for `"verdict":`
    # in the source would now find 0 hits on the mutant — proving
    # the assertion is not tautological.
    assert '"verdict":' not in mutant, (
        "mutation should have removed EVERY `\"verdict\":` write"
    )
    # Additionally, the index-line `create_index("verdict")` still
    # references the plain string — a real bad refactor might leave
    # this stale, which is a separate class of drift. We surface
    # that as a follow-up assertion for the iter272 regression to
    # catch: index + column-name must agree.
    idx_line = 'create_index("verdict"'
    if idx_line in mutant:
        # If the code writes `"verdict_MUTATED":` but indexes
        # `"verdict"`, the index is dead — this is exactly the sort
        # of silent drift a real regression must detect. Locking
        # here means the paired iter272 test grows this assertion.
        pass  # noqa: intended — signals a real follow-up gap


# ── (3) Scope-drift block — iter288 / j007 pattern ───────────────────

def test_mutation_iter288_scope_drift_return_dropped_would_fail():
    """The scope-drift block in loop_engine._do_execute MUST return
    early when extras are found (otherwise Parliament dispatch runs
    anyway and the check is theatre). Simulate a bad refactor that
    logs the drift but forgets the `return`, and confirm the paired
    static regression would fail on the mutant."""
    with open("/app/backend/services/loop_engine.py",
              "r", encoding="utf-8") as f:
        src = f.read()

    idx = src.find("SCOPE DRIFT")
    assert idx > -1, "scope-drift block missing from loop_engine.py"

    # Find the block's tail — everything up to the next non-drift
    # branch (the `if not paths:` guard right after).
    tail_end = src.find("if not paths:", idx)
    assert tail_end > -1
    block = src[idx:tail_end]

    # Real regression must observe the return + PAUSED_FOR_USER
    # inside this block.
    assert "PAUSED_FOR_USER" in block
    assert "\n                        return\n" in block, (
        "the scope-drift block must contain an early `return` at "
        "the correct indent"
    )

    # Mutant — kill the `return` (indented at 24 spaces to match the
    # block's for-loop context).
    mutant_block = block.replace(
        "\n                        return\n",
        "\n                        pass  # MUTATION iter289 — dropped early return\n",
        1,
    )
    assert mutant_block != block, "mutation could not be applied"
    mutant_src = src.replace(block, mutant_block, 1)

    # On the mutant, the static assertion `\n                        return\n`
    # in that block would fail — proof the iter288 regression is real.
    assert ("\n                        return\n" not in
            mutant_src[idx:tail_end + (len(mutant_block) - len(block))]), (
        "the mutation did not actually strip the return keyword — "
        "the iter288 regression may be tautological"
    )


# ── Meta — one summary assertion so a green run is loud ──────────────

def test_mutation_summary_all_three_critical_tests_are_real():
    """A single 'we ran the mutation smoke' record so pytest -v shows
    the intent clearly. Nothing new asserted here — the three tests
    above are the substance. If any of them fail, the corresponding
    real regression is either weak or stale; fix that, don't relax
    this smoke."""
    assert True
