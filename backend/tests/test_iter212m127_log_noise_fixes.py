"""
Iter 212m-127 — Production log-noise cleanup.

Covers four orthogonal fixes that were exposed by reading the live
production logs from auremcto.com:

  1. `/cto/projects/list` request dedup (frontend) — not unit-tested
     here, see test_iter212m127_api_dedup.spec or visual smoke.
  2. `repo_heal` permanent-failure cooldown — when `_finalise()` is
     called with `repo_gone_or_no_access` (or any reason the next
     30s poll cannot fix), the project must be blocked from being
     heal-scheduled again for 30 minutes.
  3. `repo_heal.schedule_heal()` must mark `_last_heal_at`
     synchronously to close the race where two simultaneous
     schedule_heal calls both pass `_allowed()`.
  4. `GET /codebase-health/last?project_id=X` must return
     `{ok: true, score: null}` (200) when no scan has been
     persisted, NOT 404 — the Dashboard health-ring relies on a
     200 response to render the empty state without spamming the
     error stream.
"""
from __future__ import annotations

import asyncio
import time

import pytest


# ──────────────────────────────────────────────────────────────────
# Shared in-memory Mongo doubles.
# ──────────────────────────────────────────────────────────────────
class _FakeColl:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.inserts: list[dict] = []
        self.updates: list[dict] = []

    async def find_one(self, q, projection=None, sort=None):
        # Trivial equality match for our test queries (only top-level keys).
        matches = [
            r for r in self.rows
            if all(r.get(k) == v for k, v in (q or {}).items())
        ]
        if sort:
            # sort is a list of (key, dir) tuples; we honour dir=-1 (desc).
            for key, direction in reversed(sort):
                matches.sort(key=lambda r, _k=key: r.get(_k, 0),
                             reverse=(direction == -1))
        return dict(matches[0]) if matches else None

    async def insert_one(self, doc):
        self.inserts.append(dict(doc))
        self.rows.append(dict(doc))

        class _R:
            inserted_id = "x"
        return _R()


class _FakeDB:
    def __init__(self):
        self.repo_heal_audit          = _FakeColl()
        self.codebase_health_scans    = _FakeColl()


@pytest.fixture(autouse=True)
def _reset_repo_heal_state():
    from services import repo_heal as rh
    rh._last_heal_at.clear()
    rh._inflight.clear()
    rh._cooldown_until.clear()
    yield
    rh._last_heal_at.clear()
    rh._inflight.clear()
    rh._cooldown_until.clear()


# ──────────────────────────────────────────────────────────────────
# 1) Permanent-failure cooldown blocks back-to-back heals.
# ──────────────────────────────────────────────────────────────────
def test_permanent_failure_blocks_heal_for_30_min():
    from services import repo_heal as rh
    db = _FakeDB()
    # Simulate a heal that exhausted the 404 lookup → repo deleted.
    asyncio.run(rh._finalise(
        db, "p_dead", success=False, reason="repo_gone_or_no_access",
    ))
    # Cooldown map must be populated with a future timestamp ≥ 25 min.
    until = rh._cooldown_until.get("p_dead", 0.0)
    assert until > time.time() + (25 * 60), \
        f"expected ≥25m cooldown, got {until - time.time():.0f}s"
    # `_allowed()` must now refuse to schedule another heal.
    assert rh._allowed("p_dead") is False


def test_all_tokens_failed_prefix_also_blocks():
    """`reason` strings include parenthetical details — the matcher
    must use prefix logic, not strict equality."""
    from services import repo_heal as rh
    db = _FakeDB()
    asyncio.run(rh._finalise(
        db, "p_x", success=False,
        reason="all_tokens_failed (tried: oauth,pat)",
    ))
    assert rh._allowed("p_x") is False
    assert rh._cooldown_until.get("p_x", 0.0) > time.time() + (25 * 60)


def test_transient_failure_uses_normal_cooldown_only():
    """Non-permanent failures (network glitch retries exhausted)
    must NOT extend cooldown — the 5-minute normal one suffices."""
    from services import repo_heal as rh
    db = _FakeDB()
    asyncio.run(rh._finalise(
        db, "p_net", success=False,
        reason="network_retry_exhausted: ConnectError",
    ))
    # The 30-min permanent block must NOT be set.
    assert "p_net" not in rh._cooldown_until
    # 5-min cooldown is enforced by _last_heal_at — set it explicitly
    # to confirm normal cooldown logic still works.
    rh._last_heal_at["p_net"] = time.time()
    assert rh._allowed("p_net") is False


def test_success_clears_permanent_block():
    """If a previously-dead repo comes back (e.g. user re-created
    or re-linked it), a successful heal must wipe its permanent
    cooldown so future polls aren't blocked for 30 minutes."""
    from services import repo_heal as rh
    db = _FakeDB()
    rh._cooldown_until["p_revived"] = time.time() + 1000
    asyncio.run(rh._finalise(
        db, "p_revived", success=True, reason="repo_accessible_now",
    ))
    assert "p_revived" not in rh._cooldown_until


def test_clear_cooldown_helper_unblocks():
    """`clear_cooldown()` is what the project-edit endpoints call
    after the user updates the PAT or re-links the repo."""
    from services import repo_heal as rh
    rh._cooldown_until["p_x"] = time.time() + 9999
    rh._last_heal_at["p_x"]   = time.time()
    rh.clear_cooldown("p_x")
    assert "p_x" not in rh._cooldown_until
    assert "p_x" not in rh._last_heal_at


