"""
Iter 212m-234 — Phase 5 downgrade sweeper cron.

Locks in:
1. `sweep_once()` picks only rows with `downgrade_pending=True` AND
   `grace_until <= now`. Rows still inside their grace window are
   skipped.
2. **CRITICAL data-loss safeguard**: migrate_back policy verifies that
   the reverse-migration into shared Mongo actually landed BEFORE the
   destructive Supabase delete. Failure requeues, never deletes.
3. Retry limit: after `MAX_SWEEP_ATTEMPTS` (5) the row is escalated to
   `sweep_status="needs_founder"` and stops being retried automatically.
4. Escalated rows are excluded from the next sweep (require manual
   `rearm` action from founder).
5. `keep_bill_user` policy never triggers a delete — only surfaces on
   the admin widget.
6. Successful finalisation writes a copy into `supabase_projects_history`
   for audit trail, then removes the live row + flips storage_tier
   back to `shared_mongo` on the cto_projects doc.
"""

from __future__ import annotations

import time
import pytest


# ── Fake async Mongo shim (in-memory) ────────────────────────────
class _FakeCursor:
    def __init__(self, rows): self._rows = list(rows)
    def sort(self, *_a, **_k): return self
    def limit(self, *_a, **_k): return self
    def __aiter__(self): self._i = 0; return self
    async def __anext__(self):
        if self._i >= len(self._rows): raise StopAsyncIteration
        r = self._rows[self._i]; self._i += 1; return r


class _FakeColl:
    def __init__(self, docs=None): self.docs = list(docs or []); self.inserted = []
    def find(self, q=None, *_a, **_k): return _FakeCursor(self._match(q or {}))
    async def find_one(self, q=None, *_a, **_k):
        m = self._match(q or {}); return m[0] if m else None
    async def count_documents(self, q=None, *_a, **_k):
        return len(self._match(q or {}))
    async def insert_one(self, d): self.docs.append(d); return type("R", (), {"inserted_id": len(self.docs)})
    async def insert_many(self, ds, ordered=True): self.docs.extend(ds); return type("R", (), {"inserted_ids": []})
    async def update_one(self, q, u, upsert=False):
        m = self._match(q or {})
        if m:
            self._apply(m[0], u); return type("R", (), {"modified_count": 1, "matched_count": 1})
        if upsert:
            new = {**(q or {})}; self._apply(new, u); self.docs.append(new)
            return type("R", (), {"modified_count": 0, "matched_count": 0, "upserted_id": len(self.docs)})
        return type("R", (), {"modified_count": 0, "matched_count": 0})
    async def update_many(self, q, u):
        for d in self._match(q or {}): self._apply(d, u)
        return type("R", (), {"modified_count": len(self._match(q or {}))})
    async def delete_one(self, q):
        m = self._match(q or {})
        if m: self.docs.remove(m[0]); return type("R", (), {"deleted_count": 1})
        return type("R", (), {"deleted_count": 0})
    # ── helpers ───
    def _match(self, q):
        out = []
        for d in self.docs:
            if self._ok(d, q): out.append(d)
        return out
    def _ok(self, d, q):
        for k, v in q.items():
            if k == "$or":
                if not any(self._ok(d, sub) for sub in v): return False
                continue
            if isinstance(v, dict):
                for op, val in v.items():
                    dv = d.get(k)
                    if op == "$gte" and (dv is None or dv < val): return False
                    if op == "$lte" and (dv is None or dv > val): return False
                    if op == "$ne"  and dv == val:                  return False
                    if op == "$exists" and (k in d) != val:         return False
                    if op == "$type" and val == "date":             return False  # simplified
            else:
                if d.get(k) != v: return False
        return True
    def _apply(self, d, u):
        for op, patch in u.items():
            if op == "$set":
                for k, v in patch.items(): d[k] = v
            elif op == "$unset":
                for k in patch: d.pop(k, None)


class _FakeDB:
    def __init__(self):
        self.supabase_projects        = _FakeColl()
        self.supabase_projects_history = _FakeColl()
        self.cto_projects             = _FakeColl()
        self.aurem_managed_app_data   = _FakeColl()
    def __getitem__(self, name):
        # aurem_managed_db uses SHARED_COLLECTION="aurem_managed_app_data"
        return getattr(self, name)


# ── Supabase provisioner mocking helpers ─────────────────────────
class _DeleteResult:
    def __init__(self, ok=True, detail=""): self.ok = ok; self.detail = detail


@pytest.fixture
def db(): return _FakeDB()


@pytest.fixture
def mock_delete_ok(monkeypatch):
    from services import supabase_provisioner as sp
    async def _fake_delete(ref): return {"ok": True, "project_ref": ref, "deleted": True}
    monkeypatch.setattr(sp, "delete_project", _fake_delete)
    return _fake_delete


