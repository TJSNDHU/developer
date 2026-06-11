"""
Iter 124e — Tests for the persona drift auto-eval guard rail.

Two layers:
  1. _verdict() unit tests — pure logic, no LLM. Always runs in CI.
  2. End-to-end LLM eval — only when EMERGENT_LLM_KEY or
     OPENROUTER_API_KEY is set, gated by RUN_E2E_PERSONA_EVAL=1.
"""
from __future__ import annotations

import os
import sys

import pytest

# Make scripts/ importable
HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

from persona_drift_eval import _verdict, FORBIDDEN  # noqa: E402


# ── Pure-logic tests (always run) ───────────────────────────────────────

def test_verdict_passes_when_tools_were_called():
    """If the model used tools (read-first), forbidden-phrase scan is
    the only gate."""
    reply = "Let me check your routers…"
    ok, fails = _verdict(reply, tool_calls_made=2)
    assert ok and not fails


def test_verdict_passes_with_numbered_list_no_tools():
    """If no tools used but a full numbered answer was given, pass."""
    reply = "\n".join(f"{i}. router_{i}.py — does thing" for i in range(1, 12))
    ok, fails = _verdict(reply, tool_calls_made=0)
    assert ok, fails


def test_verdict_fails_when_too_few_items_and_no_tools():
    reply = "1. admin.py — admin\n2. auth.py — auth"
    ok, fails = _verdict(reply, tool_calls_made=0)
    assert not ok
    assert any("numbered items" in f for f in fails)


@pytest.mark.parametrize("phrase", [
    "Would you like me to list them?",
    "Shall I read your routers?",
    "Want me to detail each?",
    "Should I check the dependencies?",
])
def test_verdict_fails_on_forbidden_permission_openers(phrase):
    # Even with a perfect numbered list, a permission-asking phrase
    # should fail the eval.
    body = "\n".join(f"{i}. router_{i}.py" for i in range(1, 12))
    reply = f"{body}\n\n{phrase}"
    ok, fails = _verdict(reply, tool_calls_made=0)
    assert not ok
    assert any("forbidden phrase" in f for f in fails)


def test_forbidden_list_is_non_empty_and_lowercased():
    # Sanity — drift in this list is itself a drift.
    assert len(FORBIDDEN) >= 4
    for pat in FORBIDDEN:
        assert pat == pat.lower()


# ── E2E test (only when explicitly enabled with a real LLM key) ────────

@pytest.mark.skipif(
    os.getenv("RUN_E2E_PERSONA_EVAL", "0") != "1"
    or not (os.getenv("EMERGENT_LLM_KEY") or os.getenv("OPENROUTER_API_KEY")),
    reason="E2E persona eval disabled — set RUN_E2E_PERSONA_EVAL=1 + LLM key",
)
def test_persona_drift_eval_e2e():
    """Real LLM run. Mirrors what the deploy script does."""
    from persona_drift_eval import main
    assert main() == 0
