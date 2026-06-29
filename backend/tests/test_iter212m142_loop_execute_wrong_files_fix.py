"""
Iter 212m-142 — Loop Execute wrong-file bug fix in `file_selector`.

LIVE PROD REPRO (Feb 2026 founder QA on `auremcto.com`):
  • User asked Loop Mode to: "Add a one-line comment at the top of
    backend/.gitignore that says: # Aurem CTO QA test marker"
  • Planner correctly returned `files_to_change=['backend/.gitignore']`
    with title "Add gitignore QA marker comment"
  • Execute then asked `select_relevant_files` to refine the file list.
  • The selector iterated EVERY node in the graph, scoring by keyword
    match against the user prompt. The user prompt's tokens — "comment",
    "test", "aurem", "marker", "delete" — happen to match many router
    files in the codebase. Those scored higher than `backend/.gitignore`
    (which has zero keyword matches; the planner-bonus +200 was its
    only score).
  • With `top_n=10`, the top 10 keyword-matched files (10 random
    routers) populated `candidates`. The planner's `.gitignore` was
    then APPENDED at index 10 (correct intent) — but the final return
    `candidates[:max(top_n, len(planner_set))]` =
    `candidates[:max(10, 1)] = candidates[:10]` TRUNCATED it back out.
  • Execute then modified 10 unrelated files. Verify failed with
    `FileNotFoundError`. No GitHub commit. Loop ABORTED.

ROOT FIX:
  A. Trust the planner when its scope is small (≤ 2 files). At that
     scale the keyword sweep is a net-negative — it can only mislead.
     Return the planner's files verbatim with `trusted_planner: True`.
  B. When the sweep DOES run (3+ planner files OR planner_files=[]
     fully autonomous), cap the final candidates at
     `top_n + len(planner_set)` so planner-appended entries are NEVER
     truncated. The old `max(top_n, len(planner_set))` cut planner
     files whenever `len(planner_set) < top_n` — exact PROD bug.

These two fixes together guarantee that ANY file the planner names
will reach Execute. Multi-file plans (e.g. "refactor X across Y, Z, W")
still get the keyword-similarity ranking bonus for adjacent helpers.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from services import file_selector as fs


pytestmark = pytest.mark.asyncio


def _mock_graph(paths: list[str]):
    return {
        "ok": True,
        "nodes": {p: {"layer": "service"} for p in paths},
    }


async def _setup_graph(monkeypatch, paths: list[str]):
    async def _stub(*a, **kw): return _mock_graph(paths)
    # file_selector does `from services.graph_builder import get_graph_full`
    # at call-time, so patch the source module not file_selector.
    monkeypatch.setattr(
        "services.graph_builder.get_graph_full", _stub, raising=False,
    )


async def test_small_planner_scope_is_trusted_verbatim(monkeypatch):
    """The exact PROD repro: planner picked 1 file but the keyword
    sweep would have scored other files higher. Selector must trust
    the planner and return ONLY the planner's file."""
    paths = (
        ["backend/.gitignore"]
        + [f"backend/routers/aurem_router_{i}.py" for i in range(20)]
    )
    await _setup_graph(monkeypatch, paths)
    result = await fs.select_relevant_files(
        db=None,
        project_id="p_test",
        user_id="u1",
        task_description=(
            "Add a one-line comment at the top of backend/.gitignore "
            "that says: # Aurem CTO QA test marker"
        ),
        planner_files=["backend/.gitignore"],
        top_n=10,
    )
    assert result["ok"] is True
    assert result["has_graph"] is True
    assert result["candidates"] == ["backend/.gitignore"]
    assert result.get("trusted_planner") is True


async def test_two_file_planner_scope_is_also_trusted(monkeypatch):
    """≤ 2 planner files is the trust threshold."""
    await _setup_graph(monkeypatch, ["a.py", "b.py", "c.py", "d.py"])
    result = await fs.select_relevant_files(
        db=None, project_id="p", user_id="u",
        task_description="refactor a and b",
        planner_files=["a.py", "b.py"],
        top_n=10,
    )
    assert set(result["candidates"]) == {"a.py", "b.py"}
    assert result.get("trusted_planner") is True


async def test_three_file_planner_runs_keyword_sweep(monkeypatch):
    """At 3+ planner files we run the keyword sweep to find adjacent
    helpers that the planner might have missed."""
    await _setup_graph(monkeypatch, [
        "a.py", "b.py", "c.py",
        "helper_one.py", "helper_two.py",
    ])
    result = await fs.select_relevant_files(
        db=None, project_id="p", user_id="u",
        task_description="add helper to a b c",
        planner_files=["a.py", "b.py", "c.py"],
        top_n=10,
    )
    # All planner files MUST survive even when keyword-low.
    assert {"a.py", "b.py", "c.py"} <= set(result["candidates"])
    assert result.get("trusted_planner") is not True


async def test_planner_files_survive_truncation_at_top_n_boundary(monkeypatch):
    """The exact mechanical bug: with top_n=2 and 1 planner file that
    keyword-scores 0, the old `candidates[:max(2,1)] = [:2]` would
    drop the planner file at index 2. Fixed: `[:top_n + len(planner)]`
    = `[:3]` keeps it."""
    # Force planner_set=3 so we skip the small-scope trust path.
    await _setup_graph(monkeypatch, [
        "match_one.py", "match_two.py", "match_three.py",
        "planner_only.py",
    ])
    result = await fs.select_relevant_files(
        db=None, project_id="p", user_id="u",
        task_description="match one two three",
        # 3 planner files → sweeper runs.  But two of them won't match
        # any keyword and we test only the last one needs the
        # planner-append path.
        planner_files=["match_one.py", "match_two.py", "planner_only.py"],
        top_n=2,
    )
    assert "planner_only.py" in result["candidates"], (
        "planner file with score 0 must NOT be truncated when the "
        "top_n keyword winners eat the first slots."
    )


async def test_no_planner_files_runs_keyword_sweep(monkeypatch):
    """Fully autonomous mode (planner_files=[]) — keyword sweep returns
    its top_n results."""
    await _setup_graph(monkeypatch, [
        f"file_{i}.py" for i in range(15)
    ])
    result = await fs.select_relevant_files(
        db=None, project_id="p", user_id="u",
        task_description="touch file_1 file_2 file_3",
        planner_files=[],
        top_n=5,
    )
    # Should still return up to top_n candidates from the sweep.
    assert isinstance(result["candidates"], list)
    assert result.get("trusted_planner") is not True
