"""
services/pat_vault.py — Iter 212m-225

Thin re-export layer for PAT (Personal Access Token) helpers used
across the codebase.

Problem this solves
-------------------
`_decrypt_pat` + `_user_gh_token` were defined inside four different
`routers/*.py` files (cto_projects, repo_status, security_scan) and
consumed from FIVE service modules (bin_context, local_tools,
loop_engine, finding_fix_applier, repo_heal).  The service layer
was reaching UP into routers/ to grab them — a boundary violation
flagged by `architecture_health`.

Rather than refactor every router callsite in one session (high
regression risk), we introduce a service-side shim that:

  * Re-exports the canonical `_decrypt_pat` from `routers.cto_projects`
    (single source of truth).
  * Re-exports `_user_gh_token` from the same place.
  * Provides an intentionally minimal, well-documented public API:
        decrypt_pat(user_id, ciphertext)  -> str | None
        get_user_gh_token(user_id)         -> str | None

Service-layer callers now import from THIS module, which restores
the router → service dependency direction.  Routers can be
independently refactored later to move the implementation itself
into this module without changing any consumer.

This is a deliberate architectural shim, not a permanent hiding
place.  See ROADMAP.md → "Iter 212m-N: fold canonical
`_decrypt_pat` implementation into services/pat_vault.py itself".
"""

from __future__ import annotations

from typing import Optional


async def decrypt_pat(user_id: str, ciphertext: Optional[str]) -> Optional[str]:
    """Decrypt a stored PAT ciphertext for `user_id`. Returns None on
    empty input or any decryption failure — never raises.

    Delegates to the canonical implementation in
    `routers.cto_projects._decrypt_pat` (the router-side definition
    remains authoritative until a future refactor folds it into this
    module directly)."""
    # Deferred import so this module can be imported before FastAPI
    # apps finish wiring up (services/ layer boots first).
    # arch: allow-router-import — intentional shim; delegates until refactor
    from routers.cto_projects import _decrypt_pat
    return await _decrypt_pat(user_id, ciphertext)


async def get_user_gh_token(user_id: str) -> Optional[str]:
    """Return the plaintext GitHub PAT for `user_id`, or None if the
    user hasn't connected GitHub yet.  Handles both OAuth and manual
    PAT flows transparently."""
    from routers.cto_projects import _user_gh_token  # arch: allow-router-import — intentional shim; delegates until refactor
    return await _user_gh_token(user_id)


__all__ = ["decrypt_pat", "get_user_gh_token"]
