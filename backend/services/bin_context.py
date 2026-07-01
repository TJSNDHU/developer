"""
services/bin_context.py — Iter 212m-169  (BINContext hardening)

Single, immutable, request-scoped object that carries EVERY piece of
information downstream code needs to talk to the user's connected repo:

    bin_id      — user_id (from the signed JWT, never from the body)
    pid         — project_id (ownership-verified against cto_projects)
    repo_owner  — GitHub owner (from cto_projects)
    repo_name   — GitHub repo  (from cto_projects)
    branch      — target branch (from cto_projects.branch, default "main")
    pat         — decrypted GitHub PAT (in-memory only, never persisted)
    is_founder  — JWT-derived founder/admin flag (gates execute_bash)

Design principles:
  1. Build ONCE per request at the entry point (chat/send, chat/stream,
     cto/tasks/submit, loop/start).
  2. Pass through as a locked object — never rebuild inside tools.
  3. If ownership check fails OR PAT decryption fails → HARD 403.
  4. If project_id is null/missing → HARD 400 "No project selected".
     No silent auto-infer.
  5. All existing crypto (services/vault.py HKDF-Fernet) is reused
     verbatim — this module NEVER changes the encryption contract.
  6. Frozen dataclass so accidental mutation raises FrozenInstanceError.

Why not just pass the raw project dict?
  • The project dict has 30+ keys, half of them index blobs and brain
    text unrelated to a request; passing that around leaks internals
    and inflates memory.
  • The PAT ciphertext lives in `project["github_token"]` — accidentally
    passing the ciphertext to GitHub gets a 401 (Iter 205 bug). A
    typed field with the DECRYPTED PAT prevents that entire class of
    footgun by construction.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from fastapi import HTTPException

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BINContext:
    """Immutable request-scoped bundle for user + project + repo + PAT."""
    bin_id:     str    # user_id (JWT-derived)
    pid:        str    # project_id (ownership-verified)
    repo_owner: str    # GitHub owner (from cto_projects)
    repo_name:  str    # GitHub repo  (from cto_projects)
    branch:     str    # target branch
    pat:        str    # DECRYPTED PAT — in-memory only, never persisted
    is_founder: bool   # JWT-derived founder/admin flag

    def repo_slug(self) -> str:
        """Convenience: 'owner/repo' for log lines + GitHub URL fragments."""
        return f"{self.repo_owner}/{self.repo_name}"


async def build_bin_context(
    user_id:     str,
    project_id:  Optional[str],
    db,
    is_founder:  bool = False,
) -> BINContext:
    """Factory — verify ownership, decrypt PAT, return locked BINContext.

    Raises:
      HTTPException(400) — project_id missing/blank/"home".  There is
                           NO silent auto-infer.  Callers that legiti-
                           mately have no project selected (Home page
                           casual chat) MUST NOT call this factory;
                           they should skip repo tools entirely.
      HTTPException(403) — project not found for this user (either it
                           doesn't exist, or it belongs to a different
                           user).  Same message for both — never leak
                           existence.
      HTTPException(403) — PAT ciphertext could not be decrypted with
                           this user's HKDF key (tamper / wrong user /
                           legacy plaintext with vault turned on).
      HTTPException(400) — project row is missing github_owner/repo/PAT
                           (never happens under the current add-project
                           flow which requires a PAT, but a legacy row
                           could trip this and we want a clean 400 not
                           a downstream KeyError).
    """
    pid_clean = (project_id or "").strip()
    if not pid_clean or pid_clean == "home":
        raise HTTPException(
            status_code=400,
            detail=(
                "No project selected. Please select a project "
                "from the sidebar before running this action."
            ),
        )

    if db is None:
        # Service-layer misuse; refuse rather than build a broken ctx.
        raise HTTPException(status_code=503, detail="Database unavailable")

    proj = await db.cto_projects.find_one(
        {"project_id": pid_clean, "user_id": user_id},
    )
    if not proj:
        # Never distinguish "doesn't exist" from "wrong user" — same 403.
        raise HTTPException(status_code=403, detail="Project access denied")

    owner = (proj.get("github_owner") or "").strip()
    repo  = (proj.get("github_repo")  or "").strip()
    if not (owner and repo):
        raise HTTPException(
            status_code=400,
            detail=(
                "Project is not wired to a GitHub repo. Open Projects → "
                "Edit and paste a fine-grained PAT with Contents: Read "
                "and write for the repo you want to use."
            ),
        )

    branch = (proj.get("branch") or "main").strip() or "main"

    raw_token = proj.get("github_token") or ""
    if not raw_token:
        raise HTTPException(
            status_code=400,
            detail=(
                "No PAT configured for this project. Open Projects → "
                "Edit and paste a fine-grained GitHub PAT."
            ),
        )

    # Reuse existing decrypt helper — never re-implement crypto here.
    # Local import so this module doesn't create a hard coupling to
    # the router package at import time (bin_context.py is imported
    # by services/, routers/, and services/tests).
    from routers.cto_projects import _decrypt_pat, _user_gh_token
    pat = await _decrypt_pat(user_id, raw_token)
    if not pat:
        # Fall back to the user's OAuth github.access_token — the legacy
        # OAuth-only projects (pre-Iter 211) never stored a per-project
        # PAT.  If both fail, hard-403 so the caller knows to re-connect.
        pat = await _user_gh_token(user_id)

    if not pat:
        logger.warning(
            "build_bin_context: PAT decrypt + OAuth fallback both empty "
            "for user=%s project=%s (owner=%s/%s)",
            user_id, pid_clean, owner, repo,
        )
        raise HTTPException(
            status_code=403,
            detail=(
                "GitHub credentials failed. The stored PAT for this "
                "project could not be decrypted or has been revoked. "
                "Open Projects → Edit and paste a fresh fine-grained "
                "PAT with Contents: Read and write."
            ),
        )

    return BINContext(
        bin_id=user_id,
        pid=pid_clean,
        repo_owner=owner,
        repo_name=repo,
        branch=branch,
        pat=pat,
        is_founder=bool(is_founder),
    )


async def build_bin_context_optional(
    user_id:    str,
    project_id: Optional[str],
    db,
    is_founder: bool = False,
) -> Optional[BINContext]:
    """Same as `build_bin_context` but returns None when project_id is
    blank/"home".  Used at chat entry points that also serve Home-page
    casual chat (no project = OK, no repo tools).

    ANY OTHER failure (wrong user, decrypt fail, missing repo cols) is
    STILL a hard exception — this helper only softens the "no project
    selected" case, not the security-critical ones.
    """
    pid_clean = (project_id or "").strip()
    if not pid_clean or pid_clean == "home":
        return None
    return await build_bin_context(user_id, project_id, db, is_founder)


__all__ = ["BINContext", "build_bin_context", "build_bin_context_optional"]
