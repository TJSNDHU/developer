"""Iter 166 — verify EMERGENT_LLM_KEY + emergentintegrations SDK are
fully removed from services/llm.py and Claude routes via OpenRouter.

These tests are pure static / unit checks — no network."""
import ast
import inspect
import pathlib
from unittest.mock import AsyncMock, patch

import pytest

LLM_FILE = pathlib.Path(__file__).parent.parent / "services" / "llm.py"
SRC = LLM_FILE.read_text()


def test_no_emergentintegrations_import():
    tree = ast.parse(SRC)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mod = getattr(node, "module", None) or ""
            names = [a.name for a in node.names]
            assert "emergentintegrations" not in mod
            assert not any("emergentintegrations" in n for n in names)


def test_no_emergent_llm_key_reference():
    assert "EMERGENT_LLM_KEY" not in SRC
    assert "_emergent_key" not in SRC


def test_required_functions_still_exposed():
    from services import llm
    for fn in ("_call_claude", "_call_deepseek", "call_openrouter_model",
               "call_llm", "call_llm_with_meta", "call_emergent_watchdog"):
        assert hasattr(llm, fn), f"missing {fn}"
        assert callable(getattr(llm, fn))


@pytest.mark.asyncio
async def test_call_claude_routes_via_openrouter(monkeypatch):
    """_call_claude must delegate to call_openrouter_model with the
    Claude slug — NOT touch any Emergent SDK."""
    from services import llm

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    captured = {}

    async def fake_or(model, system, user, max_tokens=1000, temperature=0.2):
        captured["model"] = model
        captured["system"] = system
        captured["user"] = user
        return "claude-says-hi"

    with patch.object(llm, "call_openrouter_model", side_effect=fake_or):
        out = await llm._call_claude("sys", "usr", max_tokens=500, temperature=0.1)

    assert out == "claude-says-hi"
    assert captured["model"].startswith("anthropic/claude")
    assert captured["system"] == "sys"
    assert captured["user"] == "usr"


@pytest.mark.asyncio
async def test_call_claude_falls_back_when_no_openrouter_key(monkeypatch):
    from services import llm
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    async def fake_deepseek(messages, system, max_tokens, temperature):
        return "deepseek-fallback"

    with patch.object(llm, "_call_deepseek", side_effect=fake_deepseek):
        out = await llm._call_claude("sys", "usr")
    assert out == "deepseek-fallback"


@pytest.mark.asyncio
async def test_watchdog_uses_openrouter(monkeypatch):
    from services import llm
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    async def fake_or(model, system, user, max_tokens=1000, temperature=0.2):
        assert model.startswith("anthropic/claude")
        return "SCORE: 9\nISSUES: none\nVERDICT: looks good"

    with patch.object(llm, "call_openrouter_model", side_effect=fake_or):
        out = await llm.call_emergent_watchdog("some output to review")

    assert out["ok"] is True
    assert out["score"] == 9
    assert out["passed"] is True


@pytest.mark.asyncio
async def test_watchdog_errors_without_openrouter_key(monkeypatch):
    from services import llm
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    out = await llm.call_emergent_watchdog("text")
    assert out["ok"] is False
    assert "OPENROUTER_API_KEY" in (out.get("error") or "")


@pytest.mark.asyncio
async def test_call_llm_with_meta_code_mode_uses_claude(monkeypatch):
    from services import llm
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    async def fake_claude(system, user, max_tokens, temperature):
        return "claude-code-out"

    with patch.object(llm, "_call_claude", side_effect=fake_claude):
        meta = await llm.call_llm_with_meta("sys", "build me", mode="code")
    assert meta["ok"] is True
    assert meta["provider"] == "claude-sonnet-openrouter"
    assert meta["content"] == "claude-code-out"


@pytest.mark.asyncio
async def test_call_llm_with_meta_chat_mode_uses_deepseek(monkeypatch):
    from services import llm
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    async def fake_ds(messages, system, max_tokens, temperature):
        return "deepseek-chat-out"

    with patch.object(llm, "_call_deepseek", side_effect=fake_ds):
        meta = await llm.call_llm_with_meta("sys", "hi", mode="chat")
    assert meta["ok"] is True
    assert meta["provider"] == "deepseek"
    assert meta["content"] == "deepseek-chat-out"
