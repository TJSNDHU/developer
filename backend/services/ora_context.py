"""
services/ora_context.py — Iter 212m-170  (ORAContext hardening — Layer 0)

ORA-specific extension of BINContext that adds the FINAL boundary layer:
ORA System files (the AUREM CTO codebase itself under /app/, /tmp/,
/var/, /etc/, /usr/, /root/, /home/, and the string tokens auremcto /
aurem-cto / auremdev) are OFF-LIMITS to every session — even founder
sessions in normal chat mode.

Layers recap:
  Layer 0  — ORAContext  (this file)  — ORA system files off-limits
  Layer 1  — BINContext  (bin_id)     — user_id JWT boundary
  Layer 2  — Project     (pid)        — user_id+project_id ownership
  Layer 3  — Request     lifecycle    — built once, dies with request

Why a NEW type instead of adding fields to BINContext?
  • Semantic clarity — ORAContext IS-A BINContext plus the ORA
    system-boundary layer.  A test that says
    "assert isinstance(ctx, ORAContext)" reads as
    "this ctx carries the ORA-boundary hardening" — clearer than
    "assert bin_ctx.ora_boundary_active is True".
  • Backwards compat — every existing caller keeps sending `bin_ctx=…`
    into chat_with_tools / LoopEngine / tools.  Since ORAContext IS-A
    BINContext (subclass of the same frozen dataclass), all downstream
    ctx["bin_ctx"].pat / .repo_owner / .repo_name access keeps
    working unchanged.
  • Future extensions — the next hardening layer (per-repo secrets
    vault, per-repo rate limits) plugs into ORAContext without
    touching BINContext's contract.

This module never touches vault.py or JWT internals.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from fastapi import HTTPException

from services.bin_context import BINContext, build_bin_context

logger = logging.getLogger(__name__)


# ── ORA system boundary — path allowlist / denylist ────────────────

# Absolute pod-local paths that must NEVER be inspected via any tool
# that a user session drives.  Path checks are prefix + boundary
# (either "/app" exact or "/app/…") so we don't false-positive on the
# user's repo-relative paths like `backend/main.py` inside their own
# GitHub repo.
ORA_SYSTEM_PATHS: frozenset[str] = frozenset({
    "/app",
    "/app/backend",
    "/app/frontend",
    "/app/services",
    "/app/tests",
    "/tmp",
    "/var",
    "/var/log",
    "/etc",
    "/usr",
    "/root",
    "/home",
})

# Case-insensitive substrings that indicate the AUREM CTO internal
# codebase.  Any command arg that contains one of these MUST be
# refused even for founders in normal mode (debug_mode is the
# only escape hatch, and only a founder can enable it — see below).
ORA_SYSTEM_STRINGS: frozenset[str] = frozenset({
    "auremcto",
    "aurem-cto",
    "auremdev",
    "aurem_master_key",
    "jwt_secret",
})

# Words that the LLM must NEVER mention when talking to an end-user.
# Used in the system-prompt boundary block below — pure documentation
# for the model, no runtime enforcement (models are not deterministic
# so we back this up with the catalog filter + dispatch refusal
# already installed in Iters 212m-168 / 212m-169).
ORA_SYSTEM_TERMS: frozenset[str] = frozenset({
    "parliament.py",
    "parliament",
    "loop_engine.py",
    "loop_engine",
    "orchestrator.py",
    "orchestrator",
    "vault.py",
    "vault",
    "llm.py",
    "chat.py",
    "local_tools.py",
    "/app/backend",
    "/app/frontend",
    "AUREM_MASTER_KEY",
    "JWT_SECRET",
    "OPENROUTER_API_KEY",
    "LANGFUSE",
    "auremcto",
})


# ── ORA boundary system prompt block ───────────────────────────────

ORA_BOUNDARY_SYSTEM_RULE_TEMPLATE = """\
=== ORA ABSOLUTE BOUNDARY (non-negotiable) ===

1. You are ORA.  You work EXCLUSIVELY on the user's connected
   repository: {repo_slug}  (branch: {branch}).

2. You have NO access to the AUREM server pod's local filesystem.
   Any path starting with `/app`, `/tmp`, `/var`, `/etc`, `/usr`,
   `/root`, `/home` refers to AUREM's OWN internal server and is
   OFF-LIMITS.  Never read, list, quote, or reference those paths.

