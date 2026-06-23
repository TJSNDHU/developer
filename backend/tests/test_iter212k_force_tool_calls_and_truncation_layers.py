"""
test_iter212k_force_tool_calls_and_truncation_layers.py

Iter 212k — Two-part production fix:

PROBLEM 1 — ORA was skipping tool calls and answering from memory for
conversational prompts like "admin.py" or "how many routes in chat.py".

  • Added _READ_VERB_RX (read|show|list|cat|open|view|grep|dump|print|fetch
    at message start) → forces EXECUTE when paired with a connected repo.
  • Added _HOW_MANY_RX ("how many <X>") → forces EXECUTE so ORA grounds
    the answer in search_repo / list_repo_files instead of guessing.
  • Added TOOL CALL ENFORCEMENT block to AUREM_CTO_PERSONA telling the
    model to call a tool first for ANY repo question.

PROBLEM 2 — Tool result truncation cascades:

  • orchestrator per-tool budget bumped 8000 → 12000 (Iter 212k).
  • search_repo: per-file hit cap 5 → 50 (was the actual root cause of
    "admin.py has 30 routes but ORA sees 5"; line cap 120 → 280; global
    cap `max_files*5` → flat 500.
  • _slice_content truncation marker now includes TOTAL char count so
    ORA can intelligently request a narrower `lines=[start,end]` slice
    instead of looping.
  • Same total-chars footer on the orchestrator's JSON-envelope cap.
"""
from __future__ import annotations

from pathlib import Path

import pytest


ORCH  = Path("/app/backend/services/orchestrator.py").read_text(encoding="utf-8")
TOOLS = Path("/app/backend/services/local_tools.py").read_text(encoding="utf-8")


# ── PROBLEM 1: _wants_execute new triggers ────────────────────────

@pytest.mark.parametrize("prompt, repo, expected", [
    # New Iter 212k triggers ──
    ("read backend/routers/admin.py", True,  True),
    ("show src/App.jsx",              True,  True),
    ("list backend/services/",        True,  True),
    ("dump .env",                     True,  True),
    ("how many routes in admin.py",   True,  True),
    ("how many tests do I have",      True,  True),
    # Bare file path keeps working (Iter 212h baseline) ──
    ("admin.py",                       True,  True),
    ("backend/routers/chat.py",        True,  True),
    # Negative — without a connected repo these stay conversational
    ("read admin.py",                 False,  False),
    ("how many users do you have",    False,  False),
    # Greetings unchanged ──
    ("hi",                             True,  False),
    ("how are you",                    True,  False),
])
def test_wants_execute_iter212k_triggers(prompt, repo, expected):
    from services.orchestrator import _wants_execute
    assert _wants_execute(prompt, repo, []) is expected, (
        f"_wants_execute({prompt!r}, repo={repo}) expected {expected}"
    )


def test_aurem_persona_includes_tool_enforcement_block():
    """The system prompt must include explicit enforcement language so
    the model stops hallucinating from memory on repo questions."""
    assert "TOOL CALL ENFORCEMENT" in ORCH
    # The block must mention the canonical tool names so the model
    # knows what to call.
    assert "read_repo_file" in ORCH
    assert "search_repo" in ORCH
    assert "list_repo_files" in ORCH
    # The directive must include "must call ... FIRST" semantic.
    assert "MUST call" in ORCH
    assert "FIRST" in ORCH


# ── PROBLEM 2: truncation layer caps ──────────────────────────────

def test_orchestrator_per_tool_budget_is_12000():
    assert "if _total > 12000:" in ORCH
    assert "result_str[:12000]" in ORCH
    # Defensive — older limits must not linger in active code.
    code_lines = [ln for ln in ORCH.splitlines() if not ln.lstrip().startswith("#")]
    code = "\n".join(code_lines)
    assert "len(result_str) > 2500" not in code
    assert "len(result_str) > 8000" not in code, (
        "Iter 212k bumped to 12000 — make sure no stale 8000 path remains."
    )


