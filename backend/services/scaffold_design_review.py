"""
services/scaffold_design_review.py — Iter 274

Lightweight held-out design review for Personal Track drafts. REUSES
the loop-mode verifier's plumbing wholesale — `_one_shot` LLM call,
verdict parser, and the `loop_verification_log` audit collection.

The ONLY thing net-new is the system prompt: scaffold reviews judge
a generated web-app file tree against a user's plain-English brief,
whereas loop-mode reviews judge a code diff against acceptance
criteria. Reusing the diff-review prompt verbatim confused the
reviewer, so this module contains one bespoke prompt and returns a
row shaped identically to the loop-mode one plus a `user_message`
field (plain-English wording safe to show a non-technical user).

Every row is stamped `origin="personal_track"` so consumers of
`loop_verification_log` can filter cross-mode without cross-mode
pollution.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Optional

from services.ora_chat.providers import one_shot as _one_shot
from services.loop_independent_verifier import (
    _log_row as _shared_log_row,           # audit-log writer, no-raise
    _COLL,                                  # "loop_verification_log"
)

logger = logging.getLogger(__name__)

_DEFAULT_MODEL   = os.getenv("SCAFFOLD_REVIEWER_MODEL", "z-ai/glm-5.2")
_MAX_FILES_SHOWN = 24
_MAX_CONTENT_PER = 1200
_MAX_TOTAL       = 16_000

_SYSTEM = (
    "You are an independent design reviewer for a generated web-app "
    "scaffold. The USER wrote a plain-English brief describing what "
    "they want to build (they may not be a developer). You are shown "
    "that brief plus the file tree that was generated for them. "
    "You do NOT see the generator's reasoning.\n\n"
    "Judge two things ONLY:\n"
    "  (1) Does the generated stack/framework roughly match what the "
    "brief describes?\n"
    "  (2) Are OBVIOUS pieces missing — e.g. brief mentions user "
    "login but no login page/route exists, brief mentions payments "
    "but no payment integration is present, brief mentions saving "
    "data but no persistence layer is set up?\n\n"
    "Do NOT nitpick styling, minor structure, or naming. Only flag "
    "gaps a non-technical user would actually feel when they open "
    "the preview.\n\n"
    "Reply on a SINGLE line as strict JSON. Schema:\n"
    '  { "verdict": "yes" | "no",\n'
    '    "technical_reason": "<one line, max 200 chars, for audit>",\n'
    '    "user_message": "<max 280 chars, plain English, no jargon, '
    'no file paths, addressed to the non-technical user>" }\n'
    "No prose outside the JSON."
)


def _format_files(files: list[dict]) -> str:
    if not files:
        return "(empty scaffold — no files generated)"
    used = 0
    parts: list[str] = []
    for i, f in enumerate(files[:_MAX_FILES_SHOWN]):
        path = (f or {}).get("path") or "?"
        content = ((f or {}).get("content") or "")[:_MAX_CONTENT_PER]
        block = f"── {path} ──\n{content}"
        if used + len(block) > _MAX_TOTAL:
            block = block[: max(_MAX_TOTAL - used, 0)]
        parts.append(block)
        used += len(block)
        if used >= _MAX_TOTAL:
            parts.append(f"…(truncated, {len(files) - i - 1} more files)")
            break
    if len(files) > _MAX_FILES_SHOWN:
        parts.append(f"…(+{len(files) - _MAX_FILES_SHOWN} more file(s) not shown)")
    return "\n\n".join(parts)


def _parse(raw: str) -> tuple[str, str, str]:
    """Fail-closed. Any parse issue → ("no", tech, user)."""
    s = (raw or "").strip()
    s = s.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    m = re.search(r"\{.*?\"verdict\".*?\}", s, re.DOTALL)
    if m:
        s = m.group(0)
    try:
        j = json.loads(s)
    except ValueError:
        return "no", "scaffold_verifier_parse_error", (
            "We couldn't confirm your app matches your description. "
            "Try clicking regenerate — usually a one-time hiccup.")
    v = str(j.get("verdict") or "").strip().lower()
    tech = str(j.get("technical_reason") or "").strip()[:200]
    user = str(j.get("user_message") or "").strip()[:280]
    if v not in ("yes", "no"):
        return "no", "scaffold_verifier_parse_error", (
            user or "We couldn't confirm your app matches your "
            "description. Try clicking regenerate — usually a "
            "one-time hiccup.")
    return v, tech or "(no reason)", user or (
        "Looks good." if v == "yes" else
        "Something in your description doesn't seem to be in the "
        "generated app yet. Try regenerating or rephrasing.")


async def verify_scaffold(db, *,
                          draft_id: str,
                          brief: str,
                          files: list[dict],
                          reviewer_model: Optional[str] = None) -> dict:
    """Non-raising. Always returns a dict; always writes ONE row to
    `loop_verification_log` with `origin="personal_track"`. Shape
    matches loop-mode `verify()` plus an extra `user_message` field."""
    started = time.time()
    model = reviewer_model or _DEFAULT_MODEL

    user_prompt = (
        "USER'S BRIEF (plain English):\n"
        f"{(brief or '').strip()[:3000]}\n\n"
        "GENERATED FILE TREE:\n"
        + _format_files(files)
        + "\n\nAnswer strictly in JSON as instructed."
    )

    try:
        text, usage, err = await _one_shot(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user",   "content": user_prompt},
            ],
            temperature=0.0,
            top_p=1.0,
            presence_penalty=0.0,
            max_tokens=260,
        )
    except Exception as e:                                    # noqa: BLE001
        row = {
            "loop_id":        draft_id,
            "verifier_model": model,
            "verdict":        "skipped_no_llm",
            "reason":         f"scaffold_reviewer_exception:{type(e).__name__}",
            "user_message":   ("The quality reviewer is temporarily "
                                "unavailable. Please try again in a minute."),
            "latency_s":      round(time.time() - started, 2),
            "created_at":     datetime.now(timezone.utc).isoformat(),
            "raw":            str(e)[:400],
            "origin":         "personal_track",
        }
        await _shared_log_row(db, row)
        return row

    if err or not (text or "").strip():
        row = {
            "loop_id":        draft_id,
            "verifier_model": model,
            "verdict":        "skipped_no_llm",
            "reason":         err or "empty_scaffold_reviewer_response",
            "user_message":   ("The quality reviewer is temporarily "
                                "unavailable. Please try again in a minute."),
            "latency_s":      round(time.time() - started, 2),
            "usage":          usage or {},
            "created_at":     datetime.now(timezone.utc).isoformat(),
            "raw":            (text or "")[:400],
            "origin":         "personal_track",
        }
        await _shared_log_row(db, row)
        return row

    verdict, tech, user_msg = _parse(text)
    row = {
        "loop_id":        draft_id,
        "verifier_model": model,
        "verdict":        verdict,
        "reason":         tech,
        "user_message":   user_msg,
        "latency_s":      round(time.time() - started, 2),
        "usage":          usage or {},
        "created_at":     datetime.now(timezone.utc).isoformat(),
        "raw":            (text or "")[:2000],
        "origin":         "personal_track",
    }
    await _shared_log_row(db, row)
    return row


__all__ = ["verify_scaffold"]
