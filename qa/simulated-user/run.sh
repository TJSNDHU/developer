#!/usr/bin/env bash
# qa/simulated-user/run.sh
# ────────────────────────────────────────────────────────────────
# Directive Session — Promptfoo simulated-user runner (self-hosted).
#
# Steps:
#   1. Seed the QA user + projects + one critical finding.
#   2. Export AUREM_QA_JWT + AUREM_QA_TOKEN so promptfoo can use them.
#   3. Also set AUREM_QA_MODE=true + AUREM_QA_TOKEN in the backend
#      env (via /var/log path is impractical — we set it live via
#      supervisorctl) so the probe endpoint accepts our token.
#   4. Run promptfoo eval headlessly with the strict env flags.
#   5. Exit with promptfoo's exit code — CI uses this to gate merges.
#
# No side effects beyond the QA user / project rows, which are safe
# to leave in place across runs (idempotent).
# ────────────────────────────────────────────────────────────────
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

echo "→ 1. Seeding QA user + projects + finding…"
SEED_OUT="$(python3 seed_qa_user.py)"
echo "$SEED_OUT" | sed 's/JWT=.*/JWT=<redacted>/;s/TOKEN=.*/TOKEN=<redacted>/'

# Consume the KEY=VALUE lines as env exports for the promptfoo run.
while IFS= read -r line; do
  export "$line"
done <<< "$SEED_OUT"

# ── Ensure the backend has AUREM_QA_MODE=true + our probe token ──
# In CI (github actions job env) these are set directly. Locally we
# push them into a supervisorctl-managed .env override + restart.
if [[ "${CI:-}" != "true" ]]; then
  echo "→ 2. Applying AUREM_QA_MODE=true to backend .env (local dev only)"
  ENV_FILE="/app/backend/.env"
  if grep -q "^AUREM_QA_MODE=" "$ENV_FILE"; then
    sed -i "s|^AUREM_QA_MODE=.*|AUREM_QA_MODE=true|" "$ENV_FILE"
  else
    echo "AUREM_QA_MODE=true" >> "$ENV_FILE"
  fi
  if grep -q "^AUREM_QA_TOKEN=" "$ENV_FILE"; then
    sed -i "s|^AUREM_QA_TOKEN=.*|AUREM_QA_TOKEN=\"${AUREM_QA_TOKEN}\"|" "$ENV_FILE"
  else
    echo "AUREM_QA_TOKEN=\"${AUREM_QA_TOKEN}\"" >> "$ENV_FILE"
  fi
  echo "→    restarting backend to pick up QA env…"
  sudo supervisorctl restart backend >/dev/null 2>&1 || true
  sleep 4
fi

# Belt-and-suspenders: also enforce the disable-remote flags on the
# shell that runs promptfoo, in case the yaml env block was tampered.
export PROMPTFOO_DISABLE_REMOTE_GENERATION=true
export PROMPTFOO_DISABLE_SHARE=true
export PROMPTFOO_DISABLE_TELEMETRY=true

echo "→ 3. Running promptfoo eval (self-hosted, no cloud calls)…"
# `npx promptfoo` uses the local devDependency install; no global
# needed. `--no-progress-bar` keeps CI logs clean.
NPX_BIN="$HERE/node_modules/.bin/promptfoo"
if [[ ! -x "$NPX_BIN" ]]; then
  echo "→    installing promptfoo (first run)…"
  npm install --no-audit --no-fund >/dev/null 2>&1
fi
"$NPX_BIN" eval -c promptfooconfig.yaml --no-progress-bar --output /tmp/aurem_qa_report.json
EXIT_CODE=$?

echo "→ 4. Done. Exit code: $EXIT_CODE"
echo "     Report: /tmp/aurem_qa_report.json"
exit $EXIT_CODE
