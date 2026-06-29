# Vanguard CI Ingest Setup — 5-min activation

The Vanguard CI ingest pipeline (Iter 212m-120) is fully built. It receives
secret-scan results from GitHub Actions, redacts the raw secrets, persists
them to Mongo, and surfaces verified hits on the dashboard. The wiring just
needs two values you control.

## 1. Set the backend secret

```bash
# Generate any 32+ char random string
openssl rand -hex 32
# Example: a3f9...b2e7
```

Add this to your production backend `.env` (NOT the codebase):

```
AUREM_CI_INGEST_TOKEN=<paste-the-random-string-here>
```

Restart the backend (or hot-reload picks it up).

**Verify**: as a founder, hit

```
GET /api/aurem-dev/vanguard/ci-ingest-status
```

You should see `"token_set": true`.

## 2. Add the matching GitHub repo secret

For each repo you want scanned (e.g. `TJSNDHU/Aurem`, `polarisbuiltinc-wq/auremdev`):

1. Go to the repo on GitHub → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**.
2. Name: `AUREM_CI_INGEST_TOKEN`
3. Value: paste the SAME random string from step 1.

Optionally also add a repo variable (Settings → Variables):
- Name: `AUREM_API_URL`
- Value: `https://auremcto.com` (defaults to this anyway)

## 3. Push a commit

The first push to `main` will fire `.github/workflows/ci.yml → secret-scan` job.
It will:
- Install trufflehog
- Scan filesystem
- POST findings (redacted) to `/api/aurem-dev/vanguard/ci-findings`

**Verify**: hit `GET /api/aurem-dev/vanguard/ci-ingest-status` again. You should see:

```json
{
  "ready": true,
  "token_set": true,
  "run_count": 1,
  "last_run": { "repo": "...", "commit": "...", ... }
}
```

The dashboard `SecretScanCard` will start showing data from this point.

## How verification gates the deploy

The `secret-scan` job is wired into the CI's `deploy-gate` `needs:` array.
If trufflehog finds a **verified** (live, working) secret, the deploy is
blocked. Pattern-only hits (could be a test fixture) warn but don't block —
this is by design to avoid noisy CI failures on dev branches.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `ci-ingest-status` returns `token_set: false` | env var not set | step 1 |
| Job runs, POSTs, gets HTTP 401 | tokens don't match | re-paste step 2 |
| Job doesn't run at all | workflow file missing in repo | copy `.github/workflows/ci.yml` from this repo |
| Job runs, gets HTTP 503 | backend env var still empty after deploy | restart backend pod after setting env |