3. If the user asks about ORA's internal code (parliament, loop_engine,
   orchestrator, vault, llm.py, chat.py, local_tools.py) or about
   AUREM secrets (AUREM_MASTER_KEY, JWT_SECRET, OPENROUTER_API_KEY,
   LANGFUSE keys), reply exactly:
     "I work with your repository only.  I don't have access to my
      own system files or credentials."

4. If the user asks "which repo are you working on?", answer with
   `{repo_slug}` ONLY.  Never mention `auremcto`, `/app/backend`,
   `/app/frontend`, or any AUREM-internal directory.

5. All file reads MUST go through the repo-scoped tools:
   `read_repo_file`, `read_repo_files`, `list_repo_files`,
   `search_repo`, `semantic_search_repo`, `get_repo_structure`,
   `get_repo_info`, `write_repo_file`, `get_commit_diff`.
   Every one of these is scoped to `{repo_slug}` — never claim to have
   run a local shell command against `/app/*` or any pod path.

6. Never invent shell output, never claim to have inspected a
   `/app/…` path, never quote from a file whose path begins with `/`.

=== END ORA ABSOLUTE BOUNDARY ===

"""

# When Home casual chat (no project connected), we can't say
# "you work on {repo_slug}" but we can still enforce the pod-filesystem
# lockdown.
ORA_BOUNDARY_NO_REPO_RULE = """\
=== ORA ABSOLUTE BOUNDARY (non-negotiable — no project connected) ===

1. You are ORA.  No GitHub repository is currently connected to
   this chat.  Direct the user to connect one via Settings → GitHub
   or the sidebar's "+ New project" flow.

2. You have NO access to the AUREM server pod's local filesystem.
   Paths under `/app`, `/tmp`, `/var`, `/etc`, `/usr`, `/root`,
   `/home` refer to AUREM's OWN internal server and are OFF-LIMITS.
   Never read, list, quote, or reference them.

3. If the user asks about ORA's internal code (parliament, loop_engine,
   orchestrator, vault, llm.py, chat.py) or about AUREM secrets
   (AUREM_MASTER_KEY, JWT_SECRET, OPENROUTER_API_KEY, LANGFUSE keys),
   reply exactly:
     "I work with your repository only.  I don't have access to my
      own system files or credentials."

4. Never invent shell output, never claim to have inspected a
   `/app/…` path, never quote from a file whose path begins with `/`.

=== END ORA ABSOLUTE BOUNDARY ===

