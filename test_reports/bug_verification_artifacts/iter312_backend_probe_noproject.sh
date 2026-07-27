#!/usr/bin/env bash
set -euo pipefail
BASE=$(grep '^REACT_APP_BACKEND_URL=' /app/frontend/.env | cut -d= -f2-)
TOKEN=$(cat /app/test_reports/bug_verification_artifacts/iter312_token.txt)
MSG="Complex long planning test for Iter 312 without project: draft a comprehensive multi-phase implementation plan touching authentication, billing, observability, CI, frontend UX, backend data models, security hardening, rollback strategy, migration plan, E2E tests, and documentation. Do not execute yet; create a careful plan only."
REQ=$(mktemp)
printf '{"project_id":null,"user_message":"%s"}' "$MSG" > "$REQ"
# cleanup no-project active/lock best-effort
ACTIVE=$(curl -sS "$BASE/api/aurem-dev/loop/active" -H "Authorization: Bearer $TOKEN" || true)
EXISTING=$(printf '%s' "$ACTIVE" | python -c 'import sys,json; s=sys.stdin.read();
try: print(((json.loads(s).get("active") or {}).get("loop_id") or ""))
except Exception: print("")')
if [ -n "$EXISTING" ]; then curl -sS -X POST "$BASE/api/aurem-dev/loop/$EXISTING/cancel" -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' --data '{}' || true; sleep 1; fi

printf '\n# BACKEND-1 curl: POST /loop/start (async default, project_id=null)\n'
RESP1=$(curl -sS -w '\nHTTP_STATUS:%{http_code}\nTIME_TOTAL:%{time_total}\n' -X POST "$BASE/api/aurem-dev/loop/start" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' --data-binary "@$REQ")
printf '%s\n' "$RESP1"
LOOP_ID=$(printf '%s\n' "$RESP1" | python -c 'import sys,json; s=sys.stdin.read(); body=s.split("\nHTTP_STATUS:",1)[0]; print(json.loads(body).get("loop_id", ""))')
printf '\nCaptured loop_id=%s project_id=null\n' "$LOOP_ID"

printf '\n# BACKEND-2 curl: immediate second POST /loop/start same no-project lock should 409\n'
RESP2=$(curl -sS -w '\nHTTP_STATUS:%{http_code}\nTIME_TOTAL:%{time_total}\n' -X POST "$BASE/api/aurem-dev/loop/start" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' --data-binary "@$REQ")
printf '%s\n' "$RESP2"

printf '\n# BACKEND-3 curl: GET /loop/active immediately after async start\n'
RESP3=$(curl -sS -w '\nHTTP_STATUS:%{http_code}\nTIME_TOTAL:%{time_total}\n' "$BASE/api/aurem-dev/loop/active" \
  -H "Authorization: Bearer $TOKEN")
printf '%s\n' "$RESP3"

printf '\n# Poll active until plan/terminal (max 180s)\n'
for i in $(seq 1 60); do
  R=$(curl -sS "$BASE/api/aurem-dev/loop/active" -H "Authorization: Bearer $TOKEN")
  printf '[poll %02d] %s\n' "$i" "$R"
  STATE=$(printf '%s' "$R" | python -c 'import sys,json; j=json.load(sys.stdin); print(((j.get("active") or {}).get("state") or "none"))')
  PLAN=$(printf '%s' "$R" | python -c 'import sys,json; j=json.load(sys.stdin); print("yes" if ((j.get("active") or {}).get("plan")) else "no")')
  if [ "$STATE" = "awaiting_confirmation" ] || [ "$PLAN" = "yes" ] || [ "$STATE" = "failed" ] || [ "$STATE" = "aborted" ] || [ "$STATE" = "completed" ] || [ "$STATE" = "none" ]; then
    break
  fi
  sleep 3
done

printf '\n# Cleanup: cancel loop if still active\n'
curl -sS -X POST "$BASE/api/aurem-dev/loop/$LOOP_ID/cancel" -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' --data '{}' || true
printf '\n'
