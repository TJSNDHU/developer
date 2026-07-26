#!/usr/bin/env bash
# Focused QA repro for Iter 309 Phase 0.2 Round 6.
# Runs the requested CI-style pytest pipe in a fresh venv context and
# prints the parent-observed exit code. Expected final line: CI_REPRO_EXIT=1.
set -u

cd /app/backend

bash -c 'set -o pipefail; python -m pytest tests/test_ci_canary_MUST_FAIL_iter309.py --tb=short -q 2>&1 | tee /tmp/out.txt; PYTEST_EXIT=${PIPESTATUS[0]}; echo "---iter309-canary-check---"; grep -E "test_ci_canary_MUST_FAIL_iter309|AssertionError.*CANARY" /tmp/out.txt || echo missing; echo "---end-canary-check---"; exit "$PYTEST_EXIT"'
status=$?
echo "CI_REPRO_EXIT=$status"
exit 0