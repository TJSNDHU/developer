"""
Iter 165 — Warm Start (project pre-load) regression.

Pins the contract:
  - POST /cto/projects/{project_id}/warm-start → starts background job
  - GET  /cto/projects/warm-start/{job_id}/status → polled by FE
  - `_run_warm_agents` fires 4 agents in parallel via asyncio.gather:
      brain, recent, structure, stack
  - Orchestrator injects `[WARM CONTEXT — pre-loaded on project select]`
    when a ready job exists, hard-capped at 1.5s
  - Frontend hook `useWarmStart` polls and exposes (status, progress)
  - Frontend `WarmStatusBar` renders during status="warming"
  - main.py creates the warm_start_jobs TTL index (1h auto-delete)
"""
from __future__ import annotations
import ast
import pathlib
import sys

import pytest

BACKEND_DIR = pathlib.Path(__file__).resolve().parents[1]
FRONTEND_SRC = BACKEND_DIR.parent / "frontend" / "src"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


# ── Backend wiring ───────────────────────────────────────────────────

def test_cto_projects_has_warm_start_endpoint():
    src = (BACKEND_DIR / "routers" / "cto_projects.py").read_text()
    assert '"/projects/{project_id}/warm-start"' in src
    assert "async def warm_start_project" in src


def test_cto_projects_has_warm_start_status_endpoint():
    src = (BACKEND_DIR / "routers" / "cto_projects.py").read_text()
    assert '"/projects/warm-start/{job_id}/status"' in src
    assert "async def warm_start_status" in src


def test_warm_agents_run_in_parallel():
    """`_run_warm_agents` MUST use asyncio.gather over the 4 agents so
    GitHub latency is paid in parallel, not serialised."""
    src = (BACKEND_DIR / "routers" / "cto_projects.py").read_text()
    runner = src[src.find("async def _run_warm_agents"):]
    runner = runner[:runner.find("@router.")] if "@router." in runner else runner
    assert "asyncio.gather" in runner, "warm agents must run in parallel"
    for agent in ("agent_brain", "agent_recent", "agent_structure", "agent_stack"):
        assert agent in runner, f"missing warm agent: {agent}"


def test_warm_start_endpoint_returns_job_id_without_blocking():
    """The endpoint MUST `create_task` and return immediately — never
    await the background runner."""
    src = (BACKEND_DIR / "routers" / "cto_projects.py").read_text()
    endpoint = src[src.find("async def warm_start_project"):]
    endpoint = endpoint[:endpoint.find("async def _run_warm_agents")]
    assert "asyncio.create_task(_run_warm_agents(" in endpoint
    # The endpoint MUST NOT directly await _run_warm_agents
    assert "await _run_warm_agents(" not in endpoint


# ── Orchestrator inject ──────────────────────────────────────────────

def test_orchestrator_injects_warm_context():
    src = (BACKEND_DIR / "services" / "orchestrator.py").read_text()
    assert "WARM CONTEXT" in src
    assert "warm_start_jobs" in src


def test_orchestrator_warm_fetch_is_bounded():
    """A stalled Mongo must NOT delay chat past 1.5s on the warm lookup."""
    src = (BACKEND_DIR / "services" / "orchestrator.py").read_text()
    idx = src.find("warm_start_jobs")
    assert idx > 0
    # Look at the 400 chars BEFORE the collection access — that's where
    # the surrounding asyncio.wait_for(..., timeout=1.5) lives.
    window = src[max(0, idx - 400):idx + 900]
    assert "asyncio.wait_for" in window
    assert "timeout=1.5" in window


# ── Frontend hook + bar ──────────────────────────────────────────────

def test_use_warm_start_hook_exists():
    src = (FRONTEND_SRC / "hooks" / "useWarmStart.js").read_text()
    assert "warm-start" in src
    assert "setInterval" in src
    assert "useWarmStart" in src


def test_warm_status_bar_exists():
    src = (FRONTEND_SRC / "components" / "WarmStatusBar.jsx").read_text()
    assert "warming" in src
    assert "progress" in src.lower()
    assert "data-testid=\"warm-status-bar\"" in src


def test_chatpanel_wires_warm_start():
    src = (FRONTEND_SRC / "components" / "ChatPanel.jsx").read_text()
    assert "useWarmStart" in src
    assert "WarmStatusBar" in src


# ── main.py TTL index ────────────────────────────────────────────────

def test_main_creates_warm_start_jobs_ttl_index():
    src = (BACKEND_DIR / "main.py").read_text()
    assert "warm_start_jobs" in src
    assert "expireAfterSeconds" in src


# ── Compile sanity ───────────────────────────────────────────────────

@pytest.mark.parametrize("relpath", [
    "routers/cto_projects.py",
    "services/orchestrator.py",
    "main.py",
])
def test_backend_files_parse_clean(relpath: str):
    ast.parse((BACKEND_DIR / relpath).read_text())
