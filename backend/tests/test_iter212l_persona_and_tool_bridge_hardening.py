"""
test_iter212l_persona_and_tool_bridge_hardening.py

Iter 212l — Five fixes against subtle ORA failure modes:

  1. `read_repo_files` no longer silently drops paths beyond the
     MAX_FILES_BULK cap. Returns `requested`, `dropped`, and a loud
     `warning` field so the LLM stops assuming it read all 8 files.

  2. Removed Shape 5 NL extraction from `tools_bridge.extract_tool_calls`.
     It parsed phrases like "let me read backend/auth.py" from the
     LLM's own prose as phantom tool calls — typically firing in the
     model's FINAL answer after it already had the data.

  3. Narrowed `_STRONG_EXECUTE_RX`: bare `run` removed; replaced with
     `run\\s+(test|build|server|script|command|npm|pip|node|yarn|make|…)`
     so "how does X run?" stays conversational.

  4. INVENTORY MODE in `AUREM_CTO_PERSONA` clarifies that "10 files
     in one turn" means 10 SEPARATE `read_repo_file` blocks, NOT
     `read_repo_files` with 10 paths (which is hard-capped at 6).

  5. `_TOOL_HELP_TEMPLATE` drops the stale "(23 total)" claim and
     surfaces the read_repo_files hard cap inline.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest


ORCH    = Path("/app/backend/services/orchestrator.py").read_text(encoding="utf-8")
TOOLS   = Path("/app/backend/services/local_tools.py").read_text(encoding="utf-8")
BRIDGE  = Path("/app/backend/services/tools_bridge.py").read_text(encoding="utf-8")


# ── Fix 1: read_repo_files dropped-paths warning ─────────────────

@pytest.mark.asyncio
async def test_read_repo_files_surfaces_dropped_paths():
    from services import local_tools
    paths = [f"f{i}.py" for i in range(9)]  # 9 paths, cap is 6

    async def _fake_resolve(*_a, **_k):
        return {"github_owner": "x", "github_repo": "y",
                "branch": "main", "github_token": "gh"}

    async def _fake_fetch(*_a, **_k):
        return "ok"

    with patch.object(local_tools, "_resolve_project",
                      new=AsyncMock(side_effect=_fake_resolve)), \
         patch.object(local_tools, "_gh_fetch_file",
                      new=AsyncMock(side_effect=_fake_fetch)):
        out = await local_tools.read_repo_files(
            ctx={"user_id": "u", "project_id": "p"},
            args={"paths": paths},
        )

    assert out["requested"] == 9
    assert out["fetched"]   == 6
    assert out["dropped"]   == ["f6.py", "f7.py", "f8.py"]
    assert "HARD-CAPS at 6" in out["warning"]
    assert "f6.py" in out["warning"]
    assert "read_repo_file" in out["warning"]


@pytest.mark.asyncio
async def test_read_repo_files_no_warning_when_under_cap():
    from services import local_tools

    async def _fake_resolve(*_a, **_k):
        return {"github_owner": "x", "github_repo": "y",
                "branch": "main", "github_token": "gh"}

    async def _fake_fetch(*_a, **_k):
        return "ok"

    with patch.object(local_tools, "_resolve_project",
                      new=AsyncMock(side_effect=_fake_resolve)), \
         patch.object(local_tools, "_gh_fetch_file",
                      new=AsyncMock(side_effect=_fake_fetch)):
        out = await local_tools.read_repo_files(
            ctx={"user_id": "u", "project_id": "p"},
            args={"paths": ["a.py", "b.py", "c.py"]},
        )

    assert out["fetched"] == 3
    assert out["dropped"] == []
    # No dropped-paths warning when under cap.
    assert "HARD-CAPS at 6" not in (out.get("warning") or "")


# ── Fix 2: Shape 5 NL extraction removed ─────────────────────────

def test_no_phantom_tool_call_from_natural_language():
    """Prose like 'Let me read X.py' must NOT produce a phantom
    tool call — extract_tool_calls returns [] when only NL is
    present."""
    from services.tools_bridge import extract_tool_calls
    assert extract_tool_calls("Let me read backend/routers/auth.py to check it.") == []
    assert extract_tool_calls("I'll fetch frontend/src/App.jsx now.") == []
    assert extract_tool_calls("Checking backend/services/llm.py.") == []
    assert extract_tool_calls("Searching for 'TODO' across the repo.") == []


def test_real_fenced_tool_call_still_extracted():
    """Don't accidentally break the legitimate fenced-JSON path."""
    from services.tools_bridge import extract_tool_calls
    txt = (
        "Going to look at auth.\n\n"
        "```tool_call\n"
        '{"tool":"read_repo_file","args":{"path":"backend/auth.py"}}\n'
        "```\n"
    )
    calls = extract_tool_calls(txt)
    assert len(calls) == 1
    assert calls[0]["tool"] == "read_repo_file"
    assert calls[0]["args"]["path"] == "backend/auth.py"


def test_shape5_block_was_removed_from_source():
    """Defensive: confirm the Shape 5 block / NL_PATTERNS table is
    physically removed from tools_bridge so it can't be silently
    re-enabled by a merge."""
    assert "Shape 5" not in BRIDGE or "REMOVED" in BRIDGE
    assert "_NL_PATTERNS" not in BRIDGE


# ── Fix 3: _STRONG_EXECUTE_RX narrowed ──────────────────────────

@pytest.mark.parametrize("prompt, expected", [
    # Conversational — must NOT trigger strong execute
    ("how does auth flow run",            False),
    ("where does this run from",          False),
    ("what does run mean here",           False),
    # Real run-something requests — must still trigger
    ("run tests on auth.py",              True),
    ("run npm install",                   True),
    ("run python setup.py",               True),
    ("run the build",                     True),
])
def test_strong_execute_run_keyword_narrowed(prompt, expected):
    from services.orchestrator import _STRONG_EXECUTE_RX
    assert bool(_STRONG_EXECUTE_RX.search(prompt)) is expected, (
        f"{prompt!r} expected strong-execute = {expected}"
    )


# ── Fix 4: persona INVENTORY MODE clarified ──────────────────────

def test_inventory_mode_distinguishes_singular_vs_bulk_tool():
    """The INVENTORY MODE bullet must point the model at separate
    `read_repo_file` blocks (parallelised by orchestrator) rather
    than the 6-cap `read_repo_files`."""
    # Either string is fine — must mention SEPARATE blocks AND the
    # 6-cap warning on the plural tool.
    assert "SEPARATE `read_repo_file` blocks" in ORCH
    assert "HARD-CAPS at 6" in ORCH


# ── Fix 5: TOOL_HELP_TEMPLATE updates ────────────────────────────

def test_tool_help_template_drops_hardcoded_count():
    """The '(23 total)' marketing claim is removed so the catalog
    doesn't go stale when tools are added or removed."""
    from services.orchestrator import _TOOL_HELP_TEMPLATE
    assert "23 total" not in _TOOL_HELP_TEMPLATE


def test_tool_help_template_surfaces_read_repo_files_cap():
    """The catalog entry for read_repo_files must mention its hard
    cap and the workaround (separate read_repo_file blocks)."""
    from services.orchestrator import _TOOL_HELP_TEMPLATE
    assert "HARD CAP at 6" in _TOOL_HELP_TEMPLATE
    assert "drops everything past the 6th" in _TOOL_HELP_TEMPLATE


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
