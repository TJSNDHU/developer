"""Iter 212m-12 — error_translator hybrid pipeline.

Locks the contract that the friendly task-failure translator
returns a complete `{plain, steps, suggestion, source, technical}`
dict for every input, never raises, and routes correctly between
the static catalog and the LLM fallback.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import error_translator  # noqa: E402


# ── Contract: never raises, always full shape ─────────────────────


@pytest.mark.asyncio
async def test_translate_empty_returns_generic():
    out = await error_translator.translate("")
    assert out["technical"] == ""
    assert out["source"] == "empty"
    assert out["plain"]
    assert isinstance(out["steps"], list) and len(out["steps"]) >= 1
    assert out["suggestion"]


@pytest.mark.asyncio
async def test_translate_none_returns_generic():
    out = await error_translator.translate(None)
    assert out["technical"] == ""
    assert out["source"] == "empty"


# ── Static catalog matches ────────────────────────────────────────


@pytest.mark.asyncio
async def test_translate_no_pat_matches_static_rule():
    out = await error_translator.translate("No PAT on project — open Edit and add one")
    assert out["source"] == "static_table"
    assert "GitHub access token" in out["plain"] or "PAT" in out["plain"]
    assert any("Projects" in s for s in out["steps"])


@pytest.mark.asyncio
async def test_translate_invalid_token_matches_static_rule():
    out = await error_translator.translate("invalid_token (401 bad credentials)")
    assert out["source"] == "static_table"
    assert "PAT" in out["plain"] or "token" in out["plain"].lower()


@pytest.mark.asyncio
async def test_translate_rate_limit_matches_static_rule():
    out = await error_translator.translate("OpenRouter HTTP 429 too many requests")
    assert out["source"] == "static_table"
    assert "throttle" in out["plain"].lower() or "load" in out["plain"].lower()


@pytest.mark.asyncio
async def test_translate_merge_conflict_matches_static_rule():
    out = await error_translator.translate("409 Conflict on push to main (fast-forward rejected)")
    assert out["source"] == "static_table"
    assert "branch" in out["plain"].lower() or "commit" in out["plain"].lower()


@pytest.mark.asyncio
async def test_translate_timeout_matches_static_rule():
    out = await error_translator.translate("httpx.ConnectTimeout after 30s")
    assert out["source"] == "static_table"


@pytest.mark.asyncio
async def test_translate_vault_unavailable_matches_static_rule():
    out = await error_translator.translate("vault_unavailable: AUREM_CTO_MASTER_KEY not set")
    assert out["source"] == "static_table"
    assert "admin" in out["plain"].lower() or "encryption" in out["plain"].lower()


@pytest.mark.asyncio
async def test_translate_tokens_exhausted_matches_static_rule():
    out = await error_translator.translate("token_exhausted: monthly task quota reached")
    assert out["source"] == "static_table"
    assert "quota" in out["plain"].lower() or "task" in out["plain"].lower()


# ── LLM fallback path ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_translate_unknown_uses_llm_fallback():
    fake_llm = AsyncMock(return_value={
        "content": '{"plain": "Code mein syntax error tha line 42 pe.", '
                   '"steps": ["File open karo", "Line 42 check karo"], '
                   '"suggestion": "Lint pehle se chala lo."}',
    })
    with patch.dict("os.environ", {"EMERGENT_LLM_KEY": "sk-test-fake"}), \
         patch.object(error_translator, "_static_match", return_value=None), \
         patch("services.llm.call_llm_with_meta", fake_llm):
        out = await error_translator.translate("UnknownInternalAssertionError at codegen.py:118")
    assert out["source"] == "llm_rewrite"
    assert "syntax" in out["plain"]
    assert len(out["steps"]) == 2
    assert "Lint" in out["suggestion"]


@pytest.mark.asyncio
async def test_translate_unknown_falls_back_to_generic_when_no_llm_key():
    with patch.dict("os.environ", {}, clear=True), \
         patch.object(error_translator, "_static_match", return_value=None):
        out = await error_translator.translate("UnknownDeepInternalError xyz")
    assert out["source"] == "generic"
    assert out["technical"] == "UnknownDeepInternalError xyz"


@pytest.mark.asyncio
async def test_translate_llm_returning_garbage_falls_back_to_generic():
    fake_llm = AsyncMock(return_value={"content": "not json, just prose"})
    with patch.dict("os.environ", {"EMERGENT_LLM_KEY": "sk-test-fake"}), \
         patch.object(error_translator, "_static_match", return_value=None), \
         patch("services.llm.call_llm_with_meta", fake_llm):
        out = await error_translator.translate("xyz_unknown_failure_mode")
    assert out["source"] == "generic"


@pytest.mark.asyncio
async def test_translate_llm_raising_exception_falls_back_to_generic():
    fake_llm = AsyncMock(side_effect=RuntimeError("openrouter down"))
    with patch.dict("os.environ", {"EMERGENT_LLM_KEY": "sk-test-fake"}), \
         patch.object(error_translator, "_static_match", return_value=None), \
         patch("services.llm.call_llm_with_meta", fake_llm):
        out = await error_translator.translate("xyz_unknown_failure_mode")
    # Translator must NEVER raise — generic fallback.
    assert out["source"] == "generic"
    assert out["plain"]


# ── Shape guarantees ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_translate_truncates_overly_long_technical_error():
    huge = "BoomError " * 200  # ~2000 chars
    out = await error_translator.translate(huge)
    assert len(out["technical"]) <= 500


@pytest.mark.asyncio
async def test_translate_static_always_has_step_list_and_suggestion():
    for raw in [
        "No PAT on project",
        "429 rate_limit hit",
        "repo_not_found 404",
        "vault_unavailable",
        "timeout exceeded",
    ]:
        out = await error_translator.translate(raw)
        assert isinstance(out["steps"], list)
        assert len(out["steps"]) >= 2
        assert out["suggestion"]
