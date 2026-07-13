"""
Iter 212m-218 — Commit identity + Conventional Commits + Co-authored-by.

Locks the three-part fix:

  1. `commit_files()` no longer accepts hardcoded "AUREM CTO" defaults.
     Callers MUST pass real developer identity via
     `services.git_identity.resolve_git_identity(db, user_id)`.

  2. Every commit message goes through
     `services.git_identity.build_commit_message(...)` which normalises
     to Conventional Commits format (`type: summary [via ORA]`).

  3. Every commit body ends with the exact
     `Co-authored-by: ORA by Aurem CTO <cto@auremcto.com>` trailer so
     GitHub credits ORA as co-author on the commit page and the PR
     contributor list.

The suite has three flavours of test:

  * Pure unit tests for `resolve_git_identity` priority order +
    `build_commit_message` shape + `infer_commit_type` classifier.
  * Contract test: `commit_files` raises ValueError when the caller
    forgets to pass author identity.
  * Static grep tests: every commit_files() call site MUST resolve
    identity before invoking the writer, and every commit_message
    routed through the writer MUST include the co-author trailer.
"""

from __future__ import annotations

import re
import pytest

from services.git_identity import (
    CO_AUTHOR_TRAILER,
    build_commit_message,
    infer_commit_type,
    resolve_git_identity,
)
from services.github_api_writer import commit_files


# ══════════════════════════════════════════════════════════════════
# 1. resolve_git_identity — priority order
# ══════════════════════════════════════════════════════════════════
class _FakeDB:
    """Minimal Motor stand-in that returns a preset dev_users doc."""
    def __init__(self, doc):
        self._doc = doc
        self.dev_users = self

    async def find_one(self, filt, projection=None):
        return self._doc


@pytest.mark.asyncio
async def test_resolve_prefers_github_subdoc_name_and_email():
    """Highest-priority path — user filled `github.name` + `github.email`
    (either from OAuth signup or connect flow). This is the ONLY
    branch that yields a fully-real developer identity."""
    db = _FakeDB({
        "email": "user@aurem.dev",
        "name":  "profile name (should lose)",
        "github": {
            "login": "tejinderauremdev",
            "name":  "Tejinder Singh",
            "email": "tejinder@aurem.dev",
        },
    })
    name, email = await resolve_git_identity(db, "u1")
    assert name  == "Tejinder Singh"
    assert email == "tejinder@aurem.dev"


@pytest.mark.asyncio
async def test_resolve_falls_back_to_profile_name_then_login():
    """Legacy row — github subdoc has only login (iter <212m-218)."""
    db = _FakeDB({
        "email": "j@aurem.dev",
        "name":  "Jane Doe",
        "github": {"login": "jdoe"},
    })
    name, email = await resolve_git_identity(db, "u2")
    assert name  == "Jane Doe"        # from top-level `name`
    assert email == "j@aurem.dev"     # from top-level email


@pytest.mark.asyncio
async def test_resolve_no_profile_name_uses_login():
    db = _FakeDB({
        "email": "k@aurem.dev",
        "github": {"login": "kkoder"},
    })
    name, email = await resolve_git_identity(db, "u3")
    assert name  == "kkoder"
    assert email == "k@aurem.dev"


@pytest.mark.asyncio
async def test_resolve_no_email_falls_back_to_gh_noreply():
    db = _FakeDB({
        "name":   "Someone",
        "github": {"login": "someone-gh"},
    })
    _, email = await resolve_git_identity(db, "u4")
    assert email == "someone-gh@users.noreply.github.com", email


@pytest.mark.asyncio
async def test_resolve_missing_user_returns_synthetic():
    db = _FakeDB(None)
    name, email = await resolve_git_identity(db, "ghost")
    assert name  == "AUREM Developer"
    assert email == "aurem-user@users.noreply.github.com"


@pytest.mark.asyncio
async def test_resolve_db_none_returns_synthetic():
    name, email = await resolve_git_identity(None, "anyone")
    assert name  == "AUREM Developer"
    assert email == "aurem-user@users.noreply.github.com"


