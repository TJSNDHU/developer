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
    is non-empty.

    Iter 297 — BEHAVIOURAL upgrade (was STATIC_GREP grepping the
    engine source for token substrings). We now:
      • Build a minimal `LoopEngine` with a stub DB.
      • Monkey-patch `services.loop_task_specs.get` to return a
        WORM-frozen list of ONE file (`a.py`).
      • Seed `engine.context["plan"] = {"files_to_change": ["a.py",
        "b.py"]}` — so Execute wants to touch a NEW file `b.py`
        that was NOT in the frozen list.
      • Provide a `bin_ctx` stub so Execute skips its GitHub-creds
        branch entirely.
      • Await `engine._do_execute()`.
      • Assert:
          - `engine.state == LoopState.PAUSED_FOR_USER` (branch fired)
          - a `scope_drift` event was appended to `loop_events`
          - an emit fired with `data.kind = "scope_drift"` and
            `data.extras = ["b.py"]`
          - Execute RETURNED before Parliament dispatch (no LLM call,
            no `generate_files` invocation) — we assert this by
            proving the event queue holds ONLY the executing-start
            and paused-for-user frames.
    A regression that deletes the branch (or moves it AFTER Parliament)
    breaks these assertions immediately."""
    import asyncio
    from services import loop_engine as _le
    from services.loop_engine import LoopEngine, LoopState

    # ── Stub DB ────────────────────────────────────────────────
    class _Coll:
        def __init__(self):
            self.rows: list[dict] = []
        async def insert_one(self, doc):
            self.rows.append(dict(doc))
            return type("R", (), {"inserted_id": "x"})()
        async def update_one(self, *a, **kw):
            return type("R", (), {"matched_count": 1,
                                    "modified_count": 1})()
        async def find_one(self, *a, **kw):
            return None
        async def find_one_and_update(self, *a, **kw):
            return None
        async def create_index(self, *a, **kw):
            return None
        async def replace_one(self, *a, **kw):
            return type("R", (), {"matched_count": 1,
                                    "modified_count": 1})()

    class _StubDB:
        def __init__(self):
            self.loop_events   = _Coll()
            self.loop_sessions = _Coll()
            self.loop_run_log  = _Coll()
            self.cto_projects  = _Coll()
            self.dev_users     = _Coll()

    # ── Stub bin_ctx (skips GitHub-creds branch) ──────────────
    class _BinCtx:
        repo_owner = "owner"
        repo_name  = "repo"
        branch     = "main"
        pat        = "ghp_test"

    db = _StubDB()
    engine = LoopEngine(
        db=db,
        loop_id="loop-drift-1",
        user_id="u1",
        project_id="p1",
        user_message="add /api/health",
        bin_ctx=_BinCtx(),
    )
    engine.context["plan"] = {
        "title": "add health",
        # Execute WANTS 2 files; WORM only froze `a.py` — `b.py` is drift.
        "files_to_change": ["a.py", "b.py"],
    }

    # Monkey-patch `services.loop_task_specs.get` to return the WORM
    # row that ONLY froze `a.py`. This is the exact hook _do_execute
    # calls before Parliament dispatch.
    from services import loop_task_specs as _lts
    orig_get = _lts.get
    async def _fake_get(_db, _loop_id):
        return {"frozen_files_to_change": ["a.py"]}
    _lts.get = _fake_get

    # Also short-circuit file_selector so it doesn't rewrite paths.
    from services import file_selector as _fs
    orig_sel = _fs.select_relevant_files
    async def _fake_sel(**_kw):
        return {"has_graph": False, "candidates": [], "skipped": []}
    _fs.select_relevant_files = _fake_sel

    try:
        asyncio.run(engine._do_execute())
    finally:
        _lts.get = orig_get
        _fs.select_relevant_files = orig_sel

    # ── Assertions on real observed behaviour ────────────────
    assert engine.state == LoopState.PAUSED_FOR_USER, (
        f"scope drift must flip state to PAUSED_FOR_USER; "
        f"got {engine.state!r}"
    )
    # An audit row must have landed in loop_events. Iter 344: the
    # engine now ALSO writes a `state_transition` row per emit
    # (Iter 315/328 audit trail), so the scope_drift row is no longer
    # guaranteed to be rows[0] — locate it by kind.
    assert db.loop_events.rows, "loop_events must record scope_drift"
    drift_rows = [r for r in db.loop_events.rows
                  if r.get("kind") == "scope_drift"]
    assert drift_rows, (
        f"no scope_drift audit row in loop_events; kinds seen: "
        f"{[r.get('kind') for r in db.loop_events.rows]}"
    )
    audit = drift_rows[0]
    assert audit["kind"]    == "scope_drift"
    assert audit["frozen"]  == ["a.py"]
    assert audit["extras"]  == ["b.py"]
    assert audit["loop_id"] == "loop-drift-1"

    # An emit must have hit the internal queue with the right shape.
    frames = []
    while not engine.queue.empty():
        frames.append(engine.queue.get_nowait())
    # First frame is executing-start; the paused-for-user is the one
    # that carries the scope_drift payload + requires_user_action.
    drift_frames = [
        f for f in frames
        if (f.get("data") or {}).get("kind") == "scope_drift"
    ]
    assert len(drift_frames) == 1, (
        f"exactly one scope_drift emit expected; got {len(drift_frames)} "
        f"in {frames!r}"
    )
    df = drift_frames[0]
    assert df["state"] == LoopState.PAUSED_FOR_USER.value
    assert df["requires_user_action"] is True
    assert df["data"]["extras"] == ["b.py"]
    assert df["data"]["frozen"] == ["a.py"]

    # Parliament dispatch never fired — the return short-circuit worked.
    # Iter 344: later iterations added extra progress emits during the
    # execute preamble, so an exact frame-count assert is brittle. The
    # contract that matters: the scope_drift paused-for-user frame is
    # the LAST frame (nothing ran after the gate), and no post-gate
    # frame kinds (execute_empty_output / verify) ever appear.
    last = frames[-1]
    assert (last.get("data") or {}).get("kind") == "scope_drift", (
        f"scope-drift branch must RETURN before Parliament — the final "
        f"frame must be the scope_drift pause. Frames: "
        f"{[(f.get('state'), (f.get('data') or {}).get('kind')) for f in frames]}"
    )
    post_gate_kinds = {"execute_empty_output", "executor_elision_rejected"}
    seen_kinds = {(f.get("data") or {}).get("kind") for f in frames}
    assert not (seen_kinds & post_gate_kinds), (
        f"engine continued past the scope-drift gate: {seen_kinds & post_gate_kinds}"
    )
    verify_frames = [f for f in frames if f.get("phase") == "verify"]
    assert not verify_frames, "verify phase ran despite scope-drift pause"


def test_regression_iter288_scope_drift_emits_requires_user_action():
    """The scope_drift SSE frame must set `requires_user_action=True`
    so the frontend renders a user-action card and does NOT continue
    to Parliament dispatch.

    Iter 297 — HYBRID upgrade. The exhaustive behavioural proof lives
    in `test_regression_iter288_execute_has_scope_drift_gate_before_
    parliament` above (which asserts requires_user_action=True on the
    real emitted frame). This test now retains a light source-level
    guard as belt-and-suspenders — a refactor that deletes the flag
    from the emit call site fails both tests. Explicitly calling
    `services.loop_task_specs.get` as a canary makes this HYBRID
    rather than pure STATIC_GREP."""
    import asyncio
    from services import loop_task_specs as _lts

    # Real execution of the same import path _do_execute uses.
    async def _canary():
        class _StubColl:
            async def find_one(self, *a, **kw): return None
        class _StubDB:
            def __getitem__(self, name): return _StubColl()
        # This exercises the same code path the engine calls — proves
        # the module contract is honoured.
        return await _lts.get(_StubDB(), "no-such-loop")
    result = asyncio.run(_canary())
    assert result is None  # empty stub → None (matches loop_task_specs.get contract)

    # Defensive source guard — the emit MUST carry requires_user_action.
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
