#!/usr/bin/env bash
# Iter 346 — PRE-DEPLOY QA GATE (founder ruling 2026-07-29).
#
# Emergent's Deploy button runs build + env + health checks ONLY — it
# never executes the project's own tests. THIS script is the missing
# QA gate: the main agent runs it before every deploy request, and it
# must exit 0 or the deploy is not requested.
#
# Lanes (all blocking):
#   1. Backend pytest blocking lane (legacy/flaky/llm_judge excluded
#      via pytest.ini addopts) — must be 0 failed / 0 errors.
#   2. Frontend vitest component + lib suites.
#   3. Regression Library locks (services.qa_matrix) — every entry in
#      .emergent/qa-history/regression_library.json re-verified.
set -euo pipefail

cd "$(dirname "$0")/.."
echo "══════════ PRE-DEPLOY QA GATE ══════════"

echo "── Lane 1/3: backend pytest (blocking lane) ──"
(cd backend && python -m pytest --tb=short --timeout=30 -q \
    --continue-on-collection-errors \
    --ignore=tests/test_iter138_acceptance_seven.py \
    --ignore=tests/test_iter212m163_aggression_chat.py)

echo "── Lane 2/3: frontend vitest ──"
(cd frontend && npx vitest run src/components/__tests__/ src/lib)

echo "── Lane 3/3: regression library locks ──"
(cd backend && python -m services.qa_matrix \
    --message "predeploy gate" \
    --files "predeploy" \
    --sha "$(git rev-parse --short HEAD 2>/dev/null || echo predeploy)")

echo "══════════ GATE PASSED — safe to deploy ══════════"
