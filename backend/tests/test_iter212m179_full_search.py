"""
Iter 212m-179 — PROPER full-repo search_repo fix.

The 15s/400-file budget hack (iter 212m-178) returned PARTIAL results
on big repos — rejected by the founder. New primary path: one tarball
snapshot per HEAD SHA, searched completely on local disk (ripgrep /
Python walk). These tests cover:
  • snapshot path returns COMPLETE results (complete=True)
  • path / ext filters work on the snapshot
  • hidden files (.github/…) are searched
  • Python-walk fallback (no rg) finds the same hits
  • budgeted GitHub API scan survives as fallback only
"""
from __future__ import annotations

import re
import shutil

import pytest

pytestmark = pytest.mark.asyncio


def _bin_ctx(uid="u_t"):
    from services.bin_context import BINContext
    return BINContext(bin_id=uid, pid="p_t", repo_owner="TJSNDHU",
                      repo_name="Aurem", branch="main", pat="ghp_x",
                      is_founder=True)


def _make_snapshot(tmp_path):
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "app.py").write_text(
        "import os\nSECRET_TOKEN = 'abc'\ndef handler():\n    return 1\n")
    (tmp_path / "backend" / "util.py").write_text(
        "def secret_token_check():\n    pass\n")
    (tmp_path / "frontend").mkdir()
    (tmp_path / "frontend" / "App.jsx").write_text(
        "const token = 'secret_token';\nexport default App;\n")
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github" / "ci.yml").write_text(
        "env:\n  SECRET_TOKEN: from-ci\n")
    (tmp_path / ".aurem_head_sha").write_text("deadbeef")
    return str(tmp_path)


async def _run_search(monkeypatch, tmp_path, args):
    import services.local_tools as lt
    snap = _make_snapshot(tmp_path)

    async def _fake_snapshot(*a, **k):
        return snap, None
    monkeypatch.setattr(lt, "_ensure_repo_snapshot", _fake_snapshot)
    ctx = {"user_id": "u_t", "project_id": "p_t", "bin_ctx": _bin_ctx()}
    return await lt.search_repo(ctx, args)


async def test_snapshot_search_is_complete(monkeypatch, tmp_path):
    r = await _run_search(monkeypatch, tmp_path, {"pattern": "secret_token"})
    assert r["ok"]
    assert r["complete"] is True
    assert r["source"] == "full_repo_snapshot"
    assert r["budget_hit"] is False
    files = {m["file"] for m in r["matches"]}
    # every file containing the pattern — including the hidden .github dir
    assert "backend/app.py" in files
    assert "backend/util.py" in files
    assert "frontend/App.jsx" in files
    assert ".github/ci.yml" in files


async def test_snapshot_search_ext_filter(monkeypatch, tmp_path):
    r = await _run_search(monkeypatch, tmp_path,
                          {"pattern": "secret_token", "ext": "py"})
    files = {m["file"] for m in r["matches"]}
    assert files == {"backend/app.py", "backend/util.py"}


async def test_snapshot_search_path_filter(monkeypatch, tmp_path):
    r = await _run_search(monkeypatch, tmp_path,
                          {"pattern": "secret_token", "path": "frontend"})
    files = {m["file"] for m in r["matches"]}
    assert files == {"frontend/App.jsx"}


async def test_snapshot_no_match_note_says_complete(monkeypatch, tmp_path):
    r = await _run_search(monkeypatch, tmp_path,
                          {"pattern": "zzz_never_matches"})
    assert r["ok"] and r["total_matches"] == 0
    assert r["complete"] is True
    assert "ENTIRE repo" in r["note"]


async def test_python_walk_fallback_matches_rg(monkeypatch, tmp_path):
    from services.local_tools import _search_snapshot_sync
    snap = _make_snapshot(tmp_path)
    compiled = re.compile("secret_token", re.IGNORECASE)
    rg_hits = _search_snapshot_sync(snap, "secret_token", compiled, "", "")
    monkeypatch.setattr(shutil, "which", lambda _n: None)
    py_hits = _search_snapshot_sync(snap, "secret_token", compiled, "", "")
    assert {(m["file"], m["line_no"]) for m in rg_hits} == \
           {(m["file"], m["line_no"]) for m in py_hits}
    assert len(py_hits) >= 4


async def test_api_fallback_used_when_snapshot_unavailable(monkeypatch):
    import services.local_tools as lt

    tree = {"truncated": False,
            "tree": [{"path": "src/app.py", "type": "blob"}]}

    class _Resp:
        status_code = 200
        def json(self): return tree
        def raise_for_status(self): return None

    class _Client:
        def __init__(self, *a, **k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, headers=None): return _Resp()

    async def _no_snapshot(*a, **k):
        return None, "tarball_status_500"

    async def _fake_fetch(owner, repo, fpath, branch, token):
        return "def foo():\n    return 1\n"

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    monkeypatch.setattr(lt, "_ensure_repo_snapshot", _no_snapshot)
    monkeypatch.setattr(lt, "_gh_fetch_file", _fake_fetch)
    ctx = {"user_id": "u_t", "project_id": "p_t", "bin_ctx": _bin_ctx()}
    r = await lt.search_repo(ctx, {"pattern": "foo"})
    assert r["ok"]
    assert r["source"] == "github_api_fallback"
    assert r["matches"][0]["file"] == "src/app.py"
