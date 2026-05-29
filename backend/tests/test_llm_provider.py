"""
test_llm_provider.py — Privacy + routing assertions for services.llm.

Mocks OpenRouter so we can:
  1. Assert that every outgoing payload contains the required privacy
     directives: data_collection=deny, allow_fallbacks=False, and the
     DeepSeek host order.
  2. Assert that call_llm_with_meta returns {ok:false, error:...} when
     OpenRouter is unreachable — i.e. there is NO silent fallback to
     Emergent / Groq / Anthropic.
"""
from __future__ import annotations
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import httpx

os.environ.setdefault("OPENROUTER_API_KEY", "sk-or-v1-TEST")
os.environ.setdefault("LLM_MODEL", "deepseek/deepseek-chat")

from services.llm import call_llm, call_llm_with_meta  # noqa: E402


def _fake_response(status: int, json_body: dict | None = None,
                    text: str = "") -> MagicMock:
    """Build a MagicMock that mimics httpx.Response enough for our code."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status
    resp.text = text or "{}"
    resp.json = MagicMock(return_value=json_body or {})

    def _raise():
        if status >= 400:
            req = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
            raise httpx.HTTPStatusError(
                f"HTTP {status}", request=req, response=resp
            )
    resp.raise_for_status = MagicMock(side_effect=_raise)
    return resp


class _FakeAsyncClient:
    """Drop-in replacement for httpx.AsyncClient as async context manager."""
    def __init__(self, *args, **kwargs):
        self.last_payload = None
        self.last_headers = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, headers=None, json=None):
        self.last_payload = json
        self.last_headers = headers
        return _fake_response(
            200,
            {
                "choices": [
                    {"message": {"content": "ok"}}
                ]
            },
        )


@pytest.mark.asyncio
async def test_payload_carries_privacy_directives():
    """Verify data_collection=deny + allow_fallbacks=false + DeepSeek order."""
    captured = {}

    class Capturing(_FakeAsyncClient):
        async def post(self, url, headers=None, json=None):
            captured["url"] = url
            captured["payload"] = json
            captured["headers"] = headers
            return _fake_response(
                200, {"choices": [{"message": {"content": "ok"}}]}
            )

    with patch("services.llm.httpx.AsyncClient", Capturing):
        out = await call_llm(
            messages=[{"role": "user", "content": "ping"}],
            system="you are terse",
            max_tokens=50,
        )

    assert out == "ok"
    assert captured["url"] == "https://openrouter.ai/api/v1/chat/completions"

    payload = captured["payload"]
    assert payload["model"] == "deepseek/deepseek-chat"
    assert payload["max_tokens"] == 50
    # System message prepended when provided
    assert payload["messages"][0] == {"role": "system", "content": "you are terse"}
    assert payload["messages"][1] == {"role": "user", "content": "ping"}

    # Privacy + routing
    provider = payload["provider"]
    assert provider["data_collection"] == "deny", "data_collection MUST be deny"
    assert provider["allow_fallbacks"] is False, "allow_fallbacks MUST be False"
    assert "deepseek" in provider["order"], "DeepSeek must be in provider order"

    # Anti-cache + attribution headers
    h = captured["headers"]
    assert h["Authorization"].startswith("Bearer ")
    assert h["X-No-Cache"] == "true"
    assert h["HTTP-Referer"] == "https://aurem.dev"
    assert h["X-Title"] == "AUREM Dev"


@pytest.mark.asyncio
async def test_call_llm_with_meta_success_reports_deepseek():
    with patch("services.llm.httpx.AsyncClient", _FakeAsyncClient):
        meta = await call_llm_with_meta(
            system="sys", user="hi", max_tokens=32
        )
    assert meta == {
        "ok": True,
        "provider": "deepseek",
        "content": "ok",
        "fallback_chain": ["deepseek"],
    }


@pytest.mark.asyncio
async def test_no_silent_fallback_on_openrouter_5xx():
    """When OpenRouter returns 500, call_llm_with_meta returns ok=False
    — it must NOT silently route to Emergent / Groq / Anthropic."""

    class Failing(_FakeAsyncClient):
        async def post(self, url, headers=None, json=None):
            return _fake_response(500, {}, text="upstream boom")

    with patch("services.llm.httpx.AsyncClient", Failing):
        meta = await call_llm_with_meta("sys", "hi")
    assert meta["ok"] is False
    assert meta["content"] == ""
    assert meta["provider"] is None
    assert "error" in meta and "LLM unavailable" in meta["error"]
    # Importantly: no other provider name in the chain
    assert meta["fallback_chain"] == ["deepseek"]


@pytest.mark.asyncio
async def test_no_silent_fallback_on_network_error():
    """Connection error → ok=False, no silent fallback."""

    class Crashing(_FakeAsyncClient):
        async def post(self, url, headers=None, json=None):
            raise httpx.ConnectError("name resolution failed")

    with patch("services.llm.httpx.AsyncClient", Crashing):
        meta = await call_llm_with_meta("sys", "hi")
    assert meta["ok"] is False
    assert meta["provider"] is None
    assert meta["fallback_chain"] == ["deepseek"]


@pytest.mark.asyncio
async def test_call_llm_raises_without_api_key(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        await call_llm(messages=[{"role": "user", "content": "hi"}])
