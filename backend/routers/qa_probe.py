"""
routers/qa_probe.py  —  Simulated-user QA introspection endpoint
================================================================

QA-ONLY endpoint. Not for production consumption. Gated by:

  1. `AUREM_QA_MODE=true` in the environment (default false).
  2. `X-QA-Probe-Token` header matching `AUREM_QA_TOKEN`.
  3. Standard `Authorization: Bearer <jwt>` from a seeded test user.

Behaviour: runs the *real* orchestrator + tool_executor chain used by
`chat_stream`, but returns a synchronous JSON with the tool-trail,
project scope, quota state, and reply text — so a Promptfoo assertion
can inspect the actual tool invocations, not just the free-form
response text.

This is what closes the "silent tool-skip" class of bug: if the model
says "done" but the trail is empty, the QA suite fires. In production
`chat_stream` the trail is only in SSE frames and post-hoc logs —
which is why the bug slipped historically.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from cto_services.auth import current_dev

router = APIRouter(prefix="/qa", tags=["QA — internal only"])
logger = logging.getLogger(__name__)


class ProbeBody(BaseModel):
    prompt:     str
    session_id: Optional[str] = None
    project_id: Optional[str] = None


def _qa_enabled() -> bool:
    return (os.environ.get("AUREM_QA_MODE") or "").lower() == "true"


def _valid_probe_token(header_value: Optional[str]) -> bool:
    expected = os.environ.get("AUREM_QA_TOKEN") or ""
    return bool(expected) and header_value == expected


@router.post("/chat-probe")
async def chat_probe(
    body: ProbeBody,
    authorization: Optional[str] = Header(None),
    x_qa_probe_token: Optional[str] = Header(None),
):
    if not _qa_enabled():
        raise HTTPException(404, "qa_disabled")
    if not _valid_probe_token(x_qa_probe_token):
        raise HTTPException(403, "invalid_qa_probe_token")

    user = await current_dev(authorization)
    user_id = user["user_id"]

    started = time.monotonic()
    tool_trail: list[dict] = []
    quota_state: dict = {}
    project_id_used: Optional[str] = body.project_id or None

    # ── Real orchestrator chain ────────────────────────────────
    # `chat_with_tools` already exposes `live_invocations_ref` — a
    # mutable list that captures every tool call during the run. We
    # reuse it as our QA tool-trail with zero orchestrator patching.
    try:
        from services.orchestrator import chat_with_tools
        # Extract the raw bearer token for chat_with_tools' jwt_token param.
        raw_jwt = (authorization or "").replace("Bearer ", "", 1).strip()
        raw_reply = await chat_with_tools(
            prompt=body.prompt,
            jwt_token=raw_jwt,
            session_id=body.session_id,
            user_id=user_id,
            project_id=project_id_used,
            live_invocations_ref=tool_trail,   # <── mutated during the run
        )
        # Normalise reply to a plain string so QA assertions can
        # treat it uniformly. chat_with_tools historically returned
        # a string; newer paths sometimes wrap in {ok, content, ...}.
        if isinstance(raw_reply, dict):
            reply = (
                raw_reply.get("content")
                or raw_reply.get("text")
                or raw_reply.get("reply")
                or ""
            )
        else:
            reply = raw_reply or ""
    except Exception as e:                              # noqa: BLE001
        logger.warning("[qa-probe] orchestrator error: %r", e)
        raise HTTPException(500, f"probe_chain_failed:{e!r}")

    # ── Quota snapshot after the run ───────────────────────────
    try:
        from services import scan_fix_quota as _sfq
        # Try common getters; the exact name in this repo may vary.
        for fn_name in ("get_current_quota", "get_quota_state", "get_task_usage"):
            fn = getattr(_sfq, fn_name, None)
            if fn is None:
                continue
            res = fn(user_id) if not callable(getattr(fn, "__await__", None)) else await fn(user_id)
            if res:
                quota_state = res if isinstance(res, dict) else {"raw": str(res)}
                break
    except Exception:
        quota_state = {}

    return {
        "ok":              True,
        "reply":           reply or "",
        "tool_trail":      tool_trail,
        "project_id_used": project_id_used,
        "quota_state":     quota_state,
        "elapsed_ms":      int((time.monotonic() - started) * 1000),
    }
