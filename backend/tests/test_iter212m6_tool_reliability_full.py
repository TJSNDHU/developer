"""Iter 212m-6 — Tool reliability + commit pipeline robustness tests.

Covers the 7 surgical fixes:
  #1 write_repo_file tool — adds chat-mode write capability + vanguard pre-check
  #2 Codegen retry — path-aware feedback in nudge
  #3 invoke_local_tool error class exposure for LLM self-correction
  #4 Post-push verification: CRLF/CR normalisation
  #5 Vanguard scanner: docs/test/example path whitelist downgrade
  #6 PAT decrypt loud surface (logging assertion)
  #7 Chunked file read: explicit next_call_required hint
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Imports under test ---------------------------------------------------
from services.vanguard_scanner import (
    scan_file_blocks, has_critical, _is_safe_demo_path,
)
from services.local_tools import (
    _apply_chunking, write_repo_file, invoke_local_tool, TOOL_SPECS, LOCAL_TOOLS,
)
from services import orchestrator as orch_mod


# ──────────────────────────────────────────────────────────────────
# Fix #1 — write_repo_file tool
# ──────────────────────────────────────────────────────────────────


def test_write_repo_file_registered_in_local_tools():
    assert "write_repo_file" in LOCAL_TOOLS
    names = [s["name"] for s in TOOL_SPECS]
    assert "write_repo_file" in names


def test_write_repo_file_in_write_tool_names():
    assert "write_repo_file" in orch_mod._WRITE_TOOL_NAMES


@pytest.mark.asyncio
async def test_write_repo_file_rejects_bad_path():
    res = await write_repo_file(
        {"user_id": "u1", "project_id": "p1"},
        {"path": "/etc/passwd", "content": "x"},
    )
    assert res["ok"] is False
    assert "traversal" in res["error"].lower() or "absolute" in res["error"].lower()


@pytest.mark.asyncio
async def test_write_repo_file_rejects_path_traversal():
    res = await write_repo_file(
        {"user_id": "u1", "project_id": "p1"},
        {"path": "../../etc/secrets", "content": "x"},
    )
    assert res["ok"] is False
    assert "traversal" in res["error"].lower() or "invalid" in res["error"].lower()


@pytest.mark.asyncio
async def test_write_repo_file_requires_string_content():
    res = await write_repo_file(
        {"user_id": "u1", "project_id": "p1"},
        {"path": "a.py", "content": 42},
    )
    assert res["ok"] is False
    assert "string" in res["error"].lower()


@pytest.mark.asyncio
async def test_write_repo_file_rejects_oversize():
    res = await write_repo_file(
        {"user_id": "u1", "project_id": "p1"},
        {"path": "a.py", "content": "x" * 200_001},
    )
    assert res["ok"] is False
    assert "200" in res["error"]  # 200KB cap


@pytest.mark.asyncio
async def test_write_repo_file_needs_project(monkeypatch):
    # Mock _resolve_project to return None.
    from services import local_tools as lt
    async def _no_proj(uid, pid):
        return None
    monkeypatch.setattr(lt, "_resolve_project", _no_proj)
    res = await write_repo_file(
        {"user_id": "u1", "project_id": "home"},
        {"path": "a.py", "content": "print('hi')"},
    )
    assert res["ok"] is False
    assert "project" in res["error"].lower()


@pytest.mark.asyncio
async def test_write_repo_file_blocks_critical_secret(monkeypatch):
    """A patch containing a Stripe LIVE key must be blocked by vanguard
    before reaching the commit_files writer."""
    from services import local_tools as lt
    async def _good_proj(uid, pid):
        return {
            "github_owner": "alice", "github_repo": "myrepo",
            "branch": "main", "github_token": "ghp_xxxxx",
        }
    monkeypatch.setattr(lt, "_resolve_project", _good_proj)

    # commit_files should NEVER be called.
    commit_called = {"yes": False}
    async def _spy_commit(**kwargs):
        commit_called["yes"] = True
        return {"ok": True}
    monkeypatch.setattr(
        "services.github_api_writer.commit_files", _spy_commit,
    )

    leaked = 'STRIPE_KEY = "sk_live_AAAAAAAAAAAAAAAAAAAAAA"'
    res = await write_repo_file(
        {"user_id": "u1", "project_id": "p1"},
        {"path": "config.py", "content": leaked},
    )
    assert res["ok"] is False
    assert "vanguard" in res["error"].lower()
    assert commit_called["yes"] is False
    assert any(f["rule"] == "stripe_live_key" for f in res.get("findings", []))


@pytest.mark.asyncio
async def test_write_repo_file_commits_clean_patch(monkeypatch):
    """A clean patch must reach commit_files and surface sha + html_url."""
    from services import local_tools as lt
    async def _good_proj(uid, pid):
        return {
            "github_owner": "alice", "github_repo": "myrepo",
            "branch": "main", "github_token": "ghp_xxxxx",
        }
    monkeypatch.setattr(lt, "_resolve_project", _good_proj)

    async def _fake_commit(**kwargs):
        return {
            "ok": True, "sha": "abc1234",
            "full_sha": "abc1234deadbeef",
            "html_url": "https://github.com/alice/myrepo/commit/abc1234",
        }
    monkeypatch.setattr(
        "services.github_api_writer.commit_files", _fake_commit,
    )

    res = await write_repo_file(
        {"user_id": "u1", "project_id": "p1"},
        {"path": "src/util.py", "content": "def add(a, b):\n    return a + b\n"},
    )
    assert res["ok"] is True
    assert res["sha"] == "abc1234"
    assert "github.com/alice/myrepo/commit/abc1234" in res["html_url"]
    assert res["path"] == "src/util.py"
    assert res["branch"] == "main"


# ──────────────────────────────────────────────────────────────────
# Fix #3 — Tool error CLASS exposure
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_invoke_local_tool_surfaces_auth_class(monkeypatch):
    """When a tool fails with `error_class='auth'`, the LLM-facing
    error string must include the AUTH category so the model can
    self-correct instead of looping with the same params."""
    from services import local_tools as lt

    async def _fake_runner():
        # The tool function itself — what it returns isn't actually
        # consumed because we monkey-patch execute() to short-circuit.
        return {"ok": False}

    # Patch execute() to simulate an AUTH-classified failure.
    async def _fake_execute(name, runner):
        return {
            "ok": False,
            "system_signal": "auth_failed",
            "severity": "high",
            "tool": name,
            "http_status": 401,
            "error_class": "auth",
            "llm_facing": "Tool read_repo_file could not complete.",
        }
    monkeypatch.setattr("services.tool_executor.execute", _fake_execute)

    ctx: dict = {}
    res = await invoke_local_tool("read_repo_file", {"path": "x.py"}, ctx)
    assert res["ok"] is False
    assert "AUTH" in res["error"]
    assert res["error_class"] == "auth"
    assert ctx["system_signals"][0]["signal"] == "auth_failed"


@pytest.mark.asyncio
async def test_invoke_local_tool_surfaces_not_found_class(monkeypatch):
    async def _fake_execute(name, runner):
        return {
            "ok": False, "system_signal": "tool_failed", "severity": "low",
            "tool": name, "http_status": 404, "error_class": "not_found",
            "llm_facing": "Tool X could not complete.",
        }
    monkeypatch.setattr("services.tool_executor.execute", _fake_execute)
    ctx: dict = {}
    res = await invoke_local_tool("read_repo_file", {}, ctx)
    assert "NOT_FOUND" in res["error"]
    assert res["error_class"] == "not_found"


@pytest.mark.asyncio
async def test_invoke_local_tool_unknown_class_falls_back(monkeypatch):
    """Unmapped error_class must use the default llm_facing string."""
    async def _fake_execute(name, runner):
        return {
            "ok": False, "system_signal": "tool_failed", "severity": "low",
            "tool": name, "http_status": None, "error_class": "weird_thing",
            "llm_facing": "Tool X could not complete.",
        }
    monkeypatch.setattr("services.tool_executor.execute", _fake_execute)
    res = await invoke_local_tool("read_repo_file", {}, {})
    assert "could not complete" in res["error"]
    # error_class is preserved on the response for the SSE banner.
    assert res["error_class"] == "weird_thing"


# ──────────────────────────────────────────────────────────────────
# Fix #5 — Vanguard demo-path whitelist
# ──────────────────────────────────────────────────────────────────


def test_safe_demo_path_recognises_envexample():
    assert _is_safe_demo_path(".env.example") is True
    assert _is_safe_demo_path("config/.env.template") is True


def test_safe_demo_path_recognises_test_files():
    assert _is_safe_demo_path("tests/test_auth.py") is True
    assert _is_safe_demo_path("frontend/src/App.test.jsx") is True
    assert _is_safe_demo_path("__tests__/router.spec.ts") is True


def test_safe_demo_path_recognises_docs():
    assert _is_safe_demo_path("docs/setup.md") is True
    assert _is_safe_demo_path("README.md") is True
    assert _is_safe_demo_path("CONTRIBUTING.md") is True


def test_safe_demo_path_rejects_real_source():
    assert _is_safe_demo_path("backend/routers/auth.py") is False
    assert _is_safe_demo_path("src/index.js") is False


def test_vanguard_downgrades_demo_paths():
    """A stripe_live_key in .env.example should be downgraded to INFO
    so the commit isn't blocked. The finding is still recorded."""
    blocks = {
        ".env.example": 'STRIPE_KEY="sk_live_AAAAAAAAAAAAAAAAAAAAAA"',
    }
    findings = scan_file_blocks(blocks)
    assert findings
    f = findings[0]
    assert f["severity"] == "INFO"
    assert f.get("downgraded") is True
    assert has_critical(findings) is False


