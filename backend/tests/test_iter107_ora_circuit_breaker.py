"""Iter 107 — persistent circuit breaker for aurem.live ORA upstream.

Bug: aurem.live's ORA service was returning 500 on every call because
its internal OpenRouter integration uses a free DeepSeek model slug
that OpenRouter has retired. Production logs were spammed with the same
500 every chat. The fallback already worked, but the noise was
unacceptable.

Fix: persistent circuit breaker in services/ora_client.py — first 5xx
(or any "openrouter HTTP 404"/"model unavailable" pattern) opens the
breaker for 1 hour via a /tmp file. Subsequent calls skip the HTTP
request entirely. File is checked from disk so fresh uvicorn workers
in the same pod also short-circuit immediately.
"""
import os
import time
import asyncio
from pathlib import Path

import pytest
from fastapi import HTTPException

from services import ora_client


@pytest.fixture(autouse=True)
def _clean_breaker(monkeypatch):
    """Remove the breaker file before AND after every test."""
    if ora_client._BREAKER_FILE.exists():
        ora_client._BREAKER_FILE.unlink()
    monkeypatch.setenv("ORA_API_KEY", "test-key-not-used-network-is-mocked")
    monkeypatch.setenv("ORA_BASE_URL", "https://aurem.live")
    yield
    if ora_client._BREAKER_FILE.exists():
        ora_client._BREAKER_FILE.unlink()


# ── breaker primitives ──────────────────────────────────────────
def test_breaker_starts_closed():
    assert ora_client._breaker_is_open() is False
    assert ora_client.is_ora_available() is True


def test_trip_breaker_opens_circuit_and_persists_to_disk():
    ora_client._trip_breaker("test_reason")
    assert ora_client._BREAKER_FILE.exists()
    assert ora_client._breaker_is_open() is True
    assert ora_client.is_ora_available() is False


def test_breaker_self_heals_after_cooldown(monkeypatch):
    ora_client._trip_breaker("self_heal_test")
    # Backdate the file to look stale
    stale_ts = time.time() - ora_client._BREAKER_COOLDOWN_SECS - 60
    os.utime(ora_client._BREAKER_FILE, (stale_ts, stale_ts))
    assert ora_client._breaker_is_open() is False
    # File should be auto-cleaned during the check
    assert not ora_client._BREAKER_FILE.exists()


def test_breaker_persists_for_fresh_worker():
    """Simulates a fresh uvicorn worker — file on disk wins."""
    ora_client._trip_breaker("persist_for_worker_test")
    # New "worker" state: no in-memory globals, only file system
    assert ora_client._breaker_is_open() is True


# ── short-circuit behaviour in call_ora ──────────────────────────
def test_call_ora_short_circuits_when_breaker_open():
    """When the breaker is open, call_ora must raise 503 WITHOUT
    issuing any HTTP request."""
    ora_client._trip_breaker("short_circuit_test")

    async def go():
        with pytest.raises(HTTPException) as ei:
            await ora_client.call_ora("hello")
        assert ei.value.status_code == 503
        assert "circuit" in str(ei.value.detail).lower()

    asyncio.run(go())


# ── trip-on-error patterns ─────────────────────────────────────
def test_fatal_patterns_include_openrouter_model_deprecation():
    assert any("openrouter HTTP 404" in p
               for p in ora_client._FATAL_UPSTREAM_PATTERNS)
    assert any("model is unavailable" in p
               for p in ora_client._FATAL_UPSTREAM_PATTERNS)


# ── integration: trip via a mocked 500 response ───────────────────
class _MockResponse:
    def __init__(self, status_code: int, body: dict):
        self.status_code = status_code
        self._body = body
        self.text = str(body)
    def json(self):
        return self._body


class _MockClient:
    def __init__(self, *a, **kw): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *a): pass
    async def post(self, *a, **kw):
        return _MockResponse(
            500,
            {"detail": 'ora_chat_error: openrouter HTTP 404: {"error":{"message":"This model is unavailable for free."}}'}
        )


def test_500_with_openrouter_404_trips_breaker(monkeypatch):
    monkeypatch.setattr(ora_client.httpx, "AsyncClient", _MockClient)

    async def go():
        # First call — hits the (mocked) broken upstream
        with pytest.raises(HTTPException) as ei:
            await ora_client.call_ora("hello")
        # Surfaces the upstream 500
        assert ei.value.status_code == 500
        # Breaker should now be tripped
        assert ora_client._breaker_is_open() is True
        # Second call — should short-circuit to 503 (no HTTP call attempted)
        with pytest.raises(HTTPException) as ei2:
            await ora_client.call_ora("hello again")
        assert ei2.value.status_code == 503
        assert "circuit" in str(ei2.value.detail).lower()

    asyncio.run(go())


def test_500_with_generic_5xx_also_trips(monkeypatch):
    class _Mock503(_MockClient):
        async def post(self, *a, **kw):
            return _MockResponse(503, {"detail": "upstream maintenance"})
    monkeypatch.setattr(ora_client.httpx, "AsyncClient", _Mock503)

    async def go():
        with pytest.raises(HTTPException):
            await ora_client.call_ora("hello")
        assert ora_client._breaker_is_open() is True
    asyncio.run(go())


def test_4xx_does_NOT_trip_breaker(monkeypatch):
    """A 401/403/422 indicates a CLIENT-side issue (bad key, bad payload).
    Retrying after backoff isn't useful but doesn't justify keeping the
    breaker open for an hour either — we want the next request to retry
    so a re-deploy with a fixed key works immediately."""
    class _Mock401(_MockClient):
        async def post(self, *a, **kw):
            return _MockResponse(401, {"detail": "bad key"})
    monkeypatch.setattr(ora_client.httpx, "AsyncClient", _Mock401)

    async def go():
        with pytest.raises(HTTPException):
            await ora_client.call_ora("hello")
        # 401 should NOT trip the breaker (no openrouter pattern, no 5xx)
        assert ora_client._breaker_is_open() is False
    asyncio.run(go())
