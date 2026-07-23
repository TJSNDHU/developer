# CONTRIBUTING.md — humans-first version of AGENTS.md

Read `/app/AGENTS.md` first — that is the source of truth for any
coding agent (Cursor, Aider, Codex, Claude Code) plus any human PR
reviewer. This file exists so humans have a familiar entry point.

## Quick rules

1. **Every bug fix ships with a regression test** named
   `test_regression_iter<N>_<what>`. Location: `/app/backend/tests/`.
2. **Touching untested code?** Add one characterization test first.
3. **Verification is real proof**, not "should work". Screenshot,
   curl output, or console log — attached to the PR description.
4. **The CI quality-gate** (`.github/workflows/quality-gate.yml`)
   blocks any PR that touches `backend/routers/`, `backend/services/`,
   or `frontend/src/components/` without also modifying at least one
   test file. Override with a `[docs-only]` or `[no-test-needed]`
   label on the PR (both require explicit reviewer sign-off).

## Test commands

```bash
# Regression + invariants (fast, must all pass on main)
cd /app/backend && python3 -m pytest tests/test_regression_iter279_281_bug_per_fix.py tests/test_invariants_continuous_quality.py -v
```

## Where the discipline lives

- **AGENTS.md**              — agent-facing full rules
- **CONTRIBUTING.md**        — human-facing quick summary (this file)
- **quality-gate.yml**       — mechanical merge gate
- **test_regression_*.py**   — bug-per-fix regression tests
- **test_invariants_*.py**   — always-on fitness functions
