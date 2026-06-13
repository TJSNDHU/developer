# Sentry Error Monitoring Setup

Sentry SDK is **already installed and wired** in `backend/main.py`.
Activation is a single environment variable — no code changes.

## Production Setup (Emergent Dashboard → Env vars)

```
SENTRY_DSN = https://your-key@sentry.io/your-project-id
```

That's it. On the next deploy, the FastAPI app will:
- Initialise the Sentry SDK explicitly (no auto-discovery, so no
  startup latency cost — see `services/sentry_config.py` and
  Iter 128 lifespan fix).
- Tag every exception with the request path, method, and (if
  authenticated) the `user_id`.
- Capture slow requests (>5s by default) as Sentry "performance"
  events with `X-Response-Time-Ms` already set on the response.

## Create a Sentry project

1. <https://sentry.io/> → **New Project** → **Python** → **FastAPI**.
2. Copy the DSN from the wizard.
3. Open Emergent deployment dashboard for `auremcto.com` → **Env Vars**
   → add `SENTRY_DSN=<your dsn>` → **Redeploy**.
4. Verify ingestion: visit `/api/_diag/memory` with an invalid admin
   token; the resulting 401 should appear in Sentry within ~30s.

## What gets captured automatically

| Signal | Source |
|---|---|
| Unhandled exceptions (500s) | `_global_exc_handler` in `main.py` |
| Cancelled SSE streams (499) | `_security_headers` middleware |
| Slow requests (>5s) | `_security_headers` middleware |
| Background task failures | `services/background_*.py` (caught) |
| MongoDB ping failures | Lifespan bootstrap (logged + Sentry) |

## What is intentionally NOT captured

- 401 / 403 from `auth.py` — these are expected behaviour, not bugs.
- 429 rate-limit responses — these are by-design from
  `services/rate_limiter.py`.
- `/api/healthz` probes — would flood Sentry.

These filters live in `services/sentry_config.py::before_send`.

## Local development

Leave `SENTRY_DSN` UNSET. The SDK detects the missing DSN and silently
no-ops. Iter 128 made sure this path doesn't add any startup latency.

## Staging branch

The CI pipeline (`.github/workflows/ci.yml`) runs on `dev` and
`staging` branches too, so you can preview tests before merging to
`main`. To gate staging deploys on Sentry-quality metrics, set
`SENTRY_DSN_STAGING` on the staging deploy environment and point a
separate Sentry project at it.
