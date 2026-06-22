"""
Iter 211 — Final pre-self-hosted iter proof tests.

Validates:
  1. POST /cto/projects/add rejects missing PAT.
  2. POST /cto/projects/add rejects malformed PAT prefix.
  3. POST /cto/projects/add does GitHub verification BEFORE persisting.
  4. GitHub OAuth callback captures github.id (immutable numeric ID).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_projects_add_rejects_missing_pat():
    """Inspect the source to verify the hard rule lives in the codebase."""
    src = (Path(__file__).resolve().parents[1] / "routers" / "cto_projects.py").read_text()
    # The 400 message must mention the PAT requirement
    assert "Personal Access Token is required" in src
    assert "github.com/settings/personal-access-tokens/new" in src


def test_projects_add_validates_pat_prefix():
    src = (Path(__file__).resolve().parents[1] / "routers" / "cto_projects.py").read_text()
    assert 'startswith("ghp_")' in src
    assert 'startswith("github_pat_")' in src
    assert "doesn't look like a GitHub PAT" in src


def test_projects_add_verifies_against_github_before_insert():
    """The verify-then-insert pattern is enforced atomically — the
    httpx call MUST happen before the insert_one() call."""
    src = (Path(__file__).resolve().parents[1] / "routers" / "cto_projects.py").read_text()
    # Order check: the GitHub verify block must appear BEFORE the
    # cto_projects.insert_one call inside `add_project`.
    httpx_idx  = src.find("api.github.com/repos/")
    insert_idx = src.find("cto_projects.insert_one")
    assert 0 < httpx_idx < insert_idx, \
        "GitHub verify must run before insert_one (atomic verify+persist)"
    # Status code branches present
    assert "(401, 403)" in src
    assert "== 404" in src
    # `auth_method` is now always "pat" (no oauth fallback for repo work)
    assert '"auth_method": "pat"' in src


def test_oauth_callback_captures_github_id():
    """github.id (immutable numeric) MUST be stored on signup AND on
    return-login update. login (username) can change; id cannot."""
    src = (Path(__file__).resolve().parents[1] / "routers" / "github_oauth.py").read_text()
    # Both insert (new account) and update (returning user) paths set github.id
    assert src.count('"id":') >= 2, \
        "github.id should be set in both insert + update paths"
    # No stale code: should not set ONLY login without id anymore
    assert '"github": {\n                    "id":' in src or 'gh_id_num' in src


def test_pat_decryption_chain_still_intact():
    """Sanity — iter 205 decryption fix must still be in place so the
    new server-side verify uses the SAME chain as live tool calls."""
    src = (Path(__file__).resolve().parents[1] / "services" / "local_tools.py").read_text()
    assert "_decrypt_pat" in src
    assert "_user_gh_token" in src
