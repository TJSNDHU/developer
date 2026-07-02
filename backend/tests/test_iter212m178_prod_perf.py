"""
Iter 212m-178 — PROD perf/hang fixes found during the second PROD
aggression run (July 2026):
  • search_repo took 79s on the 16k-file TJSNDHU/Aurem repo (fetched
    every file until 20 matches) → stalled advisor/analyze turns past
    the ingress proxy limit → zero-frame SSE hang at ~125s.
  • Council writing-vocab was too narrow (CODE_OF_CONDUCT.md → A).
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.asyncio
BACKEND = Path(__file__).resolve().parents[1]


def _bin_ctx(uid="u_t"):
    from services.bin_context import BINContext
    return BINContext(bin_id=uid, pid="p_t", repo_owner="TJSNDHU",
                      repo_name="Aurem", branch="main", pat="ghp_x",
                      is_founder=True)


# ── search_repo hard budget (perf) ───────────────────────────────────

async def test_search_repo_stops_at_file_budget(monkeypatch):
    import services.local_tools as lt

    # 5000-file tree, pattern that never matches → old code would fetch
    # all 5000 (79s on PROD). New code must cap fetches.
    big_tree = {"truncated": False,
                "tree": [{"path": f"src/mod_{i}.py", "type": "blob"}
                         for i in range(5000)]}

    class _Resp:
        status_code = 200
        def json(self): return big_tree
        def raise_for_status(self): return None

    class _Client:
        def __init__(self, *a, **k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, headers=None): return _Resp()

    fetched = {"n": 0}

    async def _fake_fetch(owner, repo, fpath, branch, token):
        fetched["n"] += 1
        return "no match here\nanother line\n"

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    monkeypatch.setattr(lt, "_gh_fetch_file", _fake_fetch)

    ctx = {"user_id": "u_t", "project_id": "p_t", "bin_ctx": _bin_ctx()}
    r = await lt.search_repo(ctx, {"pattern": "zzz_never_matches"})
    assert r["ok"]
    # Must NOT have fetched all 5000 — capped at the 400-file budget.
    assert fetched["n"] <= 420, f"fetched {fetched['n']} files (budget bust)"
    assert r["budget_hit"] is True
    assert r["files_fetched"] <= 420


async def test_search_repo_prefers_text_extensions(monkeypatch):
    import services.local_tools as lt
    tree = {"truncated": False, "tree": (
        [{"path": f"assets/img_{i}.png", "type": "blob"} for i in range(300)]
        + [{"path": "src/app.py", "type": "blob"}]
    )}

    class _Resp:
        status_code = 200
        def json(self): return tree
        def raise_for_status(self): return None

    class _Client:
        def __init__(self, *a, **k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, headers=None): return _Resp()

    seen = []

    async def _fake_fetch(owner, repo, fpath, branch, token):
        seen.append(fpath)
        return "def foo():\n    return 1\n"

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    monkeypatch.setattr(lt, "_gh_fetch_file", _fake_fetch)
    ctx = {"user_id": "u_t", "project_id": "p_t", "bin_ctx": _bin_ctx()}
    await lt.search_repo(ctx, {"pattern": "foo"})
    # Only the .py file should be fetched — .png assets skipped.
    assert seen == ["src/app.py"], seen


# ── orchestrator per-tool timeout (hang fix) ─────────────────────────

def test_orchestrator_wraps_tool_calls_in_timeout():
    src = (BACKEND / "services" / "orchestrator.py").read_text()
    assert re.search(
        r"asyncio\.wait_for\(\s*invoke_local_tool\(tool_name, tool_args, "
        r"local_ctx\),\s*timeout=45\.0",
        src), "agentic invoke_local_tool must be hard-capped at 45s"
    assert '"timed_out": True' in src


# ── council writing-vocab expansion ──────────────────────────────────

def test_council_writing_vocab_expanded():
    from core.parliament import infer_task_type, TaskRouter
    router = TaskRouter()
    writes = [
        "Write a short CODE_OF_CONDUCT.md for this repo",
        "Write a LICENSE file",
        "Write an ARCHITECTURE.md explaining the layout",
        "Draft a SECURITY.md policy",
    ]
    for p in writes:
        assert infer_task_type(p) == "write", p
        assert router._TASK_TYPE_TO_COUNCIL["write"] == "C"
    # code tasks that happen to mention a file must NOT become 'write'
    for p in ["Add a docstring to auth.py",
              "Write a function to parse dates",
              "Fix the regex in models.py"]:
        assert infer_task_type(p) is None, p


# ── bulk fix GitHub secondary-rate-limit handling ────────────────────

async def test_fetch_file_retries_on_403_secondary_limit(monkeypatch):
    import services.finding_fix_applier as ffa

    calls = {"n": 0}

    class _Resp:
        def __init__(self, code, text="", headers=None):
            self.status_code = code
            self.text = text
            self.headers = headers or {}

    class _Client:
        def __init__(self, *a, **k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, params=None, headers=None):
            calls["n"] += 1
            if calls["n"] == 1:
                return _Resp(403, headers={"Retry-After": "1"})   # secondary limit
            return _Resp(200, text="def foo():\n    return 1\n")

    monkeypatch.setattr(ffa, "httpx",
                        SimpleNamespace(AsyncClient=_Client))
    monkeypatch.setattr(ffa.asyncio, "sleep",
                        lambda *_a, **_k: _noop())
    content, err = await ffa._fetch_file_content(
        "O", "R", "main", "src/app.py", "ghp_x")
    assert err is None and "foo" in content
    assert calls["n"] == 2, "must retry the 403 once then succeed"


async def _noop():
    return None


def test_bulk_loop_paces_github_mutations():
    src = (BACKEND / "routers" / "fix_pipeline.py").read_text()
    assert "_BULK_INTER_FIX_DELAY_S" in src
    assert re.search(r"if global_idx > 1:\s*\n\s*await asyncio\.sleep\("
                     r"_BULK_INTER_FIX_DELAY_S\)", src)
