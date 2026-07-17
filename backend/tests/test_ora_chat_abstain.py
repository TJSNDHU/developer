"""
tests/test_ora_chat_abstain.py — Iter 212m-253

Regression coverage for the abstain-on-weak-signal fix.

Bug it prevents (real founder-reported):
  ORA was citing `test_iter212m201_tenant_leak.py` (a nonexistent file)
  as evidence for a "multi-tenant DB isolation" gap that also doesn't
  exist in our architecture. Root cause: when BM25 returned no
  confident matches, `_fetch_codebase` silently dropped the tool
  from the fan-out — the synth model then hallucinated file names
  from the compact tree it always sees in the system prompt.

Fix (this iter):
  1. `bm25_relevant_files` — default min_score raised 3.5 → 8.0
     (env-tunable via ORA_CODEBASE_MIN_SCORE).
  2. `_fetch_codebase` — when BM25 returns [], return an EXPLICIT
     `{ok: True, abstain: True, results: []}` marker.
  3. Orchestrator — synth prompt gets a "CRITICAL — do not cite
     specific files" rule injected when the abstain marker is present.
  4. Safety layer — new Anti-fabrication rule in AUREM_CONTEXT that
     applies to ALL responses, not just deep-research.
"""
from __future__ import annotations

import os
from unittest.mock import patch, AsyncMock

import pytest

from services.ora_chat import codebase_index as cb
from services.ora_chat import deep_research as dr
from services.ora_chat.safety import AUREM_CONTEXT


class TestScoreThreshold:
    """`min_score` default is 8.0, env-tunable via ORA_CODEBASE_MIN_SCORE."""

    @pytest.mark.asyncio
    async def test_default_threshold_is_8(self, monkeypatch):
        monkeypatch.delenv("ORA_CODEBASE_MIN_SCORE", raising=False)
        await cb.build_index(force=True)
        # "best build system aurem" — earlier version returned 5 hits
        # with top score ~13.5 (noise). With 8.0 threshold, most or
        # all of those junk hits are gone.
        hits = await cb.bm25_relevant_files("best build system aurem", top_k=5)
        for h in hits:
            assert h["score"] >= 8.0, (
                f"Threshold not enforced — got score {h['score']} for {h['path']}"
            )

    @pytest.mark.asyncio
    async def test_env_var_overrides(self, monkeypatch):
        monkeypatch.setenv("ORA_CODEBASE_MIN_SCORE", "20.0")
        await cb.build_index(force=True)
        hits = await cb.bm25_relevant_files("deep research classifier",
                                             top_k=5)
        for h in hits:
            assert h["score"] >= 20.0

    @pytest.mark.asyncio
    async def test_env_var_invalid_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("ORA_CODEBASE_MIN_SCORE", "not-a-number")
        await cb.build_index(force=True)
        # Should not crash — falls back to 8.0
        hits = await cb.bm25_relevant_files("stripe billing subscription",
                                             top_k=5)
        # Just verify it returned without crashing
        assert isinstance(hits, list)


class TestFetchCodebaseAbstain:
    """`_fetch_codebase` returns an explicit abstain marker when BM25
    returns [] (was: silent `ok: False, error: no_matches`)."""

    @pytest.mark.asyncio
    async def test_abstain_when_bm25_empty(self):
        with patch(
            "services.ora_chat.deep_research.codebase_index.bm25_relevant_files",
            new=AsyncMock(return_value=[]),
        ):
            out = await dr._fetch_codebase("meaningless meta query")
        assert out["tool"] == "codebase"
        assert out["ok"] is True           # NOT False — abstain is ok, just no data
        assert out["abstain"] is True
        assert out["results"] == []
        assert "reason" in out

    @pytest.mark.asyncio
    async def test_no_abstain_when_bm25_finds_hits(self):
        real_hits = [{"path": "backend/services/foo.py",
                       "score": 22.0, "head_excerpt": "def foo(): ..."}]
        with patch(
            "services.ora_chat.deep_research.codebase_index.bm25_relevant_files",
            new=AsyncMock(return_value=real_hits),
        ):
            out = await dr._fetch_codebase("foo function")
        assert out["ok"] is True
        assert out.get("abstain") is not True
        assert out["results"] == real_hits


