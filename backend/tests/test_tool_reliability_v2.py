"""Iter 212m-7 — Tool reliability v2: needs_tool detection, chunking
contract verification, repo structure cache.

Honest scope note for Fix #1 (`tool_choice: "any"`):
  We don't pass `tools=[...]` natively to OpenRouter — the orchestrator
  embeds the tool catalog in the SYSTEM PROMPT and parses tool calls
  from the text response (`extract_tool_calls`). The "force a tool call"
  pressure is therefore exerted at the prompt level via
  `_should_inject_tool_reminder` (Iter 212m-4) using the EXACT regex
  the user specified for Fix #1. These tests verify the prompt-level
  needs_tool gate is wired and behaves identically to the API-level
  `tool_choice` semantic the spec describes.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.orchestrator import _should_inject_tool_reminder, _wants_execute
from services.local_tools import (
    _apply_chunking,
    _extract_symbols,
    _cache_set,
    _cache_get,
    _cache_invalidate,
    _update_structure_cache,
    _REPO_STRUCTURE_CACHE,
    _REPO_CACHE_MAX_FILES_PER_PROJECT,
    get_repo_structure,
    LOCAL_TOOLS,
    TOOL_SPECS,
)


@pytest.fixture(autouse=True)
def _clean_cache():
    """Each test starts with an empty structure cache."""
    _REPO_STRUCTURE_CACHE.clear()
    yield
    _REPO_STRUCTURE_CACHE.clear()


# ──────────────────────────────────────────────────────────────────
# Fix #1 — needs_tool detection (prompt-level equivalent of tool_choice)
# ──────────────────────────────────────────────────────────────────


def test_needs_tool_file_path_with_project():
    assert _should_inject_tool_reminder("read backend/routers/admin.py", True) is True
    assert _wants_execute("read backend/routers/admin.py", True, []) is True


def test_needs_tool_no_project():
    assert _should_inject_tool_reminder("read admin.py", False) is False
    assert _wants_execute("read admin.py", False, []) is False


def test_needs_tool_greeting_with_project_stays_off():
    """Conversational greetings must NOT trigger needs_tool even on
    a connected repo (no false positives → no breaking 'hello' UX)."""
    assert _should_inject_tool_reminder("hello how are you", True) is False
    assert _wants_execute("hello how are you", True, []) is False


def test_needs_tool_topic_keywords_with_project():
    """Topic keywords (routes / functions / backend / frontend / router /
    service) on a connected project must fire — these are the exact
    phrasings the older verb-only patterns missed."""
    for prompt in [
        "show me the routers",
        "how many routes do we have",
        "list backend services",
        "what functions exist",
        "list components",
    ]:
        assert _should_inject_tool_reminder(prompt, True) is True, prompt


# ──────────────────────────────────────────────────────────────────
# Fix #2 — _apply_chunking contract
# ──────────────────────────────────────────────────────────────────


def test_apply_chunking_small_file_passthrough():
    res = _apply_chunking("x = 1\n" * 50, {})
    assert res["truncated"] is False
    assert res["content"] == "x = 1\n" * 50


def test_apply_chunking_large_no_lines_returns_structure():
    body = "\n".join(["def fn():", "    pass"] * 1000)   # ≈ 22k chars
    res = _apply_chunking(body, {})
    assert res["truncated"] is True
    assert res["total_lines"] == 2000
    # Exactly 200-line preview.
    assert len(res["content"].splitlines()) == 200
    assert "structure" in res
    assert len(res["structure"]) <= 40
    # First entry must reference the very first `def`.
    assert res["structure"][0].startswith("L1:")


def test_apply_chunking_large_with_lines_slice():
    """Sequential `line N` produce a body > 12 KB. lines=[10,20] →
    0-indexed Python slice: lines 10..19 inclusive."""
    body = "\n".join([f"line {i}" for i in range(2000)]) + "\n" + "x" * 12_000
    res = _apply_chunking(body, {"lines": [10, 20]})
    assert res["truncated"] is True
    assert "line 10" in res["content"]
    assert "line 19" in res["content"]
    # Exclusive upper bound.
    assert "line 20" not in res["content"].split("\n")


def test_apply_chunking_structure_detects_vanilla_function():
    """Iter 212m-7 added `function ` to the regex (vanilla JS)."""
    body = (
        "import x\n"
        "function helloWorld() {\n"
        "  return 1;\n"
        "}\n"
    ) * 800
    res = _apply_chunking(body, {})
    assert res["truncated"] is True
    hits = [s for s in res["structure"] if "function " in s]
    assert hits, "expected vanilla `function ` lines in structure"


# ──────────────────────────────────────────────────────────────────
# Fix #3 — Repo structure cache + get_repo_structure tool
# ──────────────────────────────────────────────────────────────────


def test_extract_symbols_picks_up_all_decl_types():
    body = """
import x
def foo():
    pass
async def bar():
    return 1
class Baz:
    pass
@router.get("/x")
async def route_handler():
    pass
