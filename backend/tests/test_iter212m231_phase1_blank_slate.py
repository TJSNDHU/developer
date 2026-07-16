"""
Iter 212m-231 — Phase 1: Blank-Slate Parliament Mode (Personal Track).

Locks in:
1. `services/bin_context.py::build_virtual_bin_context` creates a
   BINContext with `is_draft=True`, empty repo fields, and a stable
   `draft_id`. Existing Developer Track factories are unchanged.
2. `services/mode_classifier.classify_intent_v2` routes explicit
   "build me an app / from scratch / vibe code" phrases to
   `mode="NEW_PROJECT"` with 0.90 confidence.
3. `POST /api/aurem-dev/scaffold/new-project` creates a draft, persists
   it to `db.scaffold_drafts` with a TTL, and returns a file tree
   WITHOUT touching GitHub.
4. `POST /scaffold/{draft_id}/materialize` returns 501 Not Implemented
   (Phase 2 will fill it in).
5. Draft ownership is enforced — one user cannot read another user's
   draft even by guessing the id.
"""

from __future__ import annotations

import pytest


# ── VirtualBINContext behaviour ──────────────────────────────────
def test_virtual_bin_context_has_is_draft_and_no_repo():
    from services.bin_context import build_virtual_bin_context, BINContext
    ctx = build_virtual_bin_context("user_abc123")
    assert isinstance(ctx, BINContext)
    assert ctx.is_draft is True
    assert ctx.is_virtual is True
    assert ctx.repo_owner == ""
    assert ctx.repo_name == ""
    assert ctx.pat == ""
    assert ctx.pid.startswith("draft_")
    assert len(ctx.draft_id) >= 8


def test_bin_context_default_is_not_draft():
    """Existing Developer Track BINContext must still have is_draft=False
    by default so downstream code doesn't have to check both fields."""
    from services.bin_context import BINContext
    ctx = BINContext(
        bin_id="u1", pid="proj-1", repo_owner="acme", repo_name="app",
        branch="main", pat="secret", is_founder=False,
    )
    assert ctx.is_draft is False
    assert ctx.is_virtual is False


def test_virtual_bin_context_uses_provided_draft_id():
    from services.bin_context import build_virtual_bin_context
    ctx = build_virtual_bin_context("u1", draft_id="my_specific_draft")
    assert ctx.draft_id == "my_specific_draft"
    assert ctx.pid == "draft_my_specific_draft"


# ── NEW_PROJECT mode classification ──────────────────────────────
@pytest.mark.parametrize("phrase", [
    "build me an app for tracking my habits",
    "I want to build a website that shows recipes",
    "create a new app for pet adoption",
    "I have an idea for a fintech tool",
    "start a new project from scratch",
    "I want to vibe code a landing page",
    "make me a site for my portfolio",
    "brand new project — no repo yet",
])
def test_classifier_routes_blank_slate_to_new_project(phrase):
    from services.mode_classifier import classify_intent_v2
    result = classify_intent_v2(phrase)
    assert result["mode"] == "NEW_PROJECT", (
        f"Phrase {phrase!r} must route to NEW_PROJECT, "
        f"got mode={result['mode']} confidence={result['confidence']}"
    )
    assert result["confidence"] >= 0.85


@pytest.mark.parametrize("phrase", [
    "fix the bug in my auth flow",
    "add a search bar to the header",
    "refactor the payment service",
    "ship the new dashboard page",
])
def test_classifier_still_routes_repo_edits_to_mode_c(phrase):
    """Guard against over-correction — existing Developer Track intents
    must still land in Mode C, not the new NEW_PROJECT bucket."""
    from services.mode_classifier import classify_intent_v2
    result = classify_intent_v2(phrase)
    assert result["mode"] != "NEW_PROJECT", (
        f"Repo-bound edit {phrase!r} misclassified as NEW_PROJECT"
    )


# ── Scaffold router registration ─────────────────────────────────
def test_scaffold_router_registered_under_correct_prefix():
    from routers.scaffold import router
    paths = [r.path for r in router.routes]
    # Endpoints (relative to router prefix `/scaffold`).
    assert "/scaffold/new-project" in paths
    assert "/scaffold/{draft_id}" in paths
    assert "/scaffold/{draft_id}/regenerate" in paths
    assert "/scaffold/{draft_id}/materialize" in paths


def test_scaffold_router_wired_into_main_app():
    """The scaffold router must be mounted under /api/aurem-dev so the
    frontend REACT_APP_BACKEND_URL + /api/aurem-dev/scaffold/... URL
    actually resolves."""
    src = open("/app/backend/main.py").read()
    assert "from routers.scaffold import router as scaffold_router" in src
    assert "app.include_router(scaffold_router" in src


# ── Stack detection ──────────────────────────────────────────────
def test_stack_detection_defaults_to_react_fastapi():
    from routers.scaffold import _detect_stack, _DEFAULT_STACK
    assert _detect_stack("build me an app", None) == _DEFAULT_STACK


def test_stack_detection_preference_wins():
    from routers.scaffold import _detect_stack
    assert _detect_stack("anything", "vue-express") == "vue-express"
    # Invalid preference falls back to inference/default.
    assert _detect_stack("build me an app", "django-crazy") == "react-fastapi"


def test_stack_detection_infers_from_keywords():
    from routers.scaffold import _detect_stack
    assert _detect_stack("I want a next.js server-side rendered site", None) == "nextjs-node"
    assert _detect_stack("build me a static landing page", None) == "plain-html"
    assert _detect_stack("Nuxt-based blog", None) == "vue-express"


# ── File-tree generation ─────────────────────────────────────────
@pytest.mark.asyncio
async def test_generate_file_tree_react_fastapi_has_required_files():
    from routers.scaffold import _generate_file_tree
    files = await _generate_file_tree(
        "build a habit tracker", "react-fastapi", "u1", "d1",
    )
    paths = {f["path"] for f in files}
    assert "README.md" in paths
    assert "api/main.py" in paths
    assert "ui/src/App.jsx" in paths


@pytest.mark.asyncio
async def test_generate_file_tree_respects_max_files_cap():
    """The safety cap must be honoured — future LLM generations that
    over-produce files get truncated to _MAX_FILES_PER_DRAFT."""
    from routers.scaffold import _generate_file_tree, _MAX_FILES_PER_DRAFT
    files = await _generate_file_tree("x", "react-fastapi", "u1", "d1")
    assert len(files) <= _MAX_FILES_PER_DRAFT


# ── Router prefix in tags for OpenAPI ────────────────────────────
def test_scaffold_router_uses_personal_track_tag():
    from routers.scaffold import router
    assert any("Personal Track" in (t or "") for t in (router.tags or []))
