# PROD AGGRESSION TEST — FINAL REPORT
Date: 2026-07-02 | Target: https://auremcto.com (PROD) | Account: teji.ss1986@gmail.com (founder)
Repo: TJSNDHU/Aurem@main | Project: p_c2b5b8a916

## VERIFIED REAL COMMITS (7 — all on github.com/TJSNDHU/Aurem)
| SHA | Source | Task |
|---|---|---|
| 6e54e18 | Loop swift (DUPLICATE — split-brain double-ship) | get_current_user docstring |
| 0463625 | Loop swift (46.9s) | get_current_user docstring |
| 81f3f96 | Loop run 3 | hash_password docstring |
| 37887ff | Prompt+swift tasks/submit (26.2s) | require_auth docstring |
| e1466f3 | MCP ship_code (29.1s) | verify_password docstring |
| 8de126a | Loop | CONTRIBUTING.md |
| 91e8c42 | MOBILE-initiated loop (ship confirmed via API — UI bug) | require_admin docstring |

## TABLE 1 — MODE MATRIX
| Combo | Time | Council | Model | Rescue? | Ship? | Commit |
|---|---|---|---|---|---|---|
| Prompt+swift | chat 18.1s / ship 26.2s | A | longcat-2.0 | N | ✅ (2nd attempt; 1st Vanguard-blocked broken patch) | 37887ff |
| Prompt+pro | /send 119.1s proxy-killed; SSE 28.3s | A | longcat-2.0 | N | ❌ Vanguard BLOCK (hallucinated patch, 1 CRITICAL) | — |
| Prompt+maxx | chat 52.2s (SSE 15.2s) | A | longcat-2.0+claude-review | claude review fired | ❌ "AI returned no file edits" yet status=done | — |
| Loop+swift | 46.9s e2e | loop engine | — | N | ✅ | 0463625 (+dup 6e54e18) |
| Loop+pro* | 3 fails "LLM produced no usable file content"; 4th run 40s→ship | loop engine | — | split-brain rescue needed | ✅ | 81f3f96 |
| Loop+maxx* | 38.5s to ship-gate; split-brain needed 2 confirm retries | loop engine | — | — | ✅ | (same run as above set) |

*NOTE: /loop/start does NOT accept a review mode — UI auto-flips swift→pro in Loop; engine pipeline identical for all modes. Documented deviation.
*NOTE: identical docstring task became a no-op after first commit → combos 5/6 used sibling functions in the same file.

Review-mode latency (SSE, same question): swift 12.4s < maxx 15.2s < pro 28.3s.
FLAG: maxx faster than pro (mandate expected maxx slowest) — pro ran 3 tool calls vs maxx 2.
FLAG: expected GLM-5.2 primary; PROD serves longcat-2.0 as Council A primary (preview falls back to GLM — longcat invalid model id there).

## TABLE 2 — MCP TOOLS (post iter-175 scoping)
| Tool | Called? | Time(ms) | Correct data? | Notes |
|---|---|---|---|---|
| list_projects | ✅ | ~1000 | ✅ | |
| read_repo_file | ✅ | 1779 | ✅ | needs `file_path` arg; loud error for missing path |
| list_repo_files | ✅ | ~1100 | ❌ EMPTY | BUG: wrapper read `entries`, local returns `tree` — FIXED preview |
| search_repo | ✅ | 1160 | ❌ ERROR | BUG: wrapper sent `query`, tool wants `pattern` — FIXED preview |
| get_repo_structure | ✅ | ~1000 | ❌ EMPTY | BUG: key mismatch — FIXED preview |
| ship_code | ✅ | 29100 e2e | ✅ | real commit e1466f3 |
| get_task_status | ✅ | 1538 | ✅ | |

Scoped filtering: ✅ ALL PASS —
- "read the auth file" → read group (7 tools, all read-safe)
- "fix the login bug" → write group incl. ship_code/write_repo_file/get_task_status
- "run a security scan" → security group incl. get_scan_status (4 tools)
- Cap ≤7 held in every response.

## TABLE 3 — SCANNING
| Feature | Time | Result | Notes |
|---|---|---|---|
| run_vanguard_scan | scan_id in 2169ms (target <1s — marginal FLAG, incl. network RTT) | ❌ worker CRASH | `BINContext.repo_branch` attr bug — FIXED preview |
| run_health_scan | 17.4s | score 0 "CRITICAL RISK", 144 issues, 599 files | ✅ |
| /codebase-health/last | 0.2s | matches scan ✅ | API-level match |
| Codebase Health PAGE | — | ❌ shows "unscanned" | BUG: page never fetched /last — FIXED preview (FE+BE) |
| get_repo_health (MCP) | ~1000ms | score 100 "HEALTHY" | ❌ contradicts codebase-health score 0 — OPEN discrepancy |

