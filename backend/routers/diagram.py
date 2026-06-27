"""
routers/diagram.py — Iter 212m-61

POST /api/aurem-dev/diagram/generate

Generates Mermaid.js diagram syntax from a natural-language prompt.
Designed for the `/diagram <prompt>` chat command — ORA decides the
diagram type from prompt keywords, calls Claude with max_tokens=800
(diagrams are compact), validates the output starts with a real
Mermaid keyword, and retries once with a stricter prompt if not.

No DB collections.  Audit trail is a single info-level log line so
the founder can grep production logs for `diagram_generated`.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from cto_services.auth import current_dev

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/diagram", tags=["Diagram"])

# Mermaid grammar root keywords — used to validate model output.
_MERMAID_HEADS = (
    "flowchart", "graph", "sequenceDiagram", "erDiagram",
    "classDiagram", "stateDiagram", "stateDiagram-v2",
    "gantt", "pie", "mindmap", "timeline", "journey",
)

# Auto-detection rules.  First match wins.  Tuned for the user's spec.
_TYPE_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b(database|table|schema|erd|entity)\b", re.I),
     "erDiagram"),
    (re.compile(r"\b(sequence|api call|request|response|interaction)\b", re.I),
     "sequenceDiagram"),
    (re.compile(r"\b(class|inheritance|oop|polymorphism)\b", re.I),
     "classDiagram"),
    (re.compile(r"\b(architecture|system|hld|cloud|service|microservice)\b", re.I),
     "flowchart LR"),
    (re.compile(r"\b(state|lifecycle|transition)\b", re.I),
     "stateDiagram-v2"),
]
_DEFAULT_TYPE = "flowchart TD"


def detect_type(prompt: str, explicit: Optional[str] = None) -> str:
    if explicit:
        e = explicit.lower()
        # Map common aliases the frontend may send to canonical Mermaid heads.
        return {
            "flowchart": "flowchart TD",
            "erd":       "erDiagram",
            "sequence":  "sequenceDiagram",
            "hld":       "flowchart LR",
            "class":     "classDiagram",
            "state":     "stateDiagram-v2",
        }.get(e, _DEFAULT_TYPE)
    for rx, kind in _TYPE_RULES:
        if rx.search(prompt or ""):
            return kind
    return _DEFAULT_TYPE


def _strip_fences(text: str) -> str:
    """Strip ```mermaid / ``` fences — the model sometimes adds them
    despite our instruction not to."""
    text = (text or "").strip()
    if text.startswith("```"):
        # Drop first line (the ``` or ```mermaid) and trailing ```
        first_nl = text.find("\n")
        if first_nl != -1:
            text = text[first_nl + 1:]
        if text.endswith("```"):
            text = text[: -3].rstrip()
    return text.strip()


def _is_valid_mermaid(code: str) -> bool:
    code = (code or "").strip()
    if not code:
        return False
    first_token = code.split()[0] if code.split() else ""
    return any(code.startswith(h) for h in _MERMAID_HEADS) or first_token in {
        h.split()[0] for h in _MERMAID_HEADS
    }


def _build_prompt(prompt: str, diagram_type: str,
                  strict: bool = False) -> str:
    base = (
        f"Generate a {diagram_type} diagram in valid Mermaid.js syntax "
        f"for this request:\n\n{prompt}\n\n"
        "Rules:\n"
        f"- Start the response with the literal keyword `{diagram_type.split()[0]}`.\n"
        "- Output ONLY Mermaid.js syntax. No markdown fences. No prose. "
        "No explanation before or after.\n"
        "- Keep node labels short (<40 chars).\n"
        "- Use sensible direction/orientation for readability.\n"
        "- Maximum 25 nodes. Keep it readable on a phone."
    )
    if strict:
        base += (
            "\n\nSTRICT MODE: Your previous reply contained extra text or "
            "the wrong opening keyword. Respond with raw Mermaid only — "
            "the very first character of your reply must be a letter "
            "from the diagram keyword. NOTHING else."
        )
    return base


class DiagramBody(BaseModel):
    prompt:       str            = Field(..., min_length=1, max_length=2000)
    repo_id:      Optional[str]  = None
    diagram_type: Optional[str]  = Field(None, max_length=24)


@router.post("/generate")
async def generate_diagram(
    body: DiagramBody,
    authorization: Optional[str] = Header(None),
) -> dict:
    user = await current_dev(authorization)
    diagram_type = detect_type(body.prompt, body.diagram_type)
    sys_msg = (
        "You are ORA, an AI system architect.  Your job is to produce "
        "concise, correct Mermaid.js diagrams.  When given a request, "
        "respond with raw Mermaid syntax only — no markdown code fences, "
        "no commentary, no headings.  Begin with a Mermaid root keyword."
    )

    from services.llm import call_llm_with_meta

    async def _try(prompt_text: str) -> str:
        meta = await call_llm_with_meta(
            system=sys_msg,
            user=prompt_text,
            max_tokens=800,
            mode="code",
            user_id=user.get("user_id"),
            review_mode="pro",
        )
        return _strip_fences((meta or {}).get("content", ""))

    code = await _try(_build_prompt(body.prompt, diagram_type))
    if not _is_valid_mermaid(code):
        # One retry under stricter instructions.
        code = await _try(_build_prompt(body.prompt, diagram_type,
                                        strict=True))

    if not _is_valid_mermaid(code):
        raise HTTPException(
            502,
            "Could not produce a valid Mermaid diagram from this prompt. "
            "Try rephrasing with fewer constraints.",
        )

    title = body.prompt.strip()[:80]
    logger.info("diagram_generated user=%s type=%s len=%d",
                user.get("user_id", "?"), diagram_type, len(code))
    return {
        "mermaid_code": code,
        "diagram_type": diagram_type,
        "title":        title,
    }
