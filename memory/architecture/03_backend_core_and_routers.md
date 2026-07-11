# 03 — BACKEND: CORE ("PARLIAMENT") & ROUTERS
(Self-contained context module. System map: file 01. Services: file 04.)

## STACK
FastAPI on port 8001 (supervisor-managed). Entry point: `backend/main.py` (all 46 `include_router` calls). Request flow: **Router → Core (Parliament) → Services → External APIs**.

## CORE — `backend/core/` (the "Parliament" brain)
| Module | Function |
|---|---|
| `intent_gateway.py` | FIRST stop for every AI request — classifies intent (chat vs task vs scan) |
| `parliament.py` | Multi-agent router — decides which agent/mode handles the request |
| `tool_router.py` | Tool selection & dispatch |
| `task_type.py` | Task-type definitions shared across routing |
| `quality_monitor.py` | Response quality scoring (writes `quality_scores`) |
| `observability.py` | Tracing/observability hooks |

## ROUTERS — `backend/routers/` (46 files)
| Category | Files |
|---|---|
| Auth & Identity | `auth.py`, `oauth.py`, `github_oauth.py`, `mfa.py`, `onboarding.py` |
| Chat / AI | `chat.py`, `chat_commits.py`, `loop.py`, `diagram.py`, `thinking_hints.py` |
| Scanning | `codebase_health.py`, `security_scan.py`, `lint_preview.py`, `vanguard_ci.py`, `harden.py` |
| Fixing | `fix_pipeline.py` (SSE bulk fix, quota-enforced via `scan_fix_quota.py`) |
| Repo / GitHub | `cto_projects.py`, `repo_indexing.py`, `repo_status.py`, `github_bot.py`, `mcp.py` |
| Business | `payments.py`, `usage.py`, `founder_offer.py`, `unlock.py`, `feature_window.py`, `engagement.py`, `notify_interest.py`, `trust.py`, `trust_level.py`, `shipwall.py`, `wrapped.py` |
| Deploy | `deploy.py`, `vercel.py`, `hosted_deploy.py`, `github_deploy.py`, `domain.py`, `stacks.py` |
| Admin | `admin.py`, `admin_vanguard.py`, `admin_bin.py` |
| Misc | `automations.py`, `support.py`, `upload.py`, `vault.py` |

## KEY ENDPOINTS
- `POST /api/aurem-dev/scan-fix-quota` — quota snapshot for fix surfaces
- `POST /api/aurem-dev/codebase-health/fix` — apply fixes (quota-gated, SSE progress)

## QUOTA GATE CONTRACT (`assert_can_fix` in `services/scan_fix_quota.py`)
Called BEFORE any fix work runs. Error codes:
- `400 unknown_tool` — tool not in {vanguard-scan, health-scan, security-scan, bug-hunt}
- `403 fix_not_available_on_tier` — tool not in tier's fix set
- `403 bulk_fix_not_available` — count > 1 on non-Team/Founder tier
- `402 insufficient_tasks` — count > tasks remaining this month

## RULES FOR THE AI DEVELOPER (hard constraints)
1. Every route MUST be prefixed `/api` and registered in `backend/main.py`.
2. Never bypass `intent_gateway.py` — no endpoint may invoke a Parliament agent directly.
3. New endpoints go in the existing router file matching their category — no new top-level router files without clear justification.
4. Any endpoint that applies fixes MUST call `assert_can_fix()` BEFORE work starts and `record_scan_fixes()` ONLY per successful fix. Never deduct quota for failed fixes.
5. Auth flows go through the existing JWT + OAuth machinery in `auth.py`/`oauth.py`/`github_oauth.py` — no parallel auth mechanism. Auth changes require the integration playbook first.
6. Fix endpoints stream progress via SSE, matching `fix_pipeline.py`'s pattern.
7. Admin endpoints live only in `admin*.py` with admin-role checks — never expose admin logic through a user-facing router.
8. Never return raw Mongo documents (ObjectId is not JSON serializable) and never return `github_token` in any response.
