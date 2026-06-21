"""
Iter 205 regression — encrypted-PAT decryption in tool/skill resolvers.

Bug history:
  `cto_projects.github_token` is stored as Fernet ciphertext (`v1:…`).
  Tool functions in `services/local_tools.py` and `services/dev_skills.py`
  used to call `proj.get("github_token")` directly and pass that raw
  ciphertext to GitHub's API, which returned `401 Bad credentials` for
  every Mode-D/E scan and every tool invocation by the orchestrator.

Fix: both files' `_resolve_project()` now decrypt in place and fall back
to the user's GitHub OAuth `access_token` when no per-project PAT is
stored (OAuth-only flow).

These tests cover the contract:
  - decrypts ciphertext stored in proj.github_token
  - falls through to OAuth access_token when project has no PAT
  - never returns the raw `v1:…` ciphertext to the caller
"""
from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

# Make `backend/` importable when pytest runs from repo root
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture
def fake_db():
    """Mock get_db() so _resolve_project hits the fake collection."""
    db = types.SimpleNamespace()
    db.cto_projects = types.SimpleNamespace(find_one=AsyncMock())
    return db


@pytest.mark.asyncio
async def test_local_tools_resolve_decrypts_pat(fake_db):
    from services import local_tools

    fake_db.cto_projects.find_one.return_value = {
        "project_id":   "p1",
        "user_id":      "u1",
        "github_token": "v1:ENCRYPTED_BLOB",
        "github_owner": "tejisandhu",
        "github_repo":  "auremcto",
    }

    with patch.object(local_tools, "get_db", return_value=fake_db), \
         patch("routers.cto_projects._decrypt_pat",
               new=AsyncMock(return_value="ghp_REAL_DECRYPTED_TOKEN")), \
         patch("routers.cto_projects._user_gh_token",
               new=AsyncMock(return_value=None)):
        proj = await local_tools._resolve_project("u1", "p1")

    assert proj is not None
    # Critical: the encrypted ciphertext must be replaced with the
    # decrypted token before downstream tools see it.
    assert proj["github_token"] == "ghp_REAL_DECRYPTED_TOKEN"
    assert not proj["github_token"].startswith("v1:")


@pytest.mark.asyncio
async def test_local_tools_resolve_falls_back_to_oauth(fake_db):
    """OAuth-only flow — project has no PAT, fall back to dev_users.github.access_token."""
    from services import local_tools

    fake_db.cto_projects.find_one.return_value = {
        "project_id":   "p1",
        "user_id":      "u1",
        "github_token": "",          # no PAT stored
        "github_owner": "x",
        "github_repo":  "y",
    }

    with patch.object(local_tools, "get_db", return_value=fake_db), \
         patch("routers.cto_projects._decrypt_pat",
               new=AsyncMock(return_value=None)), \
         patch("routers.cto_projects._user_gh_token",
               new=AsyncMock(return_value="gho_OAUTH_TOKEN")):
        proj = await local_tools._resolve_project("u1", "p1")

    assert proj is not None
    assert proj["github_token"] == "gho_OAUTH_TOKEN"


@pytest.mark.asyncio
async def test_dev_skills_resolve_decrypts_pat(fake_db):
    from services import dev_skills

    fake_db.cto_projects.find_one.return_value = {
        "project_id":   "p1",
        "user_id":      "u1",
        "github_token": "v1:ENCRYPTED_DEV_SKILL",
        "github_owner": "x",
        "github_repo":  "y",
    }

    with patch.object(dev_skills, "get_db", return_value=fake_db), \
         patch("routers.cto_projects._decrypt_pat",
               new=AsyncMock(return_value="ghp_DEV_DECRYPTED")), \
         patch("routers.cto_projects._user_gh_token",
               new=AsyncMock(return_value=None)):
        proj = await dev_skills._resolve_project("u1", "p1")

    assert proj is not None
    assert proj["github_token"] == "ghp_DEV_DECRYPTED"


@pytest.mark.asyncio
async def test_resolve_returns_none_for_home_or_missing_ids(fake_db):
    from services import local_tools, dev_skills

    with patch.object(local_tools, "get_db", return_value=fake_db), \
         patch.object(dev_skills,  "get_db", return_value=fake_db):
        assert await local_tools._resolve_project("",   "p1")   is None
        assert await local_tools._resolve_project("u1", "")     is None
        assert await local_tools._resolve_project("u1", "home") is None
        assert await dev_skills._resolve_project("u1", "home")  is None
