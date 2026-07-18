"""
tests/test_iter268_adversarial_review.py — Adversarial Review Pass.

Deterministic unit tests (no live LLM): trigger logic, reviewer JSON
parsing, the anti-hallucination quote guard, corrective prompt, budget
skip, and classifier/deep-trigger integration.
"""
from unittest.mock import AsyncMock, patch

import pytest

import services.ora_chat.adversarial_review as ar
from services.ora_chat.adversarial_review import (
    trigger_reason, corrective_prompt, _parse_flags, verify_quotes,
    run_review,
)
from services.ora_chat.deep_research import _LABELS, should_go_deep

DRAFT = ("Humara caching cache_orchestrator.py handle karta hai. "
         "Stripe cost $4,200 monthly hai. Baaki sab theek hai.")


class TestTrigger:
    def test_high_stakes_label_fires(self):
        assert trigger_reason(["HIGH_STAKES"], None) == "high_stakes_label"
        assert trigger_reason(["NEEDS_WEB", "HIGH_STAKES"],
                              {"unverified": []}) == "high_stakes_label"

    def test_grounding_unverified_escalates(self):
        assert trigger_reason([], {"unverified": ["a.py"]}) \
            == "grounding_unverified"

    def test_routine_turn_no_trigger(self):
        assert trigger_reason(["NEEDS_WEB"], {"unverified": [],
                                               "fabricated": []}) is None
        assert trigger_reason([], None) is None


class TestParseFlags:
    def test_pass_object(self):
        flags, ok = _parse_flags('{"result":"PASS"}')
        assert ok is True and flags == []

    def test_array_of_flags(self):
        raw = ('[{"quote":"Stripe cost $4,200 monthly hai.",'
               '"type":"UNVERIFIED","reason":"no source"}]')
        flags, ok = _parse_flags(raw)
        assert ok and len(flags) == 1
        assert flags[0]["type"] == "UNVERIFIED"

    def test_fenced_json(self):
        raw = ('```json\n[{"quote":"x","type":"FABRICATED",'
               '"reason":"r"}]\n```')
        flags, ok = _parse_flags(raw)
        assert ok and flags[0]["type"] == "FABRICATED"

    def test_invalid_type_dropped(self):
        raw = '[{"quote":"x","type":"BANANA","reason":"r"}]'
        flags, ok = _parse_flags(raw)
        assert ok and flags == []

    def test_garbage_not_ok(self):
        flags, ok = _parse_flags("the draft looks bad overall")
        assert ok is False and flags == []


class TestQuoteGuard:
    def test_fake_quote_dropped(self):
        flags = [
            {"quote": "Stripe cost $4,200 monthly hai.",
             "type": "UNVERIFIED", "reason": "r"},
            {"quote": "ORA ne kal server delete kiya tha.",  # fake
             "type": "FABRICATED", "reason": "r"},
        ]
        kept, dropped = verify_quotes(flags, DRAFT)
        assert len(kept) == 1 and len(dropped) == 1
        assert dropped[0]["quote"].startswith("ORA ne kal")

    def test_all_real_kept(self):
        flags = [{"quote": "Baaki sab theek hai.",
                  "type": "OVERSTATED", "reason": "r"}]
        kept, dropped = verify_quotes(flags, DRAFT)
        assert len(kept) == 1 and dropped == []


class TestCorrectivePrompt:
    def test_contains_quotes_and_instruction(self):
        p = corrective_prompt([{"quote": "cost $4,200", "type": "FABRICATED",
                                "reason": "r"}])
        assert '"cost $4,200"' in p
        assert "Do not defend them" in p


_BUDGET_OK = AsyncMock(return_value={"day_cap_usd": 2.5,
                                      "day_spent_usd": 0.10,
                                      "mode": "normal"})
_BUDGET_NEAR_CAP = AsyncMock(return_value={"day_cap_usd": 2.5,
                                            "day_spent_usd": 2.20,
                                            "mode": "warning"})


