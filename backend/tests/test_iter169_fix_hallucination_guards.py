"""Iter 169 — guardrails against 'fix' hallucinations (PARTIAL).

  1. ~~Short 'fix'/'ship'/'do it' messages with NO prior `aurem-handoff`~~
     ~~fence in the recent history must trigger a clarification reply~~
     REMOVED in Iter 212m-26 along with the entire `_maybe_clarify_short_fix`
     and ship-shortcut auto-trigger path. Short replies now fall into
     the normal orchestrator and get a conversational answer.
  2. The CTO persona must contain Rule 8 — ANALYSIS → SPEC CONTRACT —
     forbidding analysis turns that end without a concrete spec.
  3. The orchestrator budget-hit message must surface real files read
     and a copy-pasteable next-prompt example (no generic 4-pillar
     suggestion that user can't act on).
"""
import re
from pathlib import Path


CHAT_PATH = Path(__file__).parent.parent / "routers" / "chat.py"
ORCH_PATH = Path(__file__).parent.parent / "services" / "orchestrator.py"


# ── Fix 2 ─────────────────────────────────────────────────────────────

def test_persona_has_rule_8_analysis_spec_contract():
    src = ORCH_PATH.read_text()
    assert "ANALYSIS → SPEC CONTRACT" in src, (
        "Rule 8 (ANALYSIS → SPEC CONTRACT) missing from CTO persona"
    )
    # Must reference the canonical mechanism — the aurem-handoff fence.
    rule_block = src[src.index("ANALYSIS → SPEC CONTRACT"):]
    assert "aurem-handoff" in rule_block[:1200]
    assert "Which file" in rule_block[:1200] or "Which file" in src
    # Must explicitly forbid speculating about unread files.
    assert "not actually called read_repo_file" in rule_block[:1200] \
        or "speculating about file contents" in rule_block.lower()[:1200]


def test_rule_8_lives_in_core_layer():
    """Rule 8 sits inside the TOP-OF-MIND HARD RULES block which is
    pinned to L1 core — so it's always loaded, even in execute path."""
    src = ORCH_PATH.read_text()
    rules_start = src.index("TOP-OF-MIND HARD RULES")
    # Next section heading after the rules block:
    next_heading = src.index("# MODE DETECTION", rules_start)
    rules_block = src[rules_start:next_heading]
    assert "ANALYSIS → SPEC CONTRACT" in rules_block, (
        "Rule 8 must be inside the TOP-OF-MIND HARD RULES block (L1 core)"
    )


# ── Fix 3 ─────────────────────────────────────────────────────────────

def test_budget_hit_message_is_actionable():
    src = ORCH_PATH.read_text()
    # Old vague language gone:
    assert "4-pillar sweep" not in src
    assert "the scope of your question is broader" not in src
    # New actionable language present:
    assert "ran out of time" in src
    assert "narrow the ask to one file" in src
    assert "ship-ready" in src


def test_budget_hit_example_uses_real_seen_path():
    """The example next-prompt should be generated from `seen_paths[0]`
    when available, so the user can copy-paste a file they actually
    saw the agent read this turn."""
    src = ORCH_PATH.read_text()
    # The string interpolation referencing seen_paths must exist.
    assert "seen_paths[0].split" in src
    assert "example_file" in src
