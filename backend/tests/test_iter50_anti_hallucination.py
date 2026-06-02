"""tests/test_iter50_anti_hallucination.py
=============================================
Iter 50 — Surgical anti-hallucination guards.

Real bugs hit by user in production:
  - "hi aurem" with stale F12 errors → fabricated 'call_llm() unexpected kwarg' fix
  - F12 payload with only aborted requests → Mode D fishing for non-existent files
  - Mode D LLM inventing file paths like src/api/endpoints.js

These tests lock the new guards in place.
"""
from __future__ import annotations
import pytest


# ─── Greeting wins over stale F12 noise ─────────────────────────────────

class TestGreetingClassification:
    def test_hi_aurem_with_stale_aborted_errors(self):
        from routers.chat import classify_intent
        # Browser captured an aborted request from earlier — user just
        # said hi. Must NOT route to D.
        assert classify_intent(
            "hi aurem",
            {"console_errors": [{"message": "aborted"}]},
        ) == "A"

    def test_hi_with_status_0_noise(self):
        from routers.chat import classify_intent
        assert classify_intent(
            "hi",
            {"network_errors": [{"status": 0}]},
        ) == "A"

    def test_thanks_with_noise_stays_A(self):
        from routers.chat import classify_intent
        assert classify_intent(
            "thanks!",
            {"network_errors": [{"status": 200, "url": "/api/x"}]},
        ) == "A"

    def test_greeting_wins_even_over_real_500(self):
        # User just opened the app — they haven't asked for diagnosis yet.
        from routers.chat import classify_intent
        assert classify_intent(
            "hello",
            {"network_errors": [{"status": 500, "url": "/api/x"}]},
        ) == "A"

    def test_non_greeting_with_real_500_routes_to_D(self):
        from routers.chat import classify_intent
        assert classify_intent(
            "why does this fail",
            {"network_errors": [{"status": 500, "url": "/api/x"}]},
        ) == "D"


# ─── F12 signal guard ───────────────────────────────────────────────────

class TestF12SignalGuard:
    def test_aborted_message_is_not_signal(self):
        from routers.chat import _f12_has_real_signal
        assert _f12_has_real_signal(
            {"console_errors": [{"message": "aborted"}]}
        ) is False

    def test_status_0_is_not_signal(self):
        from routers.chat import _f12_has_real_signal
        assert _f12_has_real_signal(
            {"network_errors": [{"status": 0, "url": "/api/x"}]}
        ) is False

    def test_status_200_is_not_signal(self):
        from routers.chat import _f12_has_real_signal
        assert _f12_has_real_signal(
            {"network_errors": [{"status": 200, "url": "/api/x"}]}
        ) is False

    def test_real_500_is_signal(self):
        from routers.chat import _f12_has_real_signal
        assert _f12_has_real_signal(
            {"network_errors": [{"status": 500, "url": "/api/x"}]}
        ) is True

    def test_404_is_signal(self):
        from routers.chat import _f12_has_real_signal
        assert _f12_has_real_signal(
            {"network_errors": [{"status": 404, "url": "/api/y"}]}
        ) is True

    def test_stack_trace_is_signal(self):
        from routers.chat import _f12_has_real_signal
        assert _f12_has_real_signal(
            {"stack_traces": ["TypeError: cannot read at App.jsx:1"]}
        ) is True

    def test_real_console_error_is_signal(self):
        from routers.chat import _f12_has_real_signal
        assert _f12_has_real_signal(
            {"console_errors": [
                {"message": "Cannot read property 'foo' of undefined"},
            ]}
        ) is True


# ─── Diagnosis system prompt is anti-hallucination hardened ─────────────

class TestDiagnosisPromptHardening:
    def test_diagnosis_prompt_has_anti_hallucination_section(self):
        from services.mode_d_debugger import DIAGNOSIS_SYSTEM
        assert "ANTI-HALLUCINATION" in DIAGNOSIS_SYSTEM
        assert "DO NOT invent file paths" in DIAGNOSIS_SYSTEM

    def test_diagnosis_prompt_has_empty_context_fallback(self):
        from services.mode_d_debugger import DIAGNOSIS_SYSTEM
        assert "insufficient signal" in DIAGNOSIS_SYSTEM
