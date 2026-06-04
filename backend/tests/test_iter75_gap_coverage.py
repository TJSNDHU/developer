"""Iter 75 — gap-closure tests.

Covers:
  GAP 1  e2b sandbox runner (silent no-op without key)
  GAP 2  DB-backed task_plan + structural multi-file retry
  GAP 3  TF-IDF fallback in semantic_search_repo
  GAP 4  esbuild → node --check fallback in _check_js_syntax
  GAP 5  MULTI-FILE CONTRACT persona section
"""
from __future__ import annotations

import asyncio
import os
import shutil


def _read(rel: str) -> str:
    base = os.path.join(os.path.dirname(__file__), "..", "..")
    with open(os.path.join(base, rel), encoding="utf-8") as fh:
        return fh.read()


# ── GAP 1 — sandbox_runner ────────────────────────────────────────────

def test_sandbox_runner_exports():
    from services.sandbox_runner import (
        validate_generated_files, run_python_check, run_tests_in_sandbox,
    )
    assert callable(validate_generated_files)
    assert callable(run_python_check)
    assert callable(run_tests_in_sandbox)


def test_sandbox_runner_skips_without_api_key(monkeypatch):
    monkeypatch.setenv("E2B_API_KEY", "")
    import importlib
    import services.sandbox_runner as sr
    importlib.reload(sr)
    res = asyncio.get_event_loop().run_until_complete(
        sr.validate_generated_files({"x.py": "print('hi')"})
    )
    assert res["skipped"] is True
    assert res["ok"] is True


def test_sandbox_wired_into_worker_pipeline():
    src = _read("backend/routers/cto_projects.py")
    assert "from services.sandbox_runner import validate_generated_files" in src
    assert "Sandbox tests passed" in src


# ── GAP 2 — DB-backed task plan + structural multi-file retry ─────────

def test_task_plan_persisted_on_multi_file_tasks():
    src = _read("backend/routers/cto_projects.py")
    assert "_promised_files" in src
    # Plan written to MongoDB with file/status shape
    assert "task_plan" in src
    assert '"status": "pending"' in src
    # Per-file tick-off after each commit
    assert '"task_plan.$.status": "done"' in src


def test_structural_multi_file_retry_in_runner():
    src = _read("backend/routers/cto_projects.py")
    # Tight match: the dedicated retry section + nudge phrasing
    assert "multi-file contract retry" in src
    assert "Your previous response was missing these files" in src
    # Persona has the MULTI-FILE CONTRACT block
    from services.orchestrator import AUREM_CTO_PERSONA
    assert "MULTI-FILE CONTRACT — LEGALLY BINDING" in AUREM_CTO_PERSONA


def test_task_management_panel_polls_db():
    js = _read("frontend/src/components/TaskManagementPanel.jsx")
    assert "taskId" in js
    assert "/cto/tasks/" in js
    assert "dbPlan" in js


# ── GAP 3 — TF-IDF fallback in semantic_search_repo ──────────────────

def test_semantic_search_has_tfidf_fallback():
    src = _read("backend/services/local_tools.py")
    assert "_index_tfidf_search" in src
    assert "cto_codebase_index" in src
    # Each hit carries a `source` (github_search | index_tfidf) so callers
    # can tell where the result came from.
    assert '"source": "github_search"' in src
    assert '"source": "index_tfidf"' in src


# ── GAP 4 — esbuild + node --check fallback ──────────────────────────

def test_check_js_syntax_tries_esbuild_first():
    src = _read("backend/routers/cto_projects.py")
    # esbuild attempted before node --check
    assert '"esbuild"' in src
    assert '"node"' in src and '"--check"' in src
    # And the runtime ordering — esbuild call appears earlier in the function
    fn_idx_esbuild = src.find('["esbuild"')
    fn_idx_node = src.find('["node", "--check"')
    assert fn_idx_esbuild != -1 and fn_idx_node != -1
    assert fn_idx_esbuild < fn_idx_node


def test_esbuild_installed_in_dev_env():
    """The dev environment ships esbuild 0.28+; production must match
    (Dockerfile installs the same binary)."""
    assert shutil.which("esbuild"), "esbuild not on PATH"
