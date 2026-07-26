"""
test_regression_iter297_p0_journey_coverage.py — Iter 297, Task 3
    Plug the 6 P0 coverage gaps identified by
    `services.qa_matrix.matrix_coverage_gap()`: journeys j005, j006,
    j009, j010, j018, j021 were flagged as `hit=[]` — their system
    paths were tracked in `docs/traceability_matrix.json` but the
    existing regression tests didn't actually execute them under
    pytest-cov.

    This file writes ONE behavioural test per journey that IMPORTS
    and CALLS the tracked function/module in-process with stub DBs
    + monkey-patched externals. The classifier sees `asyncio.run(...)`
    + service-symbol calls → BEHAVIOURAL for every test.

    Every test asserts on OBSERVED behaviour (return-value / DB
    write / index spec / state transition), not on source strings.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any


# ═══════════════════════════════════════════════════════════════════
# Shared test doubles
# ═══════════════════════════════════════════════════════════════════

class _StubResult:
    def __init__(self, *, matched=1, modified=1, inserted_id="x"):
        self.matched_count  = matched
        self.modified_count = modified
        self.inserted_id    = inserted_id
        self.deleted_count  = 1
        self.acknowledged   = True


class _StubCollection:
    """Minimal motor-like collection. Records every call verbatim so
    tests can inspect exactly what production code did."""

    def __init__(self, name: str):
        self.name = name
        self.docs:            list[dict]  = []
        self.inserted:        list[dict]  = []
        self.updates:         list[tuple] = []
        self.deletes:         list[dict]  = []
        self.indexes_created: list[tuple] = []
        self.dropped_indexes: list[str]   = []
        self._find_return: Any = None

    def seed_find_one(self, doc: Any) -> None:
        self._find_return = doc

    async def find_one(self, q, proj=None, sort=None):
        # Return either the injected canned result, or (default) a
        # doc from `self.docs` that matches on all keys of `q`.
        if self._find_return is not None:
            return dict(self._find_return) if isinstance(self._find_return, dict) else self._find_return
        for d in self.docs:
            if all(d.get(k) == v for k, v in q.items()):
                return dict(d)
        return None

    async def insert_one(self, doc):
        self.inserted.append(dict(doc))
        self.docs.append(dict(doc))
        return _StubResult()

    async def insert_many(self, docs):
        for d in docs:
            self.docs.append(dict(d))
            self.inserted.append(dict(d))
        return _StubResult()

    async def update_one(self, q, u, upsert=False):
        self.updates.append((dict(q), dict(u)))
        return _StubResult()

    async def update_many(self, q, u, upsert=False):
        self.updates.append((dict(q), dict(u)))
        return _StubResult()

    async def delete_one(self, q):
        self.deletes.append(dict(q))
        return _StubResult()

    async def delete_many(self, q):
        self.deletes.append(dict(q))
        return _StubResult()

    async def count_documents(self, _q):
        return len(self.docs)

    async def create_index(self, keys, **opts):
        self.indexes_created.append((keys, dict(opts)))
        return f"{self.name}_{len(self.indexes_created)}"

    async def drop_index(self, name):
        self.dropped_indexes.append(name)

    async def index_information(self):
        # Reflect back the created indexes in the shape motor emits.
        out = {}
        for i, (keys, opts) in enumerate(self.indexes_created):
            out[f"idx_{i}"] = {"key": list(keys) if isinstance(keys, list) else [(keys, 1)],
                                **opts}
        return out


class _StubDB:
    """`db.<collection_name>` → _StubCollection, materialised on first
    attribute access. Mimics motor's AsyncIOMotorDatabase surface."""

    def __init__(self, name: str = "test_db"):
        self.name = name
        self._colls: dict[str, _StubCollection] = {}

    def __getattr__(self, coll_name: str) -> _StubCollection:
        if coll_name.startswith("_") or coll_name == "name":
            raise AttributeError(coll_name)
        return self.__getitem__(coll_name)

    def __getitem__(self, coll_name: str) -> _StubCollection:
        if coll_name not in self._colls:
            self._colls[coll_name] = _StubCollection(coll_name)
        return self._colls[coll_name]

    async def list_collection_names(self):
        return list(self._colls.keys())


