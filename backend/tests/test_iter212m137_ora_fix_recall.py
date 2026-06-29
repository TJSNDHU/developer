"""
Iter 212m-137 — Phase-2 recall layer for ORA Fix-Learning.

Phase 1 (iter 212m-129) logged every fix attempt to the
`ora_fix_learning` collection.  Phase 2 closes the loop: before the
LLM rewrites a file for a finding, `recall_similar_fixes` queries
past SUCCESSFUL fixes for the same rule_id (with file-extension +
caller boosts) and `format_recall_block` renders them as a precedent
block the prompt builder concatenates ahead of the file content.

Pinned behaviour:
  • Returns [] when db is None or rule_id is empty (best-effort).
  • Highest-priority match: same user + same file extension.
  • Falls through: same user → same ext → global (rule_id only).
  • Limits to N and never duplicates across tiers.
  • format_recall_block returns "" on empty input so callers don't
    need an `if` guard.
  • finding_fix_applier._generate_patched_content passes `db=db` so
    the recall is wired into the real fix pipeline.
"""
from __future__ import annotations

import asyncio
import re
from unittest.mock import AsyncMock

import pytest

from services import ora_fix_learning as ofl


pytestmark = pytest.mark.asyncio


class _Cursor:
    def __init__(self, rows):
        self._rows = list(rows)
    def sort(self, *_a, **_kw):
        return self
    def limit(self, n):
        self._rows = self._rows[:n]
        return self
    def __aiter__(self):
        async def _gen():
            for r in self._rows:
                yield r
        return _gen()


class _FakeColl:
    def __init__(self, docs):
        self.docs = list(docs)
        self.find_calls: list[dict] = []

    def find(self, filt, projection=None):
        self.find_calls.append(filt)
        out = []
        for d in self.docs:
            ok = True
            for k, v in filt.items():
                if k == "file" and isinstance(v, dict) and "$regex" in v:
                    if not re.search(v["$regex"], d.get("file", ""), re.IGNORECASE):
                        ok = False
                        break
                elif d.get(k) != v:
                    ok = False
                    break
            if ok:
                out.append({pk: d.get(pk) for pk in
                            ["rule_id", "file", "severity", "commit_sha",
                             "html_url", "title", "scanner", "created_at"]})
        return _Cursor(out)


class _FakeDB:
    def __init__(self, docs):
        self.ora_fix_learning = _FakeColl(docs)


def _make_row(rule_id, file, user_id="u1", commit="abc1234567",
              severity="high", ts=1000.0, outcome="success", title="T"):
    return {
        "rule_id": rule_id, "file": file, "user_id": user_id,
        "commit_sha": commit, "html_url": f"https://gh/c/{commit}",
        "severity": severity, "created_at": ts, "outcome": outcome,
        "title": title, "scanner": "vanguard",
    }


def test_file_token_for_recall():
    assert ofl._file_token_for_recall("backend/main.py") == ".py"
    assert ofl._file_token_for_recall("src/App.tsx") == ".tsx"
    assert ofl._file_token_for_recall("Makefile") == ""
    assert ofl._file_token_for_recall("") == ""
    assert ofl._file_token_for_recall(".env") == ".env"


async def test_recall_returns_empty_on_none_db():
    assert await ofl.recall_similar_fixes(None, rule_id="eval_usage") == []


async def test_recall_returns_empty_on_empty_rule_id():
    db = _FakeDB([_make_row("eval_usage", "a.py")])
    assert await ofl.recall_similar_fixes(db, rule_id="") == []


async def test_recall_prefers_user_plus_extension(monkeypatch):
    db = _FakeDB([
        _make_row("eval_usage", "their/file.py", user_id="u2", commit="OTHER1"),
        _make_row("eval_usage", "mine/util.py", user_id="u1", commit="USEREXT"),
        _make_row("eval_usage", "mine/web.tsx", user_id="u1", commit="USERONLY"),
        _make_row("eval_usage", "any/file.py", user_id="u3", commit="EXTONLY"),
    ])
    out = await ofl.recall_similar_fixes(
        db, rule_id="eval_usage", file_path="backend/script.py",
        user_id="u1", limit=3,
    )
    assert len(out) == 3
    # First row must be USEREXT (user+ext tier).
    assert out[0]["commit_sha"] == "USEREXT"
    assert out[0]["match_class"] == "user+ext"
    # Second should be USERONLY (user-only tier — same caller, diff ext).
    assert out[1]["match_class"] in {"user", "user+ext"}


