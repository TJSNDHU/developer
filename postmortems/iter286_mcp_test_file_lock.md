# Iter 286 (Track 0) — MCP write-path bypassed the test-file lock
Date: 2026-02-06
Regression tests: `test_regression_iter286_mcp_test_file_lock.py` (5 tests)

## What happened
Session-internal audit (Master QA charter, Track 0) surfaced a real
authorization gap in the MCP surface. Two code paths could write and
commit to a customer repo while bypassing the pipeline gates Loop
mode enforces:

- `services/local_tools.py::write_repo_file` — direct MCP tool used
  by external clients (Claude Desktop, Cursor).
- `routers/cto_projects.py::_run_task` — the Mode-C `ship_code` MCP
  tool's downstream task pipeline.

Both committed whatever the LLM produced, gated only by Vanguard's
regex secrets scan. If an LLM tried to write `test_regression_*.py`
to satisfy a failing test — or a client asked "just fix the failing
test" — the change went through silently.

## Root cause
Two-fold:
1. `services/loop_diff_classifier.is_test_or_fixture` existed and was
   used by `loop_engine.py`, but was NOT imported / called on either
   MCP path.
2. Latency budget: `local_tools.py` had a comment stating LLM/E2B
   verification was intentionally kept off the MCP hot path. The
   speed-vs-safety trade-off leaked into a real safety hole.

## Fix
1. `write_repo_file` now imports `is_test_or_fixture` and blocks the
   write when the target is a test file, returning
   `{ok: false, gate: "test_file_lock"}`. An `allow_test_file_change`
   override in `args` bypasses the gate — designed for the Loop-mode
   post-approval commit path only.
2. `_run_task`'s commit phase (between the "Committing to GitHub…"
   emit and `gh_api_commit`) now classifies every entry in `edits`.
   If any hit `is_test_or_fixture`, the task is marked
   `status: blocked, blocked_reason: test_file_lock, blocked_paths: […]`
   and returns without committing.
3. Critically: `allow_test_file_change` is read via `cto_tasks
   .find_one({"task_id": task_id})` — i.e. from the task record set
   by human-approved code paths — NEVER from `edits` (LLM output).
   The model cannot self-grant. Enforced by a dedicated regression
   test (`test_regression_iter286_ship_code_override_not_llm_grantable`).

## Why our tests missed it
No test covered the "MCP write-path" surface. All prior regression
tests focused on the Loop pipeline, where `loop_diff_classifier` was
already invoked. The MCP router had 1,636 LOC of production infra
but zero tests exercising the write path with a test-file target.

## Prevention (what's now permanent)
- 5 regression tests locking both source-level and runtime behavior:
  - `write_repo_file` blocks test file by default (runtime).
  - `write_repo_file` allows test file with override (runtime).
  - `write_repo_file` allows normal paths (runtime).
  - `_run_task` gate is in source between phase_commit + gh_api_commit
    (source-level).
  - `allow_test_file_change` MUST NOT come from `edits` (SECURITY).

## MTTR
- Reported:  2026-02-06T01:30:00Z  (charter audit surfaced it)
- Deployed:  2026-02-06T02:00:00Z
- Total:     ~0.5 h

## Not-follow-ups
- Full held-out verifier on MCP hot path: intentionally NOT added
  in this iter per charter guidance ("if latency too heavy, gate ONLY
  the test-file-touch case"). Latency for LLM verifier on every MCP
  write would break the chat/MCP interactive UX. Test-file lock is
  the cheap, correct-for-purpose gate.
- Full BOLA audit / OWASP pass: deferred to Track 4, per charter
  sequencing.
