# 05 — DATA LAYER (MongoDB) & EXTERNAL INTEGRATIONS
(Self-contained context module. System map: file 01.)

## DATA LAYER — MongoDB via Motor
Connection: `MONGO_URL` + `DB_NAME` from `backend/.env` ONLY. Never hardcode, never change these keys.

### Key Collections (by actual usage frequency in code)
| Collection | Shape / Purpose |
|---|---|
| `dev_users` | User accounts, tier, auth identity — the primary user record |
| `cto_projects` | `{project_id, user_id, github_token, ...}` — per-user repo isolation. **`github_token` is a credential.** ALSO the single source of truth for readers that used to hit the (now-dropped) `onboarding_projects` collection. |
| `cto_tasks` | `{user_id, tool, status, cost}` — task-quota tracking (1 fix = 1 task) |
| `cto_fixed_findings` | `{project_id, rule_id, file, line, commit_sha}` — merge-gated fixed-findings ledger (pattern 2, file 01) |
| `cto_open_findings` | Iter 212m-190 · active backlog of security/health issues consumed by `ScanStatusStrip` |
| `cto_notification_dismissals` | Iter 212m-190 · `{user_id, project_id, finding_batch_id, dismissed_at}` for the strip |
| `cto_founder_suggestions` | Iter 212m-193 · founder feedback box (text + Groq-analysed benefits/risks + admin decision) |
| `council_health_probes` | Iter 212m-192 · Council A LongCat probe outcomes (persisted for the admin banner + trailing history) |
| `chat_sessions` | Multi-turn chat sessions (session_id per conversation) |
| `fix_jobs` | Bulk-fix job state consumed by SSE progress streams |
| `ora_council_logs` / `parliament_log` | Parliament/council decision logs |
| `loop_sessions` / `loop_locks` | Autonomous loop state + concurrency locks |
| `vanguard_audit` / `vanguard_ci_findings` | Vanguard security audit + CI-ingested findings |
| `codebase_health_scans` / `cto_codebase_index` / `repo_contexts` / `project_graphs` | Scan results, repo index, cached context, graphs |
| `github_connections` / `oauth_states` | GitHub identity links + OAuth CSRF state |
| `cto_payments` / `topup_alerts` / `founder_offer` / `referrals` | Billing surfaces |
| `feature_flags` / `ui_settings` / `thinking_hints` / `house_rules` | Admin-editable config |
| `api_keys` / `cto_support` / `cto_support_messages` / `frontend_errors` / `quality_scores` / `onboarding_emails` / `user_seo_claims` / `warm_start_jobs` / `cto_automations` / `aurem_cto_deploy_runs` / `aurem_cto_deploy_configs` / `github_deployments` / `integration_health` / `ora_fix_learning` / `cto_maxx_usage` | Supporting collections |

### Removed collections (Iter 212m-192 — Session 5 DB cleanup)
| Collection | Reason |
|---|---|
| `cto_review_logs` | Zero runtime writers/readers. Only referenced by the old index-creation migration. Dropped from Mongo + migration. |
| `onboarding_projects` | 5 readers (`trust.py`, `deploy.py`, `codebase_indexer.py`) but ZERO writers — every read silently returned None. Readers switched to `cto_projects`. Dead `is_production_dogfood` guard deleted from `deploy.py` (flag never populated). Dropped from Mongo + init script. |

## EXTERNAL INTEGRATIONS
| Integration | Purpose | Auth |
|---|---|---|
| GitHub REST API | Repo read/write, PRs, commits | User PAT (auto Read/Write scopes) |
| GitHub OAuth | **Identity-only** signup/login | OAuth app (internal integration) |
| Google Auth | 1-click signup | Emergent-managed endpoint |
| OpenRouter / Groq / Anthropic | LLM inference | Emergent LLM Key via `llm_router.py` |
| Meta Pixel | Marketing analytics | Frontend script |
| SSE | Real-time fix progress + chat streaming | Internal |

## RULES FOR THE AI DEVELOPER (hard constraints)
1. **NEVER expose `cto_projects.github_token`** — not in logs, not in API responses, not in the frontend, not in error messages.
2. Every query on `cto_projects` or any repo/user data MUST filter by `user_id`. Cross-user data access is a critical security bug (a repo-name leak to other users already happened once — do not repeat it).
3. `cto_fixed_findings` visibility is merge-gated: never surface a finding as resolved to the health score before its `commit_sha` is confirmed merged.
4. New product collections use the `cto_*` prefix.
5. Never return raw Mongo docs from endpoints — ObjectId → str via the model layer; datetimes use `datetime.now(timezone.utc)`.
6. All GitHub API calls go through the existing GitHub service layer (`github_api_writer.py`, `github_cache.py`, `repo_context.py`) — no scattered ad-hoc HTTP calls, and respect `.aurem_cache` first to avoid secondary rate limits.
7. OAuth (GitHub/Google) is identity-only — never conflate it with the PAT-based repo read/write flow.
8. All LLM traffic uses the Emergent LLM Key via `llm_router.py`/`smart_router.py` — never a hardcoded provider key.
9. New real-time features reuse the existing SSE infrastructure — no websockets, no polling as a parallel mechanism.
