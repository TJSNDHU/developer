# ORA Learning Callsite Proposal (Iter 328 · #3-b prep)

**Purpose**: give founder a decision-ready list of WHERE each brain-write
function should be reattached, so step (b) can ship with minimum
back-and-forth when browser access is back.

**Status of the 3 write functions** (as of Iter 328 · #3-a):
| Function | Fail-open logging | Current prod callsites |
|---|---|---|
| `update_brain_after_commit` | ✅ WARNING on fail, INFO on success | **0** (tests only) |
| `update_brain_from_conversation` | ✅ WARNING on fail, INFO on success | **0** (tests only) |
| `update_brain_after_task` | ✅ (already had try/except) | **0** (tests only) |

`_persist_parliament_row` doesn't exist as a function — the docstring
that mentions it appears to be from an aspirational spec that was never
implemented.

---

## Proposed callsites (per function)

### 1. `update_brain_after_commit(db, project_id, task_description, files_changed, was_correction_applied, issues_found, sha)`

**Where it belongs**: right after any successful `gh_api_commit` /
`revert_commit` returns a `sha`. Two natural attach points:

**A · `backend/services/loop_engine.py` `confirm_ship` success branch**
(around line 2820, right after `full_sha = ...` is set):
```python
# Iter 328 · #3-b — brain write after real GitHub commit lands.
try:
    from services.project_brain import update_brain_after_commit
    asyncio.create_task(update_brain_after_commit(
        db=self.db,
        project_id=self.project_id or "",
        task_description=(self.context.get("original_prompt") or "")[:200],
        files_changed=list(files_dict.keys()),
        was_correction_applied=bool(self.context.get("integrity_guard")),
        issues_found=[],
        sha=full_sha,
    ))
except Exception:
    pass
```
Fire-and-forget via `create_task`. Cannot block ship path.

**B · `backend/routers/cto_projects.py` `_run_task_worker` post-commit**
(if any legacy task path still uses gh_api_commit outside loop mode):
Search for `gh_api_commit(` in cto_projects.py and attach the same
call after success.

### 2. `update_brain_from_conversation(db, project_id, user_message, ora_reply, mode)`

**Where it belongs**: after Mode B (advice) chat conversations complete
streaming. Natural spot:

**A · `backend/routers/chat.py` SSE handler post-stream** — look for
where the final reply chunk is emitted; call:
```python
try:
    asyncio.create_task(update_brain_from_conversation(
        db, project_id, user_msg, full_reply, mode="B",
    ))
except Exception:
    pass
```

Mode should only be "B" (advice) — Mode A (execution) already routes
through commits and gets covered by #1 above.

### 3. `update_brain_after_task(db, project_id, user_id, changed_files, task_id, ...)`

**Where it belongs**: at loop completion, once per task, with the full
final file list. Natural spot:

**A · `backend/services/loop_engine.py` post-ship state transition**
(around line 2825 — after `self.state = LoopState.COMPLETED`):
```python
try:
    asyncio.create_task(update_brain_after_task(
        db=self.db,
        project_id=self.project_id or "",
        user_id=self.user_id,
        changed_files=list(files_dict.keys()),
        task_id=self.loop_id,
        github_token=token,
        github_owner=owner,
        github_repo=repo,
        branch=branch,
    ))
except Exception:
    pass
```

This is the Brain V2 refresh trigger. It handles both the increment
path (for existing brains) AND the full rebuild path (every N tasks).

---

## Proof step (#3-c) plan

Once callsites are attached:
1. Snapshot current `project_brains.updated_at` for a target project:
   ```python
   before = await db.project_brains.find_one({"project_id": pid})
   ```
2. Run one real loop task on that project through ship completion.
3. Immediately re-query:
   ```python
   after = await db.project_brains.find_one({"project_id": pid})
   assert after["updated_at"] > before["updated_at"]
   assert after["event_log"][-1]["sha"]  # new commit event pushed
   ```
4. Also check `parliament_log` — if any write path uses it, timestamp
   should have moved. If NOT (because parliament_log has no writer
   function today), skip that assertion or scope out a new writer.

## Canary + admin tile (#3-d/e)

Straightforward once the writes are proven landing:
- `ORA_CANARY_ENABLED=1` gates a small pct of tasks through the shadow
  path with extra logging.
- `ENABLE_EVAL_CRON=1` runs the evaluator daily.
- Admin tile at `/admin/architecture` reads `max(updated_at)` per
  learning collection; RED when >24h stale.

---

**Total scope for #3-b through #3-e**: ~40 LOC across 3 files
(loop_engine + chat + admin router) + a Mongo-timestamp proof script +
one new admin tile. Manageable single-deploy once founder OKs the
callsite choices above.
