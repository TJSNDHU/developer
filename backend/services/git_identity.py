"""
services/git_identity.py — Iter 212m-218

Central resolver for the git author identity attached to every commit
we push through `github_api_writer.commit_files()`.

Before this iter, every commit landed as
`AUREM CTO <cto@auremcto.com>` — hardcoded defaults on the writer.
That made every AUREM-shipped commit look like a bot did it, hiding
the user's actual authorship on `git blame`, GitHub contribution
graphs and PR author fields.

New model (industry-standard, matches GitHub Copilot / Devin / Cursor):

  * **author**       = the real developer (their GitHub identity —
                       resolved from `dev_users.github.name/email`,
                       or their profile row, or a `.noreply` fallback).
  * **committer**    = same as author (we don't split author vs
                       committer — one identity per commit).
  * **co-author**    = `ORA by Aurem CTO <cto@auremcto.com>` appended
                       as a `Co-authored-by:` trailer in the commit
                       body so GitHub credits both parties on the
                       commit page and the PR contributor list.

Every commit message is also normalised to Conventional Commits format:
`{type}: {summary} [via ORA]` with `type ∈ {feat, fix, refactor,
chore, docs, test, perf, style, ci, build}`.  The `[via ORA]` suffix
is a transparency marker — same convention Devin PRs use.

Public surface:

    resolve_git_identity(db, user_id) -> (name, email)
        Async. Never raises — falls back to a stable synthetic on any
        DB failure so a commit never blocks on identity resolution.

    build_commit_message(*, task_type, summary, body=None) -> str
        Sync. Returns a Conventional-Commits-formatted message with
        the `[via ORA]` marker in the subject and the co-author
        trailer in the body.

    infer_commit_type(user_message: str) -> str
        Sync. Best-effort classifier that maps a free-text task
        description to one of the Conventional Commit types.

    CO_AUTHOR_TRAILER
        Constant — the exact `Co-authored-by:` line appended to
        every commit body.  Frozen so `git log --grep` works across
        the whole history.
"""
from __future__ import annotations

import logging
import re
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


# The co-author trailer.  GitHub renders this on the commit page and
# credits the second identity on the PR contributor list.
# NB: the display name is intentionally "ORA by Aurem CTO" — matches
# the product name the user sees in the UI, so `git blame` reads as
# "written by <you>, co-authored by ORA by Aurem CTO".
CO_AUTHOR_TRAILER = (
    "Co-authored-by: ORA by Aurem CTO <cto@auremcto.com>"
)

# Conventional Commits vocabulary.  We use a fixed set so type
# inference stays deterministic across releases.
_CONVENTIONAL_TYPES = {
    "feat", "fix", "refactor", "chore", "docs",
    "test", "perf", "style", "ci", "build",
}

_TYPE_HINTS: list[tuple[re.Pattern, str]] = [
    # Order matters — the first pattern to match wins.
    (re.compile(r"\bfix(es|ed|ing)?\b|\bbug\b|\bresolve[sd]?\b|\bpatch\b|\bbroken\b|\bissue\b|\bcrash\b|\berror\b",  re.I), "fix"),
    (re.compile(r"\brefactor(ed|ing)?\b|\bcleanup\b|\brestructure\b|\brename\b|\bextract\b|\brewrite\b",              re.I), "refactor"),
    (re.compile(r"\btest(s|ing|ed)?\b|\bpytest\b|\bjest\b|\bcoverage\b|\bunit test\b|\bregression\b",                 re.I), "test"),
    (re.compile(r"\bdocs?\b|\bdocument(ed|ation)?\b|\breadme\b|\bcomment\b|\bchangelog\b",                            re.I), "docs"),
    (re.compile(r"\bperf(ormance)?\b|\boptimi[sz]e\b|\bspeed( ?up)?\b|\bfaster\b|\breduce.*(latency|memory)\b",       re.I), "perf"),
    (re.compile(r"\bstyle\b|\bformat\b|\blint\b|\bprettier\b|\bwhitespace\b",                                         re.I), "style"),
    (re.compile(r"\bci\b|\bworkflow\b|\bgithub.actions\b|\bpipeline\b",                                               re.I), "ci"),
    (re.compile(r"\bbuild\b|\bdependenc(y|ies)\b|\bpackage.json\b|\brequirements\b|\bDockerfile\b|\bbump\b|\bupgrade\b|\bdowngrade\b|\bnpm\b|\byarn\b|\bpip install\b", re.I), "build"),
    (re.compile(r"\badd(s|ed|ing)?\b|\bnew\b|\bimplement(s|ed|ing)?\b|\bcreate(s|d)?\b|\bintroduce(s|d)?\b|\bfeature\b", re.I), "feat"),
]


