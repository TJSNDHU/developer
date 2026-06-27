"""Iter 212m-51 — DeepSeek direct API as second-hop fallback.

Wiring guards (mocked httpx — no real network). The actual API
key was rejected as invalid during integration, so live behaviour
is gated on the user supplying a valid sk-... key. The wiring
itself is exhaustively unit-tested here so that when a working
key lands in .env the hop activates immediately, no code change
needed.

Chain priority under test:
  1. OpenRouter primary (paid)
  2. DeepSeek direct (paid, independent vendor)        ← THIS LAYER
  3. OpenRouter :free chain
  4. Groq emergency
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest


def _setup_env(monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-or-key")
    monkeypatch.setenv("DEEPSEEK_API_KEY",   "test-ds-direct-key")
    monkeypatch.setenv("GROQ_API_KEY",       "test-groq-key")
    monkeypatch.setenv("OPENROUTER_FREE_MODELS", "free/a:free,free/b:free")


def _mk_or(status: int, content: str = "") -> httpx.Response:
    req = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    if status == 200:
        return httpx.Response(
            200, request=req,
            json={"choices": [{"message": {"content": content}}]},
        )
    return httpx.Response(status, request=req, json={"error": {"message": "x"}})


def _mk_ds(status: int, content: str = "") -> httpx.Response:
    req = httpx.Request("POST", "https://api.deepseek.com/chat/completions")
    if status == 200:
        return httpx.Response(
            200, request=req,
            json={"choices": [{"message": {"content": content}}]},
        )
    return httpx.Response(status, request=req, json={"error": {"message": "x"}})


class _Scripted:
    """Returns scripted httpx.Response objects in order. Different
    URLs are routed via a callable so we can interleave OR and DS
    responses without per-test complexity.

    NOTE: takes the SHARED `responses_by_host` dict directly (no
    copying) so the test body can assert on remaining entries after
    the call to verify which hops were actually exercised."""
    def __init__(self, responses_by_host: dict):
        self._book = responses_by_host

    async def post(self, url, *args, **kwargs):
        host = "deepseek" if "deepseek.com" in url else "openrouter"
        if not self._book.get(host):
            raise RuntimeError(f"no scripted response for host={host}")
        r = self._book[host].pop(0)
        return r

    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False


def _factory(book):
    def _f(*a, **k):
        return _Scripted(book)
    return _f


# ─────────── TESTS ───────────


@pytest.mark.asyncio
async def test_deepseek_direct_serves_when_openrouter_primary_402(monkeypatch) -> None:
    """The whole point of this layer: OR primary 402 → DS direct
    delivers paid-tier quality WITHOUT touching the free chain or
    Groq emergency."""
    _setup_env(monkeypatch)
    import importlib
    import services.llm as llm_mod
    importlib.reload(llm_mod)

    book = {
        "openrouter": [_mk_or(402)],   # primary fails
        "deepseek":   [_mk_ds(200, "from-deepseek-direct")],
    }
    fake_groq = MagicMock()
    fake_groq.chat.completions.create = AsyncMock()
    with patch("httpx.AsyncClient", side_effect=_factory(book)), \
         patch("groq.AsyncGroq", return_value=fake_groq):
        result = await llm_mod._call_deepseek(
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=20, temperature=0.0,
        )
    assert result == "from-deepseek-direct"
    prov = llm_mod.get_last_provider()
    assert prov["provider"] == "deepseek_direct"
    assert prov["is_emergency"] is False  # DS direct is paid, not emergency
    assert prov["model"] == "deepseek-v4-flash"
    # Free chain must NOT have been touched.
    assert len(book["openrouter"]) == 0  # only the primary was consumed
    # Groq must NOT have been invoked.
    fake_groq.chat.completions.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_deepseek_direct_skipped_when_primary_ok(monkeypatch) -> None:
    """When the OpenRouter primary serves the turn, DeepSeek direct
    must NOT be called — we'd be wasting paid credits twice."""
    _setup_env(monkeypatch)
    import importlib
    import services.llm as llm_mod
    importlib.reload(llm_mod)

    book = {
        "openrouter": [_mk_or(200, "from-primary")],
        "deepseek":   [],  # must never be touched
    }
    with patch("httpx.AsyncClient", side_effect=_factory(book)):
        result = await llm_mod._call_deepseek(
            messages=[{"role": "user", "content": "x"}],
            max_tokens=20, temperature=0.0,
        )
    assert result == "from-primary"
    prov = llm_mod.get_last_provider()
    assert prov["provider"] == "openrouter"


