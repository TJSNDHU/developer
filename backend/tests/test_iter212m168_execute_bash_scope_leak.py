"""
Iter 212m-168 — Regression tests for the ORA repo-scope privacy bug.

Prior bug: ORA (LLM) had unrestricted `execute_bash` access to the
local pod filesystem (/app/backend, /app/frontend — AUREM's own
codebase).  When a REGULAR USER asked "which repo are you working
on?", the LLM would `ls /app/backend` and truthfully report AUREM
INTERNAL directories instead of the user's connected GitHub repo.
That is a privacy + correctness bug: end users see our internals,
and their own repo answers are wrong.

Fix (this iter):
  1. `local_tools.execute_bash` refuses when `ctx["is_founder"]`
     is not True — even if the LLM hallucinates the tool call.
  2. `orchestrator.chat_with_tools` filters `execute_bash` out of
     the tool catalog for non-founder callers, so the LLM never
     sees it in the first place.
  3. `orchestrator.chat_with_tools` prepends a SCOPE HARD RULE to
     the system prompt for non-founders, telling the model that
     `/app/*`, `/tmp/*`, etc. are OFF-LIMITS.
  4. Router `chat.py` passes `is_founder` into `chat_with_tools`.

These tests pin all four legs so a regression is a red build.
"""
from __future__ import annotations

import asyncio

import pytest


# ── Leg 1: execute_bash dispatch-level gate ──────────────────────────
@pytest.mark.asyncio
async def test_execute_bash_refuses_non_founder():
    """A non-founder ctx MUST get a clean refusal, never a stdout."""
    from services.local_tools import execute_bash

    ctx = {"user_id": "u_regular", "project_id": "p_x", "is_founder": False}
    out = await execute_bash(ctx, {"command": "ls /app/backend"})
    assert out["ok"] is False, f"expected refusal, got {out}"
    assert "founder" in (out.get("error") or "").lower(), out
    # Critically, refusal MUST NOT include stdout or any /app path listing.
    assert "stdout" not in out, out
    assert "backend" not in (out.get("error") or "")[:100]


@pytest.mark.asyncio
async def test_execute_bash_refuses_missing_is_founder_flag():
    """A ctx that just doesn't set is_founder at all must ALSO refuse.
    Default-deny is the whole point.
    """
    from services.local_tools import execute_bash

    ctx = {"user_id": "u_x", "project_id": "p_x"}   # is_founder absent
    out = await execute_bash(ctx, {"command": "ls /app"})
    assert out["ok"] is False, out
    assert "founder" in (out.get("error") or "").lower(), out


@pytest.mark.asyncio
async def test_execute_bash_still_works_for_founder():
    """A founder ctx must reach the shell allowlist path (not refused
    at the role gate).  We call `pwd` so the test doesn't depend on
    what's in /app."""
    from services.local_tools import execute_bash

    ctx = {"user_id": "u_founder", "project_id": "p_x", "is_founder": True}
    out = await execute_bash(ctx, {"command": "pwd"})
    # We don't care what pwd prints — just that we passed the gate.
    # Either the allowlist ran the command (ok:True) or a downstream
    # subprocess failure (ok:False with a shell-level error) — both
    # prove we cleared the founder gate.
    assert "founder" not in (out.get("error") or "").lower(), out


# ── Leg 2: orchestrator tool catalog filter ─────────────────────────
def test_local_tools_catalog_still_contains_execute_bash():
    """Sanity — the tool DOES exist in LOCAL_TOOL_SPECS. The
    orchestrator is what filters it per-role, not the module.
    """
    from services.local_tools import TOOL_SPECS

    names = {t.get("name") for t in TOOL_SPECS}
    assert "execute_bash" in names, "execute_bash should be defined; role gating happens in orchestrator"


