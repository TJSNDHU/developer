"""
test_iter73_wizard_and_parallel.py — Iter 73 Task 2 + 3.

Task 3 — NewUserWizard:
  • File exists, dismiss helpers exported, three step IDs present
  • Wired into Dashboard (mount + projects-list gate)
  • Hits the real POST /cto/projects/add and POST /cto/tasks/submit
    endpoints (no inline mock fetch)

Task 2 — parallel agent badges + per-agent sub-tapes:
  • _emit() forwards **extra fields into the SSE frame
  • Multi-domain task description triggers a `parallel` SSE event with
    an agents list (cto_projects.py)
  • TaskLiveTape renders agents and the keyframe slide exists
"""
from __future__ import annotations

import asyncio
import os
import time

import pytest


def _read(rel: str) -> str:
    base = os.path.join(os.path.dirname(__file__), "..", "..")
    with open(os.path.join(base, rel), encoding="utf-8") as fh:
        return fh.read()


# ── Task 3 — NewUserWizard ────────────────────────────────────────────

def test_new_user_wizard_component_exists():
    js = _read("frontend/src/components/NewUserWizard.jsx")
    # Stable testids the testing agent + manual QA can target
    assert 'data-testid="new-user-wizard"' in js
    assert "wizard-repo-input" in js
    assert "wizard-task-input" in js
    assert "wizard-goto-dashboard" in js
    assert "wizard-skip-link" in js
    # Hits real endpoints — no inline mocks
    assert "/cto/projects/add" in js
    assert "/cto/tasks/submit" in js
    # Renders the live tape in step 3
    assert "TaskLiveTape" in js
    # localStorage dismiss key
    assert "aurem_wizard_dismissed" in js


def test_wizard_has_inline_github_oauth():
    """Iter 73 follow-up: Connect-with-GitHub button + repo picker live
    inside step 1 so the user never has to leave the wizard."""
    js = _read("frontend/src/components/NewUserWizard.jsx")
    assert "wizard-connect-github" in js
    assert "/github/oauth/connect" in js
    assert "/github/oauth/status" in js
    assert "/github/oauth/repos" in js
    # Inline picker for the user's repos
    assert "wizard-repo-picker" in js
    # Manual-paste fallback still available
    assert 'setGhStatus("manual")' in js


def test_wizard_wired_into_dashboard():
    js = _read("frontend/src/pages/Dashboard.jsx")
    assert "NewUserWizard" in js
    # Must check projects list to decide whether to mount
    assert "/cto/projects/list" in js
    # Must respect persisted dismissal
    assert "isWizardDismissed" in js


# ── Task 2 — Parallel agent emits ─────────────────────────────────────

@pytest.mark.asyncio
async def test_emit_passes_extra_fields_into_frame():
    from routers import cto_projects as m
    tid = f"ex-{time.time_ns()}"
    m._task_queues.pop(tid, None)
    await m._emit(
        tid, "Parallel mode — 3 agents working simultaneously",
        kind="parallel", pct=30,
        agents=["Backend", "Frontend", "Config"],
    )
    frame = m._task_queues[tid].get_nowait()
    assert frame["type"] == "parallel"
    assert frame["pct"] == 30
    assert frame["agents"] == ["Backend", "Frontend", "Config"]
    # Canonical fields must not be overwritten by **extra
    assert "ts" in frame
    m._task_queues.pop(tid, None)


@pytest.mark.asyncio
async def test_emit_extra_cannot_overwrite_canonical_fields():
    """**extra must not be allowed to clobber type/step/pct/ts."""
    from routers import cto_projects as m
    tid = f"safe-{time.time_ns()}"
    m._task_queues.pop(tid, None)
    await m._emit(
        tid, "x", kind="step", pct=5,
        ts=99999999,  # try to spoof timestamp
        agents=["a", "b"],
    )
    frame = m._task_queues[tid].get_nowait()
    assert frame["type"] == "step"
    assert frame["step"] == "x"
    assert frame["pct"] == 5
    # ts must reflect the real emit time, not the kwarg attempt
    assert frame["ts"] != 99999999
    # Non-canonical extra still passes through
    assert frame["agents"] == ["a", "b"]
    m._task_queues.pop(tid, None)


def test_parallel_emit_wired_in_run_task_via_api():
    src = _read("backend/routers/cto_projects.py")
    # Pre-decompose so we can ship the roster BEFORE the LLM runs
    assert "from services.parallel_agents import (" in src
    assert "decompose_task" in src
    assert "kind=\"parallel\"" in src
    # Per-agent terminal frames
    assert "kind=\"parallel_agent\"" in src


def test_should_parallelize_triggers_for_multi_domain_task():
    from services.parallel_agents import should_parallelize, decompose_task
    file_tree = [
        "backend/routers/auth.py", "backend/services/jwt.py",
        "frontend/src/components/Login.jsx",
        "tests/test_auth.py",
    ]
    assert should_parallelize(
        "Wire up backend auth API with frontend login component", file_tree
    )
    roles = [a["role"] for a in decompose_task(
        "Wire up backend auth API with frontend login component",
        "owner/repo@main", file_tree,
    )]
    # At minimum backend + frontend agents fire
    assert "backend" in roles and "frontend" in roles


# ── TaskLiveTape parallel rendering ───────────────────────────────────

def test_task_live_tape_handles_parallel_frames():
    js = _read("frontend/src/components/TaskLiveTape.jsx")
    assert 'd.type === "parallel"' in js
    assert 'd.type === "parallel_agent"' in js
    assert "agent-mini-" in js
    assert "task-live-tape-agents" in js


def test_mini_slide_keyframe_in_index_css():
    css = _read("frontend/src/index.css")
    assert "@keyframes aurem-mini-slide" in css
