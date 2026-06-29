"""
Iter 212m-149 — Intent Gateway contract tests.

Validates the 3-tier classifier replaces the binary loop toggle:
  - Heuristic: fast, deterministic, covers the 7 founder fixtures
  - LLM fallback: triggered only when heuristic conf < 0.75
  - Ambiguity handler: emits a `clarify` tier when conf < 0.72
  - Logging: writes one row per call to `intent_classifications`
"""
import asyncio
from pathlib import Path

import pytest

from core import intent_gateway as ig


# ─── Heuristic — founder test fixtures ───────────────────────────────

@pytest.mark.parametrize("msg", [
    "Good morning",
    "Thanks ORA",
    "lol ok got it",
    "hi",
    "thanks!",
    "cool, got it",
])
def test_heuristic_casual(msg):
    r = ig.classify_heuristic_sync(msg)
    assert r["tier"] == ig.TIER_CASUAL, f"Expected casual for {msg!r}: got {r}"
    assert r["confidence"] >= 0.80, f"Casual conf too low: {r}"


@pytest.mark.parametrize("msg", [
    "Show me today's leads",
    "What is my pipeline status",
    "list all my projects",
    "explain the auth flow",
    "summarize the readme",
    "what's the current build hash",
])
def test_heuristic_query(msg):
    r = ig.classify_heuristic_sync(msg)
    assert r["tier"] == ig.TIER_QUERY, f"Expected query for {msg!r}: got {r}"
    # Must be high-enough confidence to skip LLM escalation for these
    # textbook query forms.
    assert r["confidence"] >= 0.75, f"Query conf too low: {r}"


@pytest.mark.parametrize("msg", [
    "Send follow-up to all leads from yesterday",
    "Run a security scan on the repo",
    "Fix services/llm.py timeout bug",
    "Deploy to production",
    "Create a new branch for the refactor",
    "Ship it",
])
def test_heuristic_agentic(msg):
    r = ig.classify_heuristic_sync(msg)
    assert r["tier"] == ig.TIER_AGENTIC, f"Expected agentic for {msg!r}: got {r}"
    # Imperatives should land >0.90.
    assert r["confidence"] >= 0.90, f"Agentic conf too low: {r}"


# ─── Empty / edge cases ───────────────────────────────────────────────

def test_heuristic_empty_message():
    r = ig.classify_heuristic_sync("")
    assert r["tier"] == ig.TIER_CASUAL


def test_heuristic_ambiguous_dialog_form_falls_through():
    """Mid-length statement with no clear action / query signal
    should land at <0.75 so the LLM fallback can resolve it."""
    r = ig.classify_heuristic_sync(
        "The bug we discussed earlier seems to still be there sometimes"
    )
    assert r["confidence"] < 0.75


# ─── Public async classify() ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_classify_short_circuits_high_confidence_heuristic(monkeypatch):
    """High-confidence heuristic results must NOT trigger LLM fallback."""
    calls = []
    async def _no_llm(*args, **kwargs):
        calls.append("called")
        return {"tier": ig.TIER_QUERY, "confidence": 0.99, "method": "llm",
                "signals": [], "reasoning": ""}
    monkeypatch.setattr(ig, "_classify_llm", _no_llm)
    r = await ig.classify("Fix the auth bug", db=None)
    assert r["tier"] == ig.TIER_AGENTIC
    assert calls == [], "LLM must not be called on high-confidence heuristic"


@pytest.mark.asyncio
async def test_classify_escalates_to_llm_on_low_confidence(monkeypatch):
    """Mid-confidence heuristic triggers the LLM fallback."""
    async def _stub_llm(*args, **kwargs):
        return {
            "tier":       ig.TIER_AGENTIC,
            "confidence": 0.92,
            "method":     "llm",
            "signals":    ["llm_classified"],
            "reasoning":  "LLM said agentic",
        }
    monkeypatch.setattr(ig, "_classify_llm", _stub_llm)
    r = await ig.classify(
        "The bug we discussed earlier — should I touch it now",
        db=None,
    )
    # LLM result preferred when its conf > heuristic conf.
    assert r["tier"] == ig.TIER_AGENTIC
    assert r["method"] == "llm"