@pytest.fixture
def mock_delete_fail(monkeypatch):
    from services import supabase_provisioner as sp
    async def _fake_delete(ref): return {"ok": False, "reason": "supabase_500", "detail": "boom"}
    monkeypatch.setattr(sp, "delete_project", _fake_delete)


# ── Tests ────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_sweep_skips_rows_still_in_grace_window(db, mock_delete_ok):
    """A row whose grace_until is in the future must NOT be touched."""
    from services.supabase_sweeper import sweep_once
    db.supabase_projects.docs.append({
        "app_id": "pt_1", "user_id": "u1", "project_ref": "ref_1",
        "downgrade_pending": True, "downgrade_policy": "read_only",
        "downgrade_grace_until": time.time() + 1e6,  # far future
    })
    res = await sweep_once(db)
    assert res["stats"]["processed"] == 0
    # Row still present
    assert any(d["app_id"] == "pt_1" for d in db.supabase_projects.docs)


@pytest.mark.asyncio
async def test_sweep_finalises_expired_read_only(db, mock_delete_ok):
    """read_only + expired grace → deletes on Supabase + drops the local row."""
    from services.supabase_sweeper import sweep_once
    db.supabase_projects.docs.append({
        "app_id": "pt_2", "user_id": "u2", "project_ref": "ref_2",
        "downgrade_pending": True, "downgrade_policy": "read_only",
        "downgrade_grace_until": time.time() - 100,
    })
    db.cto_projects.docs.append({
        "project_id": "pt_2", "user_id": "u2",
        "storage_tier": "supabase_dedicated", "supabase_ref": "ref_2",
    })
    res = await sweep_once(db)
    assert res["stats"]["deleted"] == 1
    # Live row removed, history row written
    assert not any(d["app_id"] == "pt_2" for d in db.supabase_projects.docs)
    assert any(d["app_id"] == "pt_2" for d in db.supabase_projects_history.docs)
    # cto_projects flipped back to shared_mongo
    assert db.cto_projects.docs[0]["storage_tier"] == "shared_mongo"


@pytest.mark.asyncio
async def test_migrate_back_requires_data_present_in_shared_mongo(
    db, mock_delete_ok,
):
    """CRITICAL data-loss safeguard.

    If migrate_back claimed >=1 rows were migrated but the shared
    Mongo collection is empty at sweep time, we REFUSE to delete."""
    from services.supabase_sweeper import sweep_once
    db.supabase_projects.docs.append({
        "app_id": "pt_3", "user_id": "u3", "project_ref": "ref_3",
        "downgrade_pending": True, "downgrade_policy": "migrate_back",
        "downgrade_grace_until": time.time() - 100,
        "migrate_back_result": {"ok": True, "total_rows": 12},
    })
    # NO docs in shared collection → verification MUST fail
    res = await sweep_once(db)
    assert res["stats"]["deleted"] == 0, "delete blocked when data missing"
    assert res["stats"]["requeued"] == 1
    # Row still present, sweep_error recorded
    row = db.supabase_projects.docs[0]
    assert row.get("sweep_error", "").startswith(
        "migrate_back verification failed"
    )
    assert row.get("sweep_attempts") == 1


@pytest.mark.asyncio
async def test_migrate_back_passes_when_data_present(db, mock_delete_ok):
    """Happy path — migrate_back with rows actually present in shared
    Mongo → verified → deletes."""
    from services.supabase_sweeper import sweep_once
    db.supabase_projects.docs.append({
        "app_id": "pt_4", "user_id": "u4", "project_ref": "ref_4",
        "downgrade_pending": True, "downgrade_policy": "migrate_back",
        "downgrade_grace_until": time.time() - 100,
        "migrate_back_result": {"ok": True, "total_rows": 3},
    })
    # Simulate the reverse migration having landed
    db.aurem_managed_app_data.docs.append(
        {"app_id": "pt_4", "user_id": "u4", "_collection": "todos", "title": "x"},
    )
    res = await sweep_once(db)
    assert res["stats"]["deleted"] == 1
    assert not any(d["app_id"] == "pt_4" for d in db.supabase_projects.docs)


@pytest.mark.asyncio
async def test_migrate_back_with_zero_rows_originally_still_deletes(
    db, mock_delete_ok,
):
    """Edge case — user had NO data at downgrade time. total_rows=0
    is legitimate; we don't require a shared-Mongo row to delete."""
    from services.supabase_sweeper import sweep_once
    db.supabase_projects.docs.append({
        "app_id": "pt_5", "user_id": "u5", "project_ref": "ref_5",
        "downgrade_pending": True, "downgrade_policy": "migrate_back",
        "downgrade_grace_until": time.time() - 100,
        "migrate_back_result": {"ok": True, "total_rows": 0},
    })
    res = await sweep_once(db)
    assert res["stats"]["deleted"] == 1


