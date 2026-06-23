"""
test_iter212j_truncation_and_oauth_ttl.py

Iter 212j — Three production fixes in one commit:

  1) Tool-result truncation: orchestrator's per-tool budget bumped
     2500 → 8000 chars. The local_tools layer already truncates file
     content at 15k; the orchestrator's second-stage JSON envelope
     cap was throwing away 80% of that signal. With 8000, ORA gets
     enough usable content from a typical read_repo_file to answer
     without re-reading.

  2) read_repo_file file-content cap: already at 15k (>10k spec).
     Locking that in too.

  3) OAuth state TTL: 5 minutes. State rows now carry
     `created_at: datetime.now(timezone.utc)` at /connect time, and
     /callback raises HTTPException(400, "OAuth state expired") if
     the row is older than 5 minutes. Prevents replay of stale state
     rows that linger in MongoDB.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

ORCH    = Path("/app/backend/services/orchestrator.py").read_text(encoding="utf-8")
TOOLS   = Path("/app/backend/services/local_tools.py").read_text(encoding="utf-8")
OAUTH_R = Path("/app/backend/routers/github_oauth.py").read_text(encoding="utf-8")


# ── 1) Orchestrator tool-result budget ───────────────────────────

def test_per_tool_result_budget_is_8000_chars():
    """The per-tool truncation budget in chat_with_tools must be 8000
    so a single 15k-char file read survives mostly intact."""
    assert "if len(result_str) > 8000:" in ORCH
    assert "result_str[:8000]" in ORCH
    # Defensive: the old 2500 limit must NOT be present anywhere in
    # the truncation block (comments are fine, but no live code).
    # Strip comments before scanning.
    code_lines = []
    for line in ORCH.splitlines():
        s = line.lstrip()
        if s.startswith("#"):
            continue
        code_lines.append(line)
    code = "\n".join(code_lines)
    # Confirm no lingering `> 2500` or `[:2500]` in active code.
    assert "len(result_str) > 2500" not in code
    assert "result_str[:2500]" not in code


# ── 2) read_repo_file file content cap ───────────────────────────

def test_max_file_chars_at_least_10000():
    """The local_tools MAX_FILE_CHARS constant must satisfy the spec
    floor of 10,000. Iter 212i raised it to 15,000."""
    from services.local_tools import MAX_FILE_CHARS
    assert MAX_FILE_CHARS >= 10_000, (
        f"MAX_FILE_CHARS={MAX_FILE_CHARS} — spec requires >= 10,000"
    )


def test_slice_content_respects_new_cap():
    """A 20k-char file is truncated at exactly MAX_FILE_CHARS, not
    earlier."""
    from services.local_tools import MAX_FILE_CHARS, _slice_content
    s = "x" * 20_000
    out, trunc = _slice_content(s, None, MAX_FILE_CHARS)
    assert trunc is True
    # Out has the truncation marker appended; the kept content is
    # exactly MAX_FILE_CHARS chars.
    assert out.startswith("x" * MAX_FILE_CHARS)
    assert "truncated" in out


# ── 3) OAuth state TTL ───────────────────────────────────────────

def test_oauth_state_inserts_carry_created_at():
    """Both the signup and connect /connect branches must write a
    tz-aware `created_at` field into the oauth_states doc."""
    # The string `"created_at": datetime.now(timezone.utc)` appears
    # exactly twice — once per branch.
    assert OAUTH_R.count('"created_at": datetime.now(timezone.utc)') >= 2


def test_oauth_callback_enforces_5_minute_ttl():
    """The /callback handler must compare `created_at` against
    `datetime.now(timezone.utc) - timedelta(minutes=5)` and raise
    HTTP 400 'OAuth state expired' on violation."""
    assert "timedelta(minutes=5)" in OAUTH_R
    assert "OAuth state expired" in OAUTH_R


def test_oauth_callback_imports_datetime():
    """The router must import datetime, timedelta, timezone."""
    assert "from datetime import datetime, timedelta, timezone" in OAUTH_R


# ── Integration: invoking the /callback path with a stale state ──

@pytest.mark.asyncio
async def test_callback_rejects_stale_state():
    """Behavioural lock-in — a state row with created_at older than
    5 minutes makes /callback raise HTTPException(400)."""
    from routers import github_oauth
    stale_doc = {
        "state":      "signup:abcd1234",
        "mode":       "signup",
        "user_id":    None,
        "created_at": datetime.now(timezone.utc) - timedelta(minutes=10),
    }

    class _FakeDB:
        class _Col:
            async def find_one(self, *_a, **_kw): return stale_doc
            async def delete_one(self, *_a, **_kw): return None
        oauth_states = _Col()

    with patch("routers.github_oauth.get_db", return_value=_FakeDB()):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            await github_oauth.callback(
                code="x", state="signup:abcd1234",
                error=None, error_description=None,
            )
        assert exc.value.status_code == 400
        assert "expired" in str(exc.value.detail).lower()


@pytest.mark.asyncio
async def test_callback_accepts_fresh_state():
    """A 30-second-old state row passes the TTL check (and progresses
    to the token-exchange step which we mock to short-circuit out)."""
    from routers import github_oauth
    fresh_doc = {
        "state":      "signup:abcd9999",
        "mode":       "signup",
        "user_id":    None,
        "created_at": datetime.now(timezone.utc) - timedelta(seconds=30),
    }

    class _FakeDB:
        class _Col:
            async def find_one(self, *_a, **_kw): return fresh_doc
            async def delete_one(self, *_a, **_kw): return None
            async def update_one(self, *_a, **_kw): return None
            async def insert_one(self, *_a, **_kw): return None
        oauth_states = _Col()
        dev_users    = _Col()

    with patch("routers.github_oauth.get_db", return_value=_FakeDB()), \
         patch("routers.github_oauth.exchange",
               new=AsyncMock(side_effect=RuntimeError("simulate downstream"))):
        # We expect downstream to fail (intentional) — the TTL guard
        # must NOT raise first. Confirm by inspecting that the
        # exception we get is NOT the "OAuth state expired" one.
        try:
            await github_oauth.callback(
                code="x", state="signup:abcd9999",
                error=None, error_description=None,
            )
        except Exception as e:                       # noqa: BLE001
            # We landed past the TTL gate if the error is the
            # downstream simulate, NOT the TTL one.
            assert "expired" not in str(e).lower(), (
                f"Fresh state was incorrectly rejected as expired: {e!r}"
            )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
