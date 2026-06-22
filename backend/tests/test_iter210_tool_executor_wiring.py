"""
Iter 210 — Live tool-executor signal propagation test.

Proves that when a real tool (`read_repo_file`) raises an HTTP-shaped
exception, `invoke_local_tool` routes it through ToolExecutor, captures
the typed signal into `ctx["system_signals"]`, and returns a neutral
`llm_facing` string to the LLM (R3 of the ORA system prompt).

Run with:
    cd /app/backend && python -m pytest tests/test_iter210_tool_executor_wiring.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class _GitHubError(Exception):
    """Mimics httpx.HTTPStatusError shape."""
    def __init__(self, status_code: int, msg: str):
        super().__init__(msg)
        self.status_code = status_code


@pytest.mark.asyncio
async def test_invoke_local_tool_emits_github_auth_failed_signal():
    """Proof 1: a 401 raised inside the tool is captured as a
    structured signal, never as a raw error string in the LLM payload."""
    from services import local_tools

    async def fake_tool(ctx, args):
        raise _GitHubError(401, "Bad credentials")

    ctx: dict = {"user_id": "u1", "project_id": "p1"}
    with patch.dict(local_tools.LOCAL_TOOLS,
                     {"read_repo_file": fake_tool}, clear=False):
        out = await local_tools.invoke_local_tool(
            "read_repo_file",
            {"path": "backend/server.py"},
            ctx,
        )

    # LLM-facing payload — neutral, no raw error text
    assert out["ok"] is False
    assert out["error"] == "Tool read_repo_file could not complete."
    assert out["system_signal"] == "github_auth_failed"
    assert "Bad credentials" not in out["error"]  # raw msg never reaches LLM

    # ctx captured the structured signal for the SSE final-frame
    assert "system_signals" in ctx
    assert len(ctx["system_signals"]) == 1
    sig = ctx["system_signals"][0]
    assert sig["signal"]      == "github_auth_failed"
    assert sig["severity"]    == "error"
    assert sig["tool"]        == "read_repo_file"
    assert sig["http_status"] == "401"

    # ctx also recorded the tool call (used by CitationGuard)
    assert ctx["tool_calls"] == [{"tool": "read_repo_file",
                                  "args": {"path": "backend/server.py"}}]


@pytest.mark.asyncio
async def test_invoke_local_tool_passes_through_clean_success():
    """Proof 2: a clean tool result is returned untouched, no signals."""
    from services import local_tools

    async def fake_tool(ctx, args):
        return {"ok": True, "content": "real file body"}

    ctx: dict = {"user_id": "u1", "project_id": "p1"}
    with patch.dict(local_tools.LOCAL_TOOLS,
                     {"read_repo_file": fake_tool}, clear=False):
        out = await local_tools.invoke_local_tool(
            "read_repo_file",
            {"path": "backend/server.py"},
            ctx,
        )

    assert out == {"ok": True, "content": "real file body"}
    assert ctx.get("system_signals", []) == []
    assert len(ctx["tool_calls"]) == 1


@pytest.mark.asyncio
async def test_invoke_local_tool_handles_404_and_403_distinctly():
    """Proof 3: each HTTP status maps to the correct signal name."""
    from services import local_tools

    cases = [
        (403, "github_permission_denied"),
        (404, "repo_not_found"),
        (429, "github_rate_limited"),
    ]
    for status, expected in cases:
        async def fake_tool(ctx, args, _s=status):
            raise _GitHubError(_s, f"err {_s}")
        ctx: dict = {"user_id": "u", "project_id": "p"}
        with patch.dict(local_tools.LOCAL_TOOLS,
                         {"read_repo_file": fake_tool}, clear=False):
            out = await local_tools.invoke_local_tool("read_repo_file", {}, ctx)
        assert out["system_signal"] == expected, f"status {status}"
        assert ctx["system_signals"][0]["signal"] == expected


@pytest.mark.asyncio
async def test_audit_log_record_signature():
    """Proof 4: audit_log accepts the canonical field set the
    orchestrator now passes from the SSE final-frame path."""
    from services import audit_log

    # No DB attached in this test context → record_turn returns None,
    # but the call itself must succeed (signature contract).
    result = await audit_log.record_turn(
        user_id="u1",
        project_id="p1",
        tools_called=["read_repo_file:backend/server.py"],
        citation_guard_triggered=True,
        citation_guard_paths_fetched=["backend/server.py"],
        citation_guard_unverified=["backend/server.py"],
        system_signals_emitted=["github_auth_failed"],
        llm_model="deepseek",
        response_tokens=42,
        was_retry=True,
    )
    # If DB is wired, returns turn_id (str). If not, returns None.
    # Either way, must NOT raise.
    assert result is None or isinstance(result, str)
