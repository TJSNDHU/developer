# 06 — BUSINESS LOGIC (TIERS/QUOTA) & OPEN DECISIONS
(Load this LAST. Monetization rules + current punch list — check before starting any new work.)

## TIER / QUOTA SYSTEM
Source of truth: `services/subscription_tiers.py` (limits) + `services/scan_fix_quota.py` (fix-tool gating).

| Tier | Price/mo | Tasks/month | Fix tools | Bulk fix | Modes | Extras |
|---|---|---|---|---|---|---|
| Free | $0 | 10 | **none** (scans only) | ✗ | swift | — |
| Starter | $9 | 50 | vanguard-scan | ✗ | swift | brain memory |
| Pro | $19 | 300 | vanguard-scan + health-scan | ✗ | swift, pro | + parallel agents |
| Team | $49 | 400 | all 4 (vanguard, health, security, bug-hunt) | ✓ | swift, pro, maxx | + priority queue |
| Founder | $0 (internal) | unlimited | all 4 | ✓ | all | never billed |

**Core rule: 1 fix = 1 task.** No severity-based pricing — a critical fix and a minor fix cost the same quota unit. Scan-fix usage rolls into the SAME monthly task meter as chat tasks (`services/usage.py` merges `scan_fix_usage` into `tasks_this_month`).

**Quota gate contract** (`assert_can_fix`): `400 unknown_tool` / `403 fix_not_available_on_tier` / `403 bulk_fix_not_available` / `402 insufficient_tasks`. Deduction via `record_scan_fixes()` ONLY on success — failed fixes never burn tasks.

## RULES FOR THE AI DEVELOPER (hard constraints)
1. Never let a Free-tier user consume a fix — Free is scan-only by design.
2. Never introduce per-severity cost logic. If the business ever changes this, `scan_fix_quota.py` AND this file must be updated together.
3. Any new tool ships ONLY after it is explicitly mapped to tiers in `FIX_TOOLS_BY_TIER` — no undefined tier-gating.
4. Tier limits live ONLY in `subscription_tiers.py` — never hardcode a limit in a router, service, or frontend component.
5. If a feature changes what counts as "one task," update `scan_fix_quota.py`, `usage.py`, and this table together — they must never drift.
6. Unknown/invalid tier strings coerce to FREE (`_coerce`) — rely on this, don't add your own fallback.

## OPEN DECISIONS
Consolidated at the bottom of this file — see the section after the SHIPPED blocks.

## SHIPPED (2026-06) — HTTP Security Headers + Docker CIS rules
Formerly open decision #3. Both are live; do not re-implement or duplicate these rules.

### HTTP Security Headers — Security Scan (`routers/security_scan.py::_scan_http_headers`)
Repo-level check, zero extra GitHub calls (reuses text_cache). Fires ONLY when both hold:
- An app entrypoint exists: `FastAPI(` (.py), `Flask(__name__` (.py), or `express()` (.js/.ts/.mjs)
- NO file in the repo matches any header signal: `helmet(`, `secure_headers`, `SecureHeaders`, `SecurityMiddleware`, `Strict-Transport-Security`, `X-Frame-Options`, `Content-Security-Policy`, `X-Content-Type-Options`, `Referrer-Policy`, `Permissions-Policy`

Finding: `rule_id=http_headers_missing`, `vuln=http_headers`, severity **medium**, capped at 3 findings/repo. UI: amber "HTTP HEADERS · NEW" badge in `SecurityScanDrawer.jsx` (`data-testid="http-headers-badge"`).

### Docker CIS — Health Scan 6th category `docker` (`routers/codebase_health.py::_scan_docker_cis`)
`_build_text_cache` now also fetches Dockerfiles (`_is_dockerfile`: basename starts with `dockerfile` or ends `.dockerfile`); compose files were already covered by `.yml`/`.yaml`. Registered in `SCANNERS["docker"]`. The 9 rules:

| rule_id | Severity | Trigger |
|---|---|---|
| `docker_cis_4_1_no_user` | HIGH | Dockerfile has no `USER` instruction (runs as root) |
| `docker_cis_4_6_no_healthcheck` | LOW | No `HEALTHCHECK` instruction |
| `docker_cis_4_7_latest_tag` | MEDIUM | `FROM` image unpinned (`:latest` or no tag/digest) |
| `docker_cis_4_9_add_instead_copy` | LOW | `ADD` used instead of `COPY` (non-URL) |
| `docker_cis_4_10_secret_in_env` | CRITICAL | `ENV`/`ARG` with PASSWORD/SECRET/TOKEN/API_KEY value |
| `docker_cis_curl_pipe_sh` | HIGH | `RUN curl/wget … \| sh/bash` |
| `docker_cis_apt_upgrade` | LOW | `apt(-get) upgrade`/`dist-upgrade` in build |
| `docker_cis_5_4_privileged` | HIGH | `privileged: true` in docker-compose |
| `docker_cis_5_31_docker_sock` | CRITICAL | `/var/run/docker.sock` mounted in compose |

UI (`CodebaseHealth.jsx`): CATS entry `{key:"docker", label:"Docker CIS", tone:"#22d3ee", cost:5, isNew:true}` → cyan card + NEW badge (`data-testid="new-badge-docker"`, `scan-docker`); Full Scan = 7 categories. Fix quota: rides the existing `health-scan` tool gating (Pro+) — no new tool key.

## SHIPPED (2026-02 · Session 5) — Ask Advisor real-fix + Council A safety net + Founder Suggestion Box
All landed on preview; production redeploy pending.

