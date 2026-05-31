"""
routers/support.py — User-facing ticket submission + admin reply/resolve.

Schema:
  cto_support: {ticket_id, user_id, user_email, subject, body, status,
                created_at, updated_at, last_reply_at}
  cto_support_messages: {ticket_id, sender ('user'|'admin'),
                          message, ts}

Mounted under /api/aurem-dev (so user routes live at /support/* and
admin routes are reused from routers/admin.py).
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from cto_services.auth import current_dev
from cto_services.db import require_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/support", tags=["Support"])


class CreateTicket(BaseModel):
    subject: str
    body: str


@router.post("/tickets")
async def create_ticket(
    body: CreateTicket,
    authorization: Optional[str] = Header(None),
) -> dict:
    user = await current_dev(authorization)
    db = require_db()
    ticket_id = f"tkt_{uuid.uuid4().hex[:12]}"
    now = time.time()
    await db.cto_support.insert_one({
        "ticket_id": ticket_id,
        "user_id": user.get("user_id"),
        "user_email": user.get("email"),
        "subject": body.subject.strip()[:200] or "(no subject)",
        "body": body.body.strip()[:5000],
        "status": "open",
        "created_at": now,
        "updated_at": now,
        "last_reply_at": now,
    })
    await db.cto_support_messages.insert_one({
        "ticket_id": ticket_id,
        "sender": "user",
        "message": body.body.strip(),
        "ts": now,
    })
    return {"ok": True, "ticket_id": ticket_id}


@router.get("/tickets")
async def list_my_tickets(authorization: Optional[str] = Header(None)) -> dict:
    user = await current_dev(authorization)
    db = require_db()
    tickets = await db.cto_support.find(
        {"user_id": user.get("user_id")},
        {"_id": 0},
    ).sort("updated_at", -1).limit(50).to_list(50)
    return {"tickets": tickets}


@router.get("/tickets/{ticket_id}")
async def get_my_ticket(
    ticket_id: str,
    authorization: Optional[str] = Header(None),
) -> dict:
    user = await current_dev(authorization)
    db = require_db()
    t = await db.cto_support.find_one(
        {"ticket_id": ticket_id, "user_id": user.get("user_id")},
        {"_id": 0},
    )
    if not t:
        raise HTTPException(404, "Ticket not found")
    msgs = await db.cto_support_messages.find(
        {"ticket_id": ticket_id}, {"_id": 0},
    ).sort("ts", 1).to_list(200)
    t["messages"] = msgs
    return t
