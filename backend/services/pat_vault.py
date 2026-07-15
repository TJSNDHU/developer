"""
services/pat_vault.py — Iter 212m-230 (canonical implementation)

Centralised PAT (Personal Access Token) helpers.  Previously this
module was a router-side shim that delegated back to
`routers.cto_projects` — creating the very cycle
`services/bin_context → services/pat_vault → routers/cto_projects → …`
that `architecture_health` was flagging.

Iter 212m-230 folds the canonical `_decrypt_pat`, `_encrypt_pat` and
`_user_gh_token` implementations HERE (services/ layer, single source
of truth).  `routers/cto_projects.py` now re-exports them for backward
compatibility, so downstream call-sites don't need to change.

Public API
==========
    async decrypt_pat(user_id, token)  -> str | None
    async encrypt_pat(user_id, token)  -> str | None
    async get_user_gh_token(user_id)   -> str | None

Legacy shim names (still exported for router-side callers):
    _decrypt_pat, _encrypt_pat, _user_gh_token
"""

from __future__ import annotations

from typing import Optional


async def _user_gh_token(user_id: str) -> Optional[str]:
    """Return the OAuth-based GitHub access token stored on
    `dev_users[<user_id>].github.access_token`.  Returns None when the
    user hasn't connected GitHub or the DB is unreachable."""
    # Deferred import: services/ layer must never import from routers/
    # at module scope — routers pull services in, not the other way
    # around.  `get_db` is defined in a stateless helper module.
    from cto_services.db import get_db
    db = get_db()
    if db is None:
        return None
    u = await db.dev_users.find_one({"user_id": user_id}, {"_id": 0, "github": 1})
    return ((u or {}).get("github") or {}).get("access_token")


async def _encrypt_pat(user_id: str, token: Optional[str]) -> Optional[str]:
    """Encrypt `token` at rest with per-user HKDF-Fernet via
    `services.vault`.  Idempotent — a `v1:`-prefixed ciphertext is
    passed through untouched.  Fails open (returns the plaintext) so
    project creation never blocks on a crypto outage."""
    if not token:
        return token
    if token.startswith("v1:"):
        return token   # already encrypted
    try:
        from services.vault import encrypt, is_vault_available
        if not is_vault_available():
            return token
        return await encrypt(user_id, token, kind="github_token")
    except Exception:
        return token   # fail-open: never block project creation on crypto


async def _decrypt_pat(user_id: str, token: Optional[str]) -> Optional[str]:
    """Decrypt a stored PAT ciphertext for `user_id`.  Returns:
      - the plaintext PAT on success,
      - the original string on legacy (pre-encryption) rows,
      - None on empty input or tamper/wrong-user failure.

    Never raises — every call site can rely on a str | None contract.
    """
    if not token:
        return token
    if not token.startswith("v1:"):
        return token   # legacy plaintext — pass through
    try:
        from services.vault import decrypt
        return await decrypt(user_id, token, kind="github_token")
    except Exception:
        return None    # tamper / wrong user → treat as missing token


# ── Public (non-underscore) API ─────────────────────────────────────
# Preferred name for new callers.  Retained the underscored aliases
# above for backwards compatibility with existing router code that
# still writes `_decrypt_pat` explicitly.
decrypt_pat        = _decrypt_pat
encrypt_pat        = _encrypt_pat
get_user_gh_token  = _user_gh_token


__all__ = [
    "decrypt_pat", "encrypt_pat", "get_user_gh_token",
    "_decrypt_pat", "_encrypt_pat", "_user_gh_token",
]
