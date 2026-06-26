"""Iter 212m-49 — Groq as TRUE last-resort fallback.

These tests mock httpx so we can deterministically force the
OpenRouter chain into 402/429/5xx and prove that Groq is invoked
as the final emergency net. No real network calls in CI.

Spec under test (verbatim from founder, 2026-02-27):
  P0 — Groq as final fallback after OR primary + OR free tier
  P1 — Same chain wired into the streaming code path
       (chat.py uses fake-streaming through `_call_deepseek`, so
       both paths share `_call_deepseek` and `call_openrouter_model`
       — proving one proves the other)
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest


def _setup_env(monkeypatch) -> None:
    """Force a known fallback chain so the tests are deterministic."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-or-key")
    monkeypatch.setenv("GROQ_API_KEY", "test-groq-key")
    monkeypatch.setenv("OPENROUTER_FREE_MODELS", "free/a:free,free/b:free")


def _mk_402_response() -> httpx.Response:
    req = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    return httpx.Response(402, request=req, json={"error": {"message": "credits exhausted"}})


def _mk_429_response() -> httpx.Response:
    req = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    return httpx.Response(429, request=req, json={"error": {"message": "rate limited"}})


def _mk_200_or_response(content: str = "from-openrouter") -> httpx.Response:
    req = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    return httpx.Response(
        200, request=req,
        json={"choices": [{"message": {"content": content}}]},
    )


# ───────────────────────────────────────────────────────────────────
#  Helper that builds a fake AsyncClient whose POST returns a
#  scripted sequence of responses (one per call).
# ───────────────────────────────────────────────────────────────────
class _ScriptedClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    async def post(self, *args, **kwargs):
        if self.calls >= len(self._responses):
            raise RuntimeError(
                f"scripted httpx ran out of responses after {self.calls} calls"
            )
        r = self._responses[self.calls]
        self.calls += 1
        if isinstance(r, Exception):
            raise r
        # Raise-for-status semantics: client code calls r.raise_for_status()
        return r

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def _client_factory(responses):
    """Returns a callable that mimics `httpx.AsyncClient(timeout=…)`."""
    def _factory(*args, **kwargs):
        return _ScriptedClient(responses)
    return _factory


# ───────────────────────────────────────────────────────────────────
#  TESTS
# ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_groq_helper_calls_async_sdk_with_versatile_model(monkeypatch) -> None:
    """_call_groq must invoke groq.AsyncGroq, NOT the sync SDK, and
    use the spec-pinned `llama-3.3-70b-versatile` model by default."""
    _setup_env(monkeypatch)
    # Re-import so the new env is picked up.
    import importlib
    import services.llm as llm_mod
    importlib.reload(llm_mod)

    fake_completion = MagicMock()
    fake_completion.choices = [MagicMock(message=MagicMock(content="hello from groq"))]
    fake_client = MagicMock()
    fake_client.chat.completions.create = AsyncMock(return_value=fake_completion)

    with patch("groq.AsyncGroq", return_value=fake_client) as ctor:
        out = await llm_mod._call_groq(
            messages=[{"role": "user", "content": "ping"}],
            system="be brief",
            max_tokens=50,
            temperature=0.2,
        )
    assert out == "hello from groq"
    ctor.assert_called_once()
    # Model must match the spec.
    called_kwargs = fake_client.chat.completions.create.await_args.kwargs
    assert called_kwargs["model"] == "llama-3.3-70b-versatile"
    # System prompt must START with the house rules (iter 212m-50)
    # and the caller-supplied system text must follow after a
    # markdown separator.
    sys_msg = called_kwargs["messages"][0]
    assert sys_msg["role"] == "system"
    assert "Groq Model House Rules — Aurem CTO" in sys_msg["content"]
    assert "You are ORA" in sys_msg["content"]
    assert "be brief" in sys_msg["content"]  # caller system preserved


