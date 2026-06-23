"""
test_iter212g_orchestrator_local_ctx_and_openrouter_model.py

Iter 212g — Two production deployment crashes, fixed in one pass:

  1) `UnboundLocalError: cannot access local variable 'local_ctx'`
     in services/orchestrator.py:chat_with_tools. `local_ctx` was
     referenced on the no-tool-call return path (~line 1476) but only
     initialised inside the tool-execution branch (~line 1487). When
     the LLM returned a final answer on the first iteration (very
     common — chat replies, short questions, etc.), the function
     crashed before returning.

  2) `OpenRouter HTTP 400 Bad Request` on every Claude call because we
     were sending `anthropic/claude-sonnet-4-5-20250929` (the
     Anthropic-native model ID), but OpenRouter expects the dotted
     version: `anthropic/claude-sonnet-4.5`.
"""
from __future__ import annotations

import inspect
from pathlib import Path


# ── 1) local_ctx is initialised BEFORE both return paths ──────────

ORCH_SRC = Path("/app/backend/services/orchestrator.py").read_text(encoding="utf-8")


def test_local_ctx_initialised_before_function_body():
    """The first assignment to `local_ctx` must happen near the top of
    `chat_with_tools`, NOT inside the tool-execution block."""
    func_start = ORCH_SRC.index("async def chat_with_tools(")
    # Find the first `local_ctx =` (or `local_ctx:`) assignment.
    first_assign = ORCH_SRC.find("local_ctx", func_start)
    # The first occurrence must be an assignment, not a `.get(...)` read.
    snippet = ORCH_SRC[first_assign : first_assign + 60]
    assert "local_ctx" in snippet
    # It must NOT be the `.get(...)` read on the early-return path.
    assert ".get(" not in snippet, (
        "First reference to local_ctx in chat_with_tools must be an "
        "ASSIGNMENT, not a `.get(...)` read on the early-return path. "
        "Hoist it to function entry."
    )


def test_local_ctx_seeded_with_required_keys():
    """`local_ctx` dict must always have system_signals + tool_calls
    keys so the no-tool-call branch can safely read them."""
    assert '"system_signals": []' in ORCH_SRC
    assert '"tool_calls":    [],' in ORCH_SRC or '"tool_calls": []' in ORCH_SRC


def test_chat_with_tools_handles_no_tool_calls_path():
    """Symbolic smoke check — the function source between its declaration
    and the early return reads local_ctx safely. This was the exact
    crash path in production."""
    func_start = ORCH_SRC.index("async def chat_with_tools(")
    # The early return that reads local_ctx
    return_idx = ORCH_SRC.index('"system_signals": local_ctx.get(', func_start)
    # Between func_start and return_idx, there MUST be an assignment.
    between = ORCH_SRC[func_start:return_idx]
    assert "local_ctx" in between
    assert "system_signals" in between, (
        "local_ctx must be initialised with system_signals before the "
        "early-return path reads from it."
    )


# ── 2) OpenRouter model IDs are dotted, not dash-date ─────────────

def test_llm_default_claude_model_uses_openrouter_format():
    """services/llm.py default model must be the dotted OpenRouter ID,
    not the dash-date Anthropic native ID (which returns 400 from
    OpenRouter)."""
    src = Path("/app/backend/services/llm.py").read_text(encoding="utf-8")
    # The default assignment in `os.getenv("CLAUDE_MODEL", "...")`.
    assert '"CLAUDE_MODEL", "anthropic/claude-sonnet-4.5"' in src, (
        "Default Claude model must be OpenRouter's dotted ID "
        "(anthropic/claude-sonnet-4.5). The dash-date format "
        "anthropic/claude-sonnet-4-5-20250929 returns HTTP 400 from "
        "OpenRouter."
    )


def test_smart_router_uses_openrouter_format():
    src = Path("/app/backend/services/smart_router.py").read_text(encoding="utf-8")
    # Both maxx_code and security defaults use the dotted ID.
    assert '"MAXX_CODE",    "anthropic/claude-sonnet-4.5"' in src
    assert '"SECURITY",     "anthropic/claude-sonnet-4.5"' in src


def test_vanguard_verify_uses_openrouter_format():
    src = Path("/app/backend/services/vanguard_verify_agent.py").read_text(encoding="utf-8")
    assert '"anthropic/claude-sonnet-4.5"' in src


def test_no_remaining_dashdate_anthropic_ids_in_runtime_code():
    """No production code path may still default to the dash-date
    format. Comments / docstrings / tests are exempt."""
    bad = "anthropic/claude-sonnet-4-5-20250929"
    runtime_files = [
        "/app/backend/services/llm.py",
        "/app/backend/services/smart_router.py",
        "/app/backend/services/vanguard_verify_agent.py",
    ]
    leaks = []
    for fp in runtime_files:
        src = Path(fp).read_text(encoding="utf-8")
        # Strip out comment lines and docstrings before scanning.
        code = []
        in_doc = False
        for line in src.splitlines():
            s = line.strip()
            if s.startswith('"""') or s.startswith("'''"):
                in_doc = not in_doc
                continue
            if in_doc:
                continue
            if s.startswith("#"):
                continue
            code.append(line)
        joined = "\n".join(code)
        if bad in joined:
            leaks.append(fp)
    assert not leaks, (
        f"Dash-date Anthropic model ID still present in runtime code "
        f"of {leaks} — OpenRouter rejects it with 400 Bad Request."
    )


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
