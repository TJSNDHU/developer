"""
test_iter83_handoff_guard.py — Defense-in-depth for the Ship via CTO
fence.

User-reported bug:
  A pure search/advice reply ("search github repo name browser use…")
  came back with a ```aurem-handoff fence containing follow-up
  *reading* instructions ("1. Read X.md  2. Inspect Y.jsx  …") and
  the UI rendered a Ship-via-CTO button below it. Clicking the button
  would have sent reading instructions to the worker as if they were
  a code-ship brief — wrong.

  Root causes:
    1. System prompt said "don't emit fence for explanations" but did
       not call out reading/follow-up instructions explicitly.
    2. UI guard checked only that the brief was ≥ 40 chars; it did
       no content validation.

  Iter 83 hardening:
    • Orchestrator system prompt now lists absolute negatives (Read,
      Inspect, Check, Review, "Would you like me to", any "?", etc.).
    • MessageBubble.extractHandoffBrief rejects briefs whose lines are
      all read-only verbs / questions, and requires at least one
      mutation verb (create, add, fix, write, edit, etc.).
    • Together: even if the model leaks a non-mutation fence, the UI
      will refuse to render the Ship button.

This test locks the system-prompt update and the UI guard contract.
The actual JS regex behaviour is covered indirectly by exercising
the patterns the guard depends on.
"""
from __future__ import annotations

import os
import re

BASE = os.path.join(os.path.dirname(__file__), "..", "..")


def _read(rel: str) -> str:
    with open(os.path.join(BASE, rel), encoding="utf-8") as fh:
        return fh.read()


# ── 1. Orchestrator system prompt hardening ───────────────────────────

def test_system_prompt_lists_absolute_negatives_for_handoff_fence():
    """The orchestrator system prompt must explicitly forbid each
    failure mode we've actually observed in the wild."""
    src = _read("backend/services/orchestrator.py")
    # Strip Python string-literal joins so a phrase split across two
    # consecutive "" literals still matches.
    joined = re.sub(r'"\s*\n\s*"', "", src)
    for must in (
        "ABSOLUTE NEGATIVES",
        "'Read X'",
        "'Inspect Y'",
        "'Check Z'",
        "'Review N'",
        "'Would you like me to'",
        "any line ending in '?'",
        "MUST contain at least one mutation verb",
    ):
        assert must in joined, (
            f"orchestrator system prompt missing required guard line: {must!r}"
        )


# ── 2. UI guard — content validation, not just length ─────────────────

def test_messagebubble_guard_requires_mutation_verb():
    src = _read("frontend/src/components/MessageBubble.jsx")
    assert "MUTATION_VERBS" in src
    assert "READ_ONLY_LINE" in src
    assert "QUESTION_LINE" in src
    # Must explicitly reject read-only briefs.
    assert "allReadOnly" in src
    # Must require at least one mutation verb to render Ship button.
    assert "MUTATION_VERBS.test(brief)" in src
    # Negative cases still gated — comments document each.
    assert "Iter 83 hardening" in src


def test_messagebubble_guard_regex_actually_rejects_offending_briefs():
    """Pull the regex out of the JS source and prove it rejects the
    real-world failing brief the user reported. Catches a regression
    where someone might delete or weaken the rules."""
    src = _read("frontend/src/components/MessageBubble.jsx")
    # Grab the MUTATION_VERBS line.
    m = re.search(r"const MUTATION_VERBS = (/[^\n]+/i);", src)
    assert m, "MUTATION_VERBS const not found in MessageBubble"
    verbs_pattern = m.group(1)
    # Convert JS regex literal to Python — strip leading / and trailing /i
    verbs_body = verbs_pattern.strip("/")
    verbs_body = verbs_body.rsplit("/", 1)[0]
    verbs_re = re.compile(verbs_body, re.IGNORECASE)

    # The user's actual leaked brief had ONLY "Read / Inspect / Check /
    # Review" verbs — must NOT match a mutation verb.
    bad_brief = (
        "1. Read .agent/skills/skills/skyvern-browser-automation/SKILL.md\n"
        "2. Inspect frontend/src/platform/AdminShell.jsx\n"
        "3. Check backend/services/dev_cto_chat.py\n"
        "4. Review memory/tier1/WATCHDOG_MODE.md"
    )
    assert not verbs_re.search(bad_brief), (
        "MUTATION_VERBS regex should NOT match the user's pure-read brief"
    )

    # A legit ship brief MUST match.
    good_brief = (
        "Create a new file backend/services/foo.py exposing get_foo(). "
        "Edit backend/routers/api.py to import and wire it under /foo. "
        "Add tests in backend/tests/test_foo.py."
    )
    assert verbs_re.search(good_brief), (
        "MUTATION_VERBS regex must match a real mutation brief"
    )


# ── 3. Honest no-mock audit (light) ───────────────────────────────────

def test_no_mocked_skill_returns_in_local_tools_or_web_skills():
    """The user is allergic to mocked returns. Our two skill registries
    (local_tools.py + web_skills.py) must never short-circuit a tool
    call with 'mock'/'fake'/'simulate'/'placeholder result'."""
    for rel in ("backend/services/local_tools.py",
                "backend/services/web_skills.py"):
        src = _read(rel)
        for bad in ("return mock", "return fake", "fake_result",
                    "mock_result", "simulate_response"):
            assert bad.lower() not in src.lower(), \
                f"{rel} appears to mock a tool result: {bad!r}"
