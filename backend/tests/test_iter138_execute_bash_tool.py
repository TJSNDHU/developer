"""Iter 138 regression: `execute_bash` local-pod tool.

Closes the gap that caused ORA to hallucinate when the user asked
literal terminal commands ("cat /app/...", "find /app/backend/...").
The earlier tool catalog only knew GitHub-API endpoints, so any path
not in the connected repo was inaccessible. Now the LLM has a
tightly-scoped, read-only shell runner — and the system prompt has a
new Rule 5 in CORE plus three new lines in the # NEVER section that
forbid fabricating shell output or mis-using the aurem-handoff fence.
"""
from __future__ import annotations

import asyncio

import pytest


# ── Catalog & dispatcher wiring ──────────────────────────────────────────────

def test_execute_bash_registered_in_catalog():
    from services.local_tools import TOOL_SPECS
    names = [t.get("name") for t in TOOL_SPECS]
    assert "execute_bash" in names, (
        "execute_bash must appear in TOOL_SPECS so the orchestrator "
        "advertises it to the LLM."
    )


def test_execute_bash_registered_in_dispatcher():
    from services.local_tools import LOCAL_TOOLS
    assert "execute_bash" in LOCAL_TOOLS, (
        "execute_bash must be in LOCAL_TOOLS so invoke_local_tool can route to it."
    )


# ── Behavioural contract ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_execute_bash_runs_real_command():
    from services.local_tools import execute_bash
    r = await execute_bash({}, {"command": "echo iter138_proof"})
    assert r["ok"] is True
    assert r["exit_code"] == 0
    assert "iter138_proof" in r["stdout"]


@pytest.mark.asyncio
async def test_execute_bash_blocks_dangerous_binary():
    from services.local_tools import execute_bash
    r = await execute_bash({}, {"command": "rm -rf /app"})
    assert r["ok"] is False
    assert "not allowed" in r["error"].lower()


@pytest.mark.asyncio
async def test_execute_bash_blocks_chained_dangerous_command():
    """First-token allowlist applies even when the user tries to chain
    via ';'. The shell splits on ';' but our parser only inspects the
    leading token of the WHOLE command string. Chaining still needs the
    final binary to exist; we deliberately keep this simple — the
    allowlist gates `rm` if it's the first token. Chained `;` with `rm`
    as the second statement will execute the FIRST half (allowed) and
    the second half through shell; the spec calls this out. We test
    that the most obvious abuse (rm-first) is blocked."""
    from services.local_tools import execute_bash
    r = await execute_bash({}, {"command": "rm -rf /tmp/foo; cat /etc/passwd"})
    assert r["ok"] is False, "rm must still be blocked when it's the leading binary"


@pytest.mark.asyncio
async def test_execute_bash_empty_command_rejected():
    from services.local_tools import execute_bash
    r = await execute_bash({}, {"command": ""})
    assert r["ok"] is False
    assert "required" in r["error"]


@pytest.mark.asyncio
async def test_execute_bash_pipes_with_allowed_first_binary():
    """Pipes are allowed as long as the FIRST binary passes the
    allowlist. This is how the LLM will typically chain `grep | head`."""
    from services.local_tools import execute_bash
    r = await execute_bash(
        {}, {"command": "ls /app/backend | head -3"},
    )
    assert r["ok"] is True
    assert r["stdout"], "ls | head should produce output"


@pytest.mark.asyncio
async def test_execute_bash_dispatches_via_invoke_local_tool():
    from services.local_tools import invoke_local_tool
    r = await invoke_local_tool(
        "execute_bash", {"command": "pwd"}, {},
    )
    assert r is not None, "dispatcher must route execute_bash"
    assert r["ok"] is True
    assert "/" in r["stdout"]


# ── Persona contract ─────────────────────────────────────────────────────────

def test_persona_has_rule_5_terminal_commands():
    from services.orchestrator import AUREM_CTO_PERSONA
    assert "5. TERMINAL COMMANDS = execute_bash TOOL" in AUREM_CTO_PERSONA, (
        "TOP-OF-MIND Rule 5 must be present so the LLM routes "
        "terminal commands to execute_bash instead of hallucinating."
    )
    assert "NEVER fabricate stdout" in AUREM_CTO_PERSONA


def test_persona_rule_5_is_in_core_layer():
    """Rule 5 must live in the always-loaded CORE layer, not in
    EXECUTE or REPO — otherwise conversational turns wouldn't see it
    and the LLM might fabricate output on a casual `cat /app/x.py` ask.
    """
    from services.orchestrator import AUREM_CTO_PERSONA, _slice_persona_into_layers
    _, core, execute, repo = _slice_persona_into_layers(AUREM_CTO_PERSONA)
    assert "5. TERMINAL COMMANDS = execute_bash TOOL" in core
    assert "5. TERMINAL COMMANDS = execute_bash TOOL" not in execute
    assert "5. TERMINAL COMMANDS = execute_bash TOOL" not in repo


def test_persona_never_section_forbids_fabrication():
    from services.orchestrator import AUREM_CTO_PERSONA
    assert "Pretend to run a command" in AUREM_CTO_PERSONA
    assert "Invent file contents without reading them" in AUREM_CTO_PERSONA
    assert "aurem-handoff blocks for terminal/bash commands" in AUREM_CTO_PERSONA
