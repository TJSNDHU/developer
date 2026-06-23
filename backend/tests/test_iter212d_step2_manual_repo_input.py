"""
test_iter212d_step2_manual_repo_input.py

Iter 212d — Step 2 now accepts a free-form `owner/repo` text input as
the PRIMARY repo selector. The OAuth-derived picker is demoted to a
secondary `<details>` shortcut. This unblocks the multi-account flow
where the active github.com OAuth session belongs to @A but the user
wants to connect a repo from @B (their PAT decides access).

Frontend wiring + backend contract lock-in.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest


PROJECTS_JSX = Path("/app/frontend/src/pages/Projects.jsx").read_text(encoding="utf-8")


# ── Frontend wiring ───────────────────────────────────────────────

def test_step2_has_manual_repo_text_input():
    """The free-form repo text input is rendered with a stable
    test-id and bound to setManualRepo()."""
    assert 'data-testid="proj-step2-repo-input"' in PROJECTS_JSX
    assert "setManualRepo" in PROJECTS_JSX
    assert "const [manualRepo, setManualRepo]" in PROJECTS_JSX


def test_effective_repo_derives_from_manual_first():
    """`effectiveRepo` must prefer manualRepo over the OAuth picker
    selection — the whole point of Iter 212d."""
    assert "const effectiveRepo = manualRepo.trim()" in PROJECTS_JSX
    assert "_parseManualRepo(manualRepo)" in PROJECTS_JSX
    assert ": selectedRepo;" in PROJECTS_JSX


def test_manual_repo_parser_accepts_owner_repo_and_full_url():
    """The parser must strip both `https://github.com/` and `.git`
    suffixes; this is what unblocks copy-pasting from the browser
    URL bar."""
    assert 'replace(/^https?:\\/\\/github\\.com\\//i, "")' in PROJECTS_JSX
    assert 'replace(/\\.git$/i, "")' in PROJECTS_JSX


def test_oauth_picker_demoted_to_details_element():
    """The OAuth-derived repo list is now collapsed inside a
    <details> element so it doesn't dominate Step 2."""
    assert 'data-testid="proj-step2-oauth-picker-details"' in PROJECTS_JSX
    # Only shown when manualRepo is empty AND there are repos to list.
    assert "!manualRepo && availableRepos.length > 0" in PROJECTS_JSX


def test_picker_row_click_fills_manual_input():
    """Clicking a row from the OAuth picker must mirror the choice
    into the manualRepo text input so the user always sees the
    canonical owner/repo string they're about to connect."""
    # The onClick handler sets BOTH manualRepo and selectedRepo.
    assert "setManualRepo(repo.full_name)" in PROJECTS_JSX
    assert "setSelectedRepo(repo)" in PROJECTS_JSX


def test_connect_handler_uses_effective_repo():
    """The Connect button must POST `effectiveRepo` (not the legacy
    `selectedRepo`) to /cto/projects/add."""
    # No stale selectedRepo refs inside handleConnectRepo.
    add_handler_block_start = PROJECTS_JSX.index("async function handleConnectRepo")
    add_handler_block_end   = PROJECTS_JSX.index("function startOAuth")
    block = PROJECTS_JSX[add_handler_block_start:add_handler_block_end]
    assert "effectiveRepo" in block
    # The legacy ref should NOT be present in this handler.
    assert "selectedRepo." not in block, (
        "handleConnectRepo must use effectiveRepo only — Iter 212d"
    )


def test_verify_pat_effect_keyed_on_effective_repo():
    """The debounced verify-pat effect must re-run when the user
    types a new repo into the text field — depend on
    `effectiveRepo?.full_name`, not `selectedRepo`."""
    assert "[repoPat, effectiveRepo?.full_name]" in PROJECTS_JSX


def test_typing_clears_picker_selection():
    """Typing into the manual text input must clear any picker
    selection so `effectiveRepo` has one unambiguous source."""
    # Look for the conditional clear inside the onChange handler.
    assert "if (selectedRepo) setSelectedRepo(null);" in PROJECTS_JSX


def test_connect_button_gated_on_effective_repo_and_verified_pat():
    assert 'disabled={!effectiveRepo || patCheck.status !== "ok" || busy}' in PROJECTS_JSX


def test_step2_robot_guide_mentions_any_github_account():
    """The robot guide must explicitly tell the user that ANY GitHub
    account works — not just @{login}."""
    assert "any GitHub account" in PROJECTS_JSX or "any GitHub" in PROJECTS_JSX


# ── Backend: /cto/projects/add still accepts arbitrary owner/repo ──

def test_parse_repo_accepts_short_form_owner_repo():
    """The `_parse_repo` helper must accept `octocat/Hello-World`
    (no scheme) as well as the full URL."""
    from routers.cto_projects import _parse_repo
    assert _parse_repo("octocat/Hello-World") == ("octocat", "Hello-World")
    assert _parse_repo("https://github.com/octocat/Hello-World") == (
        "octocat", "Hello-World",
    )
    assert _parse_repo("https://github.com/octocat/Hello-World.git") == (
        "octocat", "Hello-World",
    )


def test_parse_repo_rejects_garbage():
    from fastapi import HTTPException
    from routers.cto_projects import _parse_repo
    with pytest.raises(HTTPException):
        _parse_repo("garbage")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
