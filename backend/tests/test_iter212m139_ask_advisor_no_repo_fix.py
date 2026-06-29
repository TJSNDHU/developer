"""
Iter 212m-139 — Ask Advisor "No repo connected" bug fix.

REPRO
-----
User reported: with TJSNDHU/Aurem connected in the sidebar, Ask Advisor
still said *"No repo is connected right now — I can't inspect your
pipeline"* and the tool trace showed:

    read_repo_files → {"ok": false,
                       "error": "No project connected or project not found"}

ROOT CAUSE
----------
`AskAdvisorReal.jsx` passes `project_id: activeProject?.project_id || null`.
`activeProject` comes from `useActiveProject()` → `aurem_active_project`
in localStorage.  If the user never explicitly clicked a tab (which is
the normal case for users with EXACTLY ONE project), that key is never
set, so the frontend always sends `project_id: null`.  Every tool then
hit `_resolve_project(..., project_id=None)` which short-circuited to
`return None` → "No project connected".

FIX (defence-in-depth, two layers — both required so we never
re-grow this bug)
-----------------
1. `routers/chat.py` (entry-level): right after authenticating the
   user, if `body.project_id` is null/empty/"home" AND the user has
   EXACTLY ONE connected project (real `github_owner` + `github_repo`),
   rewrite `body.project_id` to that project.  Single source of truth
   for the whole turn.

2. `services/local_tools._resolve_project`: same inference at the
   tool-resolution layer, so any future caller (NOT just /chat/stream)
   that passes a null project_id ALSO gets the right project.  Belt
   AND braces.

3. `frontend/src/components/TabBar.jsx`: after `/cto/projects/list`
   loads, if NO active project is stamped AND exactly one connected
   project exists, auto-activate it via `setActiveProjectId`.

This file pins all three at the source so the bug can't sneak back.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


pytestmark = pytest.mark.asyncio


# ── Fake DB ──────────────────────────────────────────────────────
class _Cursor:
    def __init__(self, rows): self._rows = list(rows)
    def limit(self, n): self._rows = self._rows[:n]; return self
    def sort(self, *a, **kw): return self
    def to_list(self, n):
        async def _coro(): return self._rows[:n]
        return _coro()


class _FakeColl:
    def __init__(self, docs): self.docs = list(docs)

    def find(self, filt, projection=None):
        out = []
        for d in self.docs:
            ok = True
            for k, v in filt.items():
                if isinstance(v, dict) and "$nin" in v:
                    if d.get(k) in v["$nin"]: ok = False; break
                elif d.get(k) != v:
                    ok = False; break
            if ok: out.append(dict(d))
        return _Cursor(out)

    async def find_one(self, filt, projection=None):
        for d in self.docs:
            if all(d.get(k) == v for k, v in filt.items()):
                return dict(d)
        return None


class _FakeDB:
    def __init__(self, projects):
        self.cto_projects = _FakeColl(projects)


# ── Fixtures ─────────────────────────────────────────────────────
@pytest.fixture
def db_one_connected():
    """User has one fully-connected project + one half-created row
    with no repo wired.  The inference must skip the half-created row
    and resolve to the connected one."""
    return _FakeDB([
        {"project_id": "p_connected", "user_id": "u1",
         "github_owner": "tj", "github_repo": "aurem",
         "github_token": "", "branch": "main"},
        {"project_id": "p_half_created", "user_id": "u1",
         "github_owner": "", "github_repo": "",
         "github_token": "", "branch": "main"},
    ])


@pytest.fixture
def db_two_connected():
    """User has TWO connected projects → inference must abstain."""
    return _FakeDB([
        {"project_id": "p_a", "user_id": "u1",
         "github_owner": "tj", "github_repo": "alpha",
         "github_token": "", "branch": "main"},
        {"project_id": "p_b", "user_id": "u1",
         "github_owner": "tj", "github_repo": "beta",
         "github_token": "", "branch": "main"},
    ])


@pytest.fixture
def db_zero_connected():
    """User has 0 connected projects → inference must return None."""
    return _FakeDB([])


# ── Backend tool-layer fix ───────────────────────────────────────
async def test_resolve_project_infers_sole_project_on_null_pid(
    monkeypatch, db_one_connected,
):
    from services import local_tools
    monkeypatch.setattr(local_tools, "get_db", lambda: db_one_connected)
    # Bypass the PAT-decrypt branch — we only care about resolution.
    async def _noop(*a, **kw): return None
    monkeypatch.setattr("routers.cto_projects._decrypt_pat", _noop, raising=False)
    monkeypatch.setattr("routers.cto_projects._user_gh_token", _noop, raising=False)

    proj = await local_tools._resolve_project("u1", None)
    assert proj is not None
    assert proj["project_id"] == "p_connected"


async def test_resolve_project_infers_sole_project_on_empty_pid(
    monkeypatch, db_one_connected,
):
    from services import local_tools
    monkeypatch.setattr(local_tools, "get_db", lambda: db_one_connected)
    async def _noop(*a, **kw): return None
    monkeypatch.setattr("routers.cto_projects._decrypt_pat", _noop, raising=False)
    monkeypatch.setattr("routers.cto_projects._user_gh_token", _noop, raising=False)

    proj = await local_tools._resolve_project("u1", "")
    assert proj is not None
    assert proj["project_id"] == "p_connected"


async def test_resolve_project_infers_sole_project_on_home_pid(
    monkeypatch, db_one_connected,
):
    from services import local_tools
    monkeypatch.setattr(local_tools, "get_db", lambda: db_one_connected)
    async def _noop(*a, **kw): return None
    monkeypatch.setattr("routers.cto_projects._decrypt_pat", _noop, raising=False)
    monkeypatch.setattr("routers.cto_projects._user_gh_token", _noop, raising=False)

    proj = await local_tools._resolve_project("u1", "home")
    assert proj is not None
    assert proj["project_id"] == "p_connected"


async def test_resolve_project_does_not_infer_with_two_connected(
    monkeypatch, db_two_connected,
):
    """Two wired projects AND neither is in the reachability cache → 
    ambiguous → LLM must explicitly disambiguate."""
    from services import local_tools
    monkeypatch.setattr(local_tools, "get_db", lambda: db_two_connected)
    # Empty cache → no disambiguation possible
    from routers import repo_status
    monkeypatch.setattr(repo_status, "_CACHE", {})

    proj = await local_tools._resolve_project("u1", None)
    assert proj is None


async def test_resolve_project_picks_reachable_when_one_dead(
    monkeypatch,
):
    """Iter 212m-141 — Real-world PROD repro: 2 projects in DB,
    `p_a` is reachable, `p_b` returns 404 from GitHub. The
    reachability cache reports only `p_a` as `connected`. Inference
    must pick `p_a`."""
    from services import local_tools
    db = _FakeDB([
        {"project_id": "p_a", "user_id": "u1",
         "github_owner": "tj", "github_repo": "alpha",
         "github_token": "", "branch": "main"},
        {"project_id": "p_b", "user_id": "u1",
         "github_owner": "polaris", "github_repo": "dead",
         "github_token": "", "branch": "main"},
    ])
    monkeypatch.setattr(local_tools, "get_db", lambda: db)
    from routers import repo_status
    monkeypatch.setattr(repo_status, "_CACHE", {
        "p_a": {"project_id": "p_a", "status": "connected"},
        "p_b": {"project_id": "p_b", "status": "disconnected",
                "error": "repo_not_found"},
    })
    async def _noop(*a, **kw): return None
    monkeypatch.setattr("routers.cto_projects._decrypt_pat", _noop, raising=False)
    monkeypatch.setattr("routers.cto_projects._user_gh_token", _noop, raising=False)

    proj = await local_tools._resolve_project("u1", None)
    assert proj is not None
    assert proj["project_id"] == "p_a"


async def test_resolve_project_abstains_when_two_both_connected(
    monkeypatch,
):
    """If both projects are reachable, we still abstain — that's
    genuine ambiguity, the LLM must disambiguate explicitly."""
    from services import local_tools
    db = _FakeDB([
        {"project_id": "p_a", "user_id": "u1",
         "github_owner": "tj", "github_repo": "alpha",
         "github_token": "", "branch": "main"},
        {"project_id": "p_b", "user_id": "u1",
         "github_owner": "tj", "github_repo": "beta",
         "github_token": "", "branch": "main"},
    ])
    monkeypatch.setattr(local_tools, "get_db", lambda: db)
    from routers import repo_status
    monkeypatch.setattr(repo_status, "_CACHE", {
        "p_a": {"project_id": "p_a", "status": "connected"},
        "p_b": {"project_id": "p_b", "status": "connected"},
    })

    proj = await local_tools._resolve_project("u1", None)
    assert proj is None


async def test_resolve_project_returns_none_with_zero_connected(
    monkeypatch, db_zero_connected,
):
    from services import local_tools
    monkeypatch.setattr(local_tools, "get_db", lambda: db_zero_connected)

    proj = await local_tools._resolve_project("u1", "")
    assert proj is None


async def test_resolve_project_explicit_pid_still_works(
    monkeypatch, db_two_connected,
):
    """When the caller passes an explicit pid we must NOT silently swap
    to the inferred one — explicit wins."""
    from services import local_tools
    monkeypatch.setattr(local_tools, "get_db", lambda: db_two_connected)
    async def _noop(*a, **kw): return None
    monkeypatch.setattr("routers.cto_projects._decrypt_pat", _noop, raising=False)
    monkeypatch.setattr("routers.cto_projects._user_gh_token", _noop, raising=False)

    proj = await local_tools._resolve_project("u1", "p_a")
    assert proj["project_id"] == "p_a"


async def test_resolve_project_no_user_id_returns_none(monkeypatch):
    from services import local_tools
    monkeypatch.setattr(local_tools, "get_db", lambda: _FakeDB([]))
    assert await local_tools._resolve_project("", None) is None
    assert await local_tools._resolve_project(None, None) is None


async def test_dev_skills_resolve_project_delegates_to_local_tools(
    monkeypatch, db_one_connected,
):
    """dev_skills had a DUPLICATE _resolve_project (older, missing the
    iter 212m-139 inference). It now delegates to the local_tools
    version so the fix benefits skills like `find_symbol_usages` /
    `read_files` / `get_repo_structure` automatically."""
    from services import dev_skills, local_tools
    monkeypatch.setattr(local_tools, "get_db", lambda: db_one_connected)
    async def _noop(*a, **kw): return None
    monkeypatch.setattr("routers.cto_projects._decrypt_pat", _noop, raising=False)
    monkeypatch.setattr("routers.cto_projects._user_gh_token", _noop, raising=False)

    proj = await dev_skills._resolve_project("u1", None)
    assert proj is not None
    assert proj["project_id"] == "p_connected"


# ── Route-level fix (chat.py) — source-pattern contract ─────────
def test_chat_router_auto_infers_sole_project_on_null_body_pid():
    """The /chat/stream route must run the same single-project
    inference at the top of the handler so brain_ctx / repo_ctx /
    council retrieval ALL get the right project_id — not just the
    tool resolution layer."""
    src = Path("/app/backend/routers/chat.py").read_text(encoding="utf-8")
    # Must reference 212m-139 marker so future agents understand why
    # this block exists.
    assert "Iter 212m-139" in src, (
        "Expected the Ask-Advisor 'No repo connected' fix marker in "
        "chat.py so future refactors don't accidentally drop it."
    )
    assert "auto-inferred sole project" in src
    # Must operate on body.project_id (not just _resolve_project).
    assert "body.project_id =" in src or "body.project_id=" in src


# ── Frontend fix — source-pattern contract ───────────────────────
def test_tabbar_auto_activates_sole_connected_project():
    """TabBar.refresh() must auto-call setActiveProjectId(...) when no
    tab is active and exactly one connected project is in the list.
    Defence-in-depth: even if the backend inference somehow misses,
    the frontend never sends `project_id: null` to begin with."""
    src = Path("/app/frontend/src/components/TabBar.jsx").read_text(encoding="utf-8")
    assert "Iter 212m-139" in src
    # Must filter to wired projects (github_owner + github_repo set).
    assert "p.github_owner && p.github_repo" in src
    # Must auto-set when wired.length === 1 and no active.
    assert "wired.length === 1" in src
    assert "setActiveProjectId(wired[0].project_id)" in src
