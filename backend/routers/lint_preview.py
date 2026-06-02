"""
routers/lint_preview.py — Iter 47
=================================
Cheap pre-flight linter for the Ship-via-CTO brief text. Real edits
get linted server-side at commit time (`design_linter.lint_file_blocks`)
— this endpoint just gives the chat UI a green/amber/red badge so the
user knows in advance if the brief itself contains anything dangerous.

Mounted at /api/aurem-dev/lint/preview by main.py.
"""
from __future__ import annotations
from typing import Optional, List

from fastapi import APIRouter, Header
from pydantic import BaseModel, Field

from cto_services.auth import current_dev

router = APIRouter(prefix="/lint", tags=["Lint"])


class LintBody(BaseModel):
    brief: str = Field(..., min_length=1, max_length=20000)


@router.post("/preview")
async def preview_lint(
    body: LintBody,
    authorization: Optional[str] = Header(None),
):
    """Returns {blocked, warnings, block_reasons[], warning_list[]}."""
    await current_dev(authorization)

    # Synthetic "single file" so we reuse the real linter as-is.
    synth = {"__handoff_brief__.md": body.brief}

    try:
        from services.design_linter import lint_file_blocks
        r = lint_file_blocks(synth)
    except Exception as e:
        return {"blocked": False, "warnings": 0,
                "block_reasons": [], "warning_list": [],
                "error": str(e)[:200]}

    blocked: bool = bool(r.get("blocked"))
    block_reasons: List[str] = list(r.get("block_reasons", []))[:10]

    # The linter returns serialized issues — warnings need a count + a
    # short human message list.
    warning_msgs: List[str] = []
    for issue in r.get("issues", []):
        if issue.get("severity") == "warn":
            msg = issue.get("message") or issue.get("rule") or "warning"
            warning_msgs.append(msg[:200])
    return {
        "blocked": blocked,
        "warnings": len(warning_msgs),
        "block_reasons": block_reasons,
        "warning_list": warning_msgs[:10],
    }
