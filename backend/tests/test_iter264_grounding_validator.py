"""
tests/test_iter264_grounding_validator.py — Iter 264 Fix D unit tests.

Deterministic, per-commit CI — no LLM calls. Covers:
  - extract_claims fixtures (paths, symbols, test files)
  - classify_claims two-level split: the 5 KNOWN fabricated names,
    real paths, user-typed paths, mixed cases
  - real path with NO turn context → UNVERIFIED (soft), never
    FABRICATED (acceptance criterion #4 — false-positive check)
  - _needs_tree conditional-injection logic (Fix B)
  - AUREM_CONTEXT wording change (Fix B)
  - prompt snapshot sha determinism (Fix C)
  - integration: real canonical index flags known fabricated names
"""
import pytest

from services.ora_chat.grounding_check import (
    extract_claims, classify_claims, run_post_response_check,
)
from services.ora_chat import codebase_index
from services.ora_chat.safety import AUREM_CONTEXT, assemble_system_prompt
from services.ora_chat.prompt_snapshot import sha256_of
from routers.ora_chat import _needs_tree


# The 5 fabrications the founder actually caught in production.
KNOWN_FABRICATED = [
    "test_iter212m201_tenant_leak.py",
    "test_iter212m55_domain_flow.py",
    "test_iter212m88_stripe_stress.py",
    "cache_orchestrator.py",
    "frontend/src/hooks/useDB.js",
]

REAL_PATHS = [
    "backend/routers/ora_chat.py",
    "backend/services/ora_chat/safety.py",
    "backend/services/ora_chat/codebase_index.py",
]

# Synthetic canonical set — deterministic, no filesystem dependency.
CANONICAL = {
    "paths": set(REAL_PATHS),
    "basenames": {p.rsplit("/", 1)[-1] for p in REAL_PATHS},
    "defs": {"assemble_system_prompt", "compact_tree"},
}


class TestClassifyClaims:
    def test_known_fabricated_names_hard_flagged(self):
        reply = ("Tests covering this: " + ", ".join(KNOWN_FABRICATED)
                 + " — sab green hain.")
        claims = extract_claims(reply)
        out = classify_claims(claims, canonical=CANONICAL,
                              user_query="kya gaps hain?")
        for name in KNOWN_FABRICATED:
            assert name in out["fabricated"], f"{name} not hard-flagged"

    def test_real_path_no_context_is_soft_only(self):
        # Acceptance criterion #4 — real path on a non-codebase turn:
        # NO user-facing (fabricated) flag, soft UNVERIFIED only.
        reply = "Routing logic `backend/routers/ora_chat.py` mein hai."
        out = classify_claims(extract_claims(reply), canonical=CANONICAL,
                              user_query="routing kaha hai?")
        assert "backend/routers/ora_chat.py" not in out["fabricated"]
        assert "backend/routers/ora_chat.py" in out["unverified"]

    def test_real_path_with_turn_context_fully_grounded(self):
        reply = "Dekho backend/routers/ora_chat.py mein."
        tree = "backend/routers/\n  ora_chat.py"
        out = classify_claims(
            extract_claims(reply), canonical=CANONICAL,
            user_query="", turn_contexts=[tree])
        # normalized form appears in the tree block via basename join
        assert "backend/routers/ora_chat.py" not in out["fabricated"]

    def test_user_typed_path_excluded(self):
        fake = "my_custom_notes.py"
        reply = f"Haan, {fake} repo mein nahi hai."
        out = classify_claims(
            extract_claims(reply), canonical=CANONICAL,
            user_query=f"kya {fake} exist karta hai?")
        assert fake not in out["fabricated"]
        assert fake not in out["unverified"]

    def test_mixed_real_and_fabricated(self):
        reply = ("backend/services/ora_chat/safety.py aur "
                 "cache_orchestrator.py dono handle karte hain.")
        out = classify_claims(extract_claims(reply), canonical=CANONICAL,
                              user_query="safety kaise hota hai?")
        assert "cache_orchestrator.py" in out["fabricated"]
        assert "backend/services/ora_chat/safety.py" not in out["fabricated"]

    def test_symbols_never_hard_flagged(self):
        reply = "Ye `totally_made_up_function()` se hota hai."
        out = classify_claims(extract_claims(reply), canonical=CANONICAL,
                              user_query="")
        assert out["fabricated"] == []
        assert "totally_made_up_function" in out["unverified"]

    def test_known_def_symbol_grounded(self):
        reply = "`assemble_system_prompt` layered prompt banata hai."
        out = classify_claims(extract_claims(reply), canonical=CANONICAL,
                              user_query="")
        assert "assemble_system_prompt" not in out["unverified"]

    def test_basename_of_real_file_not_fabricated(self):
        # Wrong-directory citation of a real basename stays soft.
        reply = "Check backend/wrongdir/safety.py"
        out = classify_claims(extract_claims(reply), canonical=CANONICAL,
                              user_query="")
        assert "backend/wrongdir/safety.py" not in out["fabricated"]


class TestNeedsTree:
    def test_label_triggers(self):
        assert _needs_tree("kuch bhi", ["NEEDS_CODEBASE"]) is True

    def test_slash_mention_triggers(self):
        assert _needs_tree("/read backend/main.py", []) is True
        assert _needs_tree("pehle /find scanners chalao", None) is True

    def test_casual_query_no_tree(self):
        assert _needs_tree("aaj ka weather kaisa hai?", []) is False
        assert _needs_tree("kya best build hai hmara system main",
                           ["NEEDS_WEB"]) is False


class TestPromptWording:
    def test_old_always_injected_wording_removed(self):
        assert "prepends a compact top-level file tree" not in AUREM_CONTEXT

    def test_fallback_line_present(self):
        assert "If no FILENAME INDEX is present in this turn" in AUREM_CONTEXT

    def test_conditional_clause_present(self):
        assert "only if one is present in THIS turn" in AUREM_CONTEXT

    def test_assemble_without_tree_has_no_index_block(self):
        p = assemble_system_prompt("be direct", codebase_tree=None)
        assert "FILENAME INDEX (paths only" not in p

    def test_assemble_with_tree_includes_block(self):
        p = assemble_system_prompt(
            "be direct", codebase_tree="═══ AUREM repo FILENAME INDEX ═══")
        assert "AUREM repo FILENAME INDEX" in p


class TestPromptSnapshot:
    def test_sha_deterministic(self):
        assert sha256_of("abc") == sha256_of("abc")
        assert sha256_of("abc") != sha256_of("abd")
        assert len(sha256_of("x")) == 64


class TestAgainstRealIndex:
    @pytest.mark.asyncio
    async def test_canonical_index_has_real_paths(self):
        canonical = await codebase_index.canonical_paths()
        assert len(canonical["paths"]) > 100
        for p in REAL_PATHS:
            assert p in canonical["paths"], f"{p} missing from index"

    @pytest.mark.asyncio
    async def test_known_fabrications_flagged_vs_real_index(self):
        canonical = await codebase_index.canonical_paths()
        reply = ", ".join(KNOWN_FABRICATED)
        out = classify_claims(extract_claims(reply), canonical=canonical,
                              user_query="gaps batao")
        for name in KNOWN_FABRICATED:
            assert name in out["fabricated"], f"{name} slipped through"

    @pytest.mark.asyncio
    async def test_hook_returns_shape_and_never_raises(self):
        r = await run_post_response_check(
            user_id="t", session_id="t", query="q",
            reply="cache_orchestrator.py sab kuch karta hai",
            route="general")
        assert set(r) == {"claims", "fabricated", "unverified", "logged"}
        assert "cache_orchestrator.py" in r["fabricated"]
