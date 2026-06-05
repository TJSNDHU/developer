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
        # Iter 84 — extended rules (a)-(d).
        "ABSOLUTE NEGATIVES — extended",
        "(a) The brief contains ANY of these phrases",
        "(b) The brief contains NO file-path token",
        "(c) The brief is longer than 1500 characters",
        "(d) Any file path inside the brief was NOT successfully",
        "BRIEF FORMAT — LEARN BY EXAMPLE",
        "✓ CORRECT brief",
        "✗ INCORRECT brief #1",
        "✗ INCORRECT brief #2",
        "✗ INCORRECT brief #3",
        "Why it fails:",
        # Concrete example tokens must persist so the model has a
        # path it can pattern-match against.
        "backend/routers/auth.py",
        "frontend/src/pages/Login.jsx",
        "Would you like me to refactor",
    ):
        assert must in joined, (
            f"orchestrator system prompt missing required guard line: {must!r}"
        )


def test_orchestrator_example_block_demonstrates_each_failure_mode():
    """The example block must teach by contrast: ONE correct brief,
    THREE incorrect briefs each illustrating a different failure mode.
    This catches a future refactor where someone deletes 2 of the 3
    counter-examples 'to keep the prompt short'."""
    src = _read("backend/services/orchestrator.py")
    joined = re.sub(r'"\s*\n\s*"', "", src)
    # Exactly one ✓ block, exactly three ✗ blocks numbered #1 #2 #3.
    assert joined.count("✓ CORRECT brief") == 1
    for n in (1, 2, 3):
        assert f"✗ INCORRECT brief #{n}" in joined, \
            f"missing INCORRECT example #{n}"
    # Each ✗ block must include its own 'Why it fails:' note so the
    # rationale is paired with the example, not buried elsewhere.
    assert joined.count("Why it fails:") >= 3, (
        "Each ✗ INCORRECT brief must have its own 'Why it fails:' note"
    )


# ── 2. UI guard — content validation, not just length ─────────────────

def test_messagebubble_guard_requires_mutation_verb():
    src = _read("frontend/src/components/MessageBubble.jsx")
    assert "MUTATION_VERBS" in src
    assert "READ_ONLY_LINE" in src
    assert "PERMISSION_PHRASES" in src
    assert "FILE_PATH_TOKEN" in src
    # Must explicitly reject read-only briefs.
    assert "allReadOnly" in src
    # Must require at least one mutation verb to render Ship button.
    assert "MUTATION_VERBS.test(brief)" in src
    # Must require at least one concrete file-path token.
    assert "FILE_PATH_TOKEN.test(brief)" in src
    # Iter 84 hardening comment must stay so the rationale isn't lost.
    assert "Iter 84 tightening" in src


def test_messagebubble_mutation_verbs_regex_lists_required_verbs():
    """Lock the MUTATION_VERBS regex *contents* by string match — far
    more robust than parsing a JS regex literal back into Python. If
    someone deletes a verb, the test fails."""
    src = _read("frontend/src/components/MessageBubble.jsx")
    # Locate just the MUTATION_VERBS block to scope the assertions.
    m = re.search(
        r"const MUTATION_VERBS = new RegExp\("
        r"([\s\S]*?)"
        r"\)\s*;",
        src,
    )
    assert m, "MUTATION_VERBS block not found"
    block = m.group(1)
    for verb in (
        "create", "add", "fix", "write", "edit", "rewrite", "refactor",
        "replace", "implement", "scaffold", "wire", "install", "patch",
        "delete", "remove", "migrate", "generate", "integrate", "ship",
        "introduce", "inject", "deprecate", "rename", "move",
    ):
        assert f"|{verb}" in block or f"({verb}" in block, (
            f"MUTATION_VERBS regex missing required verb: {verb}"
        )
    # Soft verbs the previous (too-permissive) regex carried — must
    # NOT be in the new tightened list.
    for soft in ("handle", "expose", "validate", "configure", "set up"):
        assert soft not in block, (
            f"MUTATION_VERBS still contains soft verb {soft!r}; "
            f"that's what made the previous version too permissive."
        )


def test_messagebubble_file_path_token_regex_lists_required_extensions():
    """Lock FILE_PATH_TOKEN contents by string match."""
    src = _read("frontend/src/components/MessageBubble.jsx")
    m = re.search(
        r"const FILE_PATH_TOKEN = new RegExp\("
        r"([\s\S]*?)"
        r"\)\s*;",
        src,
    )
    assert m, "FILE_PATH_TOKEN block not found"
    block = m.group(1)
    # Must include the languages the worker can actually edit in this
    # codebase.
    for ext in ("py", "jsx", "tsx", "ts", "js", "md", "json", "css",
                "html?", "env"):
        assert ext in block, (
            f"FILE_PATH_TOKEN regex missing required extension: {ext}"
        )
    # Must require a slash before the filename.
    assert "/" in block


def test_messagebubble_permission_phrases_regex_lists_required_phrases():
    """Lock PERMISSION_PHRASES contents — the 9 phrases the system
    prompt forbids must all be present in the UI guard regex too."""
    src = _read("frontend/src/components/MessageBubble.jsx")
    m = re.search(
        r"const PERMISSION_PHRASES = new RegExp\("
        r"([\s\S]*?)"
        r"\)\s*;",
        src,
    )
    assert m, "PERMISSION_PHRASES block not found"
    block = m.group(1)
    for phrase in (
        "would you like", "should i", "shall i", "want me to",
        "do you want", "let me know", "tell me which",
        "happy to", "i can",
    ):
        assert phrase in block.lower(), (
            f"PERMISSION_PHRASES regex missing required phrase: {phrase!r}"
        )


def test_messagebubble_has_explicit_length_and_line_caps():
    """Gates 1 — the constants must exist with the documented values
    so a future refactor can't silently lift them to 'unbounded'."""
    src = _read("frontend/src/components/MessageBubble.jsx")
    assert "const MAX_BRIEF_CHARS = 1500;" in src
    assert "const MAX_BRIEF_LINES = 12;" in src
    # And both must actually be enforced inside extractHandoffBrief.
    assert "brief.length > MAX_BRIEF_CHARS" in src
    assert "lines.length > MAX_BRIEF_LINES" in src


def test_messagebubble_rejects_any_question_mark_anywhere():
    """Gate 2 — '?' anywhere must reject, not just at line end."""
    src = _read("frontend/src/components/MessageBubble.jsx")
    # The previous version only checked /\?[\s)]*$/ — line-end only.
    # New version must use /\?/.test(brief) covering anywhere.
    assert "if (/\\?/.test(brief)) return null;" in src


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