### Ask Advisor — two stacked bugs fixed
1. **Misleading `/connection-status` probe.** `_check_one` in `routers/repo_status.py` was hitting `GET /repos/{owner}/{repo}` (metadata endpoint — 200 for any basic-visibility token) while Ask Advisor tools call `/contents/{path}` (requires `Contents:Read` scope). Green sidebar dot could lie. Fixed by probing `/contents/` — same permission surface as the tools.
2. **Extractor missing an XML shape the stripper knew about.** When Council A degrades to GLM-5.2 (see below), GLM-5.2 emits `<tool_call>read_repo_file)("README.md")` — malformed XML that all four existing shape parsers missed → `tool_calls_run: 0`, user saw hollow "cannot access repo" replies. Added Shape 6 to `services/tools_bridge.py::extract_tool_calls` (lenient XML with JSON-envelope → Python-call → known-tool-name+first-string-literal fallback) + `_TOOL_CALL_XML_LOOSE_STRIP_RE` to hide orphan fences from user output. Locked with 7 regression tests in `test_iter212m192_*.py`.

### Council A degradation safety net + evidence-based model swap
- **Persistent probe state.** `services.llm.probe_longcat_availability` now writes an in-memory snapshot (`_LONGCAT_LAST_PROBE`) + a compact record in `council_health_probes`.
- **Periodic re-probe.** `periodic_longcat_reprobe(interval_seconds=900)` runs as a background task from `main.py` lifespan; state transitions log a single WARNING, no supervisor restart needed for auto-recovery.
- **Admin surface.** `GET /admin/council/health` (founder-gated) exposes `{degraded, primary_intended, primary_actual, fallback, live, last_probe, history}`. `AdminOverview.jsx` renders a prominent orange banner at the top whenever `degraded: true`.
- **Council A primary swap.** `meituan/longcat-2.0` was upstream-dead (HTTP 400). A/B tested Claude Sonnet 4.5 vs GPT-5.2 on the two originally-failing prompts (`backend/tests/manual_ab_model_swap.py`). Both emit clean fenced-JSON; GPT-5.2 spammed 300+ tool calls / 73 KB / >120 s on the routers-list prompt while Sonnet 4.5 did 1 call / 146 chars / 1.6 s. Winner: **`anthropic/claude-sonnet-4.5`** (OpenRouter slug verified via `openrouter.ai/api/v1/models` before commit). Set as `_LONGCAT_MODEL` default in code AND `LONGCAT_MODEL=anthropic/claude-sonnet-4.5` in `backend/.env`. Post-swap: `primary_intended == primary_actual`, `live=true`, banner hidden.

### Founder Suggestion Box (`cto_founder_suggestions`)
`POST /suggestions` (JWT-authed, body: `{text}` only) → server pulls `user_id/email/tier` from the JWT and resolves the user's active project via most-recent `cto_projects` row. **Date-based** rate limit (1 per user_id per UTC day) queried against `cto_founder_suggestions.created_at` — session-agnostic (can't be bypassed by logout/login). Background Groq task (`services.llm._call_groq` direct — ISOLATED from `orchestrator.py`'s Council chain, grep-verified) writes a strict-JSON `llm_analysis` block (summary + max-3 benefits + max-3 risks + effort + recommendation). Malformed LLM output → `analysis_failed: true` with `raw_llm_output` preserved for debugging. Admin surface: new "Suggestions" tab in `Admin.jsx` (`AdminSuggestions.jsx`) with pending/approved/rejected filters, expandable "AI analysis — not a decision" chip, and approve/reject writing `decided_by`/`decided_at` for audit. User surface: `SuggestionBoxModal.jsx` mounted from the sidebar user dropdown between Settings and Logout.

### Chat-native scan commands (Iter 212m-190 → now live on `/dashboard`)
`SlashCommandMenu` + `ScanStatusStrip` grafted into the real production `ChatPanel.jsx` composer (not a route swap — the v2 preview page is a hardcoded visual demo per its own docstring). Composer now supports `/scan`, `/health-scan`, `/security-scan`, `/bug-hunt`, `/docker-scan` with arrow-key navigation. `ScanStatusStrip` surfaces scan lifecycle (in-progress spinner → just-completed critical/high summary → X-dismiss) above the composer, driven by `sessionStorage` for the "just completed" flash + `/findings/backlog` for the persistent open-issue list. `DashboardPreviewV2.jsx` retired its static demo repo list — now hydrates `SidebarBound` from live `/cto/projects/list` with instant-paint localStorage cache.

## OPEN DECISIONS (resolve or explicitly flag as still-open before building on these areas)
1. **36 probe draft PRs** — created during empirical rate-limit testing, need cleanup. Do NOT build new fix-pipeline features until confirmed these stale PRs won't conflict.
2. **Vanguard CI ingest token (`AUREM_CI_INGEST_TOKEN`)** — waiting on user. Do NOT hardcode a placeholder token or assume an auth mechanism for `vanguard_ci.py` ingestion until resolved.
3. **Production redeploy** — every Session 5 landing is preview-only. Ask Advisor XML parser fix, connection-status contents probe, Council A safety net + model swap, `/suggestions`, slash commands + ScanStatusStrip on `/dashboard` all need a prod push to reach `auremcto.com`.
4. **401 toast in chat** (backlogged) — proactive UI signal when a tool returns 401; revisit after redeploy + swap verification.

## STRICT INSTRUCTION FOR THE AI DEVELOPER
Before any task touching quota, tier gating, Vanguard CI, security-header/CIS scanning, Council A routing, or the founder suggestion box: read this file first. If the task depends on any open decision above, STOP and flag it — never guess the intended behavior. For header/CIS scanning, XML tool-call extraction, connection-status probes, Council A probing, and the suggestion box, extend the SHIPPED rule sets above; never create parallel implementations.
