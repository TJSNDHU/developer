"""
services/advisor_vision.py — Iter 212m-212

Isolated OpenRouter vision call for the Advisor `/screenshot` slash
command.

Explicitly ISOLATED from:
  - the Council chain (advisor's main LLM path in `chat.py`)
  - the tool orchestrator (`chat_with_tools`, `services/llm.py`)
  - the retry / fallback ladder in `services/llm_router.py`

Rationale: if OpenRouter is degraded, the Gemini quota is exhausted,
or the model returns garbage, the Advisor's *normal* text reply must
keep working.  Same isolation pattern the Suggestion Box uses for
its Groq call — a bounded, single-shot, best-effort call that
returns `None` on any failure and never raises.

Contract
========

    analyze_screenshot(png_bytes, user_prompt) -> str | None

        `str`  — a concise UI description ready to inline into the
                 Advisor's context block.
        `None` — best-effort fetch failed; caller must degrade
                 gracefully.  Never raises.

Model selection
---------------

Primary (chosen 2026-02 after `manual_ab_model_swap.py` vision-lane
A/B):
    google/gemini-2.5-flash

Failover (if primary 5xx / rate-limited):
    openai/gpt-5-mini

Both are vision-capable via OpenRouter, priced at ~$0.30/M input
and ~$2.50/M output.  Failover is intentionally single-hop — no
deep ladder — to keep this call bounded (~10s wall-time cap).
"""

from __future__ import annotations

import base64
import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

PRIMARY_MODEL = os.environ.get("ADVISOR_VISION_MODEL",
                                "google/gemini-2.5-flash")
FAILOVER_MODEL = os.environ.get("ADVISOR_VISION_FAILOVER",
                                 "openai/gpt-5-mini")

# System prompt is deliberately short — vision-input tokens dominate
# cost, and we want a *description* not a novel.
SYSTEM_PROMPT = (
    "You are describing a UI screenshot to another assistant.  The "
    "user is looking at this screen and just asked a question about "
    "it.  In ≤140 words, describe what you SEE with SPATIAL "
    "SPECIFICITY — say `top-right`, `top-left`, `bottom nav`, "
    "`sidebar`, `middle of the card`, etc.  Name buttons by their "
    "visible label and colour ('orange Start Free button', 'dark "
    "Continue with GitHub button').  Call out any obvious defect "
    "(overflow, misalignment, unreadable text, broken image, error "
    "banner) and where it sits.  Do NOT invent content that isn't "
    "visible.  Do NOT speculate about backend causes.  If the "
    "screenshot is blank / a login wall / an error screen, say so "
    "in one sentence and stop.  End with one line: "
    "`PROBABLE_ISSUES:` followed by a comma-separated list, or "
    "`PROBABLE_ISSUES: none`."
)

_TIMEOUT_S = 12.0
_MAX_USER_HINT_CHARS = 400


async def _call_openrouter(
    model: str,
    png_bytes: bytes,
    user_hint: str,
) -> Optional[str]:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        logger.warning("advisor_vision: OPENROUTER_API_KEY missing")
        return None

    # OpenRouter accepts data URIs directly for image content.
    b64 = base64.b64encode(png_bytes).decode("ascii")
    data_uri = f"data:image/png;base64,{b64}"

    payload = {
        "model": model,
        "temperature": 0.2,
        "max_tokens": 400,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"User question: {user_hint[:_MAX_USER_HINT_CHARS]}"
                        ),
                    },
                    {"type": "image_url", "image_url": {"url": data_uri}},
                ],
            },
        ],
    }
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        # OpenRouter attribution headers — nice-to-have, not required.
        "HTTP-Referer": "https://auremcto.com",
        "X-Title": "Aurem CTO Advisor",
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as cx:
            r = await cx.post(OPENROUTER_URL, json=payload, headers=headers)
    except Exception as e:
        logger.warning("advisor_vision: transport error on %s: %r", model, e)
        return None

    if r.status_code != 200:
        logger.warning(
            "advisor_vision: %s returned %s — body: %s",
            model, r.status_code, r.text[:200],
        )
        return None
    try:
        j = r.json()
        content = (
            j.get("choices") or [{}]
        )[0].get("message", {}).get("content")
    except Exception as e:
        logger.warning("advisor_vision: bad JSON from %s: %r", model, e)
        return None
    if not content or not isinstance(content, str):
        return None
    return content.strip()


async def analyze_screenshot(
    png_bytes: bytes,
    user_prompt: str,
) -> Optional[str]:
    """Best-effort vision analysis.  Returns None on ANY failure so
    the caller can degrade gracefully."""
    if not png_bytes:
        return None
    if len(png_bytes) < 1024:
        # Anything under 1 KB is almost certainly a 1x1 pixel or
        # decode failure — skip the round-trip.
        logger.warning(
            "advisor_vision: rejecting tiny screenshot (%d bytes)",
            len(png_bytes),
        )
        return None

    reply = await _call_openrouter(PRIMARY_MODEL, png_bytes, user_prompt)
    if reply:
        return reply
    # Failover — intentionally single-hop so this call can't sprawl
    # into a long ladder that blocks the advisor request.
    reply = await _call_openrouter(FAILOVER_MODEL, png_bytes, user_prompt)
    return reply  # may still be None; caller handles that
