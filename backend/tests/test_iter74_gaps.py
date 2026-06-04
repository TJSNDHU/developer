"""
test_iter74_gaps.py — Iter 74 four technical gap fixes:
  GAP 1  semantic_search_repo + get_commit_diff
  GAP 2  Python AST syntax validation (vanguard + pre-push gate)
  GAP 3  Multi-file task instruction
  GAP 4  Tools in orchestrator catalog + parallel-reads persona
"""
from __future__ import annotations

import asyncio
import os
import re

import pytest


def _read(rel: str) -> str:
    base = os.path.join(os.path.dirname(__file__), "..", "..")
    with open(os.path.join(base, rel), encoding="utf-8") as fh:
        return fh.read()


# ── GAP 1 — semantic_search_repo + get_commit_diff ────────────────────

@pytest.mark.asyncio
async def test_semantic_search_requires_query():
    from services.local_tools import semantic_search_repo
    r = await semantic_search_repo({"user_id": "u1", "project_id": "p1"}, {})
    assert r["ok"] is False
    assert "query" in r["error"].lower()


@pytest.mark.asyncio
async def test_get_commit_diff_requires_sha():
    from services.local_tools import get_commit_diff
    r = await get_commit_diff({"user_id": "u1", "project_id": "p1"}, {})
    assert r["ok"] is False
    assert "sha" in r["error"].lower()


def test_both_tools_in_tool_specs_and_dispatch():
    from services.local_tools import TOOL_SPECS, LOCAL_TOOLS
    names = [t["name"] for t in TOOL_SPECS]
    assert "semantic_search_repo" in names
    assert "get_commit_diff" in names
    assert "semantic_search_repo" in LOCAL_TOOLS
    assert "get_commit_diff" in LOCAL_TOOLS


# ── GAP 2 — Python AST syntax validation ──────────────────────────────

def test_vanguard_catches_python_syntax_error():
    from services.vanguard_scanner import scan_text
    bad = "def foo(:\n    pass\n"
    findings = scan_text(bad, filepath="bad.py")
    crit = [f for f in findings if f.get("rule") == "python_syntax_error"]
    assert crit, "expected CRITICAL python_syntax_error finding"
    assert crit[0]["severity"] == "CRITICAL"
    assert crit[0]["source"] == "ast"


def test_vanguard_passes_valid_python():
    from services.vanguard_scanner import scan_text
    good = "def foo():\n    return 42\n"
    findings = scan_text(good, filepath="good.py")
    syntax = [f for f in findings if f.get("rule") == "python_syntax_error"]
    assert syntax == []


def test_pre_push_syntax_gate_present():
    """The worker pipeline must validate AST before pushing."""
    src = _read("backend/routers/cto_projects.py")
    assert "Syntax validation" in src
    assert "_syntax_errors" in src
    # AST is the source of truth for Python
    assert "_ast.parse" in src
    # JS/TS validated via `node --check` (replacing the old bracket
    # heuristic in Iter 74 follow-up)
    assert "_check_js_syntax" in src
    # Auto-retry fires before failing
    assert "AI syntax-fix auto-retry" in src


# ── GAP 3 — Multi-file task instruction ───────────────────────────────

def test_multi_file_instruction_in_runner():
    src = _read("backend/routers/cto_projects.py")
    assert "MULTI-FILE TASK DETECTED" in src
    assert "_multi_file_keywords" in src
    assert "_multi_file_instruction" in src
    # Make sure the instruction is appended to user_msg, not dropped
    assert "f\"{_multi_file_instruction}\"" in src


def test_multi_file_keywords_match_expected_tasks():
    multi_tasks = [
        "create all 4 workers",
        "scaffold the full auth system",
        "implement every pillar",
        "add multiple endpoints",
        "ship the complete implementation",
    ]
    kws = ("all ", "every ", "each ", "multiple", "scaffold",
           "workers", "pillar", "complete", "full implementation")
    for t in multi_tasks:
        assert any(kw in t.lower() for kw in kws), f"should detect: {t!r}"

    simple_tasks = ["add a button", "fix the typo", "update the title"]
    for t in simple_tasks:
        assert not any(kw in t.lower() for kw in kws), f"should NOT detect: {t!r}"


# ── GAP 4 / 5 — Orchestrator catalog + parallel-reads persona ─────────

def test_new_tools_in_orchestrator_catalog():
    from services.orchestrator import _TOOL_HELP_TEMPLATE
    assert "semantic_search_repo" in _TOOL_HELP_TEMPLATE
    assert "get_commit_diff" in _TOOL_HELP_TEMPLATE


def test_parallel_section_in_tool_help():
    from services.orchestrator import _TOOL_HELP_TEMPLATE
    assert "PARALLEL TOOL CALLS" in _TOOL_HELP_TEMPLATE
    # And the sequential-vs-parallel example survives string assembly
    assert "WRONG (sequential" in _TOOL_HELP_TEMPLATE
    assert "RIGHT (parallel" in _TOOL_HELP_TEMPLATE


def test_persona_has_search_and_multi_file_and_state_sections():
    from services.orchestrator import AUREM_CTO_PERSONA
    assert "SEARCH STRATEGY" in AUREM_CTO_PERSONA
    assert "PARALLEL READS — MANDATORY" in AUREM_CTO_PERSONA
    assert "MULTI-FILE TASK EXECUTION" in AUREM_CTO_PERSONA
    assert "TASK STATE TRACKING" in AUREM_CTO_PERSONA
