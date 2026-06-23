"""
test_iter212f_pat_dedupe_and_debug_routing.py

Iter 212f — Two core fixes:

  1) PatRequiredCTA in chat must respect the active project's
     `has_pat` flag. If the project already has a saved PAT, the
     inline "Add PAT" CTA never renders — regardless of how many
     PAT-related signals the LLM's answer contains. This kills the
     "add PAT twice" UX bug where users were prompted to re-paste
     the same token every time the LLM answered a GitHub question.

  2) Bare `debug` / `diagnose` / `investigate` no longer fire Mode D.
     `debug <target>` (e.g. "debug full repo", "investigate auth flow")
     now routes to Mode C (agentic — reads code, can call tools)
     instead of Mode D (which would bail with the "insufficient signal
     to diagnose" template).
"""
from __future__ import annotations

from pathlib import Path

import pytest


# ── 1) PatRequiredCTA respects has_pat ────────────────────────────

PAT_CTA = Path("/app/frontend/src/components/PatRequiredCTA.jsx").read_text(encoding="utf-8")


def test_pat_cta_imports_active_project_hook():
    assert "useActiveProject" in PAT_CTA


def test_pat_cta_short_circuits_when_project_has_pat():
    """If `activeProject.has_pat` is truthy, the component must
    return null BEFORE the regex `needsPat(text)` check runs."""
    # The early-return must reference has_pat directly.
    assert "activeProject?.has_pat" in PAT_CTA, (
        "PatRequiredCTA must consult activeProject.has_pat to avoid "
        "double-prompting users who already saved a PAT."
    )
    # Order matters — the early-return must precede the needsPat check.
    has_pat_idx  = PAT_CTA.index("activeProject?.has_pat")
    needs_pat_idx = PAT_CTA.index("if (!needsPat(text))")
    assert has_pat_idx < needs_pat_idx, (
        "has_pat early-return must run BEFORE the needsPat regex check "
        "so we never burn cycles on text matching when the project is "
        "already configured."
    )


# ── 2) Mode routing — bare debug verb no longer fires D ───────────

def test_bare_debug_does_not_fire_mode_d():
    from services.mode_d_debugger import is_debug_request
    assert is_debug_request("debug") is False
    assert is_debug_request("debug this please") is False
    assert is_debug_request("investigate") is False
    assert is_debug_request("diagnose") is False


def test_bare_debug_routes_to_mode_a():
    """The classifier sends bare "debug" / etc. to Mode A so the LLM
    can ask a clarifying question instead of templating a refusal."""
    from routers.chat import classify_intent
    assert classify_intent("debug", None) == "A"
    assert classify_intent("debug this", None) == "A"


def test_debug_paired_with_error_still_fires_mode_d():
    """Pairing a debug verb with a SOFT error signal still fires D —
    that's a legitimate bug report."""
    from services.mode_d_debugger import is_debug_request
    assert is_debug_request("debug this error") is True
    assert is_debug_request("the build keeps failing, debug it") is True


def test_real_error_signals_unchanged():
    """Hard signals (stack trace, HTTP code, error class) must still
    route to Mode D. Iter 212f didn't touch any of those."""
    from services.mode_d_debugger import is_debug_request
    assert is_debug_request("500 error on POST /add")     is True
    assert is_debug_request("TypeError: x is undefined")  is True
    assert is_debug_request("ECONNREFUSED on db")         is True


# ── 3) Agentic debug requests now route to Mode C ─────────────────

@pytest.mark.parametrize("msg, expected", [
    # `debug <target>` → Mode C (agentic with tools)
    ("debug full repo",        "C"),
    ("investigate auth flow",  "C"),
    ("review the auth module", "C"),
    ("trace the api flow",     "C"),
    # `scan` / `audit` → Mode E (audit mode is also agentic — reads
    # code, runs the auditor). Both are fine for the user; the only
    # bad outcome we're guarding against is the legacy Mode-D bail.
    ("scan the codebase",      "E"),
    ("audit the backend",      "E"),
])
def test_debug_target_routes_to_agentic_mode(msg, expected):
    """`debug <target>` / `investigate <target>` etc. must route to
    an agentic mode (C or E) so the LLM gets repo context + tools
    instead of bailing with the canned 'insufficient signal' template
    that Mode D used to emit."""
    from routers.chat import classify_intent
    got = classify_intent(msg, None)
    assert got == expected, (
        f"{msg!r} → expected agentic mode {expected!r}, got {got!r}. "
        "Either the regex coverage shrank, or routing got reordered."
    )


# ── 4) Greeting & unrelated questions unchanged ───────────────────

def test_greeting_still_mode_a():
    from routers.chat import classify_intent
    assert classify_intent("hi",   None) == "A"
    assert classify_intent("hey",  None) == "A"


def test_non_debug_question_routes_to_a():
    """The user's actual broken-conversation message must not get
    misrouted to Mode D anymore."""
    from routers.chat import classify_intent
    assert classify_intent("you have full access?", None) == "A"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
