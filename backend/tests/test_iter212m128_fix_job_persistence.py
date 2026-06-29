"""
Iter 212m-128 — Production-grade fix-job persistence + restart.

Covers the architectural overhaul exposed by the production video:
  • Bulk fix jobs now persist to Mongo (`fix_jobs` collection) on
    every state transition.
  • Boot-time `mark_running_orphaned()` flips any leftover
    `status:"running"` rows to `"orphaned"` so the UI surfaces a
    Restart button instead of "running forever".
  • Top-level try/except in `_run_bulk_job` catches silent
    exceptions and closes the job with `status:"failed"`.
  • `POST /restart/{job_id}` reads the persisted row, subtracts
    already-completed + terminally-failed finding IDs, and spawns
    a NEW worker on the remaining set.
  • `GET /list` returns the caller's recent jobs for the UI.
  • SSE `/stream` hydrates from Mongo when the in-memory job is
    gone (different pod / pod restart) so the drawer can render
    partial results + a Restart button.
"""
from __future__ import annotations

import asyncio
import time

import pytest


# ──────────────────────────────────────────────────────────────────
# Minimal in-memory Mongo double — supports just the operations the
# fix_job_manager + restart endpoint actually call.
# ──────────────────────────────────────────────────────────────────
class _FakeColl:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.inserts: list[dict] = []
        self.updates: list[dict] = []

    def _match(self, row, query):
        for k, v in (query or {}).items():
            if isinstance(v, dict):
                # Only support the operators we actually use in tests.
                if "$gte" in v:
                    if row.get(k, 0) < v["$gte"]:
                        return False
                if "$lt" in v:
                    if row.get(k, 0) >= v["$lt"]:
                        return False
                continue
            if row.get(k) != v:
                return False
        return True

    async def find_one(self, q, projection=None, sort=None):
        matches = [r for r in self.rows if self._match(r, q)]
        if sort:
            for key, direction in reversed(sort):
                matches.sort(key=lambda r, _k=key: r.get(_k, 0),
                             reverse=(direction == -1))
        return dict(matches[0]) if matches else None

    def find(self, q, projection=None):
        rows = [dict(r) for r in self.rows if self._match(r, q)]

        class _Cursor:
            def __init__(self, rows):
                self._rows = rows
                self._sort = None
                self._limit = None

            def sort(self, key, direction=1):
                if isinstance(key, list):
                    self._sort = key
                else:
                    self._sort = [(key, direction)]
                return self

            def limit(self, n):
                self._limit = n
                return self

            def __aiter__(self):
                ordered = list(self._rows)
                if self._sort:
                    for k, d in reversed(self._sort):
                        ordered.sort(key=lambda r, _k=k: r.get(_k, 0),
                                     reverse=(d == -1))
                if self._limit is not None:
                    ordered = ordered[: self._limit]
                self._iter = iter(ordered)
                return self

            async def __anext__(self):
                try:
                    return next(self._iter)
                except StopIteration:
                    raise StopAsyncIteration

        return _Cursor(rows)

    async def update_one(self, q, upd, upsert=False):
        for r in self.rows:
            if self._match(r, q):
                for k, v in (upd.get("$set") or {}).items():
                    r[k] = v
                for k, v in (upd.get("$inc") or {}).items():
                    r[k] = (r.get(k) or 0) + v
                class _R:
                    modified_count = 1
                    upserted_id = None
                self.updates.append({"q": q, "upd": upd})
                return _R()
        if upsert:
            new = dict()
            for k, v in (q or {}).items():
                if not isinstance(v, dict):
                    new[k] = v
            for k, v in (upd.get("$set") or {}).items():
                new[k] = v
            self.rows.append(new)
            class _R:
                modified_count = 0
                upserted_id = "x"
            return _R()
        class _R:
            modified_count = 0
            upserted_id = None
        return _R()

    async def update_many(self, q, upd):
        n = 0
        for r in self.rows:
            if self._match(r, q):
                for k, v in (upd.get("$set") or {}).items():
                    r[k] = v
                n += 1
        class _R:
            modified_count = n
        return _R()

    async def insert_one(self, doc):
        self.rows.append(dict(doc))
        self.inserts.append(dict(doc))
        class _R:
            inserted_id = "x"
        return _R()

    async def create_index(self, *_a, **_kw):
        return "ix"


