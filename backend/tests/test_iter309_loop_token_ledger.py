"""
test_iter309_loop_token_ledger.py — Iter 309 · Pre-Phase-1

Regression tests for per-loop LLM token accounting.

Guarantees under test:
  1. `loop_call_context` sets contextvars visible to nested calls.
  2. `log_llm_usage` is a NO-OP outside a loop context — regular
     chat / scaffold / deep-research callers must be unaffected.
  3. Inside a loop context, `log_llm_usage` inserts one row into
     `ora_chat_usage` with:
       - session_id = loop_id
       - route      = f"loop.{phase_tag}"
       - user_id    = the loop's user_id
       - input_tokens / output_tokens copied from the response
  4. OpenRouter-native `prompt_tokens/completion_tokens` naming
     AND ora_chat-style `input_tokens/output_tokens` naming both
     recognized (regression against silent zero-token rows).
  5. Nested contexts stack — inner overrides outer for its scope,
     then restores.
  6. Zero-token calls (empty usage dict, no error) are DROPPED so
     fallback-chain error paths don't spam the ledger.
"""
from __future__ import annotations
import asyncio
import os
import sys

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services import loop_token_ledger as ledger    # noqa: E402


class _MockDB:
    """Just enough of an async Mongo double to catch `.insert_one`
    on the `ora_chat_usage` collection."""
    def __init__(self):
        self.ora_chat_usage = _MockColl()
        self.ora_chat_budget_alerts = _MockColl()

class _MockColl:
    def __init__(self):
        self.docs: list[dict] = []
    async def insert_one(self, doc):
        self.docs.append(dict(doc))
        return type("R", (), {"inserted_id": "x"})()
    async def find_one(self, *a, **kw):
        return None
    def aggregate(self, *a, **kw):
        async def _g():
            for d in self.docs: yield d
        return _g()


@pytest_asyncio.fixture(autouse=True)
async def _mock_db(monkeypatch):
    """Point `cost_tracker.get_db` at an in-memory double so we can
    read back the inserted rows without a real Mongo."""
    mock = _MockDB()
    from services.ora_chat import cost_tracker as ct
    monkeypatch.setattr(ct, "get_db", lambda: mock)
    # cost_tracker's threshold-alert path also calls get_db —
    # short-circuit so we don't chase founder-email lookups in
    # a unit test.
    async def _no_alert(): return None
    monkeypatch.setattr(ct, "_maybe_send_threshold_alert", _no_alert)
    yield mock


@pytest.mark.asyncio
async def test_no_context_is_noop(_mock_db):
    # Outside a loop context, log_llm_usage must not insert anything.
    await ledger.log_llm_usage(
        "deepseek/deepseek-chat",
        {"prompt_tokens": 100, "completion_tokens": 40},
    )
    assert _mock_db.ora_chat_usage.docs == []


@pytest.mark.asyncio
async def test_inside_context_inserts_tagged_row(_mock_db):
    async with ledger.loop_call_context(
        loop_id="loop_abc", phase_tag="plan", user_id="u42",
    ):
        await ledger.log_llm_usage(
            "deepseek/deepseek-chat",
            {"prompt_tokens": 812, "completion_tokens": 240,
             "total_tokens": 1052},
            temperature=0.7,
        )
    assert len(_mock_db.ora_chat_usage.docs) == 1
    row = _mock_db.ora_chat_usage.docs[0]
    assert row["session_id"]    == "loop_abc"
    assert row["route"]         == "loop.plan"
    assert row["user_id"]       == "u42"
    assert row["input_tokens"]  == 812
    assert row["output_tokens"] == 240
    assert row["model"]         == "deepseek/deepseek-chat"
    # cost_tracker computes cost — deepseek at $0.14 in / $0.28 out per 1M
    # → 812 * 0.14/1e6 + 240 * 0.28/1e6 = ~0.0001809
    assert 0.0 < row["cost_usd"] < 0.001