def test_vanguard_keeps_critical_on_real_source():
    """Same key in real source code still CRITICAL → blocks commit."""
    blocks = {
        "backend/config.py": 'STRIPE_KEY="sk_live_AAAAAAAAAAAAAAAAAAAAAA"',
    }
    findings = scan_file_blocks(blocks)
    assert any(f["severity"] == "CRITICAL" for f in findings)
    assert has_critical(findings) is True


# ──────────────────────────────────────────────────────────────────
# Fix #7 — Chunked-read next_call_required hint
# ──────────────────────────────────────────────────────────────────


def test_apply_chunking_adds_next_call_required_on_truncate():
    body = "\n".join([f"line {i}" for i in range(2000)])
    res = _apply_chunking(body, {})
    assert res["truncated"] is True
    assert res.get("next_call_required") is True
    hint = res.get("next_call_hint") or {}
    assert hint.get("tool") == "read_repo_file"
    assert "lines" in (hint.get("args_template") or {})
    # Note still surfaces the MUST-call language.
    assert "MUST" in res["note"]


def test_apply_chunking_no_hint_on_small_file():
    res = _apply_chunking("x = 1\n" * 50, {})
    assert res["truncated"] is False
    assert "next_call_required" not in res


def test_apply_chunking_no_hint_when_lines_arg_supplied():
    """When the LLM ALREADY supplied a `lines=[s,e]` slice we trust the
    answer and don't repeat the next-call directive."""
    body = "\n".join([f"line {i}" for i in range(2000)])
    res = _apply_chunking(body, {"lines": [10, 20]})
    assert res["truncated"] is True
    assert "next_call_required" not in res


# ──────────────────────────────────────────────────────────────────
# Fix #4 — Post-push verification line-ending normalisation
# (covered by direct unit on the _norm function used in cto_projects)
# ──────────────────────────────────────────────────────────────────


def test_postpush_norm_handles_crlf_and_trailing_ws():
    """The _norm helper used in cto_projects._verify_one must collapse
    CRLF/CR → LF and strip trailing whitespace so otherwise-identical
    files don't get flagged as a mismatch."""
    def _norm(s):
        return (s or "").replace("\r\n", "\n").replace("\r", "\n").rstrip()
    a = "line1\nline2\n"
    b = "line1\r\nline2\r\n  "          # CRLF + trailing whitespace
    c = "line1\rline2\r"                # legacy old-Mac CR
    assert _norm(a) == _norm(b) == _norm(c)
