"""Iter 212m-14 — Runtime hallucination guard for status claims.

The persona's ANTI-HALLUCINATION CONTRACT already forbids fabricated
file paths, line numbers, and metrics. But the founder hit a class
the prompt didn't cover: **claims about resource HEALTH/STATUS**
without any tool evidence. Specifically ORA said

    [FIX] The GitHub PAT appears unauthorized — would need a fresh
    fine-grained token with `Contents: Read` access.

…even though the PAT in question was valid (verified independently
via `/cto/projects/{id}/check-pat`) AND no tool call this turn had
returned a 401. The LLM fabricated the diagnosis.

This module post-processes the final assistant content. When it
detects a credential/auth status claim with NO corresponding tool
error in the turn's invocation history, it appends a transparency
footer so the founder is signalled to verify. We deliberately
DO NOT strip the claim — rewriting LLM prose is fragile and risks
breaking handoff fences / code blocks. A visible footer is
sufficient at the trust-and-safety bar we need.

False-positive strategy:
  • Match ONLY narrow status-of-credential claims (PAT, token,
    auth, permission denied, 401/403). Vague phrases like
    "you might need a token" are NOT flagged — they're correct
    hedging.
  • If the sentence already hedges with might/may/could/perhaps/
    let-me-check/verify, treat it as honest uncertainty and skip.
  • Tool-call output containing 401/403/unauthorized/forbidden/
    bad-credentials counts as evidence — even if just one matching
    invocation, the claim is considered supported.

This guard is intentionally surgical. We do NOT broadly rewrite
LLM outputs; we only annotate one specific high-trust failure
mode that bit a real user in production.
"""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


# Narrow patterns — claims about credentials/auth being bad.
_CRED_CLAIMS = [
    re.compile(r"\bPAT\s+(?:is|appears|seems|looks|might\s+be|may\s+be|got)\s+(?:un)?authoriz(?:ed|ation)?", re.I),
    re.compile(r"\bPAT\s+(?:is|appears|seems|looks|got)\s+(?:expir|invalid|miss|broken|wrong|bad|insufficient)", re.I),
    re.compile(r"\b(?:GitHub\s+)?token\s+(?:is|appears|seems|looks|got)\s+(?:expir|invalid|miss|broken|wrong|bad|unauthor)", re.I),
    re.compile(r"\bunauthorize(?:d)?\b\s+(?:to|on|access|request)", re.I),
    re.compile(r"\bappears?\s+unauthorize", re.I),
    re.compile(r"\bpermission\s+denied\b", re.I),
    re.compile(r"\b401\s+(?:un)?authoriz", re.I),
    re.compile(r"\b403\s+forbidden", re.I),
    re.compile(r"\bcredentials?\s+(?:are|appear|seem|look)\s+(?:invalid|expir|bad|wrong|miss|insufficient)", re.I),
    re.compile(r"\binsufficient\s+(?:permissions?|scope|access)", re.I),
    re.compile(r"\bbad\s+credentials\b", re.I),
]


# Hedged language — sentences containing any of these get a pass.
# Honest uncertainty is fine; we only flag DEFINITIVE-sounding claims.
_HEDGES = re.compile(
    r"\b(?:might|maybe|may|could|perhaps|possibly|likely|i\s+haven't|"
    r"haven't\s+verified|haven't\s+checked|let\s+me\s+check|"
    r"let\s+me\s+verify|need\s+to\s+verify|will\s+verify|"
    r"would\s+(?:need|like)\s+to\s+(?:check|verify|confirm))",
    re.I,
)


# Tool errors that DO count as evidence for an auth claim.
_AUTH_FAILURE = re.compile(
    r"401|403|unauthoriz|forbidden|bad[_\s-]?credential|permission[_\s-]?denied|"
    r"invalid[_\s-]?token|expir(?:ed|ation)|insufficient[_\s-]?(?:scope|permission)|"
    r"no[_\s-]?pat|pat[_\s-]?missing|missing[_\s-]?pat",
    re.I,
)


def _has_supporting_tool_evidence(invocations: list[dict]) -> bool:
    """Did any tool call this turn surface a real auth failure?

    A failure counts if either:
      • ok=False AND error/output text matches _AUTH_FAILURE, OR
      • the tool output JSON literally contains 401/403/unauthorized
        strings (some tools return `ok=True` with an embedded error).
    """
    for inv in invocations or []:
        # Explicit failure
        if inv.get("ok") is False:
            err = str(inv.get("error") or "")
            if _AUTH_FAILURE.search(err):
                return True
        # Output payload — even successful invocations sometimes embed
        # diagnostic strings (e.g. check_pat returns state:"expired").
        out = inv.get("output")
        if out:
            text = str(out)[:4000]
            if _AUTH_FAILURE.search(text):
                return True
    return False


def _detect_unsupported_claims(content: str) -> list[str]:
    """Return the trimmed text of every line/sentence that contains
    a credential/auth status claim AND lacks a hedge."""
    flagged: list[str] = []
    if not content:
        return flagged
    # Split on sentence-ish boundaries (. ! ? \n). Keep it cheap.
    sentences = re.split(r"(?<=[.!?\n])\s+", content)
    for s in sentences:
        s_norm = s.strip()
        if not s_norm:
            continue
        # Hedged → uncertainty already signalled by the model. Skip.
        if _HEDGES.search(s_norm):
            continue
        for pat in _CRED_CLAIMS:
            if pat.search(s_norm):
                flagged.append(s_norm[:240])
                break
    return flagged


def apply(content: str, invocations: list[dict]) -> tuple[str, list[str]]:
    """Public entry point.

    Returns `(new_content, flagged_claims)`. When `flagged_claims`
    is non-empty AND there's no supporting tool evidence, the
    returned content is the original text + a transparency footer
    listing the unsupported claims. Otherwise content is unchanged.

    Never raises — guard failures must never break the chat reply.
    """
    try:
        if not content or not isinstance(content, str):
            return content, []
        flagged = _detect_unsupported_claims(content)
        if not flagged:
            return content, []
        if _has_supporting_tool_evidence(invocations):
            # The model's claim is grounded in a real tool error —
            # nothing to annotate, the [ISSUE]/[FIX] marker is honest.
            return content, []

        # Render a single concise footer. We use the [ISSUE] color
        # marker so the founder visually links this to a real
        # problem they need to verify themselves.
        first = flagged[0]
        suffix = (
            "\n\n---\n"
            "[ISSUE] Self-check: I made a status claim above without "
            "evidence from a tool call this turn:\n"
            f"  > {first}\n"
            "I did not actually verify it. Take with care — if it "
            "matters, ask me to run `check_pat` / `read_repo_file` "
            "and re-confirm.\n"
        )
        return content + suffix, flagged
    except Exception as e:                                    # noqa: BLE001
        logger.warning("hallucination_guard.apply crashed: %r", e)
        return content, []


__all__ = ["apply"]