@pytest.mark.asyncio
async def test_input_tokens_alt_naming_recognized(_mock_db):
    # Provider libs that already normalize to input_tokens/output_tokens
    # (ora_chat providers path) must ALSO be captured.
    async with ledger.loop_call_context(
        loop_id="loop_alt", phase_tag="execute", user_id="u1",
    ):
        await ledger.log_llm_usage(
            "z-ai/glm-5.2",
            {"input_tokens": 500, "output_tokens": 120},
        )
    assert len(_mock_db.ora_chat_usage.docs) == 1
    assert _mock_db.ora_chat_usage.docs[0]["input_tokens"]  == 500
    assert _mock_db.ora_chat_usage.docs[0]["output_tokens"] == 120


@pytest.mark.asyncio
async def test_zero_token_is_dropped(_mock_db):
    async with ledger.loop_call_context(
        loop_id="loop_zero", phase_tag="verify", user_id="u1",
    ):
        await ledger.log_llm_usage(
            "anthropic/claude-sonnet-4.5",
            {"prompt_tokens": 0, "completion_tokens": 0},
        )
    assert _mock_db.ora_chat_usage.docs == []


@pytest.mark.asyncio
async def test_error_token_row_is_kept(_mock_db):
    # A failed call still produces a row when `error` is passed —
    # useful for cost-of-failure tracking.
    async with ledger.loop_call_context(
        loop_id="loop_err", phase_tag="scan", user_id="u1",
    ):
        await ledger.log_llm_usage(
            "anthropic/claude-sonnet-4.5",
            {"prompt_tokens": 0, "completion_tokens": 0},
            error="OpenRouter 429 quota",
        )
    assert len(_mock_db.ora_chat_usage.docs) == 1
    assert _mock_db.ora_chat_usage.docs[0]["error"] == "OpenRouter 429 quota"


@pytest.mark.asyncio
async def test_nested_context_stacks_and_restores(_mock_db):
    async with ledger.loop_call_context(
        loop_id="outer", phase_tag="plan", user_id="u1",
    ):
        await ledger.log_llm_usage(
            "m1", {"prompt_tokens": 10, "completion_tokens": 5},
        )
        async with ledger.loop_call_context(
            loop_id="inner", phase_tag="execute", user_id="u2",
        ):
            await ledger.log_llm_usage(
                "m2", {"prompt_tokens": 20, "completion_tokens": 10},
            )
        # Back to outer — no leak from inner.
        await ledger.log_llm_usage(
            "m3", {"prompt_tokens": 30, "completion_tokens": 15},
        )
    docs = _mock_db.ora_chat_usage.docs
    assert len(docs) == 3
    assert docs[0]["session_id"] == "outer" and docs[0]["route"] == "loop.plan"
    assert docs[1]["session_id"] == "inner" and docs[1]["route"] == "loop.execute"
    assert docs[2]["session_id"] == "outer" and docs[2]["route"] == "loop.plan"


@pytest.mark.asyncio
async def test_parallel_tasks_dont_leak_context(_mock_db):
    """Regression against a naive globals-based impl. Each asyncio
    task inside a parliament fan-out must see its own contextvars."""
    async def _do(loop_id, phase):
        async with ledger.loop_call_context(
            loop_id=loop_id, phase_tag=phase, user_id="u",
        ):
            await asyncio.sleep(0.01)
            await ledger.log_llm_usage(
                "m", {"prompt_tokens": 100, "completion_tokens": 20},
            )
    await asyncio.gather(
        _do("A", "plan"),
        _do("B", "execute"),
        _do("C", "verify"),
    )
    docs = _mock_db.ora_chat_usage.docs
    routes = sorted({(d["session_id"], d["route"]) for d in docs})
    assert routes == [("A", "loop.plan"),
                      ("B", "loop.execute"),
                      ("C", "loop.verify")]