async def test_recall_falls_through_to_global(monkeypatch):
    db = _FakeDB([
        _make_row("eval_usage", "stranger/x.py", user_id="u9", commit="GLOBAL1"),
    ])
    out = await ofl.recall_similar_fixes(
        db, rule_id="eval_usage", file_path="my/x.py",
        user_id="u1", limit=3,
    )
    # No same-user matches → still gets the global precedent.
    assert len(out) == 1
    # ext matched (.py) so the class should be "ext" not "global".
    assert out[0]["match_class"] in {"ext", "global"}


async def test_recall_dedupes_across_tiers():
    """A row that matches user+ext must not also appear under the
    user-only or ext-only tier in the same result set."""
    db = _FakeDB([
        _make_row("eval_usage", "mine/a.py", user_id="u1", commit="DUP123"),
    ])
    out = await ofl.recall_similar_fixes(
        db, rule_id="eval_usage", file_path="other/b.py",
        user_id="u1", limit=3,
    )
    shas = [r["commit_sha"] for r in out]
    assert shas == ["DUP123"]


async def test_recall_respects_limit():
    docs = [
        _make_row("eval_usage", f"file{i}.py", user_id="u1",
                  commit=f"sha{i:04d}", ts=1000.0 + i)
        for i in range(10)
    ]
    db = _FakeDB(docs)
    out = await ofl.recall_similar_fixes(
        db, rule_id="eval_usage", file_path="x.py",
        user_id="u1", limit=3,
    )
    assert len(out) == 3


async def test_recall_soft_fails_on_mongo_error(monkeypatch):
    class _Boom:
        ora_fix_learning = None  # any attribute access on this will throw
    # Use a broken db that raises when .find is called.
    class _BrokenColl:
        def find(self, *a, **kw):
            raise RuntimeError("mongo down")
    class _BrokenDB:
        ora_fix_learning = _BrokenColl()
    out = await ofl.recall_similar_fixes(
        _BrokenDB(), rule_id="eval_usage", limit=3,
    )
    assert out == []


def test_format_recall_block_empty_returns_empty_string():
    assert ofl.format_recall_block([]) == ""


def test_format_recall_block_renders_lines():
    rows = [
        {"file": "a.py", "severity": "high", "commit_sha": "abcdef1234",
         "html_url": "https://gh/c/abcdef1234", "match_class": "user+ext"},
        {"file": "b.tsx", "severity": "medium", "commit_sha": "ffff0000",
         "html_url": "", "match_class": "global"},
    ]
    block = ofl.format_recall_block(rows)
    assert "PAST SUCCESSFUL FIXES" in block
    assert "[user+ext] a.py" in block
    assert "[global] b.tsx" in block
    assert "commit=abcdef12" in block
    # Tail guard rail.
    assert "STYLE GUIDANCE" in block
    # Empty html_url must NOT print "— "
    assert "b.tsx  ·  sev=medium  ·  commit=ffff0000" in block


def test_finding_fix_applier_passes_db_into_generate_patched_content():
    """Source-pattern contract: the apply pipeline must thread `db`
    into `_generate_patched_content` so recall has a Mongo handle."""
    from pathlib import Path
    src = Path("/app/backend/services/finding_fix_applier.py").read_text()
    assert "db=db," in src, (
        "Expected `_generate_patched_content(...db=db,...)` so Phase-2 "
        "recall is wired into the real fix pipeline."
    )


def test_generate_patched_content_uses_recall_block():
    """Source-pattern contract: the prompt builder must concatenate
    the recall_block ahead of FILE: so precedent appears BEFORE the
    finding details."""
    from pathlib import Path
    src = Path("/app/backend/services/finding_fix_applier.py").read_text()
    # The format string must start with recall_block then FILE:
    assert 'f"{recall_block}"' in src and 'f"FILE: {path}\\n"' in src
