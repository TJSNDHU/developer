# Section 0 — QA Sandbox Setup (founder manual steps)

The Auto-QA agent's **UI ship/rollback scenarios** and **reliable prod-CI
runs** need a dedicated, isolated identity — never the human founder's
account or the real product repo. Everything here requires GitHub / the
`auremcto.com` domain / prod access, so **only the founder can create it**
(the coding agent has no GitHub write, no OAuth, no prod DB, no prod JWT
secret). The agent has already wired the code to consume these values —
once the secrets exist, scenarios go PASS/FAIL instead of INCONCLUSIVE.

## What the founder creates (exact names the code expects)

1. **QA bot account** — email `qa-bot@auremcto.com`
   - Sign it up on production like a normal user.
   - It only needs to exist + be able to log in. No special perms.

2. **Sandbox repo** — `TJSNDHU/aurem-qa-sandbox` (private is fine)
   - Seed two dummy files ONLY:
     - `tests/qa_sandbox_marker.py`  → content exactly:
       ```
       # qa sandbox marker — baseline
       ```
       (must match `qa_matrix.PRE_SHIP_BASELINE_CONTENT` for the
        rollback-verification check)
     - `README.md` → any text
   - Connect `qa-bot@auremcto.com` to this repo via the normal
     GitHub-OAuth flow in the app. NEVER connect the real `TJSNDHU/Aurem`.

3. **`QA_BOT_SESSION_TOKEN`** (GitHub Actions secret)
   - Log into production as `qa-bot@auremcto.com`, copy the session
     token returned by `/auth/login` (or `/auth/me`'s `token`).
   - Add it as repo secret `QA_BOT_SESSION_TOKEN`.
   - The scanner uses this Bearer directly (no password in CI).

4. **`QA_REPORT_COMMIT_TOKEN`** (GitHub Actions secret)
   - A GitHub **fine-grained PAT**, scope **Contents: Read and write**,
     restricted to **`TJSNDHU/aurem-qa-sandbox` only**.
   - Add as repo secret `QA_REPORT_COMMIT_TOKEN`.
   - Used by the `auto-qa-agent` CI job to commit the report back.

## Env-var contract (already implemented in services/qa_matrix.py)

| Env var | Default (preview) | Used for |
|---|---|---|
| `QA_BOT_SESSION_TOKEN` | *(unset)* | Bearer for authed scans; **preferred**. If set, no login happens. |
| `QA_SCAN_EMAIL` | `test@aurem.dev` | Fallback login email (synthetic seed acct `test_admin_001`, NOT founder) |
| `QA_SCAN_PASSWORD` | `AuremTest2026!` | Fallback login password |
| `QA_API_BASE` | `http://localhost:8001/api/aurem-dev` | Backend base for scans |

Auth resolution: **`QA_BOT_SESSION_TOKEN` wins**; else it logs in with
`QA_SCAN_EMAIL`/`QA_SCAN_PASSWORD`; else the scenario returns
`INCONCLUSIVE` (never a false PASS).

## After setup
- Set the two secrets → the `auto-qa-agent` CI job's `secret_leak_scan`
  and (future) ship/rollback UI scenarios run against the sandbox
  identity, reliably PASS/FAIL.
- Nothing in the agent's code needs to change; it already reads these.
