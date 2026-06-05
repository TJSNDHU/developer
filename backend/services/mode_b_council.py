"""
services/mode_b_council.py — Mode B auto-upgrade: Decision Council.

When ORA's classifier picks Mode B AND the message has stuck-decision
signals ("torn between", "should I X or Y", "stuck on", "council", …),
we upgrade the standard advice response into a structured 5-adviser
council pattern + Chairman verdict.

Why this exists: vanilla Mode B can produce a balanced two-sides
response, which is the LEAST useful output for someone genuinely
paralysed. The council format forces 5 distinct viewpoints and a
final sharp call.

Wired into routers/chat.py right before the Mode F branch — a single
LLM call (Claude via review-mode) returns the full Markdown council
in one shot. ~1.5–2.5 k output tokens, so the user sees value for
~$0.02–0.04 worth of API spend.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# Patterns that prove the user is genuinely stuck on a decision.
# Tuned to fire AFTER the regular Mode B classifier already accepted
# the message — these are *upgrade* signals, not Mode B detectors.
_COUNCIL_SIGNALS = [
    r"\bstuck on\b",
    r"\bstuck between\b",
    r"\btorn between\b",
    r"\bcan'?t decide\b",
    r"\bcannot decide\b",
    r"\bcan'?t figure out\b",
    r"\bdebating between\b",
    r"\bdecision i'?m stuck on\b",
    r"\bweigh(ing)? (the )?options\b",
    r"\b(this|that) (is )?(a )?big decision\b",
    r"\bhuge decision\b",
    r"\bmajor (call|decision)\b",
    r"\bshould i (pivot|quit|fire|hire|launch|raise|shut down|kill|sell|merge)\b",
    r"\b(pivot or persevere|build or buy)\b",
    # Explicit invocation
    r"\b(decision )?council\b",
    r"\brun (the )?council\b",
    r"\b5[- ]?adviser\b",
    r"\bfive[- ]?adviser\b",
]
_COUNCIL_RE = re.compile("|".join(_COUNCIL_SIGNALS), re.IGNORECASE)


def is_council_request(message: str, mode: str) -> bool:
    """True when (1) classifier already picked Mode B AND (2) the
    message looks like a genuinely stuck decision (not a casual
    "should I add caching" code question, those get handled by
    regular Mode B advice)."""
    if mode != "B" or not message:
        return False
    return bool(_COUNCIL_RE.search(message))


_COUNCIL_SYSTEM = """You are ORA running the DECISION COUNCIL — a structured \
five-adviser framework for someone genuinely stuck on a hard decision.

You must produce EXACTLY this Markdown layout, in this exact order, with \
these exact section headers. NO extra preamble. NO closing summary outside \
the Chairman section.

# Decision Council

> **The decision:** <one-sentence restatement of what the user is stuck on>

## Adviser 1 — The Contrarian
Voice: blunt, pessimistic, looks ONLY for what fails. Lists every reason \
this is wrong, what breaks first, the worst plausible outcome. \
DO NOT balance. ~110-150 words.

## Adviser 2 — The First-Principles Thinker
Voice: physicist energy. Rips assumptions. Strips the problem to fundamentals \
and rebuilds. Asks "what would you do if you couldn't use any obvious \
framework?". ~110-150 words.

## Adviser 3 — The Expansionist
Voice: founder-mode optimist. Finds the asymmetric upside if this works. \
What does the bigger version of this open up that the user isn't seeing? \
~110-150 words.

## Adviser 4 — The Outsider
Voice: knows nothing about the industry. Asks the dumb questions only an \
outsider asks. Surfaces obvious things insiders stopped questioning. \
~90-130 words.

## Adviser 5 — The Executor
Voice: doesn't care about strategy, cares about Monday morning. Tells the \
user exactly what to do this week — the email to send, the conversation \
to have, the file to create, the decision to defer. ~90-130 words.

## Peer review

For each adviser, in ONE line, state which of the OTHER FOUR they \
ranked #1 and #4 and ONE phrase of why. Use the format \
"Adviser X ranks: #1 Y (why), #4 Z (why)". \
No long paragraphs — just five lines, one per adviser.

## Chairman's call

Be sharp. 200-240 words MAX. Include exactly these four bolded items in \
this order:

**The decision:** <single sentence — what to actually do>

**Strongest reason:** <single sentence>

**Biggest risk:** <single sentence>

**Next step (7 days):** <2-4 lines of concrete, dated actions starting \
"By <day>: ..." — no fluff>

End with a one-line gut-check question the user must answer for themself \
before executing.

Hard constraints:
- Stay in character per adviser. Different vocab, different priorities, \
  different blind spots.
- NEVER recommend "do both" or "wait and see" — every adviser must take \
  a side.
- NEVER use the phrase "great question" or "it depends".
- Output PURE Markdown. No fenced code blocks anywhere.
"""


def _build_council_user_prompt(
    prompt: str,
    repo_ctx: Optional[str],
    brain_ctx: Optional[str],
) -> str:
    parts = [f"The user's decision:\n\n{prompt.strip()}\n"]
    if (repo_ctx or "").strip():
        parts.append(
            "\nCONNECTED REPO CONTEXT (only mention if relevant to the "
            "decision; do not force-fit):\n"
            f"{repo_ctx.strip()[:1500]}\n"
        )
    if (brain_ctx or "").strip():
        parts.append(
            "\nPROJECT BRAIN (the user's tracked decisions, preferences):\n"
            f"{brain_ctx.strip()[:1200]}\n"
        )
    parts.append(
        "\nNow produce the Council output in the exact required layout."
    )
    return "\n".join(parts)


async def run_council(
    prompt: str,
    repo_ctx: Optional[str] = None,
    brain_ctx: Optional[str] = None,
) -> str:
    """Single LLM call → full council Markdown. Returns the content
    string (already valid Markdown) the chat layer can stream as-is."""
    from services.llm import call_llm_with_meta
    user = _build_council_user_prompt(prompt, repo_ctx, brain_ctx)
    try:
        meta = await call_llm_with_meta(
            system=_COUNCIL_SYSTEM,
            user=user,
            max_tokens=4096,
            mode="review",      # routes to Claude Sonnet → richer personas
        )
    except Exception as e:
        logger.exception("council LLM call failed")
        return (
            "_(Council failed to assemble: "
            f"{type(e).__name__}. Try again, or rephrase the decision "
            "more concretely — e.g. 'should I X or Y, given Z'.)_"
        )
    content = (meta.get("content") or "").strip()
    if not content:
        return (
            "_(Council returned empty. Try again, or rephrase the "
            "decision more concretely.)_"
        )
    # Soft sanity: if Claude omitted the Chairman section, fall back
    # to a clear failure note so we don't ship a half-council to the
    # user as if it were complete.
    if "Chairman" not in content:
        return content + (
            "\n\n_(Note: the Chairman's call was missing from the model "
            "output — rerun the council for the final verdict.)_"
        )
    return content