@pytest.mark.asyncio
async def test_classify_ambiguous_returns_clarify_tier(monkeypatch):
    """When neither heuristic nor LLM reaches 0.72, return clarify."""
    async def _low_llm(*args, **kwargs):
        return {
            "tier":       ig.TIER_QUERY,
            "confidence": 0.55,
            "method":     "llm",
            "signals":    [],
            "reasoning":  "uncertain",
        }
    monkeypatch.setattr(ig, "_classify_llm", _low_llm)
    r = await ig.classify(
        "the thing we discussed earlier",
        db=None,
    )
    assert r["tier"] == ig.TIER_CLARIFY
    assert r["was_ambiguous"] is True
    assert r["clarify"], "Clarify probe must be present"


@pytest.mark.asyncio
async def test_classify_writes_log_to_mongo(monkeypatch):
    """Every classification logs to `intent_classifications`."""
    rows = []
    class _FakeCol:
        async def insert_one(self, doc):
            rows.append(doc)
    class _FakeDB:
        intent_classifications = _FakeCol()
    await ig.classify("Fix the bug", db=_FakeDB(), user_id="u1", project_id="p1")
    assert len(rows) == 1
    r = rows[0]
    assert r["tier"]            in ig.VALID_TIERS
    assert r["message_preview"] == "Fix the bug"
    assert r["user_id"]         == "u1"
    assert r["project_id"]      == "p1"
    assert "gateway_ms"         in r
    assert isinstance(r["was_ambiguous"], bool)


@pytest.mark.asyncio
async def test_classify_log_failure_does_not_raise(monkeypatch):
    """A broken Mongo write must NOT block the classification."""
    class _BrokenCol:
        async def insert_one(self, doc):
            raise RuntimeError("mongo down")
    class _DB:
        intent_classifications = _BrokenCol()
    r = await ig.classify("Hi", db=_DB())
    assert r["tier"] == ig.TIER_CASUAL


@pytest.mark.asyncio
async def test_llm_timeout_fallback(monkeypatch):
    """If the LLM classifier times out, we still return a sane tier."""
    async def _hang(*args, **kwargs):
        await asyncio.sleep(10)
        return ""
    # Bypass real LLM — monkeypatch call_llm via the module loader.
    from services import llm as _llm_mod
    monkeypatch.setattr(_llm_mod, "call_llm", _hang)
    # A mid-length ambiguous statement → goes through the LLM path.
    r = await ig.classify(
        "consider whether we should bump the timeout next week",
        db=None,
    )
    # We can't deterministically know the exact tier (depends on the
    # ambiguity threshold) but the call must return a valid dict.
    assert r["tier"] in ig.VALID_TIERS
    assert r["method"] in {"heuristic", "llm_timeout", "llm_error",
                           "llm_parse_fail", "llm_unavailable", "llm"}


# ─── Parse helper ─────────────────────────────────────────────────────

def test_parse_llm_json_handles_dirty_output():
    """LLM sometimes prepends text — extractor still pulls the JSON."""
    cases = [
        '{"tier":"casual","conf":0.9}',
        'Here is the JSON: {"tier":"agentic","conf":0.85}',
        '```json\n{"tier":"query","conf":0.8}\n```',
    ]
    for raw in cases:
        p = ig._parse_llm_json(raw)
        assert p is not None, f"Failed to parse: {raw!r}"
        assert "tier" in p


def test_parse_llm_json_returns_none_on_garbage():
    assert ig._parse_llm_json("no json here") is None
    assert ig._parse_llm_json("") is None


# ─── Public exports ──────────────────────────────────────────────────

def test_module_exports_constants():
    assert ig.TIER_CASUAL  == "casual"
    assert ig.TIER_QUERY   == "query"
    assert ig.TIER_AGENTIC == "agentic"
    assert ig.TIER_CLARIFY == "clarify"


# ─── Endpoint source-pattern contract ────────────────────────────────

def test_chat_stream_uses_intent_gateway():
    """chat.py must import the gateway and emit an `intent` SSE frame."""
    src = Path(__file__).resolve().parent.parent / "routers" / "chat.py"
    text = src.read_text()
    assert "from core.intent_gateway import classify" in text
    # Allow flexible whitespace ("type": "intent" / "type":   "intent").
    import re as _re
    assert _re.search(r'"type"\s*:\s*"intent"', text), \
        "chat_stream must emit an `intent` SSE frame"
    # Casual short-circuit must exist.
    assert 'if _tier == "casual":' in text or "_tier == 'casual'" in text


def test_classify_intent_endpoint_exposed():
    """The dedicated POST /chat/classify-intent endpoint exists."""
    src = Path(__file__).resolve().parent.parent / "routers" / "chat.py"
    text = src.read_text()
    assert '@router.post("/classify-intent")' in text
    assert "escalate_to_llm=False" in text, \
        "Live UI endpoint must skip LLM for instant response"