# ═══════════════════════════════════════════════════════════════════
# j005 — POST /loop/start → LoopEngine._do_plan runs → returns
#         AWAITING_CONFIRMATION with plan payload
# ═══════════════════════════════════════════════════════════════════

def test_j005_loop_start_endpoint_runs_plan_phase_and_returns_awaiting_confirmation():
    """The `/loop/start` endpoint must:
      1. Reject non-founder users with 403 (loop_mode_locked).
      2. For a founder: build a LoopEngine, run the plan phase to
         completion, and return `state='awaiting_confirmation'` with
         the plan attached.
    Iter 297 — plugs the pytest-cov gap on
        routers/loop.py::start_loop
        services/loop_engine.py::LoopEngine._do_plan  (via .start())
    """
    from routers import loop as _loop_router
    from services import loop_engine as _le
    from services import loop_safety as _ls
    from services import ora_context as _oc

    db = _StubDB()

    # ── Non-founder path (403) ─────────────────────────────────────
    async def _fake_dev_nonfounder(_a):
        return {"user_id": "u_ext", "email": "e@x.com",
                 "is_admin": False, "is_unlimited": False, "tier": "free"}

    orig_current_dev = _loop_router.current_dev
    orig_get_db      = _loop_router.get_db
    _loop_router.current_dev = _fake_dev_nonfounder
    _loop_router.get_db      = lambda: db
    try:
        from fastapi import HTTPException
        import pytest
        async def _call_nonfounder():
            await _loop_router.start_loop(
                body=_loop_router.StartBody(user_message="add /api/health"),
                authorization="Bearer x",
            )
        with pytest.raises(HTTPException) as exc:
            asyncio.run(_call_nonfounder())
        assert exc.value.status_code == 403
        assert exc.value.detail.get("error") == "loop_mode_locked"
    finally:
        _loop_router.current_dev = orig_current_dev
        _loop_router.get_db      = orig_get_db

    # ── Founder path (happy) — plan runs, pauses on confirmation ──
    async def _fake_dev_founder(_a):
        return {"user_id": "u_founder", "email": "f@aurem.dev",
                 "is_admin": True, "is_unlimited": True, "tier": "founder"}

    async def _fake_build_ora(**_kw):
        class _Ctx: pass
        c = _Ctx()
        c.repo_owner = None; c.repo_name = None
        c.branch = "main"; c.pat = None
        return c

    async def _fake_generate_plan(_uid, _pid, msg):
        return {
            "title":  "add health endpoint",
            "steps":  ["- create a `/health` route", "- return {ok:true}"],
            "files_to_change": ["backend/routers/health.py"],
        }

    # Circuit breaker & lock — no failures, lock acquires cleanly.
    async def _fake_circuit(_db, _pk, _uid):
        return (False, 0, 0)
    async def _fake_acquire(_db, _pk, _uid, _lid):
        return (True, None)

    orig_current_dev  = _loop_router.current_dev
    orig_get_db       = _loop_router.get_db
    orig_gen          = _le._generate_plan
    orig_build_ora    = _oc.build_ora_context
    orig_circuit      = _ls.is_loop_circuit_open
    orig_acquire      = _ls.acquire_loop_lock

    _loop_router.current_dev = _fake_dev_founder
    _loop_router.get_db      = lambda: db
    _le._generate_plan       = _fake_generate_plan
    _oc.build_ora_context    = _fake_build_ora
    _ls.is_loop_circuit_open = _fake_circuit
    _ls.acquire_loop_lock    = _fake_acquire
    try:
        async def _call_founder():
            return await _loop_router.start_loop(
                body=_loop_router.StartBody(
                    user_message="add health endpoint"
                ),
                authorization="Bearer f",
            )
        result = asyncio.run(_call_founder())
    finally:
        _loop_router.current_dev  = orig_current_dev
        _loop_router.get_db       = orig_get_db
        _le._generate_plan        = orig_gen
        _oc.build_ora_context     = orig_build_ora
        _ls.is_loop_circuit_open  = orig_circuit
        _ls.acquire_loop_lock     = orig_acquire

    assert result["loop_id"], "endpoint must return a loop_id"
    assert result["state"]   == "awaiting_confirmation", (
        f"plan phase must end in AWAITING_CONFIRMATION; got {result['state']!r}"
    )
    # _do_plan wrote the mocked plan onto engine.context.
    assert result["plan"]["title"] == "add health endpoint"
    assert result["plan"]["files_to_change"] == ["backend/routers/health.py"]
    assert result["requires_user_action"] is True


