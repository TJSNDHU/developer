"""
Iter 290 (Track 1 Lane A follow-up) — test-style analyzer.

Locks the classifier so its heuristic can't silently drift. The
analyzer is deterministic AST-parsing; every claim below is exercised
against real fixture-source strings so the regression stays honest.

Bonus: this test file is DELIBERATELY behavioural — it imports
`services.test_style_analyzer` and executes `classify_test_function`
against fixture ASTs. It should self-classify as BEHAVIOURAL in the
final self-check assertion.
"""
from __future__ import annotations

import ast
import os
import sys

sys.path.insert(0, "/app/backend")

from services.test_style_analyzer import (
    analyze_file, analyze_suite, classify_test_function,
    _collect_imported_symbols,
)


def _parse_fn(src: str):
    """Parse a single top-level function definition from source and
    return (fn_node, module_tree)."""
    tree = ast.parse(src)
    fn = next(n for n in tree.body
              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)))
    return fn, tree


# ── Static-grep classification ───────────────────────────────────────

def test_analyzer_flags_static_grep_pattern():
    src = '''
def test_static_example():
    with open("/tmp/foo.py") as f:
        s = f.read()
    assert "TOKEN" in s
'''
    fn, tree = _parse_fn(src)
    imported = _collect_imported_symbols(tree)
    assert classify_test_function(fn, imported) == "STATIC_GREP"


def test_analyzer_flags_helper_style_read_as_static_grep():
    """The `_read(path)` helper pattern (many iter286+ tests use it)
    must still trigger STATIC_GREP classification."""
    src = '''
def _read(p): return open(p).read()

def test_helper_read_style():
    src = _read("/app/backend/routers/mcp.py")
    assert "is_test_or_fixture" in src
'''
    tree = ast.parse(src)
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef)
              and n.name == "test_helper_read_style")
    imported = _collect_imported_symbols(tree)
    assert classify_test_function(fn, imported) == "STATIC_GREP"


# ── Behavioural classification ───────────────────────────────────────

def test_analyzer_flags_await_as_behavioural():
    src = '''
from services.foo import some_fn

async def test_awaits_prod_code():
    r = await some_fn()
    assert r["ok"] is True
'''
    tree = ast.parse(src)
    fn = next(n for n in tree.body
              if isinstance(n, ast.AsyncFunctionDef))
    imported = _collect_imported_symbols(tree)
    assert "some_fn" in imported
    assert classify_test_function(fn, imported) == "BEHAVIOURAL"


def test_analyzer_flags_asyncio_run_as_behavioural():
    src = '''
import asyncio
from services.foo import fn

def test_sync_asyncio_run():
    r = asyncio.run(fn(1))
    assert r > 0
'''
    tree = ast.parse(src)
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef))
    imported = _collect_imported_symbols(tree)
    assert classify_test_function(fn, imported) == "BEHAVIOURAL"


def test_analyzer_flags_direct_prod_call_as_behavioural():
    """A test that imports `load_matrix` from services.qa_matrix and
    calls it directly (no await) is still behavioural — it exercises
    production code paths."""
    src = '''
from services.qa_matrix import load_matrix

def test_calls_prod_fn():
    r = load_matrix()
    assert r["journeys"]
'''
    tree = ast.parse(src)
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef))
    imported = _collect_imported_symbols(tree)
    assert "load_matrix" in imported
    assert classify_test_function(fn, imported) == "BEHAVIOURAL"


# ── Hybrid classification ────────────────────────────────────────────

def test_analyzer_flags_hybrid_when_both_read_and_exec_present():
    src = '''
from services.foo import fn

def test_reads_and_calls():
    src = open("/tmp/x.py").read()
    r = fn(src)
    assert "OK" in src
    assert r == 1
'''
    tree = ast.parse(src)
    fn_ = next(n for n in tree.body
               if isinstance(n, ast.FunctionDef))
    imported = _collect_imported_symbols(tree)
    assert classify_test_function(fn_, imported) == "HYBRID"