class _FakeDB:
    def __init__(self):
        self.fix_jobs    = _FakeColl()
        self.dev_users   = _FakeColl()
        self.cto_projects = _FakeColl()


@pytest.fixture(autouse=True)
def _reset_jobs_dict():
    from services import fix_job_manager as fjm
    fjm._JOBS.clear()
    yield
    fjm._JOBS.clear()


# ──────────────────────────────────────────────────────────────────
# 1) create_job persists initial row to Mongo
# ──────────────────────────────────────────────────────────────────
def test_create_job_persists_to_mongo():
    from services import fix_job_manager as fjm
    db = _FakeDB()
    findings = [
        {"id": "f1", "severity": "high", "file": "a.py"},
        {"id": "f2", "severity": "low",  "file": "b.py"},
    ]
    jid = asyncio.run(fjm.create_job(
        db=db, user_id="u1", kind="bulk", total=2,
        project_id="p1", findings=findings,
    ))
    assert jid.startswith("fx_")
    row = db.fix_jobs.rows[0]
    assert row["job_id"] == jid
    assert row["status"] == "running"
    assert row["all_findings"] == findings
    assert row["completed_ids"] == []
    assert row["failed_terminal_ids"] == []


# ──────────────────────────────────────────────────────────────────
# 2) emit() + persist_event() snapshots counters to Mongo
# ──────────────────────────────────────────────────────────────────
def test_persist_event_snapshots_counters():
    from services import fix_job_manager as fjm
    db = _FakeDB()

    async def go():
        jid = await fjm.create_job(
            db=db, user_id="u1", kind="bulk", total=2,
            project_id="p1",
            findings=[{"id": "f1"}, {"id": "f2"}],
        )
        fjm.emit(jid, "fix-done", ok=True, finding_id="f1",
                 commit_sha="abc1234", file="a.py", rule_id="r")
        await fjm.persist_event(db, jid)
        fjm.emit(jid, "fix-done", ok=False, finding_id="f2",
                 error="github_credentials_missing", file="b.py", rule_id="r")
        await fjm.persist_event(db, jid)
        return jid

    jid = asyncio.run(go())
    row = db.fix_jobs.rows[0]
    assert row["completed"] == 2  # 2 fix-done events
    assert row["failed"] == 1
    assert "f1" in row["completed_ids"]
    assert "f2" in row["failed_terminal_ids"]  # terminal error → tracked


# ──────────────────────────────────────────────────────────────────
# 3) Boot-time orphan sweep flips running → orphaned
# ──────────────────────────────────────────────────────────────────
def test_mark_running_orphaned_on_boot():
    from services import fix_job_manager as fjm
    db = _FakeDB()
    db.fix_jobs.rows.extend([
        {"job_id": "fx_a", "user_id": "u1", "status": "running",
         "started_at": time.time() - 60},
        {"job_id": "fx_b", "user_id": "u1", "status": "running",
         "started_at": time.time() - 30},
        {"job_id": "fx_c", "user_id": "u1", "status": "done",
         "started_at": time.time() - 120},
    ])
    n = asyncio.run(fjm.mark_running_orphaned(db))
    assert n == 2
    statuses = {r["job_id"]: r["status"] for r in db.fix_jobs.rows}
    assert statuses["fx_a"] == "orphaned"
    assert statuses["fx_b"] == "orphaned"
    assert statuses["fx_c"] == "done"


# ──────────────────────────────────────────────────────────────────
# 4) subscribe() hydrates from Mongo when in-memory job is gone
# ──────────────────────────────────────────────────────────────────
def test_subscribe_hydrates_from_mongo_for_orphaned_job():
    from services import fix_job_manager as fjm
    db = _FakeDB()
    db.fix_jobs.rows.append({
        "job_id":     "fx_orphan",
        "user_id":    "u1",
        "kind":       "bulk",
        "status":     "orphaned",
        "total":      3,
        "completed":  1,
        "failed":     0,
        "results":    [{"finding_id": "f1", "ok": True,
                        "commit_sha": "abc", "file": "x.py"}],
        "all_findings":        [{"id": "f1"}, {"id": "f2"}, {"id": "f3"}],
        "completed_ids":       ["f1"],
        "failed_terminal_ids": [],
        "started_at": time.time() - 600,
        "closed_at":  time.time() - 60,
        "message":    "Worker lost.",
    })

    async def collect():
        out = []
        async for ev in fjm.subscribe("fx_orphan", db=db):
            out.append(ev)
            if ev.get("phase") == "hydrated":
                break
        return out

    events = asyncio.run(collect())
    assert len(events) >= 1
    h = events[0]
    assert h["phase"] == "hydrated"
    assert h["status"] == "orphaned"
    assert h["completed"] == 1
    assert h["can_restart"] is True
    assert h["results"][0]["finding_id"] == "f1"


