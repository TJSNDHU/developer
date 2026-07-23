"""
Iter 288 (j007) — Frozen-plan scope-enforcement during Execute.

Bug (loop_1f8, loop_bff): plan approved with N files, Execute silently
began generating N+k files (extras never made it through user
approval). Vanguard did not flag it (extras were legit paths); the
independent verifier only compared at ship time, wasting a full run.

Fix (this iter):
  1. loop_task_specs.freeze() now persists `frozen_files_to_change`
     — the exact list of paths the user approved — as a separate,
     structured field on the WORM row.
  2. loop_engine._do_execute() looks up the frozen row BEFORE
     dispatching Parliament tasks, computes
     `extras = current_paths - frozen_paths`, and if non-empty:
       • writes a `scope_drift` row to `loop_events`
       • flips the loop state to PAUSED_FOR_USER
       • emits an SSE frame with `data.kind = "scope_drift"` + the
         concrete `frozen` / `extras` / `planned_now` lists
       • RETURNS — no LLM call happens until the user re-approves.
  3. Per-file diagnostic — when generate_files returns 0 usable
     files, we now write an `execute_empty_output` row to
     `loop_run_log` with the per-path outcome, so the NEXT
     "LLM produced no usable file content" incident is diagnosable
     from the DB instead of the ephemeral console. (loop_1f8 /
     loop_bff had no persisted diagnostic and were unrecoverable.)

These tests lock:
  (a) loop_task_specs.freeze() writes the new `frozen_files_to_change`
      field, unchanged if the plan is a raw string.
  (b) loop_engine.py contains the scope-drift branch: BEFORE Parliament
      dispatch, with the exact three side effects (loop_events insert,
      state=PAUSED_FOR_USER, scope_drift kind on the emit).
  (c) The empty-output failure path writes `execute_empty_output` to
      loop_run_log with per-file diag.
"""
from __future__ import annotations
import asyncio


# ── (a) loop_task_specs schema extension ─────────────────────────────

def test_regression_iter288_freeze_persists_files_to_change():
    """`freeze()` must extract `plan['files_to_change']` and store it
    as `frozen_files_to_change` on the row so Execute can pre-check."""
    from services.loop_task_specs import freeze

    class _StubColl:
        def __init__(self):
            self.rows = []
        async def find_one(self, q):
            for r in self.rows:
                if all(r.get(k) == v for k, v in q.items()):
                    return dict(r)
            return None
        async def insert_one(self, doc):
            self.rows.append(dict(doc))
            return type("R", (), {"inserted_id": "x"})()
        async def create_index(self, *a, **kw):
            return None

    class _StubDB:
        def __init__(self):
            self._c = _StubColl()
        def __getitem__(self, name):
            return self._c

    async def _run():
        db = _StubDB()
        plan = {
            "title": "add /api/health",
            "files_to_change": ["backend/routers/health.py",
                                 "backend/main.py"],
            "bullets": ["Add router", "Wire it up"],
        }
        row = await freeze(
            db,
            loop_id="test_j007_freeze_1",
            task_id="t1",
            user_id="u1",
            project_id="p1",
            user_message="add health check",
            plan=plan,
        )
        assert "frozen_files_to_change" in row
        assert row["frozen_files_to_change"] == [
            "backend/routers/health.py", "backend/main.py"]

    asyncio.run(_run())


def test_regression_iter288_freeze_empty_files_when_plan_is_string():
    """A raw-string plan (no structured `files_to_change`) must yield
    `frozen_files_to_change = []` — never crash."""
    from services.loop_task_specs import freeze

    class _StubColl:
        def __init__(self):
            self.rows = []
        async def find_one(self, q):
            return None
        async def insert_one(self, doc):
            self.rows.append(dict(doc))
            return type("R", (), {"inserted_id": "x"})()
        async def create_index(self, *a, **kw): return None

    class _StubDB:
        def __init__(self):
            self._c = _StubColl()
        def __getitem__(self, name):
            return self._c

    async def _run():
        db = _StubDB()
        row = await freeze(
            db,
            loop_id="test_j007_freeze_str",
            task_id="t1", user_id="u1", project_id="p1",
            user_message="raw", plan="just a text plan, no dict",
        )
        assert row["frozen_files_to_change"] == []

    asyncio.run(_run())


# ── (b) loop_engine scope-drift branch is present ────────────────────

def test_regression_iter288_execute_has_scope_drift_gate_before_parliament():
    """The scope-drift block MUST live inside `_do_execute`, BEFORE the
    Parliament dispatch (`asyncio.gather` of `_gen_via_parliament`), and
    MUST return early (PAUSED_FOR_USER) when `current_set - frozen_set`
    is non-empty."""
    src = open("/app/backend/services/loop_engine.py").read()
    idx_exec = src.find("async def _do_execute")
    assert idx_exec > -1, "_do_execute must exist"
    idx_gather = src.find("_gen_via_parliament(_client, p) for p in paths", idx_exec)
    assert idx_gather > -1, "Parliament dispatch site must exist"
    block = src[idx_exec:idx_gather]

    # Explicit contract — every one of these tokens must appear inside
    # the pre-Parliament block, keyed to the exact fix.
    assert "SCOPE DRIFT" in block, (
        "iter288: pre-Parliament SCOPE DRIFT logger.warning missing"
    )
    assert "frozen_files_to_change" in block, (
        "the scope-drift check must read the WORM-frozen list"
    )
    assert "PAUSED_FOR_USER" in block, (
        "scope-drift must flip state to PAUSED_FOR_USER"
    )
    assert '"kind":       "scope_drift"' in block \
        or '"kind":        "scope_drift"' in block, (
        "the loop_events audit row must carry kind='scope_drift'"
    )
    # scope_drift SSE emit + early return
    assert "return" in block


def test_regression_iter288_scope_drift_emits_requires_user_action():
    """The scope_drift SSE frame must set `requires_user_action=True`
    so the frontend renders a user-action card and does NOT continue
    to Parliament dispatch."""
    src = open("/app/backend/services/loop_engine.py").read()
    idx = src.find("SCOPE DRIFT")
    assert idx > -1
    tail = src[idx: idx + 4000]
    assert "requires_user_action=True" in tail, (
        "the scope_drift emit MUST require a user action"
    )
    assert "return" in tail, (
        "scope_drift MUST return before Parliament dispatch — no "
        "extra files may be generated silently"
    )


# ── (c) empty-output failure now writes a diagnostic row ─────────────

def test_regression_iter288_empty_output_writes_diag_row():
    """When Execute exits with 0 usable files, a diagnostic row must
    land in `loop_run_log` with kind='execute_empty_output' + the
    per-file outcomes. This is what loop_1f8 needed and lacked."""
    src = open("/app/backend/services/loop_engine.py").read()
    # rfind → the LAST occurrence (the actual _fail call, not a comment
    # referencing it above).
    idx = src.rfind('"LLM produced no usable file content')
    assert idx > -1
    head = src[max(0, idx - 4000): idx]
    assert "execute_empty_output" in head, (
        "empty-output path must persist an execute_empty_output row"
    )
    assert "loop_run_log" in head
    assert "per_file" in head, (
        "the diag row must include per-file outcomes so the "
        "raw truncation vs parse-failure question is answerable "
        "next time"
    )
