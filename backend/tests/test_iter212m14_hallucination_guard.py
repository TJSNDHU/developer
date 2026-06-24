"""Iter 212m-14 — Hallucination guard for credential/auth status claims.

Locks the exact bug that bit production: ORA said
'[FIX] The GitHub PAT appears unauthorized' even though the PAT
was valid and no tool call surfaced a 401. The guard appends a
visible transparency footer when such a claim has no evidence in
this turn's tool invocation history.

Coverage:
  • Definitive auth claims WITHOUT evidence → footer appended
  • Same claim WITH a 401-tool-error in invocations → no footer
  • Hedged language ("might be invalid", "let me check") → no footer
  • Empty/None inputs → safe pass-through
  • Guard crash → original content returned untouched
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import hallucination_guard as hg  # noqa: E402


# ── Empty / safe pass-through ─────────────────────────────────────


def test_empty_content_returns_unchanged():
    out, flagged = hg.apply("", [])
    assert out == ""
    assert flagged == []


def test_none_content_returns_unchanged():
    out, flagged = hg.apply(None, [])
    assert out is None
    assert flagged == []


def test_no_claim_no_footer():
    text = "Read README.md. It says 'AUREM CTO is an AI engineer'."
    out, flagged = hg.apply(text, [])
    assert out == text
    assert flagged == []


# ── Unsupported credential claims get a footer ───────────────────


def test_pat_appears_unauthorized_without_evidence_is_flagged():
    """The exact bug from production."""
    text = "[FIX] The GitHub PAT appears unauthorized — would need a fresh token."
    out, flagged = hg.apply(text, [])
    assert "Self-check" in out
    assert "without evidence" in out
    assert len(flagged) == 1


def test_token_is_expired_claim_without_evidence_is_flagged():
    text = "The token is expired. Please refresh it."
    out, flagged = hg.apply(text, [])
    assert "Self-check" in out
    assert len(flagged) == 1


def test_403_forbidden_claim_without_evidence_is_flagged():
    text = "Got a 403 forbidden response from GitHub when trying to read."
    out, flagged = hg.apply(text, [])
    # 403 mention without a 403 in tool invocations → flagged
    assert "Self-check" in out
    assert len(flagged) == 1


def test_permission_denied_claim_without_evidence_is_flagged():
    text = "I attempted to read the file but permission denied."
    out, flagged = hg.apply(text, [])
    assert "Self-check" in out


def test_bad_credentials_claim_without_evidence_is_flagged():
    text = "GitHub returned bad credentials, so I cannot proceed."
    out, flagged = hg.apply(text, [])
    assert "Self-check" in out


# ── Hedged claims are NOT flagged ────────────────────────────────


def test_hedged_might_be_invalid_is_not_flagged():
    text = "The PAT might be invalid — let me check by calling check_pat."
    out, flagged = hg.apply(text, [])
    assert out == text
    assert flagged == []


def test_hedged_haven_t_verified_is_not_flagged():
    text = "I haven't verified the token yet; would need to check."
    out, flagged = hg.apply(text, [])
    assert out == text
    assert flagged == []


def test_hedged_let_me_verify_is_not_flagged():
    text = "The credentials may be expired. Let me verify with check_pat."
    out, flagged = hg.apply(text, [])
    assert out == text
    assert flagged == []


# ── Supported claims (tool evidence present) get NO footer ───────


def test_pat_unauthorized_claim_with_401_evidence_is_allowed():
    text = "The PAT is unauthorized — GitHub returned 401."
    invocations = [{
        "tool": "read_repo_file",
        "ok": False,
        "error": "401 Unauthorized",
        "args": {"path": "README.md"},
    }]
    out, flagged = hg.apply(text, invocations)
    assert out == text
    assert flagged == []


def test_token_expired_claim_with_explicit_expired_tool_output_is_allowed():
    text = "Token is expired — refresh required."
    invocations = [{
        "tool": "check_pat",
        "ok": True,
        "output": {"state": "expired", "expires_at": "2025-01-01"},
    }]
    out, flagged = hg.apply(text, invocations)
    assert out == text
    assert flagged == []


def test_permission_denied_claim_with_403_tool_error_is_allowed():
    text = "Permission denied on /admin path."
    invocations = [{
        "tool": "read_repo_file",
        "ok": False,
        "error": "403 forbidden",
    }]
    out, flagged = hg.apply(text, invocations)
    assert out == text
    assert flagged == []


def test_no_pat_claim_with_explicit_no_pat_tool_error_is_allowed():
    text = "PAT is missing on this project."
    invocations = [{
        "tool": "list_repo_files",
        "ok": False,
        "error": "no_pat",
    }]
    out, flagged = hg.apply(text, invocations)
    assert out == text
    assert flagged == []


# ── Robustness ────────────────────────────────────────────────────


def test_guard_never_raises_on_garbage_invocation():
    text = "The PAT is invalid."
    bad = [{"tool": None, "ok": "weird", "output": {"nested": {"obj": object()}}}]
    out, flagged = hg.apply(text, bad)
    # Either flagged (no evidence in the garbage) or skipped — must not raise
    assert isinstance(out, str)
    assert isinstance(flagged, list)


def test_multiple_claims_only_one_footer():
    text = (
        "[ISSUE] The PAT is unauthorized. "
        "[ISSUE] The token is expired too. "
        "Some other line."
    )
    out, flagged = hg.apply(text, [])
    # Footer appears exactly once regardless of how many claims
    assert out.count("Self-check") == 1
    assert len(flagged) >= 1


def test_claim_in_code_fence_still_flagged_by_design():
    """We deliberately do NOT try to skip claims inside code fences —
    the LLM has been known to fabricate inside markdown comments too.
    Conservative: flag everywhere, let founder verify."""
    text = "Here's the issue:\n```\nThe PAT is unauthorized\n```"
    out, flagged = hg.apply(text, [])
    assert "Self-check" in out


def test_partial_word_match_not_triggered():
    """`appearance` or `tokens` (plural) shouldn't catastrophically
    trigger when the surrounding sentence isn't a real status claim."""
    text = "The appearance of the tokens is professional."
    out, flagged = hg.apply(text, [])
    # No status-of-credential claim → no footer
    assert out == text


def test_footer_uses_issue_marker_for_visual_alignment():
    """The footer is rendered with [ISSUE] so it gets the red pill
    treatment in RenderedMessage.jsx — consistent with the color-tag
    UX shipped in iter 212m-12."""
    text = "PAT is invalid."
    out, _ = hg.apply(text, [])
    assert "[ISSUE]" in out
