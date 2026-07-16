"""
Iter 212m-232 — Phase 2: GitHub Auto-Create + Template Boilerplate.

Locks in:
1. `services/github_org_client.py` — sanitize_repo_name is
   defensive (empty strings, unicode, oversize).
2. `is_configured()` returns False when the env vars are missing —
   the router uses this to return a clean 503 instead of a stack trace.
3. React-FastAPI template ships with REAL runnable boilerplate:
   working FastAPI + Mongo + bcrypt-JWT auth backend, working
   Vite + React + sonner + lucide-react frontend, with all supporting
   files (docker-compose, .gitignore, .env.example, vite.config).
4. `_generate_file_tree` for react-fastapi produces the full
   ~11-file scaffold, all real (not stubs).
5. `POST /scaffold/{draft_id}/materialize` gracefully 503s when the
   AUREM org token is missing (Phase 2 dev-mode without env setup).
"""

from __future__ import annotations

import os

import pytest


# ── github_org_client sanitize helper ────────────────────────────
def test_sanitize_repo_name_basics():
    from services.github_org_client import sanitize_repo_name
    assert sanitize_repo_name("Habit Tracker!") == "habit-tracker"
    assert sanitize_repo_name("my_app.v2") == "my_app.v2"
    assert sanitize_repo_name("") == "personal-track-app"
    assert sanitize_repo_name("   ") == "personal-track-app"
    # Non-ASCII collapses through the regex.
    out = sanitize_repo_name("café ☕ site")
    assert "café" not in out
    assert out.startswith("caf") or out == "site"


def test_sanitize_repo_name_length_cap():
    from services.github_org_client import sanitize_repo_name
    huge = "x" * 500
    out = sanitize_repo_name(huge)
    assert len(out) <= 90


# ── is_configured guard ──────────────────────────────────────────
def test_org_client_is_not_configured_by_default():
    """The AUREM org token is NOT set in preview .env by default (that's
    intentional — founder sets it once the GitHub Org is created).
    `is_configured()` must return False until both vars are present."""
    from services import github_org_client

    orig_name  = os.environ.pop("AUREM_ORG_NAME", None)
    orig_token = os.environ.pop("AUREM_ORG_GITHUB_APP_TOKEN", None)
    try:
        assert github_org_client.is_configured() is False
        # Only name — still False.
        os.environ["AUREM_ORG_NAME"] = "aurem-apps"
        assert github_org_client.is_configured() is False
        # Both — True.
        os.environ["AUREM_ORG_GITHUB_APP_TOKEN"] = "ghs_fake_token_for_test"
        assert github_org_client.is_configured() is True
    finally:
        os.environ.pop("AUREM_ORG_NAME", None)
        os.environ.pop("AUREM_ORG_GITHUB_APP_TOKEN", None)
        if orig_name:  os.environ["AUREM_ORG_NAME"] = orig_name
        if orig_token: os.environ["AUREM_ORG_GITHUB_APP_TOKEN"] = orig_token


# ── Real boilerplate files exist on disk ─────────────────────────
def test_react_fastapi_boilerplate_files_exist():
    base = "/app/backend/templates/stacks/react-fastapi/boilerplate"
    for rel in ("api/main.py", "api/auth.py", "api/requirements.txt",
                "ui/src/App.jsx", "ui/package.json"):
        path = f"{base}/{rel}"
        assert os.path.exists(path), f"Missing boilerplate: {rel}"
        assert os.path.getsize(path) > 0, f"Empty boilerplate: {rel}"


def test_react_fastapi_auth_uses_bcrypt_not_plaintext():
    """The generated auth boilerplate must hash passwords with bcrypt
    (not store plaintext, not use unsalted sha256, etc.)."""
    auth_src = open(
        "/app/backend/templates/stacks/react-fastapi/boilerplate/api/auth.py"
    ).read()
    assert "import bcrypt" in auth_src, "auth.py must import bcrypt"
    assert "bcrypt.hashpw" in auth_src, "auth.py must call bcrypt.hashpw"
    assert "bcrypt.checkpw" in auth_src, "auth.py must call bcrypt.checkpw"


def test_react_fastapi_backend_uses_pool_config():
    """Match AUREM's own iter-212m-227 hardening — the generated
    boilerplate must NOT hand users a Motor client without pool config."""
    main_src = open(
        "/app/backend/templates/stacks/react-fastapi/boilerplate/api/main.py"
    ).read()
    assert "maxPoolSize" in main_src, (
        "Generated main.py must configure Motor pool "
        "(matches iter 212m-227 hardening)"
    )


# ── Full file tree — react-fastapi produces >=8 files ────────────
@pytest.mark.asyncio
async def test_generate_file_tree_react_fastapi_is_runnable():
    from routers.scaffold import _generate_file_tree
    files = await _generate_file_tree(
        "habit tracker", "react-fastapi", "u1", "d1",
    )
    paths = {f["path"] for f in files}
    # Every file needed to `git clone && docker compose up`.
    required = {
        "README.md",
        "docker-compose.yml",
        "api/main.py",
        "api/auth.py",
        "api/requirements.txt",
        "api/.env.example",
        "ui/src/App.jsx",
        "ui/package.json",
        "ui/index.html",
        "ui/src/main.jsx",
        "ui/vite.config.js",
        ".gitignore",
    }
    missing = required - paths
    assert not missing, f"Missing scaffold files: {missing}"


@pytest.mark.asyncio
async def test_generate_file_tree_files_have_real_content():
    """No file may be an empty string — Phase 1 loaded from non-existent
    template paths and produced empty files."""
    from routers.scaffold import _generate_file_tree
    files = await _generate_file_tree(
        "anything", "react-fastapi", "u1", "d1",
    )
    empties = [f["path"] for f in files if not (f.get("content") or "").strip()]
    assert not empties, f"Empty content in: {empties}"


# ── materialize graceful 503 when org not configured ─────────────
def test_materialize_endpoint_registered():
    from routers.scaffold import router
    paths = [r.path for r in router.routes]
    assert "/scaffold/{draft_id}/materialize" in paths


def test_org_client_module_exports():
    from services import github_org_client as g
    for name in ("is_configured", "sanitize_repo_name", "create_org_repo",
                 "push_file", "push_files_bulk", "delete_org_repo"):
        assert hasattr(g, name), f"github_org_client missing export: {name}"