@pytest.mark.asyncio
async def test_groq_house_rules_silent_skip_when_file_missing(monkeypatch, tmp_path) -> None:
    """If groq_house_rules.md is missing, _call_groq must still work
    — silent skip, no crash, no error log at ERROR level."""
    _setup_env(monkeypatch)
    import importlib
    import services.llm as llm_mod
    importlib.reload(llm_mod)
    # Point the loader at a path that doesn't exist.
    missing = str(tmp_path / "nope.md")
    monkeypatch.setattr(llm_mod, "_GROQ_HOUSE_RULES_PATH", missing)
    # Clear the module-level cache so the next call re-reads.
    if hasattr(llm_mod._load_groq_house_rules, "_cached"):
        delattr(llm_mod._load_groq_house_rules, "_cached")

    fake_completion = MagicMock()
    fake_completion.choices = [MagicMock(message=MagicMock(content="ok"))]
    fake_client = MagicMock()
    fake_client.chat.completions.create = AsyncMock(return_value=fake_completion)
    with patch("groq.AsyncGroq", return_value=fake_client):
        out = await llm_mod._call_groq(
            messages=[{"role": "user", "content": "ping"}],
            system="my-system",
        )
    assert out == "ok"
    # System message must be ONLY the caller's text (no rules prepended).
    sys_msg = fake_client.chat.completions.create.await_args.kwargs["messages"][0]
    assert sys_msg["content"] == "my-system"


@pytest.mark.asyncio
async def test_groq_house_rules_applied_when_no_caller_system(monkeypatch) -> None:
    """When the caller passes system="", the house rules alone must
    become the system message — Groq must still get the rules so it
    behaves as ORA even on rules-only turns."""
    _setup_env(monkeypatch)
    import importlib
    import services.llm as llm_mod
    importlib.reload(llm_mod)
    fake_completion = MagicMock()
    fake_completion.choices = [MagicMock(message=MagicMock(content="ok"))]
    fake_client = MagicMock()
    fake_client.chat.completions.create = AsyncMock(return_value=fake_completion)
    with patch("groq.AsyncGroq", return_value=fake_client):
        out = await llm_mod._call_groq(
            messages=[{"role": "user", "content": "ping"}],
            system="",
        )
    assert out == "ok"
    msgs = fake_client.chat.completions.create.await_args.kwargs["messages"]
    assert msgs[0]["role"] == "system"
    assert "Groq Model House Rules" in msgs[0]["content"]


@pytest.mark.asyncio
async def test_groq_helper_raises_when_key_missing(monkeypatch) -> None:
    """Per spec, Groq is the LAST link — if its key is missing we
    raise loudly so the caller can decide. Silent "" returns would
    hide the fact that the chain is dead."""
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    import importlib
    import services.llm as llm_mod
    importlib.reload(llm_mod)
    with pytest.raises(RuntimeError, match="GROQ_API_KEY not set"):
        await llm_mod._call_groq(messages=[{"role": "user", "content": "x"}])


