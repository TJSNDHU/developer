"""Iter 212m-6 — Full tool wiring audit. Locks invariants so a tool added to
LOCAL_TOOLS but missed in tools_bridge / orchestrator / TOOL_SPECS is caught
at CI time, not in production by a hallucinated tool call.
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.local_tools import LOCAL_TOOLS, TOOL_SPECS, invoke_local_tool
from services.orchestrator import _WRITE_TOOL_NAMES, _WEB_TOOLS
from services.web_skills import WEB_TOOLS, WEB_TOOL_SPECS
from services.dev_skills import DEV_TOOLS, DEV_TOOL_SPECS
from services.tools_bridge import extract_tool_calls


# ──────────────────────────────────────────────────────────────────
# Catalog ↔ dispatch table integrity
# ──────────────────────────────────────────────────────────────────


def test_every_local_tool_has_a_spec():
    spec_names = {s["name"] for s in TOOL_SPECS}
    missing = [n for n in LOCAL_TOOLS if n not in spec_names]
    assert not missing, f"handlers without TOOL_SPECS entry: {missing}"


def test_every_spec_has_a_handler():
    missing = [s["name"] for s in TOOL_SPECS if s["name"] not in LOCAL_TOOLS]
    assert not missing, f"TOOL_SPECS entries without a handler: {missing}"


def test_all_specs_have_description_and_args():
    for s in TOOL_SPECS:
        assert s.get("description"), f"{s.get('name')} has no description"
        assert isinstance(s.get("args_spec"), dict), \
            f"{s.get('name')} has no/bad args_spec"


def test_every_handler_is_async_with_ctx_args():
    bad = []
    for name, fn in LOCAL_TOOLS.items():
        if not inspect.iscoroutinefunction(fn):
            bad.append((name, "not coroutine"))
            continue
        params = list(inspect.signature(fn).parameters)
        if params != ["ctx", "args"]:
            bad.append((name, f"params={params}"))
    assert not bad, f"handlers with wrong signature: {bad}"


# ──────────────────────────────────────────────────────────────────
# Web tools / dev tools sub-registries
# ──────────────────────────────────────────────────────────────────


def test_web_tools_fully_registered():
    for name in WEB_TOOLS:
        assert name in LOCAL_TOOLS, f"web tool {name} missing from LOCAL_TOOLS"
    for s in WEB_TOOL_SPECS:
        assert s["name"] in WEB_TOOLS, f"WEB_TOOL_SPECS {s['name']} no handler"


def test_dev_tools_fully_registered():
    for name in DEV_TOOLS:
        assert name in LOCAL_TOOLS, f"dev tool {name} missing from LOCAL_TOOLS"
    for s in DEV_TOOL_SPECS:
        assert s["name"] in DEV_TOOLS, f"DEV_TOOL_SPECS {s['name']} no handler"


# ──────────────────────────────────────────────────────────────────
# Orchestrator hot paths
# ──────────────────────────────────────────────────────────────────


def test_orchestrator_web_tools_set_is_accurate():
    """`_WEB_TOOLS` in orchestrator is used to extract citation
    sources. It must exactly match the actual WEB_TOOLS dict keys."""
    assert _WEB_TOOLS == set(WEB_TOOLS.keys()), \
        f"orchestrator _WEB_TOOLS drift: {_WEB_TOOLS} ≠ {set(WEB_TOOLS)}"


def test_write_repo_file_is_in_write_tool_names():
    """Post-edit build hook only fires for names in _WRITE_TOOL_NAMES."""
    assert "write_repo_file" in _WRITE_TOOL_NAMES


def test_write_repo_file_in_python_repl_known_tools():
    """tools_bridge._KNOWN_TOOLS gate for Python-style tool emissions
    (DeepSeek REPL-mimic fallback). A missing entry here silently
    drops the call. Verify via the public parser — emit a Python-style
    call and ensure it's recognised."""
    calls = extract_tool_calls(
        "I'll fix this: write_repo_file(path='src/util.py', content='print(1)')"
    )
    assert calls, "Python-style write_repo_file emission was not recognised"
    assert calls[0]["tool"] == "write_repo_file"


def test_known_python_repl_tools_covers_local_tools():
    """The Python-REPL gate set in tools_bridge MUST cover every
    handler in LOCAL_TOOLS. If a new tool is added but not whitelisted
    here, DeepSeek's bare-call emissions get silently dropped."""
    from services import tools_bridge
    # extract_tool_calls inlines _KNOWN_TOOLS; check it via a probe
    # call covering each LOCAL_TOOLS entry.
    missing: list[str] = []
    for name in LOCAL_TOOLS:
        calls = extract_tool_calls(f"using {name}()")
        if not any(c["tool"] == name for c in calls):
            missing.append(name)
    assert not missing, (
        "Python-REPL recognition missing for: " + ", ".join(missing) +
        " — add them to tools_bridge.extract_tool_calls._KNOWN_TOOLS."
    )


# ──────────────────────────────────────────────────────────────────
# Dispatch
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_invoke_local_tool_returns_none_for_unknown():
    """Unknown tool MUST return None so the orchestrator falls through
    to the upstream `invoke_tool` (FastAPI bridge). Without this contract
    the LLM gets a typed error instead of the upstream fallback."""
    res = await invoke_local_tool("definitely_not_a_real_tool", {}, {})
    assert res is None


@pytest.mark.asyncio
async def test_write_repo_file_dispatches_through_invoke_local_tool():
    """End-to-end dispatch: orchestrator → invoke_local_tool → write_repo_file
    returns a typed envelope (here without a project, so should error out
    cleanly with `ok=False`)."""
    res = await invoke_local_tool(
        "write_repo_file",
        {"path": "test.py", "content": "x = 1"},
        {"user_id": "u_test", "project_id": "home"},
    )
    # Either tool_executor wraps it as failure envelope OR the bare
    # return surfaces ok=False with error. Both shapes are acceptable.
    assert res is not None
    assert res.get("ok") is False
    # Error message must reference the actual cause (no project).
    err = (res.get("error") or "").lower()
    assert "project" in err or "home" in err or "no project" in err