@pytest.mark.asyncio
async def test_resolve_db_failure_never_raises():
    class _BrokenDB:
        dev_users = None
        async def find_one(self, *a, **kw):
            raise RuntimeError("mongo down")
    _BrokenDB.dev_users = _BrokenDB()
    name, email = await resolve_git_identity(_BrokenDB(), "u5")
    # Fallback identity — a DB blip MUST NOT block the commit path.
    assert name  == "AUREM Developer"
    assert email == "aurem-user@users.noreply.github.com"


# ══════════════════════════════════════════════════════════════════
# 2. infer_commit_type — classifier
# ══════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("text, expected", [
    ("fix broken login on Safari",                  "fix"),
    ("Fix: race condition in auth.py",              "fix"),
    ("Resolve SQL injection in user query",         "fix"),
    ("Add dark mode toggle to settings",            "feat"),
    ("implement password reset flow",               "feat"),
    ("refactor: extract validation helpers",        "refactor"),
    ("Refactor to use async/await",                 "refactor"),
    ("update README with new setup steps",          "docs"),
    ("optimize database query performance",         "perf"),
    ("add pytest coverage for scan endpoint",       "test"),
    ("bump lodash to 4.17.21",                      "build"),
    ("Prettier formatting across components",       "style"),
    ("Update GitHub Actions workflow",              "ci"),
    ("Random unrelated string with no keywords",    "chore"),
    ("",                                            "chore"),
])
def test_infer_commit_type(text, expected):
    assert infer_commit_type(text) == expected, (text, infer_commit_type(text))


def test_already_prefixed_message_stays_as_is():
    """`feat(auth): …` MUST stay `feat`, not get reclassified by the
    body text."""
    assert infer_commit_type("feat(auth): add oauth signup") == "feat"
    assert infer_commit_type("chore: nothing important") == "chore"


# ══════════════════════════════════════════════════════════════════
# 3. build_commit_message — Conventional Commits + trailer
# ══════════════════════════════════════════════════════════════════
def test_build_message_from_user_message_infers_type():
    msg = build_commit_message(user_message="Fix the login race condition")
    subject = msg.splitlines()[0]
    assert subject.startswith("fix: "), subject
    assert subject.endswith(" [via ORA]"), subject
    assert CO_AUTHOR_TRAILER in msg


def test_build_message_with_explicit_type_and_summary():
    msg = build_commit_message(
        task_type="feat",
        summary="add rate-limit countdown toast",
    )
    assert msg.startswith("feat: add rate-limit countdown toast [via ORA]\n\n")
    assert msg.rstrip().endswith(CO_AUTHOR_TRAILER)


def test_build_message_never_double_appends_via_ora_marker():
    """A message that already carries the marker should NOT get a
    second one appended."""
    msg = build_commit_message(
        task_type="fix",
        summary="already tagged [via ORA]",
    )
    # Count occurrences of the marker — must be exactly one.
    assert msg.count("[via ORA]") == 1, msg


def test_build_message_subject_capped_at_72_chars():
    long_summary = "add a very very very very very very very very long feature description that surely exceeds line budget"
    msg = build_commit_message(task_type="feat", summary=long_summary)
    subject = msg.splitlines()[0]
    assert len(subject) <= 72, (len(subject), subject)
    assert subject.endswith("[via ORA]")


def test_build_message_includes_body():
    msg = build_commit_message(
        task_type="fix",
        summary="fix broken thing",
        body="This resolves a race condition when two clients hit /login at once.",
    )
    lines = msg.splitlines()
    assert lines[0].startswith("fix: fix broken thing")
    # Body -> blank line -> trailer
    assert "race condition" in msg
    assert lines[-1] == CO_AUTHOR_TRAILER


def test_build_message_unknown_type_falls_back_to_chore():
    msg = build_commit_message(task_type="nonsense", summary="something")
    assert msg.startswith("chore: something")


