"""Iter 346 — dedup guard on Vanguard draft PRs (founder ruling).

Locks: _create_draft_pr NEVER opens a duplicate draft when an open PR
already carries the same finding-set fingerprint. Root cause of the
170-draft pile-up: every auto_pr scan opened a fresh draft with zero
dedup.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from routers.security_scan import (
    _vanguard_fingerprint, _FP_MARKER, _create_draft_pr,
)

FINDINGS = [
    {"id": "secret_aws_access_key", "file": "backend/config.py", "line": 10},
    {"id": "sql_string_format", "file": "backend/db.py", "line": 44},
]


def test_fingerprint_stable_and_line_insensitive():
    fp1 = _vanguard_fingerprint(FINDINGS)
    moved = [dict(f, line=f["line"] + 100) for f in FINDINGS]
    assert _vanguard_fingerprint(moved) == fp1
    reordered = list(reversed(FINDINGS))
    assert _vanguard_fingerprint(reordered) == fp1
    different = FINDINGS + [{"id": "ssti_jinja_user_render", "file": "x.py"}]
    assert _vanguard_fingerprint(different) != fp1


def _client_with_open_prs(prs):
    client = MagicMock()
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = prs
    client.get = AsyncMock(return_value=resp)
    client.post = AsyncMock(side_effect=AssertionError(
        "dedup guard must SKIP creation — no POST should fire"))
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx, client


def test_dedup_skips_when_open_pr_has_same_fingerprint():
    fp = _vanguard_fingerprint(FINDINGS)
    existing = [{
        "number": 42,
        "html_url": "https://github.com/o/r/pull/42",
        "body": f"old report\n\n<!-- {_FP_MARKER}{fp} -->",
    }]
    ctx, client = _client_with_open_prs(existing)
    with patch("routers.security_scan.httpx.AsyncClient", return_value=ctx):
        url, err = asyncio.run(_create_draft_pr(
            owner="o", repo="r", pat="ghp_x",
            report={"pr_draft_title": "t", "pr_draft_body": "b"},
            fallback_findings=FINDINGS,
        ))
    assert err is None
    assert url == "https://github.com/o/r/pull/42"
    client.get.assert_awaited()   # the listing check actually ran


def test_no_dedup_match_proceeds_to_creation_path():
    ctx, client = _client_with_open_prs([])   # no open PRs
    # First POST in the creation path will raise our AssertionError —
    # proving the guard fell through to creation (instead of skipping).
    with patch("routers.security_scan.httpx.AsyncClient", return_value=ctx):
        url, err = asyncio.run(_create_draft_pr(
            owner="o", repo="r", pat="ghp_x",
            report={"pr_draft_title": "t", "pr_draft_body": "b"},
            fallback_findings=FINDINGS,
        ))
    assert url is None
    assert "exception" in (err or "") or "repo_meta" in (err or "")
