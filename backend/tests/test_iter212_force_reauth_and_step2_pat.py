"""
test_iter212_force_reauth_and_step2_pat.py

Iter 212 — Fix AddProject dialog (4 bugs in one commit):
  1) PAT input field missing in Step 2
  2) Robot guide 2-stage helper (pick repo → paste PAT)
  3) Repo filter removed — show all repos with "Connected" pill
  4) Step 1 "Switch GitHub account" link → force_reauth=1 →
     `prompt=select_account` appended to GitHub OAuth URL

Backend-only contract test: `auth_url(state, force_reauth=True)`
appends `prompt=select_account` so GitHub re-shows the authorize
page. Also verifies the frontend Projects.jsx has wired the four
new data-testids that the UI fixes introduce.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest


# ── Backend: services/github_oauth.py contract ─────────────────────

def _set_oauth_env():
    os.environ.setdefault("GITHUB_OAUTH_CLIENT_ID", "test_cid")
    os.environ.setdefault("GITHUB_OAUTH_CLIENT_SECRET", "test_sec")
    os.environ.setdefault("GITHUB_REDIRECT_URI", "https://example.com/cb")


def test_auth_url_default_omits_prompt_select_account():
    _set_oauth_env()
    from services.github_oauth import auth_url
    url = auth_url("state-xyz")
    assert "prompt=select_account" not in url
    assert "state=state-xyz" in url


def test_auth_url_force_reauth_appends_prompt_select_account():
    _set_oauth_env()
    from services.github_oauth import auth_url
    url = auth_url("state-xyz", force_reauth=True)
    assert "prompt=select_account" in url
    assert "state=state-xyz" in url


def test_auth_url_force_reauth_false_is_same_as_default():
    _set_oauth_env()
    from services.github_oauth import auth_url
    assert auth_url("s") == auth_url("s", force_reauth=False)


# ── Backend: routers/github_oauth.py `/connect` accepts force_reauth ─

def test_connect_router_signature_includes_force_reauth():
    """The /connect endpoint must accept `force_reauth` as a Query
    param so the frontend "Switch GitHub account" link can request a
    re-auth flow."""
    src = Path("/app/backend/routers/github_oauth.py").read_text(encoding="utf-8")
    # Function signature mentions force_reauth as a Query() param.
    assert "force_reauth" in src, "router missing force_reauth param"
    assert "prompt=select_account" not in src or "force_reauth" in src
    # Ensure auth_url() is invoked with force_reauth=fr at both call sites
    # (signup branch + connect branch).
    assert src.count("force_reauth=fr") >= 2, (
        "expected auth_url() to be invoked with force_reauth=fr in both branches"
    )


# ── Frontend: Projects.jsx wiring lock-in ──────────────────────────

PROJECTS_JSX = Path("/app/frontend/src/pages/Projects.jsx").read_text(encoding="utf-8")


def test_step2_pat_input_field_exists():
    """FIX 1 — PAT input field present in Step 2."""
    assert 'data-testid="proj-step2-pat-input"' in PROJECTS_JSX
    assert 'data-testid="proj-step2-pat-block"' in PROJECTS_JSX
    assert 'data-testid="proj-step2-pat-github-link"' in PROJECTS_JSX


def test_step2_pat_github_link_targets_pat_creation_page():
    """FIX 1 — the prominent GitHub link deep-links to the fine-grained
    PAT creation page so the user reaches it in one click."""
    # The href is built inline; ensure the github.com path is present.
    assert "github.com/settings/personal-access-tokens/new" in PROJECTS_JSX


def test_robot_guide_step2_has_two_stage_messaging():
    """Iter 212/212d — robot guide branches on `effectiveRepo` for
    stage-a vs stage-b vs stage-c messaging.

    Stage A (no repo yet): "Type the owner/repo below"
    Stage B (repo set, PAT empty): "Open GitHub → Create PAT"
    Stage C (verified): "Token verified ✓"
    """
    # Stage-a hint (Iter 212d copy — "Type the owner/repo").
    assert re.search(r"Type the .*?owner/repo", PROJECTS_JSX), "stage-a hint missing"
    # Stage-b hint mentions PAT creation.
    assert re.search(r"Open GitHub.*?Create PAT", PROJECTS_JSX), "stage-b CTA copy missing"


def test_repo_filter_removed_show_all_repos():
    """FIX 3 — `availableRepos` no longer filters out connected repos;
    instead each row uses `isRepoConnected(repo)` to render a disabled
    "Connected" pill."""
    # No more silent filtering (`.filter(...)`) of repos by connectedKeys.
    # The current implementation aliases `availableRepos = repos`.
    assert "const availableRepos = repos;" in PROJECTS_JSX, (
        "repos must NOT be filtered in Step 2 — show all with Connected pill"
    )
    assert "isRepoConnected" in PROJECTS_JSX
    # Pill text
    assert ">Connected</span>" in PROJECTS_JSX or ">Connected<" in PROJECTS_JSX


def test_step1_primary_cta_is_fresh_oauth():
    """FIX (Iter 212c) — primary amber CTA is ALWAYS "Continue with
    GitHub" with force_reauth=true. The cached-session shortcut is a
    small secondary link, no longer the default action.

    Rationale: builders managing multiple client orgs were grabbing the
    wrong cached account because the @login was the primary CTA.
    Forcing a fresh `prompt=select_account` flow on every "+ Add
    Project" click guarantees the right account is chosen explicitly
    on github.com."""
    # Primary CTA testid still exists, but onClick must use startOAuth(true).
    assert 'data-testid="oauth-connect-cta"' in PROJECTS_JSX
    # The primary CTA's onClick is startOAuth(true) — gating fresh OAuth.
    assert re.search(
        r'data-testid="oauth-connect-cta"[^}]*?onClick=\{\(\)\s*=>\s*startOAuth\(true\)\}',
        PROJECTS_JSX, re.DOTALL,
    ), "Primary CTA must call startOAuth(true) for force_reauth"
    # Cached-session shortcut still reachable as a small secondary link.
    assert 'data-testid="oauth-pick-repo-cta"' in PROJECTS_JSX
    # The cached @login is no longer surfaced in the robot guide.
    assert "Welcome back" not in PROJECTS_JSX, (
        "Robot guide must not greet the cached @login as the default — "
        "every new project starts fresh."
    )
    # The frontend still appends force_reauth=1 to the OAuth URL.
    assert "force_reauth=1" in PROJECTS_JSX


def test_startoauth_signature_accepts_force_reauth():
    """The frontend startOAuth() function must accept a forceReauth
    arg so the Switch link can request re-auth without duplicating
    the entire popup-OAuth boilerplate."""
    assert re.search(r"function\s+startOAuth\s*\(\s*forceReauth", PROJECTS_JSX)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