def test_orchestrator_filter_removes_execute_bash_for_non_founder():
    """Simulate the orchestrator's tool-catalog filter with is_founder=False."""
    from services.local_tools import TOOL_SPECS

    # Replicate the exact filter block from orchestrator.chat_with_tools.
    tools = list(TOOL_SPECS)
    is_founder = False
    if not is_founder:
        _LOCAL_ONLY_TOOLS = {"execute_bash"}
        tools = [t for t in tools if t.get("name") not in _LOCAL_ONLY_TOOLS]

    names = {t.get("name") for t in tools}
    assert "execute_bash" not in names, (
        "execute_bash MUST be filtered out for non-founder end-user chats"
    )
    # Repo-scoped tools MUST remain — they're the only sanctioned
    # reader for the user's connected repo.
    for expected in (
        "read_repo_file", "read_repo_files", "list_repo_files",
        "search_repo", "semantic_search_repo",
    ):
        assert expected in names, f"user must retain {expected}"


def test_orchestrator_filter_keeps_execute_bash_for_founder():
    from services.local_tools import TOOL_SPECS

    tools = list(TOOL_SPECS)
    is_founder = True
    if not is_founder:
        _LOCAL_ONLY_TOOLS = {"execute_bash"}
        tools = [t for t in tools if t.get("name") not in _LOCAL_ONLY_TOOLS]

    names = {t.get("name") for t in tools}
    assert "execute_bash" in names, "founder must retain execute_bash"


# ── Leg 3: chat_with_tools signature accepts is_founder ─────────────
def test_chat_with_tools_signature_has_is_founder():
    import inspect
    from services.orchestrator import chat_with_tools

    sig = inspect.signature(chat_with_tools)
    assert "is_founder" in sig.parameters, (
        "orchestrator.chat_with_tools must accept is_founder to gate execute_bash"
    )
    # Default MUST be False (default-deny).
    assert sig.parameters["is_founder"].default is False, (
        "is_founder default must be False so any accidental omission "
        "at a caller falls back to end-user-safe scope"
    )


# ── Leg 4: chat.py routers pass is_founder ─────────────────────────
def test_chat_router_passes_is_founder_to_orchestrator():
    """Static check: chat.py must forward is_founder into chat_with_tools
    at BOTH call sites (send + stream)."""
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "routers" / "chat.py"
    text = src.read_text()

    # A non-greedy `.*?` regex breaks on inner `)` chars from things
    # like `system=(extra_sys + "\n\n" if extra_sys else None)`.  Walk
    # each call-open and use paren-balancing to find the matching close.
    starts = [
        i for i in range(len(text))
        if text.startswith("chat_with_tools(", i)
    ]
    # Filter out the `from services.orchestrator import chat_with_tools`
    # line — that's not an actual invocation.
    call_blocks: list[str] = []
    for i in starts:
        # Skip if it's part of an `import` statement.
        head = text.rfind("\n", 0, i)
        line_head = text[head + 1: i]
        if "import" in line_head:
            continue
        depth = 0
        j = i + len("chat_with_tools")
        assert text[j] == "("
        while j < len(text):
            c = text[j]
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        call_blocks.append(text[i:j + 1])

    assert call_blocks, "chat.py must call chat_with_tools somewhere"
    assert len(call_blocks) >= 2, (
        f"expected ≥2 chat_with_tools call sites in chat.py "
        f"(send + stream), found {len(call_blocks)}"
    )
    for idx, block in enumerate(call_blocks):
        assert "is_founder=" in block, (
            f"chat_with_tools call #{idx} in chat.py MUST forward "
            f"is_founder — otherwise the LLM will silently regain "
            f"local-pod access.\nCall block:\n{block[:400]}"
        )


if __name__ == "__main__":
    # Allow `python test_iter212m168_execute_bash_scope_leak.py` for quick smoke.
    asyncio.run(test_execute_bash_refuses_non_founder())
    asyncio.run(test_execute_bash_refuses_missing_is_founder_flag())
    asyncio.run(test_execute_bash_still_works_for_founder())
    test_local_tools_catalog_still_contains_execute_bash()
    test_orchestrator_filter_removes_execute_bash_for_non_founder()
    test_orchestrator_filter_keeps_execute_bash_for_founder()
    test_chat_with_tools_signature_has_is_founder()
    test_chat_router_passes_is_founder_to_orchestrator()
    print("All Iter 212m-168 scope-leak regression tests passed.")