class TestOrchestratorAbstainInjection:
    """When the codebase tool abstains, the synth prompt MUST include
    an explicit no-cite rule."""

    @pytest.mark.asyncio
    async def test_synth_prompt_gets_no_cite_rule_on_abstain(self):
        captured_prompts: list[str] = []

        async def capture_one_shot(model, messages, **kwargs):
            captured_prompts.append(messages[1]["content"])
            return ("some synth response", {"input_tokens": 10, "output_tokens": 5}, None)

        async def fake_codebase(q):
            # Simulate BM25 returning nothing → abstain
            return {"tool": "codebase", "ok": True, "abstain": True,
                    "results": [], "reason": "no_confident_match_above_threshold"}

        with patch("services.ora_chat.deep_research._fetch_codebase",
                   side_effect=fake_codebase), \
             patch("services.ora_chat.deep_research.cost_tracker.budget_status",
                   new=AsyncMock(return_value={"day_cap_usd": 2.5,
                                                "day_spent_usd": 0.1,
                                                "mode": "normal"})), \
             patch("services.ora_chat.deep_research.one_shot",
                   side_effect=capture_one_shot):
            out = await dr.orchestrate("kya humne X banaya",
                                        ["NEEDS_CODEBASE"])

        assert out["ok"] is True
        assert len(captured_prompts) == 1
        prompt = captured_prompts[0]
        # The abstain marker must appear in the untrusted-wrapped
        # section OR the CRITICAL rule.
        assert "codebase_abstain" in prompt or "MUST NOT" in prompt
        # The explicit no-cite rule must be present.
        assert "cite specific file paths" in prompt
        # And the fallback advice must be present.
        assert "confident code match" in prompt

    @pytest.mark.asyncio
    async def test_synth_prompt_no_abstain_rule_when_hits_present(self):
        captured_prompts: list[str] = []

        async def capture_one_shot(model, messages, **kwargs):
            captured_prompts.append(messages[1]["content"])
            return ("synth", {"input_tokens": 10, "output_tokens": 5}, None)

        async def fake_codebase(q):
            return {"tool": "codebase", "ok": True,
                    "results": [{"path": "backend/services/foo.py",
                                  "score": 22.0, "head_excerpt": "x"}]}

        with patch("services.ora_chat.deep_research._fetch_codebase",
                   side_effect=fake_codebase), \
             patch("services.ora_chat.deep_research.cost_tracker.budget_status",
                   new=AsyncMock(return_value={"day_cap_usd": 2.5,
                                                "day_spent_usd": 0.1,
                                                "mode": "normal"})), \
             patch("services.ora_chat.deep_research.one_shot",
                   side_effect=capture_one_shot):
            await dr.orchestrate("how does foo work",
                                  ["NEEDS_CODEBASE"])

        prompt = captured_prompts[0]
        # Normal citation rule stays; no abstain rule.
        assert "(source: codebase)" in prompt
        assert "CRITICAL — codebase abstain" not in prompt


class TestGlobalAntiFabricationRule:
    """The Anti-fabrication rule in AUREM_CONTEXT applies to EVERY
    response, not just deep-research. Static check on the prompt text."""

    def test_anti_fabrication_rule_present(self):
        assert "Anti-fabrication rule" in AUREM_CONTEXT
        # Must be a hard rule, not a soft suggestion.
        assert "MUST OBEY" in AUREM_CONTEXT or "NEVER" in AUREM_CONTEXT

    def test_names_the_specific_prior_hallucination(self):
        # Cite the known past failure so the model has a concrete
        # example of what NOT to do.
        assert "test_iter212m201_tenant_leak.py" in AUREM_CONTEXT

    def test_provides_fallback_response_template(self):
        # The rule must give the model an escape hatch — an honest
        # sentence to say when it doesn't know.
        assert "confident code match" in AUREM_CONTEXT

    def test_filename_neq_content_rule_present(self):
        # Iter 212m-263 — must explicitly teach the model that seeing
        # a filename in the index ≠ knowing its content.
        assert "Filename" in AUREM_CONTEXT and "content" in AUREM_CONTEXT
        assert "PATHS only" in AUREM_CONTEXT or "index lists PATHS" in AUREM_CONTEXT

    def test_pushback_retraction_rule_present(self):
        # Iter 212m-263 — when user challenges, model must retract,
        # not double down. Specific challenge phrases named.
        assert "RETRACT" in AUREM_CONTEXT
        # Must warn against inventing more filenames to defend
        assert "double down" in AUREM_CONTEXT or "invent additional" in AUREM_CONTEXT

    def test_names_the_iter263_hallucination_cluster(self):
        # The new batch of caught hallucinations from the same user
        # feedback loop — locks in the anti-recurrence expectation.
        for name in ("test_iter212m55_domain_flow",
                      "test_iter212m88_stripe_stress",
                      "cache_orchestrator.py",
                      "frontend/src/hooks/useDB.js"):
            assert name in AUREM_CONTEXT
