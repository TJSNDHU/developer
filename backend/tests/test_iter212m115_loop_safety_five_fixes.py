"""
Iter 212m-115 — Five production safety primitives for Loop Mode + Fix:

  1. PAT pre-flight (validate_github_token)
  2. Concurrent loop lock (acquire_loop_lock / release_loop_lock)
  3. Resume paused Ship on browser refresh (GET /loop/active)
  4. Circuit breaker (record_loop_failure / is_loop_circuit_open)
  5. Branch-per-fix mode (aurem_branch_name + create_or_reuse_branch
     + open_draft_pr wired into finding_fix_applier.apply_finding_fix)
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock


# ─── 1. PAT pre-flight ────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_validate_github_token_happy_path(monkeypatch):
    from services import loop_safety as ls
    import httpx
    class _R:
        status_code = 200
        headers = {}
    class _Client:
        def __init__(self, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def get(self, *a, **k): return _R()
    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    ok, err = await ls.validate_github_token("o", "r", "ghp_x")
    assert ok is True and err is None


@pytest.mark.asyncio
async def test_validate_github_token_401_returns_clean_error(monkeypatch):
    from services import loop_safety as ls
    import httpx
    class _R:
        status_code = 401
        headers = {}
    class _Client:
        def __init__(self, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def get(self, *a, **k): return _R()
    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    ok, err = await ls.validate_github_token("o", "r", "ghp_bad")
    assert ok is False
    assert err == "pat_invalid_or_expired"


@pytest.mark.asyncio
async def test_validate_github_token_missing_args():
    from services.loop_safety import validate_github_token
    ok, err = await validate_github_token("", "r", "ghp_x")
    assert ok is False and err == "missing_args"


# ─── 2. Concurrent-loop lock ─────────────────────────────────────────
@pytest.mark.asyncio
async def test_acquire_loop_lock_succeeds_first_time():
    from services.loop_safety import acquire_loop_lock
    inserted = []
    class _Locks:
        async def delete_many(self, q): return
        async def insert_one(self, doc):
            inserted.append(doc); return type("R", (), {"inserted_id": 1})()
        async def find_one(self, q, proj=None): return None
    class _DB: loop_locks = _Locks()
    ok, existing = await acquire_loop_lock(_DB(), "p1", "u1", "loop_1")
    assert ok is True and existing is None
    assert inserted[0]["loop_id"] == "loop_1"


@pytest.mark.asyncio
async def test_acquire_loop_lock_refuses_second_concurrent_run():
    from services.loop_safety import acquire_loop_lock
    class _Locks:
        async def delete_many(self, q): return
        async def insert_one(self, doc):
            raise Exception("E11000 duplicate key")
        async def find_one(self, q, proj=None):
            return {"loop_id": "loop_running", "acquired_at": 1234.5}
    class _DB: loop_locks = _Locks()
    ok, existing = await acquire_loop_lock(_DB(), "p1", "u1", "loop_2")
    assert ok is False
    assert existing["loop_id"] == "loop_running"


@pytest.mark.asyncio
async def test_release_loop_lock_calls_delete():
    from services.loop_safety import release_loop_lock
    deletes = []
    class _Locks:
        async def delete_one(self, q):
            deletes.append(q); return type("R", (), {"deleted_count": 1})()
    class _DB: loop_locks = _Locks()
    await release_loop_lock(_DB(), "p1", "u1", "loop_1")
    assert deletes == [{"project_id": "p1", "user_id": "u1", "loop_id": "loop_1"}]


def test_start_loop_router_uses_concurrent_lock():
    src = open("/app/backend/routers/loop.py").read()
    assert "acquire_loop_lock" in src
    assert "loop_already_running" in src
    # 409 status is the correct conflict code.
    assert "HTTPException(409" in src


# ─── 3. Resume paused Ship on refresh ─────────────────────────────────
@pytest.mark.asyncio
async def test_get_active_loop_returns_paused_ship_state(monkeypatch):
    from routers import loop as lr
    async def fake_current_dev(auth=None): return {"user_id": "u1"}
    monkeypatch.setattr(lr, "current_dev", fake_current_dev)

    class _Sessions:
        async def find_one(self, q, sort=None):
            assert q["user_id"] == "u1"
            assert "paused_for_user" in q["state"]["$in"]
            return {
                "_id":        "obj",
                "loop_id":    "loop_x",
                "state":      "paused_for_user",
                "phase":      "ship",
                "project_id": "p1",
                "context":    {
                    "plan": {"title": "T"},
                    "ship_pending": {
                        "owner": "o", "repo": "r", "branch": "main",
                        # MUST be stripped from the response:
                        "token": "ghp_secret",
                        "files": {"a.py": "x"},
                        "commit_message": "m",
                    },
                    "files_changed": ["a.py"],
                },
                "updated_at": 123,
            }
    class _DB: loop_sessions = _Sessions()
    monkeypatch.setattr(lr, "get_db", lambda: _DB())

    res = await lr.get_active_loop(project_id="p1", authorization="Bearer x")
    assert res["ok"] is True
    active = res["active"]
    assert active["loop_id"] == "loop_x"
    assert active["state"] == "paused_for_user"
    assert active["phase"] == "ship"
    # The PAT must NEVER appear in the response.
    assert "token" not in active["ship_pending"]
    assert active["ship_pending"]["owner"] == "o"
    assert active["ship_pending"]["files"] == {"a.py": "x"}


@pytest.mark.asyncio
async def test_get_active_loop_returns_none_when_no_active(monkeypatch):
    from routers import loop as lr
    async def fake_current_dev(auth=None): return {"user_id": "u1"}
    monkeypatch.setattr(lr, "current_dev", fake_current_dev)
    class _Sessions:
        async def find_one(self, q, sort=None): return None
    class _DB: loop_sessions = _Sessions()
    monkeypatch.setattr(lr, "get_db", lambda: _DB())
    res = await lr.get_active_loop(authorization="Bearer x")
    assert res["active"] is None


# ─── 4. Circuit breaker ──────────────────────────────────────────────
@pytest.mark.asyncio
async def test_circuit_breaker_opens_after_3_failures():
    from services.loop_safety import is_loop_circuit_open
    import time
    now = time.time()
    class _Cursor:
        async def to_list(self, length=None):
            return [
                {"occurred_at": now - 60},
                {"occurred_at": now - 30},
                {"occurred_at": now - 5},
            ]
    class _Fails:
        def find(self, q, proj=None):  # Motor's find() is SYNC
            return _Cursor()
    class _DB: loop_failures = _Fails()
    is_open, count, retry_after = await is_loop_circuit_open(_DB(), "p1", "u1")
    assert is_open is True
    assert count == 3
    assert retry_after > 0


@pytest.mark.asyncio
async def test_circuit_breaker_closed_under_threshold():
    from services.loop_safety import is_loop_circuit_open
    import time
    now = time.time()
    class _Cursor:
        async def to_list(self, length=None):
            return [
                {"occurred_at": now - 60},
                {"occurred_at": now - 30},
            ]
    class _Fails:
        def find(self, q, proj=None):
            return _Cursor()
    class _DB: loop_failures = _Fails()
    is_open, count, retry_after = await is_loop_circuit_open(_DB(), "p1", "u1")
    assert is_open is False
    assert count == 2
    assert retry_after is None


def test_start_loop_router_invokes_circuit_breaker():
    src = open("/app/backend/routers/loop.py").read()
    assert "is_loop_circuit_open" in src
    assert "loop_circuit_open" in src
    assert "HTTPException(429" in src


# ─── 5. Branch-per-fix ───────────────────────────────────────────────
def test_aurem_branch_name_is_deterministic_and_safe():
    from services.loop_safety import aurem_branch_name
    name = aurem_branch_name("fix", "secret_aws/key!")
    assert name.startswith("aurem/fix-")
    # Must NOT contain unsafe path chars.
    assert "/" not in name[len("aurem/"):]
    assert "!" not in name


@pytest.mark.asyncio
async def test_finding_fix_applier_uses_branch_per_fix(monkeypatch):
    """The full apply pipeline must (a) create the dedicated fix branch,
    (b) commit to it (NOT to main), (c) open a draft PR."""
    from services import finding_fix_applier as ff
    from services import loop_safety as ls

    class _Proj:
        async def find_one(self, q, proj=None):
            return {"github_owner": "o", "github_repo": "r",
                    "github_branch": "main", "github_token": None}
    class _Users:
        async def find_one(self, q, proj=None):
            return {"github": {"access_token": "ghp_x"}}
    class _Fixes:
        async def insert_one(self, doc): pass
    class _DB:
        cto_projects  = _Proj()
        dev_users     = _Users()
        finding_fixes = _Fixes()

    import routers.security_scan as ss
    async def fake_decrypt(uid, tok): return None
    monkeypatch.setattr(ss, "_decrypt_pat", fake_decrypt)

    async def fake_fetch(*a, **k):
        return "API_KEY = 'AKIA'\n", None
    monkeypatch.setattr(ff, "_fetch_file_content", fake_fetch)

    async def fake_llm(*, path, current_content, finding, user_id, **_kw):
        return "import os\nAPI_KEY = os.environ.get('K')\n", None
    monkeypatch.setattr(ff, "_generate_patched_content", fake_llm)
    monkeypatch.setattr(ff, "_finding_still_present",
                        lambda *a, **k: False)

    # Capture the branch the commit landed on + the PR creation call.
    captured_branch = {}
    pr_calls: list[dict] = []

    async def fake_create_branch(**kw):
        captured_branch["new_branch"] = kw["new_branch"]
        captured_branch["base"] = kw["base_branch"]
        return True, None
    monkeypatch.setattr(ls, "create_or_reuse_branch", fake_create_branch)
    # finding_fix_applier imports these symbols locally, so patch on
    # the actual finding_fix_applier module-level "from" import too —
    # but since the function uses `from services.loop_safety import …`
    # locally each call, patching the source module is sufficient.

    async def fake_open_pr(**kw):
        pr_calls.append(kw)
        return "https://github.com/o/r/pull/42", None
    monkeypatch.setattr(ls, "open_draft_pr", fake_open_pr)

    commits = []
    async def fake_commit(**kw):
        commits.append(kw)
        return {"sha": "abc1234",
                "full_sha": "abc1234deadbeef",
                "html_url": "https://github.com/o/r/commit/abc1234"}
    import services.github_api_writer as gw
    monkeypatch.setattr(gw, "commit_files", fake_commit)

    res = await ff.apply_finding_fix(
        db=_DB(), user={"user_id": "u1"}, project_id="p1",
        finding={
            "rule_id":  "secret_aws_access_key",
            "file":     "app.py",
            "line":     1,
            "severity": "critical",
            "title":    "AWS leak",
            "message":  "x",
            "snippet":  "x",
        },
    )
    assert res["ok"] is True
    assert res["pr_url"] == "https://github.com/o/r/pull/42"
    # The commit landed on the fix branch, NOT on main.
    assert commits and commits[0]["branch"].startswith("aurem/fix-secret_aws_access_key-")
    # The branch we created matches what we committed to.
    assert captured_branch["new_branch"] == commits[0]["branch"]
    assert captured_branch["base"] == "main"
    # The draft PR was opened with the correct head/base.
    assert pr_calls[0]["head_branch"] == commits[0]["branch"]
    assert pr_calls[0]["base_branch"] == "main"
    # The user-facing message mentions the branch.
    assert "branch" in res["message"]


@pytest.mark.asyncio
async def test_finding_fix_applier_falls_back_to_base_if_branch_create_fails(monkeypatch):
    """If branch creation fails for any reason, fall back to base
    branch instead of leaving the fix un-applied — preserves backward
    compatibility for legacy projects."""
    from services import finding_fix_applier as ff
    from services import loop_safety as ls

    class _Proj:
        async def find_one(self, q, proj=None):
            return {"github_owner": "o", "github_repo": "r",
                    "github_branch": "main", "github_token": None}
    class _Users:
        async def find_one(self, q, proj=None):
            return {"github": {"access_token": "ghp_x"}}
    class _Fixes:
        async def insert_one(self, doc): pass
    class _DB:
        cto_projects  = _Proj()
        dev_users     = _Users()
        finding_fixes = _Fixes()

    import routers.security_scan as ss
    monkeypatch.setattr(ss, "_decrypt_pat", AsyncMock(return_value=None))
    async def fake_fetch(*a, **k): return "x = 1\n", None
    monkeypatch.setattr(ff, "_fetch_file_content", fake_fetch)
    async def fake_llm(**kw): return "x = 2\n", None
    monkeypatch.setattr(ff, "_generate_patched_content", fake_llm)
    monkeypatch.setattr(ff, "_finding_still_present", lambda *a, **k: False)

    async def fail_branch(**kw):
        return False, "base_ref_status_500"
    monkeypatch.setattr(ls, "create_or_reuse_branch", fail_branch)

    commits = []
    async def fake_commit(**kw):
        commits.append(kw)
        return {"sha": "x", "full_sha": "xxxxx", "html_url": "h"}
    import services.github_api_writer as gw
    monkeypatch.setattr(gw, "commit_files", fake_commit)

    res = await ff.apply_finding_fix(
        db=_DB(), user={"user_id": "u1"}, project_id="p1",
        finding={"rule_id": "x", "file": "a.py", "title": "t"},
    )
    assert res["ok"] is True
    # Fell back to main.
    assert commits[0]["branch"] == "main"
    # No PR URL since we committed to base.
    assert res["pr_url"] is None


# ─── 6. Source-level invariants (the wiring is permanent) ─────────────
def test_loop_engine_calls_pat_preflight_in_plan_phase():
    src = open("/app/backend/services/loop_engine.py").read()
    plan_block = src.split("async def _do_plan(", 1)[1].split("async def confirm(", 1)[0]
    assert "validate_github_token" in plan_block
    # Must check project_id before doing preflight (skip if no project).
    assert "self.project_id" in plan_block


def test_loop_engine_releases_lock_on_completion_and_failure():
    src = open("/app/backend/services/loop_engine.py").read()
    # Lock release wired into both _fail and confirm_ship completion.
    assert src.count("release_loop_lock") >= 3
    assert "record_loop_failure" in src


def test_main_py_creates_loop_safety_indexes_on_boot():
    src = open("/app/backend/main.py").read()
    assert "ensure_loop_lock_index" in src
    assert "ix_loop_fail_window" in src
