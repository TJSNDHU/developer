"""
services/ora_chat/grounding_check.py — Iter 212m-254

CHEAP post-response grounding check. Runs after every ORA reply that
had a retrieval context (deep-research fan-out OR compact-tree only)
and flags "specific claims" in the reply that aren't grounded in the
retrieved context.

Design (deliberately conservative):
  - Pure regex extraction — NO LLM calls in the fast path. Adding an
    LLM per response would multiply cost + latency.
  - Only flags SPECIFIC, VERIFIABLE claims:
      * `test_iter*.py` and other named test files
      * paths ending in `.py` / `.jsx` / `.tsx` / `.ts` / `.js`
      * symbols in backticks that look like function/class names
        (`snake_case()` or `CamelCase`)
      * common cron/service filename patterns (`*_cron.py`,
        `*_service.py`)
  - A claim is "grounded" iff its exact string appears in EITHER the
    retrieved context (fan-out results) OR the compact-tree/system-
    highlights blocks that were in the system prompt.
  - Anything ungrounded → written to Mongo collection
    `ora_hallucination_log` with the query + reply + evidence.

Later layers (Iter 212m-255, 212m-256) will:
  - Batch-classify the log every 20 entries via DeepSeek V3.
  - Surface founder-approved patterns as candidate house rules.

NEVER auto-inject rules — human-in-the-loop for the promotion step.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Iterable, Optional

from cto_services.db import get_db

logger = logging.getLogger(__name__)

# ─── Claim extractors ────────────────────────────────────────────
# Only match tokens that look like SPECIFIC identifiers (not any word).
# Regex is intentionally strict to keep false-positive rate low.
_RE_PY_TEST      = re.compile(r"\btest_iter[0-9a-z_]+\.py\b")
_RE_PY_FILE      = re.compile(r"\b[A-Za-z_][A-Za-z0-9_/-]*\.(?:py|jsx|tsx|ts|js)\b")
_RE_SYMBOL_BT    = re.compile(r"`([A-Za-z_][A-Za-z0-9_]{2,})(?:\(\))?`")
_RE_CRON_LIKE    = re.compile(r"\b[a-z_]+_(?:cron|scheduler|watcher|worker)\.py\b")

# Stop-list of well-known false positives — words that look like symbols
# but are conversational or generic (Python keywords, common vars).
_SYMBOL_STOP: frozenset = frozenset({
    "None", "True", "False", "Exception", "self", "cls",
    "async", "await", "yield", "return", "import", "from",
    "GET", "POST", "PUT", "DELETE", "PATCH", "HTTP",
    "JSON", "HTML", "URL", "API", "SDK", "DNS", "TLS",
})


def extract_claims(text: str) -> list[str]:
    """Extract concrete, verifiable claims from a chat reply.

    Returns a de-duped list of tokens that look like file paths,
    function names, or specific test-file names. Everything else
    (adjectives, plans, opinions) is left alone.
    """
    if not text:
        return []
    claims: list[str] = []
    seen: set[str] = set()
    def _add(x: str) -> None:
        x = x.strip()
        if x and x not in seen:
            seen.add(x)
            claims.append(x)
    for m in _RE_PY_TEST.finditer(text):
        _add(m.group(0))
    for m in _RE_PY_FILE.finditer(text):
        _add(m.group(0))
    for m in _RE_CRON_LIKE.finditer(text):
        _add(m.group(0))
    for m in _RE_SYMBOL_BT.finditer(text):
        sym = m.group(1)
        if sym not in _SYMBOL_STOP and not sym.isupper():
            _add(sym)
    return claims


def find_ungrounded(claims: Iterable[str],
                     contexts: Iterable[str]) -> list[str]:
    """A claim is grounded iff its exact string is a substring of
    ANY of the provided contexts (retrieved results + system-prompt
    tree/highlights). Case-sensitive on purpose — filenames matter.
    """
    joined = "\n".join(c or "" for c in contexts)
    return [c for c in claims if c and c not in joined]


async def log_hallucination(*,
                             user_id: str,
                             session_id: str,
                             query: str,
                             reply: str,
                             ungrounded: list[str],
                             route: str,
                             sources_fired: list[str],
                             contexts_seen: dict,
                             fabricated: Optional[list[str]] = None,
                             unverified: Optional[list[str]] = None) -> None:
    """Write one row to `ora_hallucination_log` — never blocks the
    reply path. Kept idempotent-ish: same (session_id, message_hash)
    within 60s is deduped so a single burst of streaming events
    doesn't create 50 rows.
    """
    try:
        db = get_db()
    except Exception as e:
        logger.warning("hallucination_log skipped — db unavailable: %s", e)
        return
    try:
        # Cheap dedup — same session + same claim-set within a minute.
        recent = await db.ora_hallucination_log.find_one({
            "session_id": session_id,
            "ungrounded": ungrounded,
        }, {"_id": 1})
        if recent:
            return
        await db.ora_hallucination_log.insert_one({
            "created_at":     datetime.now(timezone.utc).isoformat(),
            "user_id":        user_id,
            "session_id":     session_id,
            "query":          query[:2000],
            "reply":          reply[:6000],
            "ungrounded":     ungrounded[:20],
            "fabricated":     (fabricated or [])[:20],
            "unverified":     (unverified or [])[:20],
            "route":          route,
            "sources_fired":  sources_fired,
            "contexts_seen":  {k: (v or "")[:2000]
                                for k, v in contexts_seen.items()},
            "reviewed":       False,
        })
    except Exception as e:                                      # noqa: BLE001
        logger.warning("hallucination_log write failed: %r", e)


async def check_and_log(*,
                         user_id: str,
                         session_id: str,
                         query: str,
                         reply: str,
                         route: str,
                         sources_fired: Optional[list[str]] = None,
                         retrieved_context: Optional[str] = None,
                         codebase_tree: Optional[str] = None,
                         system_highlights: Optional[str] = None) -> dict:
    """One-shot: extract claims → check grounding → log if needed.
    Returns a summary dict for the caller (mostly for tests):
        {claims: [...], ungrounded: [...], logged: bool}
    """
    claims = extract_claims(reply)
    if not claims:
        return {"claims": [], "ungrounded": [], "logged": False}
    ungrounded = find_ungrounded(
        claims,
        [retrieved_context, codebase_tree, system_highlights],
    )
    if not ungrounded:
        return {"claims": claims, "ungrounded": [], "logged": False}
    await log_hallucination(
        user_id=user_id, session_id=session_id, query=query, reply=reply,
        ungrounded=ungrounded, route=route,
        sources_fired=sources_fired or [],
        contexts_seen={
            "retrieved":         retrieved_context or "",
            "codebase_tree":     codebase_tree or "",
            "system_highlights": system_highlights or "",
        },
    )
    return {"claims": claims, "ungrounded": ungrounded, "logged": True}


# ─── Iter 269 P2a — line-number + slash-command claim checks ───────
_LINE_CLAIM_RE = re.compile(
    r"([\w/.\-]+\.(?:py|jsx|tsx|ts|js))[^.\n]{0,40}?\bline\s+(\d+)|"
    r"\bline\s+(\d+)[^.\n]{0,40}?(?:of|in|mein|me)\s+`?([\w/.\-]+\.(?:py|jsx|tsx|ts|js))",
    re.IGNORECASE)
_SLASH_CMD_RE = re.compile(r"(?<![\w/.])/([a-z][a-z0-9\-]{3,})(?![\w/\-])")


def extract_line_claims(reply: str) -> list[tuple[str, int]]:
    """`file.py ... line N` / `line N of file.py` pairs — a line-number
    claim is only trustworthy with /read output from this turn."""
    out = []
    for m in _LINE_CLAIM_RE.finditer(reply or ""):
        fname = m.group(1) or m.group(4)
        line = m.group(2) or m.group(3)
        if fname and line:
            out.append((fname, int(line)))
    return out[:10]


def extract_unknown_commands(reply: str) -> list[str]:
    """Slash-command tokens in the reply that are NOT real ORA
    commands (e.g. an invented `/deploy-production`)."""
    from services.ora_chat.safety import KNOWN_COMMANDS
    known = {c.lstrip("/") for c in KNOWN_COMMANDS}
    out = []
    for m in _SLASH_CMD_RE.finditer(reply or ""):
        tok = m.group(1)
        if tok not in known and f"/{tok}" not in out:
            out.append(f"/{tok}")
    return out[:10]


# ─── Iter 264 Fix A — two-level classification vs canonical index ──
_PATH_EXTS = (".py", ".jsx", ".tsx", ".ts", ".js")


def _normalize_path(claim: str) -> str:
    c = claim.strip().lstrip("/")
    if c.startswith("app/"):
        c = c[4:]
    return c


def classify_claims(claims: Iterable[str], *, canonical: dict,
                    user_query: str = "",
                    turn_contexts: Optional[list] = None) -> dict:
    """Two-level split (Fix A2):
      FABRICATED  — path claim whose file does NOT exist anywhere in
                    the canonical index and wasn't typed by the user
                    → hard flag (user-facing warning).
      UNVERIFIED  — path exists in the repo but wasn't retrieved this
                    turn (no tree/BM25//read) → soft flag, log-only.
    Symbol claims (backticked names) are NEVER hard-flagged — too
    noisy — they go to UNVERIFIED at worst.
    """
    joined = "\n".join(c or "" for c in (turn_contexts or []))
    q = user_query or ""
    paths: set = canonical.get("paths") or set()
    basenames: set = canonical.get("basenames") or set()
    defs: set = canonical.get("defs") or set()
    fabricated: list[str] = []
    unverified: list[str] = []
    for c in claims:
        if not c or c in q:
            continue  # user typed it → they may discuss it freely
        if c.endswith(_PATH_EXTS):
            n = _normalize_path(c)
            base = n.rsplit("/", 1)[-1]
            exists = (n in paths or base in basenames
                      or any(p.endswith("/" + n) for p in paths))
            if not exists:
                fabricated.append(c)
            elif c not in joined and n not in joined:
                unverified.append(c)
        else:
            if c not in joined and c not in defs:
                unverified.append(c)
    return {"fabricated": fabricated, "unverified": unverified}


async def run_post_response_check(*,
                                   user_id: str,
                                   session_id: str,
                                   query: str,
                                   reply: str,
                                   route: str,
                                   sources_fired: Optional[list[str]] = None,
                                   retrieved_context: Optional[str] = None,
                                   codebase_tree: Optional[str] = None,
                                   system_highlights: Optional[str] = None) -> dict:
    """Iter 264 Fix A3 — SHARED post-response hook, called after EVERY
    assistant turn (general /message, fallback, slash, deep-research).
    Never raises; returns:
        {claims, fabricated, unverified, logged}
    """
    empty = {"claims": [], "fabricated": [], "unverified": [], "logged": False}
    try:
        claims = extract_claims(reply)
        line_claims = extract_line_claims(reply)        # Iter 269 P2a
        unknown_cmds = extract_unknown_commands(reply)  # Iter 269 P2a
        if not claims and not line_claims and not unknown_cmds:
            return empty
        from services.ora_chat import codebase_index
        try:
            canonical = await codebase_index.canonical_paths()
        except Exception as e:                               # noqa: BLE001
            logger.warning("canonical index unavailable: %r", e)
            canonical = {}
        if claims and canonical.get("paths"):
            cls = classify_claims(
                claims, canonical=canonical, user_query=query,
                turn_contexts=[retrieved_context, codebase_tree,
                               system_highlights],
            )
        else:
            cls = {"fabricated": [], "unverified": []}
        # Iter 269 P2a — line-number claims are UNVERIFIED unless the
        # file's content was actually retrieved this turn.
        joined_ctx = "\n".join(x or "" for x in (retrieved_context,
                                                  codebase_tree,
                                                  system_highlights))
        for fname, line in line_claims:
            base = fname.rsplit("/", 1)[-1]
            if base in (query or ""):
                continue
            tag = f"{fname}:L{line}"
            if base not in joined_ctx and tag not in cls["unverified"]:
                cls["unverified"].append(tag)
        # Iter 269 P2a — invented slash-commands are HARD fabrications
        # (deterministic: KNOWN_COMMANDS lookup, zero cost).
        for cmd in unknown_cmds:
            if cmd not in (query or "") and cmd not in cls["fabricated"]:
                cls["fabricated"].append(cmd)
        logged = False
        if cls["fabricated"] or cls["unverified"]:
            await log_hallucination(
                user_id=user_id, session_id=session_id,
                query=query, reply=reply,
                ungrounded=cls["fabricated"] + cls["unverified"],
                fabricated=cls["fabricated"],
                unverified=cls["unverified"],
                route=route, sources_fired=sources_fired or [],
                contexts_seen={
                    "retrieved":         retrieved_context or "",
                    "codebase_tree":     codebase_tree or "",
                    "system_highlights": system_highlights or "",
                },
            )
            logged = True
        return {"claims": claims, "fabricated": cls["fabricated"],
                "unverified": cls["unverified"], "logged": logged}
    except Exception as e:                                   # noqa: BLE001
        logger.warning("post-response grounding hook failed: %r", e)
        return empty
