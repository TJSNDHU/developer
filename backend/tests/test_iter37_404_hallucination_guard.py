"""
test_iter37_404_hallucination_guard.py — verifies the loud 404 + "stop
guessing paths" warning the AI now gets when it guesses non-existent
files (which was the actual production hallucination root cause —
7/8 priority files were 404'ing for TJSNDHU/Aurem and the AI just
plowed ahead).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_read_repo_file_404_returns_stop_warning():
    """When the file doesn't exist, the AI MUST be told 'STOP guessing,
    call list_repo_files' — not a polite 'may not exist' fallback."""
    from services.local_tools import read_repo_file
    with patch("services.local_tools._resolve_project",
               new=AsyncMock(return_value={
                   "github_owner": "x", "github_repo": "y",
                   "branch": "main", "github_token": None,
               })), \
         patch("services.local_tools._gh_fetch_file",
               new=AsyncMock(return_value=None)):
        res = await read_repo_file(
            {"user_id": "u", "project_id": "p"}, {"path": "fake.py"},
        )
    assert res["ok"] is False
    assert res["status"] == 404
    assert "STOP guessing" in res["error"]
    assert "list_repo_files" in res["error"]


@pytest.mark.asyncio
async def test_read_repo_files_50pct_404_returns_hallucination_warning():
    """The actual production scenario: 7 out of 8 paths 404 → AI gets
    a top-level `warning` field telling it to call list_repo_files."""
    from services.local_tools import read_repo_files

    async def fake_fetch(owner, repo, path, branch, token):
        return None if path != "real.md" else "# Real"

    with patch("services.local_tools._resolve_project",
               new=AsyncMock(return_value={
                   "github_owner": "x", "github_repo": "y",
                   "branch": "main", "github_token": None,
               })), \
         patch("services.local_tools._gh_fetch_file", new=fake_fetch):
        res = await read_repo_files(
            {"user_id": "u", "project_id": "p"},
            {"paths": ["fake1.py", "fake2.py", "fake3.py", "fake4.py",
                       "fake5.py", "real.md"]},
        )
    assert res["fetched"] == 1
    assert "warning" in res, "high-failure warning missing"
    assert "HALLUCINATION RISK" in res["warning"]
    assert "list_repo_files" in res["warning"]


@pytest.mark.asyncio
async def test_read_repo_files_no_warning_when_most_succeed():
    """If most paths succeed, no false-alarm warning."""
    from services.local_tools import read_repo_files

    async def fake_fetch(owner, repo, path, branch, token):
        return None if path == "fake.py" else "content"

    with patch("services.local_tools._resolve_project",
               new=AsyncMock(return_value={
                   "github_owner": "x", "github_repo": "y",
                   "branch": "main", "github_token": None,
               })), \
         patch("services.local_tools._gh_fetch_file", new=fake_fetch):
        res = await read_repo_files(
            {"user_id": "u", "project_id": "p"},
            {"paths": ["a.py", "b.py", "c.py", "d.py", "fake.py"]},
        )
    assert res["fetched"] == 4
    assert "warning" not in res


def test_priority_files_covers_backend_style_repos():
    """Iter 37 widened _PRIORITY_FILES to include backend/routers etc.
    so non-React layouts (like TJSNDHU/Aurem) don't 404 entirely."""
    from services.repo_context import _PRIORITY_FILES
    assert "backend/main.py" in _PRIORITY_FILES
    assert "backend/server/main.py" in _PRIORITY_FILES
    assert "backend/routers/__init__.py" in _PRIORITY_FILES
    # And keep the frontend ones too
    assert "src/App.jsx" in _PRIORITY_FILES