class TestRunReview:
    @pytest.mark.asyncio
    async def test_hard_soft_split_and_fake_quote_dropped(self):
        reviewer_json = (
            '[{"quote":"Humara caching cache_orchestrator.py handle karta hai.",'
            '"type":"FABRICATED","reason":"file not in context"},'
            '{"quote":"Stripe cost $4,200 monthly hai.",'
            '"type":"UNVERIFIED","reason":"no source"},'
            '{"quote":"YE QUOTE DRAFT MEIN NAHI HAI",'
            '"type":"FABRICATED","reason":"hallucinated"}]')
        logged = {}
        async def fake_log_err(user_id, session_id, dropped, raw):
            logged["dropped"] = dropped
        with patch.object(ar.cost_tracker, "budget_status", new=_BUDGET_OK), \
             patch.object(ar.cost_tracker, "log_call", new=AsyncMock(return_value=0.001)), \
             patch.object(ar, "_log_reviewer_errors", new=fake_log_err), \
             patch.object(ar, "one_shot",
                          new=AsyncMock(return_value=(reviewer_json,
                                                       {"input_tokens": 100,
                                                        "output_tokens": 50},
                                                       None))):
            out = await run_review(user_id="u", session_id="s",
                                   query="q", draft=DRAFT, context="ctx")
        assert out["skipped"] is None
        assert len(out["hard"]) == 1 and len(out["soft"]) == 1
        assert out["dropped"] == 1
        assert logged["dropped"][0]["quote"] == "YE QUOTE DRAFT MEIN NAHI HAI"
        assert out["passed"] is False

    @pytest.mark.asyncio
    async def test_budget_skip_no_llm_call(self):
        one = AsyncMock()
        with patch.object(ar.cost_tracker, "budget_status", new=_BUDGET_NEAR_CAP), \
             patch.object(ar, "one_shot", new=one):
            out = await run_review(user_id="u", session_id="s",
                                   query="q", draft=DRAFT, context="ctx")
        assert out["skipped"] == "review_skipped_budget"
        one.assert_not_called()

    @pytest.mark.asyncio
    async def test_pass_result(self):
        with patch.object(ar.cost_tracker, "budget_status", new=_BUDGET_OK), \
             patch.object(ar.cost_tracker, "log_call", new=AsyncMock(return_value=0.001)), \
             patch.object(ar, "one_shot",
                          new=AsyncMock(return_value=('{"result":"PASS"}',
                                                       {"input_tokens": 10,
                                                        "output_tokens": 5},
                                                       None))):
            out = await run_review(user_id="u", session_id="s",
                                   query="q", draft=DRAFT, context="ctx")
        assert out["passed"] is True and out["flags"] == []

    @pytest.mark.asyncio
    async def test_reviewer_error_skips_gracefully(self):
        with patch.object(ar.cost_tracker, "budget_status", new=_BUDGET_OK), \
             patch.object(ar, "one_shot",
                          new=AsyncMock(return_value=("", {}, "http_500"))):
            out = await run_review(user_id="u", session_id="s",
                                   query="q", draft=DRAFT, context="ctx")
        assert out["skipped"].startswith("reviewer_error")

    @pytest.mark.asyncio
    async def test_empty_draft_skipped(self):
        out = await run_review(user_id="u", session_id="s",
                               query="q", draft="  ", context="ctx")
        assert out["skipped"] == "empty_draft"


class TestClassifierIntegration:
    def test_high_stakes_in_labels(self):
        assert "HIGH_STAKES" in _LABELS

    @pytest.mark.asyncio
    async def test_high_stakes_never_triggers_deep(self):
        assert await should_go_deep(["HIGH_STAKES"]) is False
        assert await should_go_deep(["HIGH_STAKES", "NEEDS_WEB"]) is False

    @pytest.mark.asyncio
    async def test_deep_trigger_unchanged_otherwise(self):
        assert await should_go_deep(["NEEDS_GITHUB"]) is True
        assert await should_go_deep(["NEEDS_WEB", "NEEDS_NEWS"]) is True
        assert await should_go_deep(["NEEDS_WEB"]) is False