def infer_commit_type(user_message: str) -> str:
    """Best-effort classifier: free-text task → Conventional Commits type.
    Defaults to `chore` when nothing matches so we never crash on empty
    input.  This is intentionally lightweight — no LLM call, no DB
    lookup — because the git-writer path must stay fast + offline-safe.
    """
    if not user_message:
        return "chore"
    # Already-prefixed messages ("fix: …") stay as-is — respect what
    # the caller decided.
    m = re.match(r"^\s*(feat|fix|refactor|chore|docs|test|perf|style|ci|build)"
                 r"(?:\([^)]+\))?\s*:", user_message, re.I)
    if m and m.group(1).lower() in _CONVENTIONAL_TYPES:
        return m.group(1).lower()
    for rx, t in _TYPE_HINTS:
        if rx.search(user_message):
            return t
    return "chore"


def _first_line(text: str, cap: int = 72) -> str:
    """Return the first non-empty line trimmed to `cap` chars.
    Conventional Commits recommends <= 72 char subject lines."""
    for raw in (text or "").splitlines():
        line = raw.strip()
        if line:
            if len(line) > cap:
                line = line[: cap - 1].rstrip() + "…"
            return line
    return ""


def build_commit_message(
    *,
    task_type: Optional[str] = None,
    summary:   Optional[str] = None,
    body:      Optional[str] = None,
    user_message: Optional[str] = None,
) -> str:
    """Assemble a Conventional-Commits-formatted message with the
    `[via ORA]` transparency marker and the co-author trailer.

    Subject:   `{type}: {summary} [via ORA]`
    Body:      optional caller-supplied paragraph, then a blank line,
               then the `Co-authored-by:` trailer.

    Both `task_type` and `summary` are inferred from `user_message`
    when omitted, so a caller with only a raw task description can
    just pass `user_message="..."`.
    """
    if not task_type:
        task_type = infer_commit_type(user_message or summary or "")
    if task_type not in _CONVENTIONAL_TYPES:
        task_type = "chore"

    if not summary:
        summary = _first_line(user_message or "") or "apply automated change"

    # If the summary already carries the "[via ORA]" marker (e.g. a
    # test that pre-formats it) — don't double-append.
    subject = f"{task_type}: {summary}"
    if "[via ORA]" not in subject:
        # Cap subject at 72 chars INCLUDING the marker; trim summary
        # first so we don't accidentally exceed.
        marker = " [via ORA]"
        max_summary_len = 72 - len(f"{task_type}: ") - len(marker)
        if len(summary) > max_summary_len:
            summary = summary[: max_summary_len - 1].rstrip() + "…"
        subject = f"{task_type}: {summary}{marker}"

    body_lines: list[str] = []
    if body:
        body_lines.append(body.strip())
    # Blank line separating body from trailers (git convention).
    body_lines.extend(["", CO_AUTHOR_TRAILER])
    return subject + "\n\n" + "\n".join(body_lines).lstrip("\n")


async def resolve_git_identity(db, user_id: str) -> Tuple[str, str]:
    """Look up the real developer's git author identity for `user_id`.

    Priority order (first non-empty wins):
        name    : dev_users.github.name  →
                  dev_users.name          →
                  dev_users.github.login →
                  local part of email     →
                  "AUREM Developer"       (last-resort synthetic)
        email   : dev_users.github.email →
                  dev_users.email        →
                  "<login>@users.noreply.github.com"  (GitHub-native fallback)
                  "aurem-user@users.noreply.github.com"  (last-resort synthetic)

    Never raises — a DB blip degrades to the synthetic fallback so a
    commit push is never blocked on identity resolution.
    """
    fallback_name  = "AUREM Developer"
    fallback_email = "aurem-user@users.noreply.github.com"
    if db is None or not user_id:
        return fallback_name, fallback_email
    try:
        u = await db.dev_users.find_one(
            {"user_id": user_id},
            {"_id": 0, "email": 1, "name": 1, "github": 1},
        )
    except Exception as e:                                # noqa: BLE001
        logger.debug("resolve_git_identity: db read failed: %r", e)
        return fallback_name, fallback_email

    if not u:
        return fallback_name, fallback_email

    gh    = (u.get("github") or {}) if isinstance(u.get("github"), dict) else {}
    email = (u.get("email") or "").strip()
    login = (gh.get("login") or "").strip()

    # ── Name ──────────────────────────────────────────────────────
    name = (
        (gh.get("name")  or "").strip() or
        (u.get("name")   or "").strip() or
        login or
        (email.split("@", 1)[0] if email else "") or
        fallback_name
    )

    # ── Email ─────────────────────────────────────────────────────
    resolved_email = (
        (gh.get("email") or "").strip() or
        email or
        (f"{login}@users.noreply.github.com" if login else "") or
        fallback_email
    )

    return name, resolved_email


__all__ = [
    "CO_AUTHOR_TRAILER",
    "resolve_git_identity",
    "build_commit_message",
    "infer_commit_type",
]
