"""
tests/test_iter212m173_flow_b_removed.py

Iter 212m-173 — Flow B removal contract tests.

Ensures the legacy `/projects/create` + `/projects/plan` +
`/projects/build/{id}` + `/projects/{id}/files` endpoint family AND all
its support services have been fully removed, and that Flow A
(`/cto/projects/add`) remains the single source of truth for project
creation.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, "/app/backend")

BACKEND_ROOT = Path("/app/backend")
FRONTEND_ROOT = Path("/app/frontend/src")


# ─── Files that MUST no longer exist ─────────────────────────────────

def test_flow_b_backend_files_removed():
    """Flow B backend router + support services must be deleted."""
    dead = [
        BACKEND_ROOT / "routers/projects.py",
        BACKEND_ROOT / "services/project_generator.py",
        BACKEND_ROOT / "services/github_auto.py",
        BACKEND_ROOT / "services/mongo_provisioner.py",
        BACKEND_ROOT / "services/doc_generator.py",
    ]
    still_present = [str(p) for p in dead if p.exists()]
    assert not still_present, f"Flow B files still present: {still_present}"


def test_flow_b_frontend_files_removed():
    """Database.jsx (Flow B's only user surface) must be deleted."""
    assert not (FRONTEND_ROOT / "pages/Database.jsx").exists(), (
        "frontend/src/pages/Database.jsx should have been deleted"
    )


# ─── Import graph must be clean (no dangling refs) ───────────────────

def test_main_py_does_not_import_projects_router():
    src = (BACKEND_ROOT / "main.py").read_text()
    assert "from routers.projects import" not in src
    # `cto_projects_router` is fine — Flow A router.  Only the bare
    # `projects_router` symbol (Flow B) must be gone.
    lines = src.splitlines()
    bare_ref = [
        ln for ln in lines
        if "projects_router" in ln and "cto_projects_router" not in ln
    ]
    assert not bare_ref, f"Flow B `projects_router` still referenced: {bare_ref}"


def test_no_backend_imports_deleted_services():
    """Search the whole backend for imports of the deleted services —
    nothing should reference them any more."""
    banned_modules = [
        "services.project_generator",
        "services.github_auto",
        "services.mongo_provisioner",
        "services.doc_generator",
    ]
    offenders: list[tuple[Path, str]] = []
    for py in BACKEND_ROOT.rglob("*.py"):
        # Skip the test file itself + pycache
        if py.name == "test_iter212m173_flow_b_removed.py":
            continue
        if "__pycache__" in py.parts:
            continue
        content = py.read_text()
        for mod in banned_modules:
            if f"from {mod}" in content or f"import {mod}" in content:
                offenders.append((py.relative_to(BACKEND_ROOT), mod))
    assert not offenders, (
        "Deleted modules are still imported somewhere:\n" +
        "\n".join(f"  {p} → {m}" for p, m in offenders)
    )


def test_frontend_no_dangling_database_refs():
    """Shell.jsx nav + App.jsx route must have been cleaned up."""
    shell = (FRONTEND_ROOT / "components/Shell.jsx").read_text()
    assert 'to: "/database"' not in shell
    assert 'testid: "nav-database"' not in shell

    app_jsx = (FRONTEND_ROOT / "App.jsx").read_text()
    assert 'path="/database"' not in app_jsx
    assert 'import("./pages/Database")' not in app_jsx


def test_no_frontend_calls_to_flow_b_endpoints():
    """No component should still POST to /projects/create,
    /projects/plan, or /projects/build/*."""
    banned = ["/projects/create", "/projects/plan", "/projects/build/"]
    offenders: list[tuple[Path, str]] = []
    for f in FRONTEND_ROOT.rglob("*.jsx"):
        if "node_modules" in f.parts:
            continue
        content = f.read_text()
        for pattern in banned:
            if pattern in content:
                offenders.append((f.relative_to(FRONTEND_ROOT), pattern))
    assert not offenders, (
        "Flow B endpoints still called from frontend:\n" +
        "\n".join(f"  {p} → {q}" for p, q in offenders)
    )


# ─── Flow A remains the single source of truth ──────────────────────

def test_flow_a_add_endpoint_still_exists():
    """The Flow A endpoint must still be present and untouched."""
    src = (BACKEND_ROOT / "routers/cto_projects.py").read_text()
    assert '@router.post("/projects/add")' in src
    assert "async def add_project" in src


def test_flow_a_encrypts_pat_before_storing():
    """Regression: PAT is HKDF-Fernet encrypted at rest, never plain."""
    src = (BACKEND_ROOT / "routers/cto_projects.py").read_text()
    assert "_encrypt_pat" in src
    assert 'auth_method": "pat"' in src


def test_flow_a_verifies_pat_against_github_before_insert():
    """PAT must be verified against GitHub /repos/{o}/{r} BEFORE the
    cto_projects row is inserted (never save a broken project)."""
    src = (BACKEND_ROOT / "routers/cto_projects.py").read_text()
    # The verify block hits GitHub before insert_one.
    assert "https://api.github.com/repos/{owner}/{repo}" in src
    # And the insert happens after the verify branch.
    verify_idx = src.find("https://api.github.com/repos/{owner}/{repo}")
    insert_idx = src.find("cto_projects.insert_one")
    assert 0 < verify_idx < insert_idx, (
        "Verify block must precede cto_projects.insert_one — "
        f"verify_idx={verify_idx}, insert_idx={insert_idx}"
    )


# ─── Init collections cleanup ───────────────────────────────────────

def test_init_prod_collections_no_project_plans():
    """project_plans collection init must be removed alongside the
    Flow B endpoints that were its only writers."""
    src = (BACKEND_ROOT / "scripts/init_prod_collections.py").read_text()
    # No live spec line — the removal comment is fine, but the tuple
    # entry `("project_plans", [...])` must be gone.
    assert '("project_plans", [' not in src