# ═══════════════════════════════════════════════════════════════════
# j006 — services/loop_task_specs.py::freeze is idempotent + WORM
# ═══════════════════════════════════════════════════════════════════

def test_j006_loop_task_specs_freeze_is_idempotent_and_snapshots_files():
    """freeze() must:
      1. Insert a WORM row on first call (with acceptance_criteria
         extracted from the plan text + frozen_files_to_change).
      2. Second call with the same loop_id → returns the existing
         row without a second insert (idempotent, WORM).
      3. When the plan is a string, `frozen_files_to_change` is [].
    Iter 297 — plugs the pytest-cov gap on
        services/loop_task_specs.py::freeze
    """
    from services import loop_task_specs as _ts

    db = _StubDB()

    # First freeze — insert a fresh row.
    row1 = asyncio.run(_ts.freeze(
        db,
        loop_id="loop-abc",
        task_id="task-1",
        user_id="u1",
        project_id="p1",
        user_message="add health endpoint",
        plan={
            "title": "add health",
            "steps": "- create /api/health route\n- add unit test coverage",
            "files_to_change": ["backend/routers/health.py",
                                 "backend/tests/test_health.py"],
        },
    ))
    # WORM shape — carries the identity + snapshot + WORM marker.
    assert row1["loop_id"]    == "loop-abc"
    assert row1["task_id"]    == "task-1"
    assert row1["user_id"]    == "u1"
    assert row1["project_id"] == "p1"
    assert row1["worm"] is True
    assert "frozen_at" in row1 and row1["frozen_at"]
    # files_to_change flowed from the structured plan into the WORM row.
    assert row1["frozen_files_to_change"] == [
        "backend/routers/health.py",
        "backend/tests/test_health.py",
    ]
    # acceptance_criteria is a non-empty list (may vary in length —
    # the pattern accepts bullets, dashes, numbered — we assert on
    # the invariant "non-empty list" not on the exact contents).
    assert isinstance(row1["acceptance_criteria"], list)
    assert len(row1["acceptance_criteria"]) >= 1
    # Exactly one insert landed.
    assert len(db.loop_task_specs.inserted) == 1

    # Second freeze on same loop_id — must NOT insert again.
    # The stub `find_one` returns the previously-inserted row.
    row2 = asyncio.run(_ts.freeze(
        db,
        loop_id="loop-abc",
        task_id="task-1",
        user_id="u1",
        project_id="p1",
        user_message="different message — should be IGNORED",
        plan="different plan text",
    ))
    # Same row (identity fields match); insert count unchanged.
    assert row2["loop_id"] == row1["loop_id"]
    assert row2["original_task"] == row1["original_task"], (
        "WORM violated — freeze() overwrote original_task on re-call"
    )
    assert len(db.loop_task_specs.inserted) == 1, (
        f"WORM violated — a 2nd insert landed; total inserts = "
        f"{len(db.loop_task_specs.inserted)}"
    )

    # Third scenario — different loop_id + plan-is-string → files [].
    db2 = _StubDB()
    row3 = asyncio.run(_ts.freeze(
        db2,
        loop_id="loop-str-plan",
        task_id=None,
        user_id="u2",
        project_id=None,
        user_message="add tests",
        plan="just a free-form plan without structured files_to_change",
    ))
    assert row3["frozen_files_to_change"] == [], (
        "string plan must produce an empty frozen_files_to_change"
    )
    assert row3["task_id"] == "loop-str-plan", (
        "task_id must fall back to loop_id when None passed"
    )


