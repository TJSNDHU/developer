"""
Iter 209 — core architecture proof tests.

Covers:
  Test 1 — Citation guard blocks hallucination (no repo read = retry path).
  Test 2 — Tool error router maps a 401 to `github_auth_failed` signal.
  Test 3 — Clean response (real read_repo_file call) passes through.
  Bonus — TOOL_ERROR_MAP covers the canonical status codes.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.citation_guard import CitationGuard, _extract_claims, _read_paths_this_turn
from services.tool_executor   import TOOL_ERROR_MAP, collect_signals, execute


# ───────────────────────────────────────────────────────────────────
# CitationGuard
# ───────────────────────────────────────────────────────────────────
def test_extract_claims_picks_up_file_paths_and_versions():
    text = (
        "Backend lives in `backend/server.py` and "
        "uses Anthropic 0.84.0 (see backend/requirements.txt). "
        "Total of 142 packages."
    )
    claims = _extract_claims(text)
    assert "backend/server.py" in claims["paths"]
    assert "backend/requirements.txt" in claims["paths"]
    assert "0.84.0" in claims["versions"]
    assert any("142" in c for c in claims["counts"])


def test_read_paths_this_turn_handles_both_tool_names():
    tool_calls = [
        {"tool": "read_repo_file",  "args": {"path": "backend/server.py"}},
        {"tool": "read_repo_files", "args": {"paths": ["a.py", "b.py"]}},
        {"tool": "search_repo",     "args": {"query": "foo"}},
    ]
    assert _read_paths_this_turn(tool_calls) == {"backend/server.py", "a.py", "b.py"}


def test_verify_passes_when_all_paths_were_read():
    g = CitationGuard()
    text = "I checked `backend/server.py` and it looks fine."
    tool_calls = [{"tool": "read_repo_file", "args": {"path": "backend/server.py"}}]
    report = g.verify(text, tool_calls)
    assert report["pass"] is True
    assert report["unverified_paths"] == []


def test_verify_fails_when_paths_are_unsourced():
    """Test 1 — guard catches a hallucinated README scenario."""
    g = CitationGuard()
    text = (
        "Backend (`backend/server.py`) uses FastAPI 0.110.0.\n"
        "See `backend/requirements.txt` for the 142 packages."
    )
    report = g.verify(text, tool_calls=[])
    assert report["pass"] is False
    assert "backend/server.py"       in report["unverified_paths"]
    assert "backend/requirements.txt" in report["unverified_paths"]


@pytest.mark.asyncio
async def test_enforce_auto_fetches_and_retries_when_unverified():
    """Test 1 (continued) — auto-fetch + LLM retry path."""
    g = CitationGuard()

    # Fake `read_repo_file` returns specific content for each path.
    fake_rrf = AsyncMock(side_effect=lambda ctx, args: {
        "ok": True,
        "content": f"# real contents of {args['path']}",
    })

    captured = {}

    async def fake_llm(*, original_messages=None, additional_context=None, instruction=None):
        captured["additional_context"] = additional_context
        captured["instruction"]        = instruction
        return "REWRITTEN: only the verified content was used."

    draft = "Backend lives in `backend/server.py`. See `pkg/foo.py`."
    out = await g.enforce(
        response_text=draft,
        tool_calls=[],
        ctx={"user_id": "u", "project_id": "p"},
        llm_caller=fake_llm,
        read_repo_file=fake_rrf,
    )
    assert out["retried"] is True
    assert "backend/server.py" in out["fetched"]
    assert "pkg/foo.py"        in out["fetched"]
    assert out["text"].startswith("REWRITTEN:")
    # The retry prompt MUST contain the real file contents.
    assert "real contents of backend/server.py" in captured["additional_context"]


@pytest.mark.asyncio
async def test_enforce_passes_clean_response_through_untouched():
    """Test 3 — clean response gets no retry."""
    g = CitationGuard()
    fake_llm = AsyncMock()
    fake_rrf = AsyncMock()

    draft = "I checked backend/server.py and it boots cleanly."
    tool_calls = [{"tool": "read_repo_file", "args": {"path": "backend/server.py"}}]
    out = await g.enforce(
        response_text=draft,
        tool_calls=tool_calls,
        ctx={},
        llm_caller=fake_llm,
        read_repo_file=fake_rrf,
    )
    assert out["retried"] is False
    assert out["text"] == draft
    fake_llm.assert_not_called()
    fake_rrf.assert_not_called()


# ───────────────────────────────────────────────────────────────────
# ToolExecutor
# ───────────────────────────────────────────────────────────────────
class _StubGitHubError(Exception):
    def __init__(self, status_code: int, msg: str):
        super().__init__(msg)
        self.status_code = status_code


@pytest.mark.asyncio
async def test_executor_returns_ok_on_success():
    async def runner():
        return {"path": "x.py", "content": "hi"}
    out = await execute("read_repo_file", runner)
    assert out["ok"] is True
    assert out["data"]["content"] == "hi"
    assert out["tool"] == "read_repo_file"


@pytest.mark.asyncio
async def test_executor_maps_401_to_github_auth_failed():
    """Test 2 — 401 from GitHub becomes a typed signal, not a string."""
    async def runner():
        raise _StubGitHubError(401, "Bad credentials")
    out = await execute("read_repo_file", runner)
    assert out["ok"] is False
    assert out["system_signal"] == "github_auth_failed"
    assert out["severity"]      == "error"
    assert out["http_status"]   == "401"
    # LLM must only ever see the neutral string:
    assert out["llm_facing"] == "Tool read_repo_file could not complete."
    # Raw error message stays out of the LLM context but is logged in
    # the structured payload for the audit log:
    assert "Bad credentials" in out["error_message"]


@pytest.mark.asyncio
async def test_executor_maps_403_404_and_rate_limit():
    async def r403(): raise _StubGitHubError(403, "Permission denied")
    async def r404(): raise _StubGitHubError(404, "Not Found")
    async def r429(): raise _StubGitHubError(429, "rate limit")

    a = await execute("t", r403)
    b = await execute("t", r404)
    c = await execute("t", r429)
    assert a["system_signal"] == "github_permission_denied"
    assert b["system_signal"] == "repo_not_found"
    assert c["system_signal"] == "github_rate_limited"


@pytest.mark.asyncio
async def test_collect_signals_dedupes_and_drops_successes():
    async def r401(): raise _StubGitHubError(401, "Bad credentials")
    async def rok():  return {"x": 1}
    results = [
        await execute("read_repo_file",  r401),
        await execute("read_repo_files", r401),
        await execute("search_repo",     rok),
    ]
    signals = collect_signals(results)
    # Only failures show up
    assert all(s["signal"] for s in signals)
    assert len(signals) == 2
    assert signals[0]["signal"] == "github_auth_failed"


def test_tool_error_map_covers_required_status_codes():
    """The map MUST cover the codes the user spec'd in Iter 209."""
    for code in (401, 403, 404, 422, 500):
        assert code in TOOL_ERROR_MAP, f"Missing mapping for {code}"