def test_co_author_trailer_is_exact_github_format():
    """GitHub's parser is strict: `Co-authored-by: Name <email>`. Any
    typo (missing space, wrong angle bracket) breaks credit assignment.
    """
    assert CO_AUTHOR_TRAILER == "Co-authored-by: ORA by Aurem CTO <cto@auremcto.com>"
    # Also match GitHub's regex spec:
    # https://docs.github.com/en/pull-requests/committing-changes-to-your-project/creating-and-editing-commits/creating-a-commit-with-multiple-authors
    m = re.match(r"^Co-authored-by:\s+.+\s+<.+@.+>$", CO_AUTHOR_TRAILER)
    assert m, "trailer does not match GitHub's Co-authored-by spec"


# ══════════════════════════════════════════════════════════════════
# 4. commit_files contract — hardcoded defaults removed
# ══════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_commit_files_rejects_empty_author_name():
    with pytest.raises(ValueError) as ei:
        await commit_files(
            owner="o", repo="r", branch="main", token="t",
            files={"a.py": "x"}, commit_message="chore: x",
            author_name="",  author_email="e@ex.com",
        )
    assert "author_name" in str(ei.value)


@pytest.mark.asyncio
async def test_commit_files_rejects_empty_author_email():
    with pytest.raises(ValueError) as ei:
        await commit_files(
            owner="o", repo="r", branch="main", token="t",
            files={"a.py": "x"}, commit_message="chore: x",
            author_name="Dev",  author_email="",
        )
    assert "author_email" in str(ei.value)


def test_commit_files_signature_has_no_hardcoded_bot_defaults():
    """Static assertion: the writer function MUST NOT re-introduce the
    old `AUREM CTO <cto@auremcto.com>` defaults. If a future refactor
    adds them back, every commit reverts to bot attribution."""
    src = open("/app/backend/services/github_api_writer.py").read()
    # Locate the commit_files() signature block.
    m = re.search(
        r"async def commit_files\([^)]*\)",
        src, re.DOTALL,
    )
    assert m, "could not locate commit_files signature"
    sig = m.group(0)
    assert 'author_email: str = "cto@auremcto.com"' not in sig, (
        "commit_files re-added the hardcoded bot email default. "
        "Every commit will attribute to AUREM CTO again — remove it."
    )
    assert 'author_name: str = "AUREM CTO"' not in sig, (
        "commit_files re-added the hardcoded bot name default."
    )


# ══════════════════════════════════════════════════════════════════
# 5. Every caller resolves identity before invoking commit_files
# ══════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("path", [
    "/app/backend/services/loop_engine.py",
    "/app/backend/services/finding_fix_applier.py",
    "/app/backend/services/local_tools.py",
    "/app/backend/services/seo/orchestrator.py",
    "/app/backend/services/repo_indexing.py",
])
def test_caller_resolves_git_identity_before_committing(path):
    """Grep-based static check: every file that calls commit_files()
    MUST also import + call `resolve_git_identity` in the same file.
    Catches the "developer copies old commit_files() usage into a
    new caller and forgets identity" regression."""
    src = open(path).read()
    assert "commit_files(" in src, path
    assert "resolve_git_identity" in src, (
        f"{path} calls commit_files() but does not resolve identity "
        f"via services.git_identity.resolve_git_identity — commits "
        f"from this path will fall back to synthetic 'AUREM Developer'."
    )
    assert "author_name=" in src and "author_email=" in src, (
        f"{path} does not pass author_name / author_email to commit_files()."
    )


# ══════════════════════════════════════════════════════════════════
# 6. Every commit_message routed through the writer carries the trailer
# ══════════════════════════════════════════════════════════════════
def test_every_caller_routes_message_through_build_commit_message():
    """Each caller must use `build_commit_message` (which appends the
    Co-authored-by trailer). A hardcoded literal `commit_message="…"`
    that doesn't go through the builder would bypass the trailer.
    """
    for path in [
        "/app/backend/services/loop_engine.py",
        "/app/backend/services/finding_fix_applier.py",
        "/app/backend/services/local_tools.py",
        "/app/backend/services/seo/orchestrator.py",
        "/app/backend/services/repo_indexing.py",
    ]:
        src = open(path).read()
        assert "build_commit_message" in src, (
            f"{path} bypasses build_commit_message — Co-authored-by "
            f"trailer will be missing."
        )