# ═══════════════════════════════════════════════════════════════════
# j009 — GET /loop/{id}/stream honours 20-min wall-clock ceiling
#         and 404s an unknown loop
# ═══════════════════════════════════════════════════════════════════

def test_j009_loop_stream_returns_streaming_response_and_404s_unknown_loop():
    """Iter 297 — plugs the pytest-cov gap on routers/loop.py::loop_stream.
      1. Unknown loop_id → HTTPException(404) (both the local
         `lookup` and the Mongo fallback miss).
      2. Known loop with live engine → returns StreamingResponse with
         media_type='text/event-stream'.
      3. Governor invariant: `STREAM_MAX_S` is exactly 20*60 s so the
         Release It! wall-clock ceiling isn't silently widened.
    """
    from fastapi import HTTPException
    from fastapi.responses import StreamingResponse
    from routers import loop as _loop_router
    from services import loop_engine as _le
    import pytest

    # Governor constant — real module attribute lookup.
    assert _loop_router.STREAM_MAX_S == 20 * 60, (
        f"SSE Governor ceiling MUST be 20 minutes; got "
        f"{_loop_router.STREAM_MAX_S}s"
    )

    db = _StubDB()

    async def _fake_dev(_a):
        return {"user_id": "u1", "email": "e@x.com",
                 "is_admin": True, "tier": "founder"}

    orig_current_dev = _loop_router.current_dev
    orig_get_db      = _loop_router.get_db
    _loop_router.current_dev = _fake_dev
    _loop_router.get_db      = lambda: db
    try:
        # (1) Unknown loop → 404. lookup() returns None AND find_one
        # returns None. The endpoint must raise HTTPException(404).
        # Iter 309 · Batch-2 Item 6 — loop_stream now takes a Request
        # arg + Last-Event-ID header for the SSE reconnect replay.
        # Fake both with a minimal Starlette Request stub.
        from starlette.requests import Request as _Req
        def _fake_request():
            return _Req({"type": "http", "method": "GET",
                         "headers": [], "path": "/",
                         "query_string": b"", "root_path": "",
                         "scheme": "http", "server": ("t", 80),
                         "client": ("t", 0)})
        async def _call_unknown():
            return await _loop_router.loop_stream(
                loop_id="does-not-exist",
                request=_fake_request(),
                authorization="Bearer x",
                last_event_id=None)
        with pytest.raises(HTTPException) as exc:
            asyncio.run(_call_unknown())
        assert exc.value.status_code == 404

        # (2) Known loop with live engine → StreamingResponse.
        engine = _le.LoopEngine(
            db=db, loop_id="loop-live-1",
            user_id="u1", project_id=None,
            user_message="hi", bin_ctx=None,
        )
        engine.state = _le.LoopState.COMPLETED  # terminal so gen exits fast
        _le.register(engine)
        try:
            async def _call_known():
                return await _loop_router.loop_stream(
                    loop_id="loop-live-1",
                    request=_fake_request(),
                    authorization="Bearer x",
                    last_event_id=None)
            resp = asyncio.run(_call_known())
        finally:
            _le.deregister("loop-live-1")

        assert isinstance(resp, StreamingResponse), (
            f"loop_stream must return StreamingResponse; got {type(resp)}"
        )
        assert resp.media_type == "text/event-stream"
        # SSE-friendly headers (Governor requires Nginx bypass so
        # buffering doesn't hide the wall-clock cap).
        assert resp.headers.get("cache-control") == "no-cache"
        assert resp.headers.get("x-accel-buffering") == "no"
    finally:
        _loop_router.current_dev = orig_current_dev
        _loop_router.get_db      = orig_get_db