## ADDITIONAL TASKS
- "Analyze the health of this codebase" → Council B: **UNVERIFIED** — turn hangs, ZERO SSE frames, proxy-killed ~125s (2/2 repro). P1 OPEN. Pre-gen timing instrumentation added in preview.
- "Write a short CONTRIBUTING.md" → expected Council C, got **A** — ROUTING MISMATCH (open). Loop-ship of same task succeeded (8de126a).
- "Check auth routes for missing input validation" → Council A ✅ glm-5.2 ✅ 36.7s; Vanguard skills injected ✅.

## ASK ADVISOR
- Run 1: streamed 27 steps, read repo files FIRST (R1 rule ✅), 70.5s, not orchestrator-fallback (step trace shows repo tools). Model=GLM-5.2: PARTIAL (provider frame not captured).
- Run 2: zero frames, killed 125.5s — same intermittent hang family (P1).
- Force-fail primary: SKIPPED (user-approved 4a — no safe PROD hook).
- Langfuse traces/tokens: UNVERIFIED (no API exposure; user-approved 3a).

## MOBILE PASS (390×844, PROD)
- Login ✅, hamburger drawer ✅ (opens, lists repos, closes on outside tap)
- Loop toggle visible ✅, OFF→ON ✅, auto mode flip swift→pro ✅
- Repo select via drawer sets active project ✅ (BUT fresh session does NOT auto-select the only repo — UX gap)
- Plan card ✅ → Approve ✅ → Execute ✅ → backend reached ship gate ✅
- ❌ **Ship button NEVER rendered** (SSE cross-worker event gap) — commit completed via API confirm (91e8c42)
- ❌ Page refresh does NOT restore pending-ship card (loop invisible to user)
- ❌ Loop errors render "[object Object]" (409 dict detail) — FIXED preview

## TABLE 4 — FAILURES / BUGS
| # | What broke | Where | Exact error | Severity | Status |
|---|---|---|---|---|---|
| 1 | list_repo_files empty | routers/mcp.py | key `entries` vs `tree` | HIGH | FIXED preview |
| 2 | search_repo always errors | routers/mcp.py | `Missing required arg pattern` | HIGH | FIXED preview |
| 3 | get_repo_structure empty | routers/mcp.py | key mismatch | MED | FIXED preview |
| 4 | Vanguard scan worker crash | routers/mcp.py:827 | `'BINContext' object has no attribute 'repo_branch'` | HIGH | FIXED preview |
| 5 | pause-response retry/skip 499 | routers/loop.py | confirm() state-guard ValueError unhandled | HIGH | FIXED preview |
| 6 | confirm-ship silent no-op (split-brain) | loop_engine `_LIVE` | stale worker engine; ValueError swallowed in create_task | CRITICAL | FIXED preview (evict + 409 guard) |
| 7 | Double commit (6e54e18+0463625) | same as #6 | duplicate ship | HIGH | mitigated by #6 |
| 8 | Health page ignores persisted scan | CodebaseHealth.jsx + /last | shows "unscanned" after paid scan | HIGH | FIXED preview |
| 9 | "[object Object]" loop error | ChatPanel.jsx | dict detail template-stringified | MED | FIXED preview |
| 10 | verify-pat passes read-only PAT | cto_projects.py | no push-permission check | HIGH | FIXED preview (+ prefilled PAT links now set contents=write) |
| 11 | analyze-health/advisor zero-frame hang | chat/stream pre-gen | proxy kill ~125s, intermittent | P1 OPEN | instrumentation added |
| 12 | Council C routing → A | council router | CONTRIBUTING task misrouted | MED OPEN | |
| 13 | Mobile ship button not rendered + no refresh-restore | loop SSE / FE | user stuck at ship gate | HIGH OPEN | |
| 14 | Task status "done" with zero edits | task pipeline | misleading success | MED OPEN | |
| 15 | Write-model hallucination on file rewrites | execute/task pipeline | Vanguard/ruff catch it, but 3/6 first-attempt failures | HIGH OPEN (quality) | |
| 16 | get_repo_health(100) vs codebase-health(0) | scoring sources | contradictory | MED OPEN | |
| 17 | /chat/send >125s proxy-killed (pro 119.1s) | infra ingress | empty body | LOW (UI uses SSE) | |

## FINAL VERDICT
Checks passed: **31 / 44** (10 FIXED in preview awaiting redeploy, 7 OPEN, force-fail + Langfuse tokens = UNVERIFIED by design).
NOTHING was faked or mocked — every commit URL above is live on GitHub; all timings from real PROD calls.
Skips: force-fail primary (no safe prod hook — user approved), Langfuse token counts (not exposed via API — user approved).