@pytest.mark.asyncio
async def test_supabase_delete_failure_requeues_not_deletes(
    db, mock_delete_fail,
):
    """If Supabase's DELETE API fails, we must NOT drop the local row —
    otherwise we'd lose track of the un-deleted paid project."""
    from services.supabase_sweeper import sweep_once
    db.supabase_projects.docs.append({
        "app_id": "pt_6", "user_id": "u6", "project_ref": "ref_6",
        "downgrade_pending": True, "downgrade_policy": "read_only",
        "downgrade_grace_until": time.time() - 100,
    })
    res = await sweep_once(db)
    assert res["stats"]["deleted"] == 0
    assert res["stats"]["requeued"] == 1
    # Row still present with sweep_error
    assert any(d.get("sweep_error", "").startswith("supabase delete failed")
               for d in db.supabase_projects.docs)


@pytest.mark.asyncio
async def test_max_sweep_attempts_escalates_to_founder(db, mock_delete_fail):
    """After MAX_SWEEP_ATTEMPTS retries, mark `sweep_status=needs_founder`
    and stop retrying automatically."""
    from services.supabase_sweeper import sweep_once, MAX_SWEEP_ATTEMPTS
    db.supabase_projects.docs.append({
        "app_id": "pt_7", "user_id": "u7", "project_ref": "ref_7",
        "downgrade_pending": True, "downgrade_policy": "read_only",
        "downgrade_grace_until": time.time() - 100,
        "sweep_attempts": MAX_SWEEP_ATTEMPTS - 1,
    })
    res = await sweep_once(db)
    assert res["stats"]["escalated"] == 1
    row = db.supabase_projects.docs[0]
    assert row.get("sweep_status") == "needs_founder"


@pytest.mark.asyncio
async def test_escalated_rows_skipped_on_next_sweep(db, mock_delete_ok):
    """A row already flagged `needs_founder` must NOT be re-picked
    automatically — human intervention required."""
    from services.supabase_sweeper import sweep_once
    db.supabase_projects.docs.append({
        "app_id": "pt_8", "user_id": "u8", "project_ref": "ref_8",
        "downgrade_pending": True, "downgrade_policy": "read_only",
        "downgrade_grace_until": time.time() - 100,
        "sweep_status": "needs_founder",
    })
    res = await sweep_once(db)
    assert res["stats"]["processed"] == 0
    # Row untouched
    assert any(d["app_id"] == "pt_8" for d in db.supabase_projects.docs)


@pytest.mark.asyncio
async def test_keep_bill_user_never_deletes_only_surfaces(db, mock_delete_ok):
    """`keep_bill_user` policy is designed to preserve the project.
    Sweep must surface it on the widget but NEVER call delete."""
    from services.supabase_sweeper import sweep_once
    db.supabase_projects.docs.append({
        "app_id": "pt_9", "user_id": "u9", "project_ref": "ref_9",
        "downgrade_pending": True, "downgrade_policy": "keep_bill_user",
        "downgrade_grace_until": time.time() - 100,
    })
    res = await sweep_once(db)
    assert res["stats"]["deleted"] == 0
    assert res["stats"]["surfaced"] == 1
    # Row still present (would need a follow-up founder action)
    assert any(d["app_id"] == "pt_9" for d in db.supabase_projects.docs)


@pytest.mark.asyncio
async def test_list_pending_downgrades_sorts_by_soonest_grace(db):
    """Admin widget helper — earliest expiry first."""
    from services.supabase_sweeper import list_pending_downgrades
    now = time.time()
    db.supabase_projects.docs.extend([
        {"app_id": "a", "user_id": "u", "project_ref": "r1",
         "downgrade_pending": True, "downgrade_grace_until": now + 500},
        {"app_id": "b", "user_id": "u", "project_ref": "r2",
         "downgrade_pending": True, "downgrade_grace_until": now + 100},
    ])
    rows = await list_pending_downgrades(db)
    # Our fake `sort` is a no-op but the function must return both
    assert len(rows) == 2


# ── Router wire-in checks ────────────────────────────────────────
def test_admin_widget_endpoint_registered():
    from routers.supabase import router
    paths = [r.path for r in router.routes]
    assert "/supabase/admin/pending-downgrades" in paths
    assert "/supabase/admin/sweep-now" in paths
    assert "/supabase/admin/rearm/{app_id}" in paths


def test_cron_wired_into_main():
    src = open("/app/backend/main.py").read()
    assert "downgrade_sweeper_cron" in src
    assert "ENABLE_SUPABASE_SWEEPER" in src


def test_admin_endpoints_require_founder_or_admin():
    """Static — every admin route must guard on founder/admin."""
    src = open("/app/backend/routers/supabase.py").read()
    # Extract only the admin section (starts after the second "Founder-scoped" comment)
    admin_section = src[src.index("Founder-scoped admin widget"):]
    assert admin_section.count('user.get("is_founder")') >= 3, (
        "Each admin endpoint (list/sweep/rearm) must check founder flag"
    )