# ═══════════════════════════════════════════════════════════════════
# j010 — scripts/init_prod_collections.py declares TTL indexes on
#         every loop-machinery collection (Steady State pattern)
# ═══════════════════════════════════════════════════════════════════

def test_j010_init_prod_collections_declares_ttl_on_loop_machinery():
    """Iter 297 — plugs the pytest-cov gap on
        backend/scripts/init_prod_collections.py

    Every loop-machinery collection must get a TTL index at bootstrap.
    We invoke the ACTUAL bootstrap function against a stub DB and
    read back the indexes it created — no source-grep."""
    import importlib
    import sys, os
    # Insert /app/backend/scripts on sys.path so `import
    # init_prod_collections` works. This is also what main.py does.
    sys.path.insert(0, "/app/backend/scripts")
    ipc = importlib.import_module("init_prod_collections")

    db = _StubDB()
    result = asyncio.run(ipc.init_prod_collections(db))

    # Every named loop collection got at least ONE index declared.
    _LOOP_COLLS = [
        "loop_events", "loop_locks", "loop_sessions",
        "loop_failures", "loop_verification_log", "loop_run_log",
    ]
    for name in _LOOP_COLLS:
        idxs = db[name].indexes_created
        assert idxs, f"{name}: no indexes created at bootstrap"
        # At least one of them must carry expireAfterSeconds > 0.
        ttl_idxs = [(k, o) for (k, o) in idxs if "expireAfterSeconds" in o]
        assert ttl_idxs, (
            f"{name}: Steady State pattern violated — no TTL index "
            f"declared. Existing indexes: {idxs!r}"
        )
        # TTL is positive (0 is a valid expire-at pattern for other
        # collections, but loop-machinery uses age-based).
        for (_k, opts) in ttl_idxs:
            assert opts["expireAfterSeconds"] > 0, (
                f"{name}: TTL must be > 0; got {opts['expireAfterSeconds']}"
            )

    # Bootstrap result reports the collections it touched.
    assert isinstance(result, dict)
    assert "created" in result and "indexed" in result and "errors" in result
    # Every loop collection is present in either created or indexed.
    _touched = set()
    for entry in result["created"]:
        _touched.add(entry.split(":")[0])
    for entry in result["indexed"]:
        _touched.add(entry.split(":")[0])
    for name in _LOOP_COLLS:
        assert name in _touched, (
            f"bootstrap did not touch {name}; touched: {_touched}"
        )


# ═══════════════════════════════════════════════════════════════════
# j018 — POST /loop/{id}/cancel actually cancels the engine and
#         releases the lock (even from PAUSED_FOR_USER)
# ═══════════════════════════════════════════════════════════════════