@pytest.mark.asyncio
async def test_chain_walks_past_deepseek_direct_when_key_bad(monkeypatch) -> None:
    """If the DS key is invalid (401) — exactly what happened with
    the founder's first attempt — the chain MUST walk forward to
    the OR free models. Not abort. Not raise. Not 500 the user."""
    _setup_env(monkeypatch)
    import importlib
    import services.llm as llm_mod
    importlib.reload(llm_mod)

    book = {
        "openrouter": [
            _mk_or(402),                       # primary fails
            _mk_or(200, "from-free-chain"),    # free/a serves
        ],
        "deepseek": [_mk_ds(401)],             # bad key
    }
    fake_groq = MagicMock()
    fake_groq.chat.completions.create = AsyncMock()
    with patch("httpx.AsyncClient", side_effect=_factory(book)), \
         patch("groq.AsyncGroq", return_value=fake_groq):
        result = await llm_mod._call_deepseek(
            messages=[{"role": "user", "content": "x"}],
            max_tokens=20, temperature=0.0,
        )
    assert result == "from-free-chain"
    prov = llm_mod.get_last_provider()
    assert prov["provider"] == "openrouter"
    assert prov["model"] == "free/a:free"
    fake_groq.chat.completions.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_chain_walks_past_deepseek_direct_on_402_balance(monkeypatch) -> None:
    """DS account out of balance (402) — same behaviour as 401:
    walk forward to free chain, don't abort."""
    _setup_env(monkeypatch)
    import importlib
    import services.llm as llm_mod
    importlib.reload(llm_mod)

    book = {
        "openrouter": [
            _mk_or(402),
            _mk_or(200, "free-served"),
        ],
        "deepseek": [_mk_ds(402)],
    }
    with patch("httpx.AsyncClient", side_effect=_factory(book)), \
         patch("groq.AsyncGroq", return_value=MagicMock()):
        result = await llm_mod._call_deepseek(
            messages=[{"role": "user", "content": "x"}],
            max_tokens=20, temperature=0.0,
        )
    assert result == "free-served"


@pytest.mark.asyncio
async def test_deepseek_direct_400_aborts_chain(monkeypatch) -> None:
    """A 400/422 from DS direct means the PROMPT is malformed — the
    same prompt will fail every downstream model. Abort early to
    save free quota."""
    _setup_env(monkeypatch)
    import importlib
    import services.llm as llm_mod
    importlib.reload(llm_mod)

    book = {
        "openrouter": [_mk_or(402)],
        "deepseek":   [_mk_ds(400)],
    }
    with patch("httpx.AsyncClient", side_effect=_factory(book)), \
         patch("groq.AsyncGroq", return_value=MagicMock()):
        with pytest.raises(httpx.HTTPStatusError) as exc:
            await llm_mod._call_deepseek(
                messages=[{"role": "user", "content": "x"}],
                max_tokens=20, temperature=0.0,
            )
        assert exc.value.response.status_code == 400


@pytest.mark.asyncio
async def test_deepseek_direct_skipped_when_key_missing(monkeypatch) -> None:
    """If DEEPSEEK_API_KEY isn't set, the hop is silently skipped —
    chain goes straight from primary failure to free chain."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-or-key")
    monkeypatch.setenv("OPENROUTER_FREE_MODELS", "free/a:free")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    import importlib
    import services.llm as llm_mod
    importlib.reload(llm_mod)

    book = {
        "openrouter": [_mk_or(402), _mk_or(200, "free-ok")],
        "deepseek":   [],  # must never be hit
    }
    with patch("httpx.AsyncClient", side_effect=_factory(book)):
        result = await llm_mod._call_deepseek(
            messages=[{"role": "user", "content": "x"}],
            max_tokens=20, temperature=0.0,
        )
    assert result == "free-ok"


@pytest.mark.asyncio
async def test_call_openrouter_model_also_uses_deepseek_direct(monkeypatch) -> None:
    """Second entry point — agents / Vanguard / Mode D — must also
    benefit from the DS direct hop."""
    _setup_env(monkeypatch)
    import importlib
    import services.llm as llm_mod
    importlib.reload(llm_mod)

    book = {
        "openrouter": [_mk_or(402)],
        "deepseek":   [_mk_ds(200, "ds-agent")],
    }
    with patch("httpx.AsyncClient", side_effect=_factory(book)), \
         patch("groq.AsyncGroq", return_value=MagicMock()):
        result = await llm_mod.call_openrouter_model(
            model="z-ai/glm-5.2",
            system="be brief",
            user="ping",
            max_tokens=20, temperature=0.0,
        )
    assert result == "ds-agent"
    prov = llm_mod.get_last_provider()
    assert prov["provider"] == "deepseek_direct"