def test_orchestrator_truncation_marker_includes_total_chars():
    """When the orchestrator truncates a tool result, the marker must
    expose the TOTAL char count so ORA knows how much was cut."""
    # The new format string mentions both `total chars` and `first 12000`.
    assert "total chars, showing" in ORCH
    assert "first 12000" in ORCH


def test_search_repo_per_file_hit_cap_is_50():
    """search_repo must return up to 50 hits per file (was 5), so a
    file like admin.py with 30 @router decorators surfaces them all."""
    assert "len(hits) >= 50:" in TOOLS
    # The old 5-cap must NOT be present in active code.
    code_lines = [ln for ln in TOOLS.splitlines() if not ln.lstrip().startswith("#")]
    code = "\n".join(code_lines)
    assert "len(hits) >= 5:" not in code, (
        "Old search_repo per-file cap of 5 still lurks — replace with 50."
    )


def test_search_repo_line_snippet_280_chars():
    """Per-line snippets in search_repo results should be up to 280
    chars (was 120) so route decorators with comments don't get cut."""
    assert 'line.strip()[:280]' in TOOLS
    code_lines = [ln for ln in TOOLS.splitlines() if not ln.lstrip().startswith("#")]
    code = "\n".join(code_lines)
    assert 'line.strip()[:120]' not in code


def test_search_repo_global_cap_is_500():
    """Global match cap is a flat 500 (was `max_files * 5` which gave
    just 100 for the default max_files=20)."""
    assert "matches[:500]" in TOOLS


def test_max_file_chars_still_15000_or_higher():
    """read_repo_file content cap floor — must remain >= 15k."""
    from services.local_tools import MAX_FILE_CHARS
    assert MAX_FILE_CHARS >= 15_000, f"got {MAX_FILE_CHARS}"


def test_slice_content_marker_includes_total_chars():
    """When read_repo_file/read_repo_files truncates, the marker tells
    ORA the original size + offers the lines= escape hatch."""
    from services.local_tools import _slice_content
    s = "x" * 25_000
    out, trunc = _slice_content(s, None, 15_000)
    assert trunc is True
    assert "25000 total chars" in out
    assert "showing first 15000" in out
    assert "lines=[start,end]" in out


# ── End-to-end search_repo behaviour ──────────────────────────────

def test_search_repo_returns_more_than_five_router_decorators(monkeypatch):
    """Synthetic smoke test — feed search_repo a fake file with 30
    @router lines and ensure it returns ALL 30 (not just 5)."""
    import asyncio

    fake_file = "\n".join([f"@router.get('/r{i}')" for i in range(30)])

    from services import local_tools

    async def _fake_fetch_file(*_args, **_kw):
        return fake_file

    async def _fake_resolve_project(*_args, **_kw):
        return {"github_owner": "x", "github_repo": "y", "branch": "main",
                "github_token": "ghp_fake"}

    class _FakeResp:
        def raise_for_status(self): pass
        def json(self):
            return {"truncated": False,
                    "tree": [{"path": "admin.py", "type": "blob"}]}

    class _FakeClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def get(self, *_a, **_kw): return _FakeResp()

    monkeypatch.setattr(local_tools, "_gh_fetch_file", _fake_fetch_file)
    monkeypatch.setattr(local_tools, "_resolve_project", _fake_resolve_project)
    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kw: _FakeClient())

    out = asyncio.run(local_tools.search_repo(
        ctx={"user_id": "u1", "project_id": "p1"},
        args={"pattern": "@router", "path": "", "ext": ".py", "max": 50},
    ))
    assert out["ok"] is True
    # All 30 @router lines must surface (per-file cap is 50, global 500).
    assert out["total_matches"] >= 30, (
        f"search_repo only returned {out['total_matches']} hits — "
        f"per-file cap is still throttling."
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
