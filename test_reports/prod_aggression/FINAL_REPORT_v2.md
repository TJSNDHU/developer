# PROD Aggression Re-run #2 + Feature Audit — 2026-07-02 (Iter 178)
Target: https://auremcto.com | Founder account | Repo TJSNDHU/Aurem@main | Project p_c2b5b8a916
NOTE: PROD currently runs Iter-177 code. Fixes below are PREVIEW-only → need redeploy.

## FEATURE AUDIT — really working? (real calls, real commits)
| Feature | Result | Time | Proof |
|---|---|---|---|
| Security Scan (`/security-scan/run`) | ✅ WORKS | 24.2s | 4 real findings (2×redos HIGH, 2×lpdos MED) |
| Fix — ONE-BY-ONE (`/security-scan/fix`) | ✅ WORKS | 33.1s | commit `8feec75`, correct bounded-quantifier minimal 2-line diff |
| Fix — BULK (`/fix-pipeline/bulk`) | ⚠️ PARTIAL→FIXED | 1st fix commits, 2nd+ `github_status_403` | GitHub SECONDARY rate limit (burst writes) |
| MCP scoped filtering | ✅ WORKS | — | caps: read=5, write=7, security=4 (all ≤7) |
| Health scan + score unify (P1-5) | ✅ WORKS | 32.4s | codebase-health=0, /last=0, MCP get_repo_health=0 (all match now) |

## MCP TOOLS (PROD, Iter-177 live) — 7/7 return correct data
| Tool | ms | OK |
|---|---|---|
| list_projects | 1795 | ✅ |
| read_repo_file | 1716 | ✅ |
| list_repo_files | 3076 | ✅ |
| search_repo | **79095** ⚠️ | ✅ data, but 79s → **FIXED (budget cap → <15s)** |
| get_repo_structure | 1360 | ✅ |
| get_task_status | 1385 | ✅ |
| ship_code | ~29000 | ✅ (real commits verified in run #1) |

## COUNCIL ROUTING (PROD)
- "Summarize recent commits" → **council B**, task_type=analysis ✅ (P0-3 inference LIVE on PROD)
- "Write CODE_OF_CONDUCT.md" → **council A** ❌ (vocab gap) → **FIXED (vocab expanded; now 'write'→C; 11/11 local cases pass)**

## CHAT HANG (P1-6) — ROOT CAUSE FOUND
| Turn | frames | total_s |
|---|---|---|
| review swift | **0** | **125.8 (proxy-killed)** |
| review pro | 53 | 20.1 ✅ |
| review maxx | 82 | 15.2 ✅ |
| advisor (parliament) | **0** | **126.3 (killed)** |
| analyze health | **0** | **125.6 (killed)** |

ROOT CAUSE: the agentic advisor/analyze turn calls repo tools; **search_repo took 79s**
on the 16k-file repo (fetched every file until 20 matches). The tool call had NO timeout,
so the whole SSE turn stalled past the ~125s ingress proxy limit → ZERO frames.
My earlier Iter-177 pre-gen timeouts were the WRONG layer (hang is INSIDE gen() tool loop).

FIXES (preview, need redeploy):
1. `search_repo` hard budget: max 400 files fetched OR 15s wall-clock, prefer code/text
   extensions, return `budget_hit`/`files_fetched`. Verified via unit tests (5000-file tree
   → ≤420 fetched; .png assets skipped).
2. orchestrator agentic loop: every `invoke_local_tool` hard-capped at 45s → typed
   `timed_out` the LLM can react to, instead of stalling the whole turn.

## BULK-FIX 403 — ROOT CAUSE + FIX
Not a submodule (no .gitmodules), file readable via Contents API (200) when isolated.
The 403 hit only mid-bulk right after a fix's blob+tree+commit+ref burst = GitHub
**secondary rate limit**. FIX (preview):
- `_fetch_file_content` retries 403/429 honouring `Retry-After` (≤30s, 2 retries).
- bulk loop paces GitHub mutations with 1.5s gap between findings.

## TESTS
- `test_iter212m178_prod_perf.py` (6) + `test_iter212m177_prod_reliability.py` (17) = **23/23 PASS**.
- Regression: parliament leak-test still red but IDENTICAL to baseline (4 pre-existing:
  admin_bin, ora_context, orchestrator, smart_router) — my changes add ZERO new leaks
  (task_type inference extracted to `core/task_type.py`; chat.py no longer imports parliament).
- Other pre-existing reds (vercel repl-tools, iter138 echo, iter163 REACT_APP env) unrelated.

## VERDICT
Working on PROD NOW: security scan, single fix, MCP 7 tools, scoped filtering, health-score unify, council B.
Fixed in preview (redeploy needed): search_repo 79s, chat/advisor zero-frame hang, bulk-fix 403, council-C vocab.
