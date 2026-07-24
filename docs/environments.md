# docs/environments.md — Environment Parity Ledger

> **Ground rule** (from AUREM QA Meta-Layer, Part A):
> Document what actually IS true, verified by inspection. Never
> what SHOULD be true. Every claim below has a `_verified_at` date
> and the exact shell command that was run. When any of them
> changes, update this file — don't let it drift into "aspirational".

Verified: **2026-02 (Iter 292)** — via direct inspection of
`/app/backend/.env`, `/app/frontend/.env`, and `sudo supervisorctl
status` on this preview container.

---

## 0. Three environments (naming convention this project uses)

| Nickname       | Where                                              | Reachable now?                                     |
|----------------|----------------------------------------------------|----------------------------------------------------|
| `local`        | Inside this pod / dev container                    | `curl http://localhost:8001/...`                   |
| `preview`      | The public Emergent preview URL for this job       | `curl https://launch-pad-237.preview.emergentagent.com/api/...` |
| `production`   | Real prod behind the founder's custom domain       | Deployed via **`emergent__send_to_deployer`** tool; not directly reachable from this pod |

The critical mistake this ledger exists to prevent: treating
"green on preview" as "green on production." Every deploy-completion
report from this iter onward MUST state, per changed file/feature,
**live on preview: yes/no** and **live on production: yes/no** —
never one blanket "deployed" claim.

---

## 1. MongoDB — different databases per environment

| Env         | MONGO_URL                        | DB_NAME                           | Verified how                                                  |
|-------------|----------------------------------|-----------------------------------|---------------------------------------------------------------|
| local       | `mongodb://localhost:27017`      | `aurem_dev`                       | Same physical Mongo as `preview` in this pod.                 |
| preview     | `mongodb://localhost:27017`      | `aurem_dev` **(confirmed)**       | Iter 292 — `AsyncIOMotorClient(MONGO_URL).list_database_names()` returned `['admin','aurem_dev','config','local']`. `/loop/_diagnostics` returns `db_name: "aurem_dev"` for a founder-authenticated caller. |
| production  | Emergent-managed prod Mongo      | **UNVERIFIED from this pod**      | Prod runs behind a separate Emergent-managed URL that this preview pod cannot reach. See "How to verify prod" below. |

**Prior confusion the project has hit**:
Preview's `aurem_dev` was silently treated as if it were prod's DB —
real bug data (loop_1f8, loop_bff) was searched here and returned 0
matches because it lived in prod, not preview. TTL evictions (7-day,
per iter282) further prune anything old.

**How to verify prod DB name (founder-only, one-shot)**:
```bash
curl -s "https://<PROD_URL>/api/aurem-dev/loop/_diagnostics" \
     -H "Authorization: Bearer <FOUNDER_JWT>" | jq .db_name
```
Paste the value back into this ledger under the `production` row.
Until then, the prod db_name field above stays **UNVERIFIED** —
not "likely X", not guessed. Iter 292 explicitly declined to guess.

**Rule**: never assume a query against this pod's Mongo tells you
anything about prod. If you need prod DB evidence, ask the
founder to run the query against prod, or route through the
deployer agent.

---

## 2. Environment variables — which ones MUST be identical in prod

Verified list of keys present in `/app/backend/.env` on preview
(values redacted; audit via `grep -E "^[A-Z_]+=" /app/backend/.env`):

Standard app config (must exist in both preview + prod):
```
JWT_SECRET, MONGO_URL, DB_NAME, EMERGENT_LLM_KEY, OPENROUTER_API_KEY,
STRIPE_API_KEY, STRIPE_WEBHOOK_SECRET, STRIPE_*_PRICE_ID (6 SKUs),
GITHUB_OAUTH_CLIENT_ID, GITHUB_OAUTH_CLIENT_SECRET,
GITHUB_REDIRECT_URI, RESEND_API_KEY, RESEND_FROM_EMAIL,
SENTRY_DSN, SENTRY_ENV, SENTRY_RELEASE, APP_URL, CORS_ORIGINS,
FOUNDER_EMAILS, AUREM_MASTER_KEY
```

Third-party integrations (must exist in both):
```
FIRECRAWL_API_KEY, TAVILY_API_KEY, GROQ_API_KEY, DEEPSEEK_API_KEY,
LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, LANGFUSE_BASE_URL,
VERCEL_API_TOKEN, VERCEL_DEPLOY_HOOK_URL
```

Feature flags (may legitimately differ per env):
```
AUREM_QA_MODE (preview=true; prod SHOULD be false but is unverified)
ORA_CANARY_ENABLED (preview=1)
LONGCAT_ENABLED, COUNCIL_B_GLM_ENABLED, CEO_RESCUE_ENABLED
```

**Currently MISSING in preview** (5 vars needed for Track 1
Lane B + `/personal-track/materialize` — flagged since iter289):
```
AUREM_ORG_NAME, AUREM_ORG_GITHUB_APP_TOKEN,
AUREM_CANARY_REPO_OWNER, AUREM_CANARY_REPO_NAME, AUREM_CANARY_BRANCH
```

Frontend `.env` on preview (verified):
```
REACT_APP_BACKEND_URL=https://launch-pad-237.preview.emergentagent.com
ENABLE_HEALTH_CHECK, WDS_SOCKET_PORT
```

**Rule**: any deploy that adds or changes an env-var reference in
code MUST also verify the var exists in BOTH the preview .env
AND the production env panel — with an explicit checklist tick.
No "should be there" assumptions.

---

## 3. GitHub App installation scope

Verified on preview:
- `GITHUB_OAUTH_CLIENT_ID` + `GITHUB_OAUTH_CLIENT_SECRET` are set → OAuth flow works locally.
- `GITHUB_TOKEN` (org-level PAT for AUREM's ops) is set.
- `GITHUB_ORG` is set (value redacted).

**Not verified** (need founder confirmation):
- Whether the AUREM GitHub App is installed on the founder's org on prod, and which repos it can access.
- Whether the org-level PAT on prod is the SAME token as on preview or a different one.

Rule: for any feature that pushes to a real GitHub repo (Loop
ship, personal-track materialize, canary Lane B), the deploy
report must state "GitHub App installed on {repo}: yes/no on prod".

---

## 4. Services running (supervisor-managed) — this pod only

```
backend           RUNNING   (uvicorn on 0.0.0.0:8001)
frontend          RUNNING   (yarn dev on 3000)
mongodb           RUNNING   (local mongod, port 27017)
nginx-code-proxy  RUNNING
webhook-crond     RUNNING
code-server       STOPPED   (unused in prod path)
```

Production runs behind Emergent's managed infra — the process list
above does NOT necessarily reflect what's running in prod. Don't
assume symmetry.

---

## 5. The minimal promotion gate

Before any change may be claimed "production-ready":

1. Run the diagnostic endpoint (or its per-feature equivalent) on
   **both** preview and prod. Paste both outputs side-by-side in
   the finish-summary — never assume prod matches preview just
   because preview is green.
2. If the change touches env vars: assert the var exists in BOTH
   `/app/backend/.env` on preview AND the production env panel.
   The absence of either half means the change is NOT
   production-ready.
3. If the change touches Mongo indexes / TTLs / collection
   creation: verify via `sudo supervisorctl restart backend` on
   preview succeeds AND the `init_prod_collections.py` bootstrap
   ran cleanly against prod (this was the iter282 lesson).

---

## Update log

- 2026-02 (Iter 292) — file created, first verified pass.
