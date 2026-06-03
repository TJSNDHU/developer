"""
test_iter69_brain_dump_and_build_hash.py

Locks in:
  • In-task auto-regenerate before Vanguard fail (Pattern #1 deep fix)
  • Brain dump admin endpoint
  • Frontend BrainDump page + route wiring
  • /api/health returns build_hash + env
"""
from __future__ import annotations

import os
import re


def _read(rel):
    base = os.path.join(os.path.dirname(__file__), "..", "..")
    with open(os.path.join(base, rel), encoding="utf-8") as fh:
        return fh.read()


# ── In-task auto-regenerate (Pattern #1 deep) ─────────────────────────

def test_run_task_auto_regenerates_before_failing():
    """When the model returns zero/empty edits, we must give it ONE
    chance to regenerate with explicit guidance BEFORE failing the task.
    Without this the user has to click Retry by hand — and the retry
    used to do the same thing again (since-fixed in iter 67)."""
    src = _read("backend/routers/cto_projects.py")
    # The nudge text must be present + actionable
    assert "auto-regenerating with explicit guidance" in src
    assert "FILE: <path>" in src
    assert "real code, not " in src
    # A second call_llm with the nudge appended must exist
    assert "AI codegen auto-retry" in src
    # The user-facing final error must include "Try rephrasing"
    assert "Try rephrasing: specify which file to edit and " in src
    assert "what to change" in src


def test_run_task_only_retries_once():
    """The auto-regenerate must NOT loop — only one extra call. Otherwise
    a stuck model could burn 10 LLM calls on a single task."""
    src = _read("backend/routers/cto_projects.py")
    # Count occurrences of "AI codegen auto-retry" — should be exactly 1
    assert src.count("AI codegen auto-retry") == 1, (
        "Auto-regenerate must fire exactly once, not loop"
    )


# ── Brain dump admin endpoint ─────────────────────────────────────────

def test_brain_dump_endpoint_registered():
    from routers.admin import router as adm
    paths = [r.path for r in adm.routes]
    assert "/admin/brain/{project_id}/dump" in paths


def test_brain_dump_handler_requires_admin_and_returns_shape():
    src = _read("backend/routers/admin.py")
    m = re.search(
        r"async def admin_brain_dump.*?(?=\n@router\.|\Z)",
        src, re.DOTALL,
    )
    assert m, "admin_brain_dump handler must exist"
    body = m.group(0)
    assert "_require_admin(authorization)" in body
    # Strip mongo _id so the response is JSON-clean
    assert 'brain_doc.pop("_id", None)' in body
    # Surfaces the 4 diagnostic flags the page renders
    for flag in ("has_github_commits", "has_aurem_commits",
                 "has_decisions", "has_preferences", "had_pat",
                 "context_length_chars"):
        assert flag in body, f"brain_dump response must include {flag}"


# ── Frontend BrainDump page wired ─────────────────────────────────────

def test_brain_dump_page_exists_with_required_testids():
    src = _read("frontend/src/pages/BrainDump.jsx")
    for testid in (
        "brain-back",
        "brain-assembled",
    ):
        assert f'data-testid="{testid}"' in src
    # Inline delete buttons exist for decisions + prefs
    assert "brain-decision-delete-" in src
    assert "brain-pref-delete-" in src
    # Uses real endpoint, not a mock
    assert "/admin/brain/" in src
    assert "${projectId}/dump" in src


def test_app_jsx_wires_brain_dump_route():
    src = _read("frontend/src/App.jsx")
    assert "import BrainDump" in src
    assert '/admin/brain/:projectId' in src


# ── Build hash banner ────────────────────────────────────────────────

def test_health_endpoint_returns_build_hash():
    src = _read("backend/main.py")
    # Resolver function
    assert "def _resolve_build_hash" in src
    # Env-first then git
    assert 'os.getenv("BUILD_HASH")' in src
    assert "git" in src and "rev-parse" in src
    # /health response includes the field
    m = re.search(r"async def health\(\):.*?\n\n", src, re.DOTALL)
    assert m
    health_body = m.group(0)
    assert '"build_hash"' in health_body
    assert '"env"' in health_body


def test_admin_overview_renders_build_banner():
    src = _read("frontend/src/pages/AdminOverview.jsx")
    assert 'data-testid="admin-build-banner"' in src
    # Banner is conditional on health.build_hash being set
    assert "health?.build_hash" in src
    # Renders the hash + env
    assert "health.build_hash" in src
    assert "health.env" in src