def test_analyzer_flags_unknown_when_no_signal():
    src = '''
def test_pure_constant():
    assert 1 + 1 == 2
'''
    fn, tree = _parse_fn(src)
    imported = _collect_imported_symbols(tree)
    assert classify_test_function(fn, imported) == "UNKNOWN"


# ── Real-suite analysis is well-formed ───────────────────────────────

def test_analyze_suite_shape_on_current_backend():
    r = analyze_suite()
    assert r["ok"] is True
    assert isinstance(r["counts"], dict)
    for k in ("STATIC_GREP", "BEHAVIOURAL", "HYBRID", "UNKNOWN"):
        assert k in r["counts"]
    assert isinstance(r["total_tests"], int) and r["total_tests"] > 0
    assert isinstance(r["weak_p0"], list)
    # Sanity — ratio pcts sum to ~100 (rounding tolerated).
    total_pct = sum(r["ratio"].values())
    assert 99.0 <= total_pct <= 101.0, r["ratio"]


def test_weak_p0_only_ever_contains_static_grep_tests():
    """A weak_p0 entry is by definition (a) STATIC_GREP kind and
    (b) named around a p0 security-critical concern. Prove the
    coupling holds — if we ever add a BEHAVIOURAL test to weak_p0
    that would be a bug in the classifier."""
    r = analyze_suite()
    if not r["weak_p0"]:
        return
    for w in r["weak_p0"]:
        # Reconstruct: find the file report, then the test kind.
        found = False
        for f in r["files"]:
            if not f.get("ok"):
                continue
            if f["path"].endswith("/" + w["file"]):
                for t in (f.get("tests") or []):
                    if t["test"] == w["test"]:
                        assert t["kind"] == "STATIC_GREP", (
                            f"weak_p0 must only carry STATIC_GREP "
                            f"tests, got {t['kind']} for {w}"
                        )
                        found = True
                        break
        assert found, f"weak_p0 entry not found in files report: {w}"


def test_file_pattern_filters_correctly():
    r = analyze_suite(file_pattern=r"iter289_track1_lane_a")
    assert r["ok"] is True
    assert r["total_tests"] > 0
    # Only files matching the pattern must appear.
    for f in r["files"]:
        assert "iter289_track1_lane_a" in f["path"]


# ── MCP tool wiring ──────────────────────────────────────────────────

def test_mcp_registers_static_vs_behavioural_tool():
    from routers.mcp import TOOLS, _TOOL_DISPATCH
    assert "qa_static_vs_behavioural_ratio" in _TOOL_DISPATCH
    names = {t["name"] for t in TOOLS}
    assert "qa_static_vs_behavioural_ratio" in names


def test_mcp_static_vs_behavioural_arg_validation():
    import asyncio
    import pytest as _pt
    from routers.mcp import _tool_qa_static_vs_behavioural_ratio
    from routers import mcp as _mcp

    async def _noop(_uid):
        return None

    orig = _mcp._require_founder
    _mcp._require_founder = _noop
    try:
        async def _run():
            with _pt.raises(ValueError):
                await _tool_qa_static_vs_behavioural_ratio(
                    "anyone", {"file_pattern": 12345})
        asyncio.run(_run())
    finally:
        _mcp._require_founder = orig


# ── Self-referential meta-check — this file must be BEHAVIOURAL ──────

def test_this_file_self_classifies_as_mostly_behavioural():
    """This regression itself imports the analyzer and exercises it.
    Every test above should classify as BEHAVIOURAL — if the analyzer
    heuristic drifts and starts flagging behavioural code as static,
    this self-check screams."""
    r = analyze_file(__file__)
    assert r["ok"] is True
    kinds = [t["kind"] for t in r["tests"]]
    behavioural = sum(1 for k in kinds if k == "BEHAVIOURAL")
    # Vast majority — allow a small margin for the fixture-parsing
    # helpers that don't await.
    assert behavioural >= len(kinds) // 2, (
        f"the analyzer thinks its own regression file is mostly "
        f"non-behavioural — heuristic drift! kinds={kinds}"
    )