# ──────────────────────────────────────────────────────────────────
# 2) schedule_heal must close the race by marking _last_heal_at
#    synchronously before handing the task to the event loop.
# ──────────────────────────────────────────────────────────────────
def test_schedule_heal_synchronously_marks_last_heal_at():
    """Verifies the race-fix from iter 212m-127: two simultaneous
    schedule_heal() calls in the same event-loop tick must NOT both
    pass `_allowed()` and spawn duplicate heal tasks."""
    from services import repo_heal as rh

    async def _run():
        # First call passes `_allowed` and sets _last_heal_at.
        rh.schedule_heal(
            db=_FakeDB(), user_id="u1", project_id="p_race",
            prior_status={"error": "github_rejected", "auth": "pat"},
        )
        # The map must be populated IMMEDIATELY — before the spawned
        # task gets a chance to run.
        assert "p_race" in rh._last_heal_at
        # A second schedule_heal() in the same tick must be blocked.
        assert rh._allowed("p_race") is False
        # Drain the task we spawned so pytest doesn't warn about an
        # unfinished asyncio task.  Cancellation is fine — heal_project
        # checks `_allowed()` itself before doing any real work.
        for t in asyncio.all_tasks():
            if t is not asyncio.current_task():
                t.cancel()
        await asyncio.gather(*(t for t in asyncio.all_tasks()
                               if t is not asyncio.current_task()),
                             return_exceptions=True)

    asyncio.run(_run())


# ──────────────────────────────────────────────────────────────────
# 3) `_is_permanent_failure` classifier sanity-check.
# ──────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("reason,expected", [
    ("repo_gone_or_no_access",                     True),
    ("no_oauth_to_attach",                         True),
    ("no_token_for_retry",                         True),
    ("no_token_for_lookup",                        True),
    ("needs_user_input",                           True),
    ("not_owned",                                  True),
    ("all_tokens_failed (tried: oauth,pat)",       True),
    ("network_retry_exhausted: ConnectError",      False),
    ("github_rejected_then_recovered",             False),
    ("oauth_token_works",                          False),
    ("",                                           False),
])
def test_is_permanent_failure_classifier(reason, expected):
    from services.repo_heal import _is_permanent_failure
    assert _is_permanent_failure(reason) is expected


# ──────────────────────────────────────────────────────────────────
# 4) `GET /codebase-health/last` returns 200 with `score: null`
#    when no scan has been persisted yet.
# ──────────────────────────────────────────────────────────────────
def test_last_returns_null_score_when_no_scan_persisted(monkeypatch):
    """Replaces the 404 log-noise — the Dashboard health ring already
    treats `score: null` as the empty state."""
    import importlib
    ch = importlib.import_module("routers.codebase_health")

    # Stub get_db to point at our in-memory double.
    fake_db = _FakeDB()
    monkeypatch.setattr(ch, "get_db", lambda: fake_db)

    # Stub current_dev so we don't need a real JWT.
    async def fake_current_dev(_auth):
        return {"user_id": "u1", "is_admin": False}
    monkeypatch.setattr(ch, "current_dev", fake_current_dev)

    res = asyncio.run(ch.last_scan(project_id="p_fresh", authorization=None))
    assert res["ok"] is True
    assert res["score"] is None


def test_last_returns_persisted_scan_when_present(monkeypatch):
    import importlib
    ch = importlib.import_module("routers.codebase_health")
    fake_db = _FakeDB()
    # Seed two scans for the same user+project; the newest must win.
    asyncio.run(fake_db.codebase_health_scans.insert_one({
        "user_id":    "u1", "project_id": "p1",
        "score":      62, "label": "Fair", "tone": "amber",
        "total":      11, "scanned_files": 320,
        "summary":    "older summary",
        "categories": ["security"],
        "created_at": time.time() - 3600,
    }))
    asyncio.run(fake_db.codebase_health_scans.insert_one({
        "user_id":    "u1", "project_id": "p1",
        "score":      87, "label": "Good", "tone": "emerald",
        "total":      4,  "scanned_files": 600,
        "summary":    "newest summary",
        "categories": ["security", "performance"],
        "created_at": time.time(),
    }))
    monkeypatch.setattr(ch, "get_db", lambda: fake_db)

    async def fake_current_dev(_auth):
        return {"user_id": "u1", "is_admin": False}
    monkeypatch.setattr(ch, "current_dev", fake_current_dev)

    res = asyncio.run(ch.last_scan(project_id="p1", authorization=None))
    assert res["ok"] is True
    assert res["score"] == 87
    assert res["summary"] == "newest summary"
    assert res["total"] == 4


def test_last_400_when_project_id_missing(monkeypatch):
    import importlib
    from fastapi import HTTPException
    ch = importlib.import_module("routers.codebase_health")
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(ch.last_scan(project_id=None, authorization=None))
    assert exc_info.value.status_code == 400


# ──────────────────────────────────────────────────────────────────
# 5) Graph agent timeout override (12 s → 25 s).  This is a config
#    contract test that pins the constant so a future refactor
#    doesn't accidentally regress it.
# ──────────────────────────────────────────────────────────────────
def test_graph_agent_has_longer_warm_start_timeout():
    """Read the constant out of cto_projects via the source file so
    the test doesn't need to spin up the whole _run_warm_agents."""
    src = open("/app/backend/routers/cto_projects.py").read()
    # Sanity: the dict literal contains both the brain (12.0) and
    # graph (25.0) timeout overrides.
    assert '"brain":     12.0' in src
    assert '"graph":     25.0' in src
