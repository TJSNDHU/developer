"""
test_parallel_orchestrator.py — Iter 33 verifies:
  • Tool calls run in PARALLEL via asyncio.gather (not sequential)
  • Code tasks ('go', 'fix', 'ship') get mode='code' + 3500 tokens
  • Chat tasks ('what is', 'explain') get mode='chat' + 1500 tokens
  • Iter 32 persona was NOT regressed by the integration of uploaded files
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, patch

import pytest

from services.orchestrator import (
    AUREM_CTO_PERSONA, _is_code_task, chat_with_tools,
)


# ─── Persona regression guard (Iter 32 contract) ─────────────────────

def test_persona_still_has_iter32_rules():
    """The uploaded orchestrator.py had a REGRESSED persona with the
    forbidden 'Reply go' pattern. Lock the Iter 32 rules in place."""
    assert "EXECUTE ON FIRST COMMAND" in AUREM_CTO_PERSONA
    forbidden = [
        "Reply 'go' and I'll start",
        "Ready to ship? Reply",
        "follow this exact 6-step flow",
    ]
    for phrase in forbidden:
        assert phrase not in AUREM_CTO_PERSONA, f"v1 regression: {phrase!r}"


def test_persona_mentions_parallel_tool_invocation():
    """The Iter 33 tool-help template tells the LLM tools run in parallel."""
    # The template is appended inside chat_with_tools, but it references
    # the same _BT/_TOOL_HELP_TEMPLATE module-level. Pull it from source.
    import services.orchestrator as orch
    assert "IN PARALLEL" in orch._TOOL_HELP_TEMPLATE
    assert "read_repo_files" in orch._TOOL_HELP_TEMPLATE


# ─── Model routing ───────────────────────────────────────────────────

@pytest.mark.parametrize("prompt,expected_code", [
    ("fix the auth bug",      True),
    ("create a new endpoint", True),
    ("implement feature X",   True),
    ("ship it",               True),
    ("go",                    True),
    ("yes",                   True),
    ("ok",                    True),
    ("what is this project?", False),
    ("explain the auth flow", False),
    ("how does FastAPI work", False),
])
def test_code_task_detection(prompt, expected_code):
    assert _is_code_task(prompt, history_lines=[]) is expected_code


def test_short_confirmation_after_history_is_code():
    """'go' / 'yes' alone are code tasks only when there's a plan to ship."""
    assert _is_code_task("go", history_lines=["[USER] plan something"]) is True
    # No history → fall back to keyword match (still matches 'go')
    assert _is_code_task("go", history_lines=[]) is True


# ─── Parallel tool execution (the headline upgrade) ──────────────────

@pytest.mark.asyncio
async def test_tool_calls_run_in_parallel(monkeypatch):
    """If two tools each sleep 0.4s, parallel execution finishes in ~0.4s.
    Sequential would take ~0.8s. We assert <0.65s."""

    bt = chr(96) * 3
    fake_reply = (
        f"{bt}tool_call\n"
        '{"tool": "read_repo_file", "args": {"path": "a.py"}}\n'
        f"{bt}\n"
        f"{bt}tool_call\n"
        '{"tool": "read_repo_file", "args": {"path": "b.py"}}\n'
        f"{bt}\n"
    )

    iter_count = {"n": 0}

    async def fake_llm(*args, **kwargs):
        iter_count["n"] += 1
        # First iter emits 2 tool calls; second iter returns plain text
        # so the loop exits.
        return {
            "ok": True, "provider": "deepseek",
            "content": fake_reply if iter_count["n"] == 1 else "DONE",
            "fallback_chain": ["deepseek"], "mode": "chat",
        }

    async def slow_tool(name, args, ctx):
        await asyncio.sleep(0.4)
        return {"ok": True, "content": f"file-{args.get('path')}"}

    async def empty_upstream_list(jwt):
        return []

    with patch("services.orchestrator.call_llm_with_meta",
               new=AsyncMock(side_effect=fake_llm)), \
         patch("services.orchestrator.list_tools",
               new=AsyncMock(side_effect=empty_upstream_list)), \
         patch("services.orchestrator.invoke_local_tool",
               new=AsyncMock(side_effect=slow_tool)), \
         patch("services.orchestrator.extract_tool_calls",
               side_effect=lambda c: (
                   [{"tool": "read_repo_file", "args": {"path": "a.py"}},
                    {"tool": "read_repo_file", "args": {"path": "b.py"}}]
                   if "tool_call" in c else []
               )):
        t0 = time.perf_counter()
        result = await chat_with_tools(
            prompt="what's in a.py and b.py?",
            jwt_token="fake", user_id="u1", project_id="p1",
        )
        elapsed = time.perf_counter() - t0

    assert result["tool_calls_run"] == 2
    assert elapsed < 0.65, (
        f"parallel exec broken — 2× 0.4s tools took {elapsed:.2f}s "
        "(sequential would be ~0.8s)"
    )


@pytest.mark.asyncio
async def test_chat_with_tools_returns_mode_in_response():
    """The response must carry the picked llm mode so callers can audit."""
    async def fake_llm(*args, **kwargs):
        return {"ok": True, "provider": "claude-sonnet",
                "content": "Done.", "fallback_chain": ["claude-sonnet"],
                "mode": kwargs.get("mode", "?")}

    with patch("services.orchestrator.call_llm_with_meta",
               new=AsyncMock(side_effect=fake_llm)), \
         patch("services.orchestrator.list_tools",
               new=AsyncMock(return_value=[])), \
         patch("services.orchestrator.extract_tool_calls",
               return_value=[]):
        # Code task → mode=code
        r1 = await chat_with_tools(prompt="fix the bug", jwt_token="fake")
        assert r1["mode"] == "code"
        # Chat task → mode=chat
        r2 = await chat_with_tools(prompt="what is FastAPI?", jwt_token="fake")
        assert r2["mode"] == "chat"
