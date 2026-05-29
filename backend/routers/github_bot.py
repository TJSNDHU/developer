"""
aurem_cto.routers.github_bot — GitHub integration.

  • /github/status   — connection state
  • /github/push     — push staged files from a chat session to a repo
                        (uses GITHUB_TOKEN PAT for now; P3 OAuth pending)
"""
from __future__ import annotations
import base64
import logging
import os
import re
from typing import List, Optional

import httpx
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from cto_services.auth import current_dev
from cto_services.db import get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/github", tags=["AUREM CTO GitHub"])

GITHUB_API = "https://api.github.com"


class FileEntry(BaseModel):
    path: str
    content: str


class PushBody(BaseModel):
    repo: str  # "owner/name"
    branch: str = "main"
    commit_message: str = "AUREM Dev: push from chat session"
    files: Optional[List[FileEntry]] = None
    session_id: Optional[str] = None  # if files omitted, extract from session


_FILE_BLOCK_RE = re.compile(
    r"\[File:\s*([^\]\n]+)\]\s*\n```[a-zA-Z0-9]*\n(.*?)\n```",
    re.DOTALL,
)


def _extract_files_from_text(text: str) -> List[FileEntry]:
    """Find [File: path]\\n```...```\\n blocks in any message."""
    out: List[FileEntry] = []
    for m in _FILE_BLOCK_RE.finditer(text or ""):
        path = m.group(1).strip()
        content = m.group(2)
        if path:
            out.append(FileEntry(path=path, content=content))
    return out


@router.get("/status")
async def status(authorization: str = Header(None)) -> dict:
    me = await current_dev(authorization)
    token = os.getenv("GITHUB_TOKEN", "")
    return {
        "user_id": me["user_id"],
        "bot_account": "@aurem-cto-bot",
        "connected": bool(token),
        "token_configured": bool(token),
        "oauth_configured": False,
        "default_org": os.getenv("GITHUB_ORG", ""),
    }


@router.post("/push")
async def push(body: PushBody, authorization: str = Header(None)) -> dict:
    """Push files to a GitHub repo on a branch.

    File source priority: body.files > files extracted from session messages.
    Uses GITHUB_TOKEN PAT. Creates/updates each file via the Contents API.
    """
    me = await current_dev(authorization)
    token = os.getenv("GITHUB_TOKEN", "")
    if not token:
        raise HTTPException(
            400,
            "GitHub not configured — set GITHUB_TOKEN in backend/.env",
        )

    # Resolve file set
    files: List[FileEntry] = list(body.files or [])
    if not files and body.session_id:
        db = get_db()
        if db is None:
            raise HTTPException(503, "Database not connected")
        doc = await db.chat_sessions.find_one(
            {"session_id": body.session_id, "user_id": me["user_id"]},
            {"_id": 0, "turns": 1},
        )
        if doc:
            for turn in (doc.get("turns") or []):
                files.extend(_extract_files_from_text(turn.get("content") or ""))

    if not files:
        raise HTTPException(
            400,
            "No files to push. Include `body.files` or a session_id whose "
            "messages contain `[File: path]` code blocks.",
        )

    # De-dup by path (last wins)
    by_path = {}
    for f in files:
        by_path[f.path] = f
    files = list(by_path.values())

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    base = f"{GITHUB_API}/repos/{body.repo}/contents"
    results = []
    async with httpx.AsyncClient(timeout=30) as c:
        for f in files:
            # Get current sha (if file exists) so we can update instead of error
            sha = None
            r = await c.get(
                f"{base}/{f.path}",
                params={"ref": body.branch},
                headers=headers,
            )
            if r.status_code == 200:
                sha = r.json().get("sha")
            elif r.status_code not in (404,):
                results.append({
                    "path": f.path, "ok": False,
                    "error": f"sha lookup HTTP {r.status_code}: {r.text[:200]}",
                })
                continue

            payload = {
                "message": body.commit_message,
                "content": base64.b64encode(f.content.encode()).decode(),
                "branch": body.branch,
            }
            if sha:
                payload["sha"] = sha
            r = await c.put(f"{base}/{f.path}", headers=headers, json=payload)
            if r.status_code in (200, 201):
                results.append({
                    "path": f.path, "ok": True,
                    "commit": r.json().get("commit", {}).get("sha", "")[:8],
                })
            else:
                results.append({
                    "path": f.path, "ok": False,
                    "error": f"HTTP {r.status_code}: {r.text[:200]}",
                })

    ok_count = sum(1 for r in results if r.get("ok"))
    return {
        "ok": ok_count == len(files),
        "repo": body.repo,
        "branch": body.branch,
        "pushed": ok_count,
        "total": len(files),
        "results": results,
    }