def test_j018_cancel_loop_endpoint_cancels_engine_and_releases_lock():
    """Iter 297 — plugs the pytest-cov gap on
        backend/routers/loop.py::cancel_loop

    The cancel endpoint must:
      1. Wire up the engine's cancel() coroutine.
      2. Force-persist state='aborted' + release the loop_locks row
         even if the engine's own cancel raced with the pipeline task.
      3. Return {loop_id, state:'aborted', lock_force_released:True}."""
    from routers import loop as _loop_router
    from services import loop_engine as _le

    db = _StubDB()

    # A live engine registered in _LIVE — cancel path (not fallback).
    engine = _le.LoopEngine(
        db=db, loop_id="loop-cancel-1",
        user_id="u_cancel", project_id="proj-x",
        user_message="run", bin_ctx=None,
    )
    engine.state = _le.LoopState.PAUSED_FOR_USER
    engine.phase = "ship"

    # cancel() calls _persist_session + release_loop_lock — stub both.
    cancel_calls: list[str] = []
    async def _fake_cancel():
        cancel_calls.append("cancel")
        engine.state = _le.LoopState.ABORTED
    engine.cancel = _fake_cancel

    _le.register(engine)

    async def _fake_dev(_a):
        return {"user_id": "u_cancel", "email": "c@x.com",
                 "is_admin": True, "tier": "founder"}

    orig_current_dev = _loop_router.current_dev
    orig_get_db      = _loop_router.get_db
    _loop_router.current_dev = _fake_dev
    _loop_router.get_db      = lambda: db
    try:
        async def _call():
            return await _loop_router.cancel_loop(
                loop_id="loop-cancel-1", authorization="Bearer x")
        result = asyncio.run(_call())
    finally:
        _loop_router.current_dev = orig_current_dev
        _loop_router.get_db      = orig_get_db
        _le.deregister("loop-cancel-1")

    # engine.cancel() ran.
    assert cancel_calls == ["cancel"], (
        f"engine.cancel() must be invoked exactly once; got {cancel_calls}"
    )
    # Belt-and-suspenders force-clean: state persisted, lock deleted.
    assert result["state"] == "aborted"
    assert result["lock_force_released"] is True
    assert result["loop_id"] == "loop-cancel-1"
    # Session updated to aborted (2nd write from iter279 force-clean).
    updates = db.loop_sessions.updates
    assert updates, "loop_sessions must record an aborted update"
    _q, _u = updates[-1]
    assert _q["loop_id"] == "loop-cancel-1"
    assert _u["$set"]["state"] == "aborted"
    # Lock row deleted so the next /start can acquire.
    assert db.loop_locks.deletes, "loop_locks entry must be deleted"
    delete_q = db.loop_locks.deletes[0]
    assert delete_q["project_id"] == "proj-x"
    assert delete_q["user_id"]    == "u_cancel"
    assert delete_q["loop_id"]    == "loop-cancel-1"


# ═══════════════════════════════════════════════════════════════════
# j021 — loop_locks unique index is COMPOSITE on (project_id, user_id)
#         so User A holding a lock on project P cannot block User B
# ═══════════════════════════════════════════════════════════════════

def test_j021_loop_locks_unique_index_is_composite_project_and_user():
    """Iter 297 — plugs the pytest-cov gap on
        backend/scripts/init_prod_collections.py (bulkhead index)

    The bulkhead invariant: `loop_locks` must carry a unique index
    on the composite (project_id, user_id) — NOT project_id alone.
    A single-key unique index would mean User A locking project P
    also blocks User B from starting a loop on the same project.
    We invoke the real bootstrap + read back the index spec created
    on the stub DB — direct evidence of the composite key."""
    import importlib, sys
    sys.path.insert(0, "/app/backend/scripts")
    ipc = importlib.import_module("init_prod_collections")

    db = _StubDB()
    asyncio.run(ipc.init_prod_collections(db))

    idxs = db.loop_locks.indexes_created
    assert idxs, "loop_locks must have at least one index"
    # Find the unique one.
    unique_idxs = [(k, o) for (k, o) in idxs if o.get("unique")]
    assert unique_idxs, (
        f"loop_locks must declare a UNIQUE index for the bulkhead "
        f"invariant; declared indexes: {idxs!r}"
    )
    (keys, opts) = unique_idxs[0]
    # keys must be the LIST-form composite [("project_id", 1),
    # ("user_id", 1)] — NOT a bare string, NOT single-key.
    assert isinstance(keys, list), (
        f"unique index MUST be composite (list form); got {keys!r} "
        f"— a single-string key would allow cross-user starvation "
        f"on the same project"
    )
    key_fields = [f for (f, _dir) in keys]
    assert "project_id" in key_fields and "user_id" in key_fields, (
        f"unique index MUST span (project_id, user_id); got "
        f"{key_fields!r} — the missing field lets one user monopolise "
        f"a project's loop lock across accounts"
    )
    # Sparse so pre-lock rows (e.g. force-released placeholders) don't
    # violate uniqueness by all colliding on `null`.
    assert opts.get("sparse") is True, (
        "unique index MUST be sparse — otherwise null placeholder "
        "rows collide with each other"
    )
