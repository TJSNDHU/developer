"""
services/mode_f_engage.py — Mode F (Engage / Market).

Token-cheap single LLM call. The user asks something market-flavoured
("how do we beat <competitor>", "who's our competition for X", "write
a tweet announcing Y") and we route through a focused system prompt
instead of the full code-orchestrator (which is expensive and unused
for this kind of task).
"""
from __future__ import annotations

import re
import logging

logger = logging.getLogger(__name__)

_ENGAGE_PATTERNS = [
    # Iter 162 engage-pass — tightened to require EXPLICIT marketing /
    # GTM intent. Removed the bare `audience|ideal customer|icp|persona`
    # match that was silently routing technical conversations like
    # "persona is wrong here" or "icp is enterprise devs" through the
    # market-copy persona prompt. False positives wasted tokens and
    # gave irrelevant answers.
    # competitive / positioning — always require positioning context
    r"\b(competit(or|ion|ive)|rival|alternative to)\s+(analysis|teardown|matrix|landscape)\b",
    r"\bwho('?s|\s+is|\s+are)\s+(my|our|the)\s+(competit|rival)",
    r"\bhow (do|can|should) (we|i) (beat|differentiate|position|out-position)\b",
    r"\b(market|positioning|gtm|go.to.market|launch strategy)\b",
    r"\b(usp|moat|unfair advantage|differentiator)\b",
    # explicit ICP / persona ASKS, not bare nouns
    r"\b(define|describe|write)\s+(our|my|the)\s+(audience|ideal customer|icp|persona|target market)\b",
    r"\bwho('?s|\s+is)\s+(our|my|the)\s+(audience|ideal customer|icp|persona|target market)\b",
    # sales / copy / outreach — always require WRITE intent
    r"\bwrite (me )?(a|the|some) (tweet|post|copy|pitch|headline|tagline|launch|landing\s+page)",
    r"\b(announce|launch post|cold (email|outreach))\b",
    # pricing / business — must be on user's own product
    r"\bhow (do|should) (i|we) price\b",
    r"\b(price|pricing|monet[iz]e|charge)\b.*\b(my|our)\b.*\b(product|app|saas|service)\b",
]


def is_engage_request(message: str) -> bool:
    """Quick regex classifier — keeps Mode F off the LLM budget for
    obvious coding requests. Falls through to A/B if nothing matches."""
    if not message:
        return False
    msg = message.lower()
    return any(re.search(p, msg, re.IGNORECASE) for p in _ENGAGE_PATTERNS)


_ENGAGE_SYSTEM = (
    "You are ORA in MARKET mode — the user is asking a business / "
    "positioning / GTM question, NOT a coding question.\n\n"
    "Tone: sharp founder-friend, no fluff, no 'great question'. Lead "
    "with the answer in the first sentence. 120-220 words max.\n\n"
    "If you know the user's repo from the CONNECTED REPO CONTEXT, use "
    "it — tie your advice to what they're actually building, not a "
    "generic SaaS playbook.\n\n"
    "STRUCTURE — keep it tight, only include sections that fit the ask:\n"
    "  • **Take** — one-line opinion on the right move.\n"
    "  • **Why** — 2-3 bullet reasons, ground them in the user's "
    "actual product when possible.\n"
    "  • **Do this** — 2-3 concrete next actions the user can run "
    "today (URLs, snippets, exact wording where useful).\n\n"
    "If the user asked for copy / a tweet / a tagline, just write the "
    "copy — don't lecture them on positioning first. Output the copy "
    "in a fenced block so they can grab it."
)


async def run_engage(prompt: str, repo_ctx: str = "",
                     brain_ctx: str = "") -> str:
    """Single DeepSeek call. ~600 tokens output cap to keep cost low."""
    from services.llm import call_llm_with_meta
    system = _ENGAGE_SYSTEM
    if repo_ctx:
        system += "\n\n" + repo_ctx
    if brain_ctx:
        system += "\n\n" + brain_ctx
    res = await call_llm_with_meta(
        system=system,
        user=prompt,
        max_tokens=600,
        mode="chat",
    )
    return (res.get("content") or "").strip()