export default function Comp() {}
export const myConst = 1;
function legacy() {}
""".lstrip()
    syms = _extract_symbols(body)
    kinds = " ".join(s["symbol"] for s in syms)
    assert "def foo" in kinds
    assert "async def bar" in kinds
    assert "class Baz" in kinds
    assert "@router.get" in kinds
    assert "export default function" in kinds
    assert "export const myConst" in kinds
    assert "function legacy" in kinds


def test_extract_symbols_capped_at_100():
    body = "\n".join([f"def f_{i}():" for i in range(300)])
    syms = _extract_symbols(body)
    assert len(syms) == 100  # cap


def test_cache_set_and_get():
    _cache_set("proj_a", "file.py", [{"line": 1, "symbol": "def foo"}])
    res = _cache_get("proj_a", "file.py")
    assert res == [{"line": 1, "symbol": "def foo"}]
    # Whole-project read returns the bucket.
    whole = _cache_get("proj_a")
    assert "file.py" in whole


def test_cache_invalidate_single_path():
    _cache_set("proj_a", "f1.py", [{"line": 1, "symbol": "def f1"}])
    _cache_set("proj_a", "f2.py", [{"line": 1, "symbol": "def f2"}])
    _cache_invalidate("proj_a", "f1.py")
    assert _cache_get("proj_a", "f1.py") is None
    assert _cache_get("proj_a", "f2.py") is not None


def test_cache_invalidate_whole_project():
    _cache_set("proj_a", "f1.py", [{"line": 1, "symbol": "def f1"}])
    _cache_set("proj_a", "f2.py", [{"line": 1, "symbol": "def f2"}])
    _cache_invalidate("proj_a")
    assert _cache_get("proj_a") is None


def test_cache_file_count_capped():
    """File-level FIFO eviction kicks in at 200 files / project."""
    for i in range(_REPO_CACHE_MAX_FILES_PER_PROJECT + 5):
        _cache_set("proj_a", f"f{i}.py", [{"line": 1, "symbol": f"def f{i}"}])
    bucket = _cache_get("proj_a")
    assert len(bucket) == _REPO_CACHE_MAX_FILES_PER_PROJECT
    # First 5 should have been evicted.
    for i in range(5):
        assert f"f{i}.py" not in bucket


@pytest.mark.asyncio
async def test_update_structure_cache_skips_home():
    """`project_id='home'` (the unconnected workspace) must NEVER
    populate the cache — there's no repo context."""
    await _update_structure_cache("home", "foo.py", "def x(): pass")
    assert _cache_get("home") is None


@pytest.mark.asyncio
async def test_update_structure_cache_skips_empty_symbols():
    """No def/class/etc → no cache entry (we don't waste memory on
    files with no addressable structure)."""
    await _update_structure_cache("proj_a", "blob.py", "x = 1\ny = 2\n")
    assert _cache_get("proj_a") is None


@pytest.mark.asyncio
async def test_update_structure_cache_indexes_real_file():
    body = (
        "def first():\n"
        "    pass\n"
        "class Bar:\n"
        "    pass\n"
    )
    await _update_structure_cache("proj_a", "real.py", body)
    syms = _cache_get("proj_a", "real.py")
    assert syms
    kinds = [s["symbol"] for s in syms]
    assert any("def first" in k for k in kinds)
    assert any("class Bar" in k for k in kinds)


# ──────────────────────────────────────────────────────────────────
# get_repo_structure tool — public-facing API
# ──────────────────────────────────────────────────────────────────


def test_get_repo_structure_registered():
    assert "get_repo_structure" in LOCAL_TOOLS
    names = [s["name"] for s in TOOL_SPECS]
    assert "get_repo_structure" in names


@pytest.mark.asyncio
async def test_get_repo_structure_requires_project():
    res = await get_repo_structure({"project_id": "home"}, {})
    assert res["ok"] is False
    assert "project" in res["error"].lower()


@pytest.mark.asyncio
async def test_get_repo_structure_cold_cache():
    """Empty cache → ok=True with a hint telling the LLM to call
    read_repo_file first (no useless error)."""
    res = await get_repo_structure({"project_id": "p1"}, {})
    assert res["ok"] is True
    assert res["files_cached"] == 0
    assert "read_repo_file" in res["hint"]


@pytest.mark.asyncio
async def test_get_repo_structure_whole_project_after_read():
    await _update_structure_cache("p1", "a.py", "def a(): pass\nclass A: pass")
    await _update_structure_cache("p1", "b.py", "def b(): pass")
    res = await get_repo_structure({"project_id": "p1"}, {})
    assert res["ok"] is True
    assert res["files_cached"] == 2
    assert "a.py" in res["symbols"]
    assert "b.py" in res["symbols"]


@pytest.mark.asyncio
async def test_get_repo_structure_single_path():
    await _update_structure_cache("p1", "a.py", "def a(): pass\nclass A: pass")
    res = await get_repo_structure({"project_id": "p1"}, {"path": "a.py"})
    assert res["ok"] is True
    assert res["cached"] is True
    assert res["count"] == 2


@pytest.mark.asyncio
async def test_get_repo_structure_missing_path_hint():
    """Asking for a path that's never been read returns ok=True but
    `cached=False` + a hint — not an error."""
    await _update_structure_cache("p1", "a.py", "def a(): pass")
    res = await get_repo_structure({"project_id": "p1"}, {"path": "never_read.py"})
    assert res["ok"] is True
    assert res["cached"] is False
    assert "never_read.py" in res["hint"]