"""


# ── ORAContext dataclass ───────────────────────────────────────────


@dataclass(frozen=True)
class ORAContext(BINContext):
    """Extended, request-scoped context that carries the ORA system-
    boundary flag on top of the BINContext (user + project + PAT).

    Additional fields:
      ora_boundary_active — always True by default.  A founder can
                            set it to False (via `debug_mode`) to
                            unlock /app/* inspection for AUREM dev
                            work.  A NON-founder attempting to set
                            this to False is refused at build time
                            (see build_ora_context).
      debug_mode          — Founder-only escape hatch.  When True,
                            execute_bash allows /app/* paths.  When
                            False (default), execute_bash refuses
                            /app/* even for founders.
    """
    ora_boundary_active: bool = True
    debug_mode: bool = False

    @property
    def repo_full_name(self) -> str:
        """Convenience — 'owner/repo' for log lines and system prompts."""
        return f"{self.repo_owner}/{self.repo_name}"


# ── Path / string boundary checks ──────────────────────────────────


def path_hits_ora_boundary(candidate: str) -> Optional[str]:
    """Return the first ORA_SYSTEM_PATHS entry the candidate matches,
    else None.  Match is prefix + boundary — `/app/backend` matches
    `/app`, `/app/backend`, `/app/backend/main.py`, but NOT `backend/`.

    We also scan for ORA_SYSTEM_STRINGS (case-insensitive substring —
    catches `cat /root/.env`, `grep AUREM_MASTER_KEY`, etc.).
    """
    if not candidate or not isinstance(candidate, str):
        return None
    cand = candidate.strip()
    if not cand:
        return None

    # 1) Path prefix / exact match check.
    for p in ORA_SYSTEM_PATHS:
        # Exact match, or path with a trailing "/" or the whole thing
        # is a token bounded by shell separators (whitespace / | & ;).
        if cand == p or cand.startswith(p + "/"):
            return p
        # Also scan the candidate as a shell arg blob — `cat /app/x`
        # contains the token `/app/x` after tokenisation.  Use a
        # simple regex that finds the path as a word boundary.
        if re.search(rf"(?:^|[\s|&;<>])({re.escape(p)})(?:[\s/|&;<>]|$)", cand):
            return p

    # 2) String allowlist — case-insensitive substring.
    low = cand.lower()
    for s in ORA_SYSTEM_STRINGS:
        if s in low:
            return s

    return None


# ── Factory ────────────────────────────────────────────────────────


async def build_ora_context(
    user_id:      str,
    project_id:   Optional[str],
    db,
    is_founder:   bool = False,
    debug_mode:   bool = False,
) -> ORAContext:
    """Factory — wraps `build_bin_context` and adds the ORA system-
    boundary flag.  Same 400/403 semantics as the parent (missing
    project → 400, wrong-user / bad-PAT → 403).

    debug_mode:
      • Founder-only.  Non-founder callers pass debug_mode=True →
        we silently coerce back to False (never trust caller-supplied
        privileges).
      • When True AND is_founder=True → ora_boundary_active=False
        so execute_bash allows /app/* paths (founder ORA-on-AUREM
        development mode).  Even in this mode the LLM still sees
        the boundary rule in its system prompt — the model is asked
        to opt back in explicitly by the founder.
    """
    # Reuse the vetted BINContext factory — ALL its checks (ownership,
    # decrypt, missing repo cols) fire here first.
    bc = await build_bin_context(
        user_id=user_id,
        project_id=project_id,
        db=db,
        is_founder=is_founder,
    )

    # Coerce debug_mode: only founders may enable it.  Anyone else
    # sending debug_mode=True gets it silently stripped — do NOT
    # raise, that would leak the existence of the flag to attackers.
    eff_debug = bool(debug_mode) and bool(is_founder)
    boundary_active = not eff_debug   # False only in founder debug mode.

    return ORAContext(
        bin_id=bc.bin_id,
        pid=bc.pid,
        repo_owner=bc.repo_owner,
        repo_name=bc.repo_name,
        branch=bc.branch,
        pat=bc.pat,
        is_founder=bc.is_founder,
        ora_boundary_active=boundary_active,
        debug_mode=eff_debug,
    )


async def build_ora_context_optional(
    user_id:    str,
    project_id: Optional[str],
    db,
    is_founder: bool = False,
    debug_mode: bool = False,
) -> Optional[ORAContext]:
    """Same as `build_ora_context` but returns None for blank/"home"
    project_id.  Used at chat entry points that also serve Home
    casual chat.  All other failure modes (wrong user, decrypt fail)
    still raise — this helper only softens "no project selected"."""
    pid_clean = (project_id or "").strip()
    if not pid_clean or pid_clean == "home":
        return None
    return await build_ora_context(
        user_id, project_id, db, is_founder, debug_mode
    )


def render_ora_boundary_prompt(ctx: Optional[ORAContext]) -> str:
    """Return the ORA boundary system-prompt block to prepend for
    THIS session.  When ctx has a repo, we inject the repo slug
    into the boundary; when there's no ctx (Home chat), we emit the
    no-repo variant.

    Founders in debug_mode still see the boundary block — the model
    is told to reset its default, and the founder can override via
    a specific "run bash on /app" phrasing that is then permitted
    at dispatch time.  We don't remove the prompt block; we just
    lift the execute_bash gate.
    """
    if ctx is None:
        return ORA_BOUNDARY_NO_REPO_RULE
    return ORA_BOUNDARY_SYSTEM_RULE_TEMPLATE.format(
        repo_slug=ctx.repo_full_name,
        branch=ctx.branch or "main",
    )


__all__ = [
    "ORAContext",
    "build_ora_context",
    "build_ora_context_optional",
    "path_hits_ora_boundary",
    "render_ora_boundary_prompt",
    "ORA_SYSTEM_PATHS",
    "ORA_SYSTEM_STRINGS",
    "ORA_SYSTEM_TERMS",
    "ORA_BOUNDARY_SYSTEM_RULE_TEMPLATE",
    "ORA_BOUNDARY_NO_REPO_RULE",
]
