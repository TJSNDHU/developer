"""
tests/test_ora_chat_hallucination_loop.py — Iter 212m-254/255/256

3-step hallucination self-improvement loop:
  A. grounding_check: extract specific claims + flag ungrounded ones
  B. hallucination_classifier: batch pattern detection + candidate rules
  C. Admin approval endpoints — human-in-the-loop promotion

These tests use static assertions + mocked DB/LLM so they're fast (<1s)
and don't need a live Mongo/OpenRouter connection.
"""
from __future__ import annotations

import json
from unittest.mock import patch, AsyncMock, MagicMock

import pytest

from services.ora_chat import grounding_check as gc
from services.ora_chat import hallucination_classifier as hc


# ══════════════════════════════════════════════════════════════════
# STEP A — grounding_check.extract_claims
# ══════════════════════════════════════════════════════════════════
class TestExtractClaims:
    def test_extracts_test_iter_file(self):
        text = "See `test_iter212m201_tenant_leak.py` for the audit."
        c = gc.extract_claims(text)
        assert "test_iter212m201_tenant_leak.py" in c

    def test_extracts_py_and_jsx_paths(self):
        text = "The router at backend/routers/foo.py calls Dashboard.jsx."
        c = gc.extract_claims(text)
        assert "backend/routers/foo.py" in c
        assert "Dashboard.jsx" in c

    def test_extracts_backtick_symbols(self):
        text = "The `assemble_system_prompt` helper builds it."
        c = gc.extract_claims(text)
        assert "assemble_system_prompt" in c

    def test_ignores_generic_words(self):
        text = "The system is Great and the API is JSON."
        c = gc.extract_claims(text)
        # No file paths, no interesting symbols
        assert c == []

    def test_dedupes_repeats(self):
        text = "See foo.py. Really foo.py. Definitely foo.py."
        c = gc.extract_claims(text)
        assert c.count("foo.py") == 1

    def test_ignores_python_keywords_and_uppercase_acronyms(self):
        text = "Use `None` or `True`. Also `HTTP` and `URL`."
        c = gc.extract_claims(text)
        assert "None" not in c
        assert "True" not in c
        assert "HTTP" not in c


class TestFindUngrounded:
    def test_flags_ungrounded_files(self):
        claims = ["test_iter212m201_tenant_leak.py", "foo.py"]
        contexts = ["backend/routers/foo.py exists in the tree",
                     "system_highlights: no leak test"]
        ung = gc.find_ungrounded(claims, contexts)
        assert "test_iter212m201_tenant_leak.py" in ung
        assert "foo.py" not in ung  # grounded via contexts[0]

    def test_all_grounded_returns_empty(self):
        assert gc.find_ungrounded(
            ["assemble_system_prompt"],
            ["def assemble_system_prompt(): ..."],
        ) == []

    def test_empty_input_safe(self):
        assert gc.find_ungrounded([], ["anything"]) == []


class TestCheckAndLog:
    @pytest.mark.asyncio
    async def test_no_claims_no_log(self):
        with patch("services.ora_chat.grounding_check.log_hallucination",
                   new=AsyncMock()) as mock_log:
            r = await gc.check_and_log(
                user_id="u1", session_id="s1",
                query="q", reply="Hello, no specific claims here.",
                route="general",
            )
        assert r["logged"] is False
        mock_log.assert_not_called()

    @pytest.mark.asyncio
    async def test_grounded_claims_no_log(self):
        with patch("services.ora_chat.grounding_check.log_hallucination",
                   new=AsyncMock()) as mock_log:
            r = await gc.check_and_log(
                user_id="u1", session_id="s1",
                query="q", reply="See `foo` in bar.py.",
                route="general",
                codebase_tree="bar.py — has foo function",
            )
        assert r["logged"] is False
        mock_log.assert_not_called()

    @pytest.mark.asyncio
    async def test_ungrounded_triggers_log(self):
        with patch("services.ora_chat.grounding_check.log_hallucination",
                   new=AsyncMock()) as mock_log:
            r = await gc.check_and_log(
                user_id="u1", session_id="s1",
                query="q",
                reply="See test_iter212m201_tenant_leak.py — fake!",
                route="deep",
                codebase_tree="only real files here",
            )
        assert r["logged"] is True
        assert "test_iter212m201_tenant_leak.py" in r["ungrounded"]
        mock_log.assert_called_once()


# ══════════════════════════════════════════════════════════════════
# STEP B — hallucination_classifier
# ══════════════════════════════════════════════════════════════════
class TestClassifierParsing:
    def test_parses_bare_json_array(self):
        text = '[{"pattern_name": "fake-test", "example_cases": [1, 2, 3]}]'
        r = hc._parse_patterns(text)
        assert r[0]["pattern_name"] == "fake-test"

    def test_parses_code_fence(self):
        text = '```json\n[{"pattern_name": "x", "example_cases": [1]}]\n```'
        r = hc._parse_patterns(text)
        assert len(r) == 1

    def test_returns_empty_on_garbage(self):
        assert hc._parse_patterns("not json at all") == []

    def test_returns_empty_on_missing_brackets(self):
        assert hc._parse_patterns("") == []


class TestClassifyBatch:
    @pytest.mark.asyncio
    async def test_skips_when_below_trigger(self):
        with patch("services.ora_chat.hallucination_classifier.unreviewed_count",
                   new=AsyncMock(return_value=5)):
            r = await hc.classify_batch(force=False)
        assert r["skipped"] is True
        assert r["unreviewed"] == 5


class TestHumanInTheLoop:
    """Human-in-the-loop is the whole point of the approval design.
    These are static checks that the code enforces it."""

    def test_approve_pattern_signature_requires_admin_email(self):
        import inspect
        sig = inspect.signature(hc.approve_pattern)
        params = list(sig.parameters.keys())
        assert "admin_email" in params
        assert "user_id" in params
        assert "slug" in params

    def test_classifier_never_calls_house_rules_update_at_module_top(self):
        # The classifier module MUST not import + call house_rules.update
        # at module scope — only inside approve_pattern (after human
        # gate). Static source check on IMPORT/CALL statements only.
        src = open("/app/backend/services/ora_chat/hallucination_classifier.py").read()
        approve_idx = src.find("async def approve_pattern")
        # Find the actual `from services.ora_chat import house_rules`
        # statement or `ora_house_rules.update(` call.
        import_stmt = "from services.ora_chat import house_rules"
        update_call = "ora_house_rules.update("
        for needle in (import_stmt, update_call):
            idx = src.find(needle)
            assert idx == -1 or idx > approve_idx, (
                f"'{needle}' appears before approve_pattern — "
                "moving it to module scope would risk auto-application."
            )