@pytest.mark.asyncio
async def test_call_deepseek_walks_to_groq_when_all_openrouter_fail(monkeypatch) -> None:
    """The whole point of iter 212m-49: when OpenRouter primary 402's
    AND every free-tier candidate fails too (429), Groq must serve.
    Provenance must record `is_emergency=True`."""
    _setup_env(monkeypatch)
    import importlib
    import services.llm as llm_mod
    importlib.reload(llm_mod)

    # 3 candidates: primary + 2 free. Each gets one POST that returns
    # an error. After _MAX_RETRIES the chain advances.
    # primary 402 (no retry on 402), then free/a 429 (1 retry → 2 calls),
    # then free/b 429 (1 retry → 2 calls). Total 5 mock responses.
    or_responses = [
        _mk_402_response(),                  # primary
        _mk_429_response(), _mk_429_response(),  # free/a + retry
        _mk_429_response(), _mk_429_response(),  # free/b + retry
    ]

    fake_completion = MagicMock()
    fake_completion.choices = [MagicMock(message=MagicMock(content="from-groq"))]
    fake_groq = MagicMock()
    fake_groq.chat.completions.create = AsyncMock(return_value=fake_completion)

    with patch("httpx.AsyncClient", side_effect=_client_factory(or_responses)), \
         patch("groq.AsyncGroq", return_value=fake_groq):
        # raise_for_status should fire on the 402/429s, triggering the
        # walk. Monkey-patch httpx.Response to actually raise.
        result = await llm_mod._call_deepseek(
            messages=[{"role": "user", "content": "hi"}],
            system="x",
            max_tokens=20,
            temperature=0.0,
        )
    assert result == "from-groq"
    prov = llm_mod.get_last_provider()
    assert prov["provider"] == "groq"
    assert prov["is_emergency"] is True
    assert prov["model"] == "llama-3.3-70b-versatile"
    fake_groq.chat.completions.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_call_openrouter_model_walks_to_groq(monkeypatch) -> None:
    """Second entry point (used by agents / Vanguard / Mode D) must
    also fall through to Groq when the OpenRouter chain dies."""
    _setup_env(monkeypatch)
    import importlib
    import services.llm as llm_mod
    importlib.reload(llm_mod)

    or_responses = [
        _mk_402_response(),  # caller's primary model
        _mk_429_response(),  # free/a
        _mk_429_response(),  # free/b
    ]
    fake_completion = MagicMock()
    fake_completion.choices = [MagicMock(message=MagicMock(content="agent-from-groq"))]
    fake_groq = MagicMock()
    fake_groq.chat.completions.create = AsyncMock(return_value=fake_completion)

    with patch("httpx.AsyncClient", side_effect=_client_factory(or_responses)), \
         patch("groq.AsyncGroq", return_value=fake_groq):
        result = await llm_mod.call_openrouter_model(
            model="z-ai/glm-5.2",  # paid → 402
            system="be brief",
            user="ping",
            max_tokens=20,
            temperature=0.0,
        )
    assert result == "agent-from-groq"
    prov = llm_mod.get_last_provider()
    assert prov["provider"] == "groq"
    assert prov["is_emergency"] is True


@pytest.mark.asyncio
async def test_groq_not_invoked_when_openrouter_primary_succeeds(monkeypatch) -> None:
    """Groq is the EMERGENCY net — must NEVER be called when the
    primary OpenRouter call succeeds. Otherwise we'd burn Groq's
    free quota for nothing."""
    _setup_env(monkeypatch)
    import importlib
    import services.llm as llm_mod
    importlib.reload(llm_mod)

    or_responses = [_mk_200_or_response("from-primary-or")]
    fake_groq = MagicMock()
    fake_groq.chat.completions.create = AsyncMock()
    with patch("httpx.AsyncClient", side_effect=_client_factory(or_responses)), \
         patch("groq.AsyncGroq", return_value=fake_groq):
        result = await llm_mod._call_deepseek(
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=20, temperature=0.0,
        )
    assert result == "from-primary-or"
    fake_groq.chat.completions.create.assert_not_awaited()
    prov = llm_mod.get_last_provider()
    assert prov["provider"] == "openrouter"
    assert prov["is_emergency"] is False


@pytest.mark.asyncio
async def test_groq_skipped_when_key_missing(monkeypatch) -> None:
    """If GROQ_API_KEY isn't set, the chain just exhausts and re-raises
    the last OpenRouter error. Groq must NOT be invoked."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-or-key")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_FREE_MODELS", "free/a:free")
    import importlib
    import services.llm as llm_mod
    importlib.reload(llm_mod)

    or_responses = [
        _mk_402_response(),  # primary
        _mk_429_response(), _mk_429_response(),  # free/a + retry
    ]
    fake_groq_ctor = MagicMock()
    with patch("httpx.AsyncClient", side_effect=_client_factory(or_responses)), \
         patch("groq.AsyncGroq", fake_groq_ctor):
        with pytest.raises(httpx.HTTPStatusError):
            await llm_mod._call_deepseek(
                messages=[{"role": "user", "content": "x"}],
                max_tokens=20, temperature=0.0,
            )
    # Groq client must NEVER have been constructed.
    fake_groq_ctor.assert_not_called()


def test_get_last_provider_starts_with_safe_default() -> None:
    """Cold-start provenance must be a sane default, not None, so
    callers can read it unconditionally."""
    import importlib
    import services.llm as llm_mod
    importlib.reload(llm_mod)
    llm_mod.reset_last_provider()
    prov = llm_mod.get_last_provider()
    assert prov["provider"] == "openrouter"
    assert prov["is_emergency"] is False
    assert prov["model"] == ""
