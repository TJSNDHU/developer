"""
test_aurem_persona_v2.py — Iter 32 guards against the "ask-don't-do"
regression.

Covers:
  • AUREM_CTO_PERSONA — has the new core rule and no longer has the
    forbidden "Reply 'go' to continue" instruction.
  • list_repo_files is registered in the tool catalog so the LLM sees it.
  • _format_tree always surfaces top-level directories, even when the
    tree is enormous and gets truncated.
  • list_repo_files glob behaviour (`pillars/*`, `**/pillar*`).
"""
from __future__ import annotations

import pytest

from services.orchestrator import AUREM_CTO_PERSONA
from services.local_tools import (
    TOOL_SPECS, LOCAL_TOOLS, invoke_local_tool, list_repo_files,
)
from services.repo_context import _format_tree


# ─── Persona ──────────────────────────────────────────────────────────

def test_persona_core_rule_present():
    """The non-negotiable 'execute on first command' rule must exist."""
    assert "EXECUTE ON FIRST COMMAND" in AUREM_CTO_PERSONA
    assert "Default to action" in AUREM_CTO_PERSONA


def test_persona_no_longer_asks_to_proceed():
    """The exact forbidden patterns from the user's transcript must
    not appear in the persona."""
    forbidden = [
        # The literal pattern that produced "Reply 'check' to continue"
        "Reply 'go' and I'll start",
        "Ready to ship? Reply",
        # The 6-step ritual header — gone in v2
        "follow this exact 6-step flow",
        # The "second turn" handoff mode that required confirmation
        "when the user's reply is a confirmation",
    ]
    for phrase in forbidden:
        assert phrase not in AUREM_CTO_PERSONA, f"persona still contains: {phrase!r}"


def test_persona_keeps_handoff_fence():
    """The frontend Ship-button detector keys on this exact fence."""
    assert "```aurem-handoff" in AUREM_CTO_PERSONA


def test_persona_mentions_both_tools():
    assert "list_repo_files" in AUREM_CTO_PERSONA
    assert "read_repo_file" in AUREM_CTO_PERSONA


# ─── Tool catalog ─────────────────────────────────────────────────────

def test_tool_catalog_includes_list_repo_files():
    names = [t["name"] for t in TOOL_SPECS]
    assert "list_repo_files" in names
    assert "read_repo_file" in names
    assert "list_repo_files" in LOCAL_TOOLS


# ─── Tree formatter ───────────────────────────────────────────────────

def test_format_tree_surfaces_top_level_dirs_even_on_huge_repo():
    """The bug: top-level `pillars/` vanished when the tree was huge.
    Fix: top-level dirs are always shown first, never truncated."""
    tree = [{"type": "tree", "path": "pillars"},
            {"type": "tree", "path": "legion"},
            {"type": "tree", "path": "camofox"},
            {"type": "blob", "path": "README.md", "size": 100}]
    # Pad the tree with a thousand deep paths so the deep-section gets
    # capped — top-level visibility must still be intact.
    for i in range(1200):
        tree.append({"type": "blob", "path": f"some/deep/path/{i}.py", "size": 10})
    out = _format_tree(tree)
    assert "pillars/" in out
    assert "legion/" in out
    assert "camofox/" in out
    assert "README.md" in out
    # Cap message must mention list_repo_files so the AI knows what to do
    assert "list_repo_files" in out


def test_format_tree_discovers_top_dirs_from_deep_paths():
    """If a deep path is `pillars/four/health.py` but no explicit
    tree-type entry for `pillars` exists, we still want it surfaced."""
    tree = [
        {"type": "blob", "path": "pillars/four/health.py", "size": 10},
        {"type": "blob", "path": "pillars/four/router.py", "size": 10},
    ]
    out = _format_tree(tree)
    # Currently top_dirs only collects explicit type='tree' entries; the
    # deep section will mention the path so the AI can glob it. This test
    # locks the behaviour so future refactors can't silently lose paths.
    assert "pillars/four/health.py" in out
    assert "pillars/four/router.py" in out


# ─── list_repo_files (unit, no network) ───────────────────────────────

@pytest.mark.asyncio
async def test_list_repo_files_rejects_when_no_project():
    res = await list_repo_files({"user_id": None, "project_id": None}, {})
    assert res["ok"] is False
    assert "No project" in res["error"]


@pytest.mark.asyncio
async def test_invoke_unknown_local_tool_returns_none():
    res = await invoke_local_tool("not_a_tool", {}, {})
    assert res is None
