"""
Iter 124 — Tests for:
  1) ORA persona enforces REPO-CONNECTED MODE (read-first) and prohibits
     permission-asking on read-only ops.
  2) LLM gateway retries on 429 / 5xx / timeout with backoff.
"""
from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, patch, MagicMock

import httpx
import pytest


# ── (1) Persona text checks ─────────────────────────────────────────────

def test_persona_has_repo_connected_mode_section():
    from services.orchestrator import AUREM_CTO_PERSONA
    assert "REPO-CONNECTED MODE" in AUREM_CTO_PERSONA
    assert "get_dependencies" in AUREM_CTO_PERSONA
    assert "detect_framework" in AUREM_CTO_PERSONA


def test_persona_forbids_permission_asking_on_reads():
    from services.orchestrator import AUREM_CTO_PERSONA
    # Iter 129 — relaxed phrasing match. The rule is encoded; what matters
    # is that the persona STILL forbids permission-asking on reads.
    assert "READ-ONLY" in AUREM_CTO_PERSONA
    assert "Permission is ONLY for WRITES" in AUREM_CTO_PERSONA \
        or "READ-ONLY OPS NEVER REQUIRE PERMISSION" in AUREM_CTO_PERSONA \
        or "permission to perform any READ-ONLY" in AUREM_CTO_PERSONA


def test_persona_lists_inventory_question_triggers():
    """The persona must call out 'how many tools/skills/endpoints' style
    questions as inventory triggers that demand a repo read."""
    from services.orchestrator import AUREM_CTO_PERSONA
    txt = AUREM_CTO_PERSONA.lower()
    assert "how many" in txt
    assert "tech stack" in txt or "framework" in txt
    assert "dependencies" in txt


# ── (2) LLM gateway retry behaviour ────────────────────────────────────

def _mk_response(status: int, body: dict | None = None) -> httpx.Response:
    req = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    return httpx.Response(status, json=body or {}, request=req)


@pytest.mark.asyncio
async def test_deepseek_retries_on_429_then_succeeds(monkeypatch):
    """First call returns 429, second returns 200 — should succeed."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    # Avoid sleeping in tests
    monkeypatch.setattr("services.llm.asyncio.sleep", AsyncMock())

    from services import llm

    call_count = {"n": 0}

    async def fake_post(self, url, headers=None, json=None):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return _mk_response(429, {"error": "rate limit"})
        return _mk_response(200, {
            "choices": [{"message": {"content": "hello"}}]
        })

    with patch.object(httpx.AsyncClient, "post", new=fake_post):
        out = await llm._call_deepseek(
            messages=[{"role": "user", "content": "hi"}],
            system="sys", max_tokens=10, temperature=0.0,
        )
    assert out == "hello"
    assert call_count["n"] == 2  # retried exactly once


@pytest.mark.asyncio
async def test_deepseek_does_not_retry_on_400(monkeypatch):
    """400 is a permanent client error — must not retry."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr("services.llm.asyncio.sleep", AsyncMock())

    from services import llm

    call_count = {"n": 0}

    async def fake_post(self, url, headers=None, json=None):
        call_count["n"] += 1
        return _mk_response(400, {"error": "bad request"})

    with patch.object(httpx.AsyncClient, "post", new=fake_post):
        with pytest.raises(httpx.HTTPStatusError):
            await llm._call_deepseek(
                messages=[{"role": "user", "content": "hi"}],
                system="sys", max_tokens=10, temperature=0.0,
            )
    assert call_count["n"] == 1


@pytest.mark.asyncio
async def test_deepseek_gives_up_after_max_retries(monkeypatch):
    """Persistent 429 — should retry _MAX_RETRIES times then raise."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr("services.llm.asyncio.sleep", AsyncMock())

    from services import llm

    call_count = {"n": 0}

    async def fake_post(self, url, headers=None, json=None):
        call_count["n"] += 1
        return _mk_response(429, {"error": "rate limit"})

    with patch.object(httpx.AsyncClient, "post", new=fake_post):
        with pytest.raises(httpx.HTTPStatusError):
            await llm._call_deepseek(
                messages=[{"role": "user", "content": "hi"}],
                system="sys", max_tokens=10, temperature=0.0,
            )
    # 1 original + _MAX_RETRIES retries
    assert call_count["n"] == llm._MAX_RETRIES + 1


@pytest.mark.asyncio
async def test_call_llm_with_meta_surfaces_friendly_429(monkeypatch):
    """When all retries fail with 429, error message should be friendly."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.delenv("EMERGENT_LLM_KEY", raising=False)
    monkeypatch.setattr("services.llm.asyncio.sleep", AsyncMock())

    from services import llm

    async def fake_post(self, url, headers=None, json=None):
        return _mk_response(429, {"error": "rate limit"})

    with patch.object(httpx.AsyncClient, "post", new=fake_post):
        res = await llm.call_llm_with_meta(
            system="s", user="u", max_tokens=10, mode="chat",
        )
    assert res["ok"] is False
    assert "rate-limited" in (res.get("error") or "").lower()


@pytest.mark.asyncio
async def test_retry_delay_bounded():
    """Backoff should be bounded and non-negative."""
    from services.llm import _retry_delay, _BASE_DELAY_S
    for attempt in range(1, 5):
        d = _retry_delay(attempt)
        assert d >= 0
        # full-jitter cap
        assert d <= _BASE_DELAY_S * (2 ** (attempt - 1)) + 0.01