def test_subscribe_emits_gone_when_no_mongo_row():
    from services import fix_job_manager as fjm
    db = _FakeDB()

    async def collect():
        out = []
        async for ev in fjm.subscribe("fx_missing", db=db):
            out.append(ev)
            if ev.get("phase") == "gone":
                break
        return out

    events = asyncio.run(collect())
    assert events[0]["phase"] == "gone"
    assert events[0]["can_restart"] is False


# ──────────────────────────────────────────────────────────────────
# 5) list_jobs returns user's own jobs newest-first
# ──────────────────────────────────────────────────────────────────
def test_list_jobs_isolates_users_and_sorts():
    from services import fix_job_manager as fjm
    db = _FakeDB()
    db.fix_jobs.rows.extend([
        {"job_id": "fx_1", "user_id": "u1", "status": "done",
         "started_at": 100},
        {"job_id": "fx_2", "user_id": "u1", "status": "orphaned",
         "started_at": 200},
        {"job_id": "fx_3", "user_id": "u2", "status": "done",
         "started_at": 150},
    ])
    rows_u1 = asyncio.run(fjm.list_jobs(db, "u1", limit=10))
    ids_u1 = [r["job_id"] for r in rows_u1]
    assert ids_u1 == ["fx_2", "fx_1"]   # newest first
    rows_u1_orphan = asyncio.run(fjm.list_jobs(db, "u1", status="orphaned"))
    assert [r["job_id"] for r in rows_u1_orphan] == ["fx_2"]


# ──────────────────────────────────────────────────────────────────
# 6) Restart endpoint logic — pure function exercise on the helper
# ──────────────────────────────────────────────────────────────────
def test_get_persisted_owner_check():
    from services import fix_job_manager as fjm
    db = _FakeDB()
    db.fix_jobs.rows.append({
        "job_id": "fx_a", "user_id": "u1", "status": "orphaned",
    })
    found = asyncio.run(fjm.get_persisted(db, "fx_a", "u1"))
    assert found and found["job_id"] == "fx_a"
    other = asyncio.run(fjm.get_persisted(db, "fx_a", "u2"))
    assert other is None


# ──────────────────────────────────────────────────────────────────
# 7) Top-level exception handler — `_run_bulk_job` must not silently
#    die when `apply_finding_fix` raises an unexpected error.  We
#    monkeypatch the helper to raise and confirm a job-error event
#    plus a `failed` Mongo close are produced.
# ──────────────────────────────────────────────────────────────────
def test_run_bulk_job_top_level_exception_handled(monkeypatch):
    from routers import fix_pipeline as fp
    from services import fix_job_manager as fjm

    db = _FakeDB()
    findings = [{"id": "f1", "severity": "high", "file": "a.py"}]

    # Stub apply_finding_fix to NOT raise — the worker should
    # complete cleanly.  We then inject a poisoned _interleave_by_
    # severity that DOES raise to trigger the top-level path.
    async def fake_apply(*a, **kw):
        return {"ok": True, "commit_sha": "abc1234",
                "full_sha": "abc1234" + "0" * 33,
                "html_url": "https://x", "file": "a.py", "rule_id": "r"}
    monkeypatch.setattr(fp, "apply_finding_fix", fake_apply)

    def poisoned(_):
        raise RuntimeError("simulated worker crash")
    monkeypatch.setattr(fp, "_interleave_by_severity", poisoned)

    async def go():
        jid = await fjm.create_job(
            db=db, user_id="u1", kind="bulk", total=1,
            project_id="p1", findings=findings,
        )
        await fp._run_bulk_job(
            job_id=jid, db=db, user={"user_id": "u1"},
            project_id="p1", findings=findings, is_unlim=True,
        )
        return jid

    jid = asyncio.run(go())
    # Mongo row must be flipped to status=failed.
    row = next((r for r in db.fix_jobs.rows if r["job_id"] == jid), None)
    assert row is not None
    assert row["status"] == "failed"
    # Worker must NOT have died silently — the job is closed.
    assert row.get("closed_at") is not None
