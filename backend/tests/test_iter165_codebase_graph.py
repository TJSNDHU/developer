"""
Iter 165 — Codebase Graph (hybrid regex + LLM top-20).

Locks in:
  - graph_builder module exports the full contract
  - LLM step is wired to MiniMax M2.5 via call_openrouter_model
  - TOP_FILES_FOR_LLM = 20 + MAX_FILES = 200 are the cost caps
  - Endpoints /build-graph + /graph exist on cto_projects router
  - warm-start `agents_total` includes "graph" + agent_graph is fired
  - Orchestrator injects [CODEBASE GRAPH] block with 1.5s timeout cap
  - Frontend GraphPanel + ChatPanel toggle + ora-inject wiring present
"""
from __future__ import annotations
import ast
import asyncio
import pathlib
import sys

import pytest

BACKEND_DIR = pathlib.Path(__file__).resolve().parents[1]
FRONTEND_SRC = BACKEND_DIR.parent / "frontend" / "src"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


# ── Builder surface ──────────────────────────────────────────────────

def test_graph_builder_exports_contract():
    from services import graph_builder as gb
    for name in (
        "build_graph", "get_graph", "get_graph_full",
        "get_graph_for_agent",
        "extract_symbols", "extract_imports", "detect_layer",
        "MAX_FILES", "TOP_FILES_FOR_LLM",
    ):
        assert hasattr(gb, name), f"graph_builder.{name} missing"


def test_cost_caps_are_locked():
    """Token + GitHub cost depends on these caps — pin them."""
    from services.graph_builder import MAX_FILES, TOP_FILES_FOR_LLM
    assert MAX_FILES == 200
    assert TOP_FILES_FOR_LLM == 20


def test_llm_step_uses_minimax_m25():
    """The single LLM call MUST route through MiniMax M2.5 (per user
    spec). Any swap to a more expensive model regresses cost economics."""
    src = (BACKEND_DIR / "services" / "graph_builder.py").read_text()
    assert "minimax/minimax-m2.5" in src
    # Defensive: the old deepseek default must not linger
    assert "deepseek/deepseek-chat" not in src


def test_llm_step_uses_unified_openrouter_caller():
    """All LLM calls must route through services.llm.call_openrouter_model
    so timeouts + auth + fallback live in one place."""
    src = (BACKEND_DIR / "services" / "graph_builder.py").read_text()
    assert "call_openrouter_model" in src


# ── Layer + symbol extraction ────────────────────────────────────────

def test_detect_layer_classifies_paths():
    from services.graph_builder import detect_layer
    assert detect_layer("backend/routers/chat.py") == "API"
    assert detect_layer("backend/services/orchestrator.py") == "Service"
    assert detect_layer("frontend/src/components/Chat.jsx") == "UI"
    assert detect_layer("frontend/src/hooks/useThing.js") == "Hook"
    assert detect_layer("backend/models/user.py") == "Data"
    assert detect_layer("backend/utils/x.py") == "Util"
    assert detect_layer("backend/tests/test_x.py") == "Test"
    assert detect_layer("README.md") == "Other"


def test_extract_symbols_python():
    from services.graph_builder import extract_symbols
    src = (
        "import os\n"
        "async def fetch_user():\n    pass\n"
        "def make_token():\n    pass\n"
        "class UserService:\n    pass\n"
        "def _private():\n    pass\n"
    )
    syms = extract_symbols(src, "backend/services/x.py")
    assert "fetch_user" in syms
    assert "make_token" in syms
    assert "UserService" in syms
    assert "_private" not in syms  # underscores excluded


def test_extract_symbols_jsx():
    from services.graph_builder import extract_symbols
    src = (
        "export function ChatPanel() {}\n"
        "export const useThing = () => {}\n"
        "export default function App() {}\n"
        "class Helper {}\n"
    )
    syms = extract_symbols(src, "frontend/src/components/X.jsx")
    assert "ChatPanel" in syms
    assert "useThing" in syms
    assert "App" in syms
    assert "Helper" in syms


def test_extract_imports_python():
    from services.graph_builder import extract_imports
    src = (
        "from services.llm import call_openrouter_model\n"
        "from cto_services.db import get_db\n"
        "import os\n"
    )
    imps = extract_imports(src, "backend/x.py")
    assert any("services/llm" in i for i in imps)
    assert any("cto_services/db" in i for i in imps)


# ── Endpoints ────────────────────────────────────────────────────────

def test_cto_projects_has_build_graph_endpoint():
    src = (BACKEND_DIR / "routers" / "cto_projects.py").read_text()
    assert '"/projects/{project_id}/build-graph"' in src
    assert "async def build_project_graph" in src


def test_cto_projects_has_get_graph_endpoint():
    src = (BACKEND_DIR / "routers" / "cto_projects.py").read_text()
    assert '"/projects/{project_id}/graph"' in src
    assert "async def get_project_graph" in src


def test_build_graph_endpoint_is_non_blocking():
    src = (BACKEND_DIR / "routers" / "cto_projects.py").read_text()
    fn = src[src.find("async def build_project_graph"):]
    fn = fn[:fn.find("@router.")] if "@router." in fn else fn
    assert "asyncio.create_task(build_graph(" in fn
    assert "await build_graph(" not in fn


# ── Warm-start wiring ────────────────────────────────────────────────

def test_warm_start_includes_graph_agent():
    src = (BACKEND_DIR / "routers" / "cto_projects.py").read_text()
    # agents_total list must include "graph"
    assert '"agents_total":  ["brain", "recent", "structure", "stack", "graph"]' in src \
        or '"agents_total":  ["brain", "recent", "structure", "stack", "graph"]'.replace("  ", " ") in src
    # agent_graph must be defined AND called via gather
    assert "async def agent_graph" in src
    assert "agent_graph()," in src


# ── Orchestrator inject ──────────────────────────────────────────────

def test_orchestrator_injects_graph_context():
    src = (BACKEND_DIR / "services" / "orchestrator.py").read_text()
    assert "get_graph_for_agent" in src


def test_orchestrator_graph_fetch_is_bounded():
    src = (BACKEND_DIR / "services" / "orchestrator.py").read_text()
    idx = src.find("get_graph_for_agent")
    assert idx > 0
    window = src[max(0, idx - 200):idx + 400]
    assert "asyncio.wait_for" in window
    assert "timeout=1.5" in window


# ── Frontend wiring ──────────────────────────────────────────────────

def test_graph_panel_component_exists():
    src = (FRONTEND_SRC / "components" / "GraphPanel.jsx").read_text()
    assert "LAYER_COLORS" in src
    assert "ora-inject" in src
    assert "Ask ORA about this file" in src
    assert "data-testid=\"graph-panel\"" in src
    assert "data-testid=\"graph-search-input\"" in src


def test_chatpanel_wires_graph_panel():
    src = (FRONTEND_SRC / "components" / "ChatPanel.jsx").read_text()
    assert 'from "./GraphPanel"' in src
    assert "graphOpen" in src
    assert "ora-inject" in src
    assert 'data-testid="graph-toggle-btn"' in src
    assert "<GraphPanel" in src


# ── Compile sanity ───────────────────────────────────────────────────

def test_graph_builder_parses_clean():
    ast.parse((BACKEND_DIR / "services" / "graph_builder.py").read_text())


def test_cto_projects_parses_clean():
    ast.parse((BACKEND_DIR / "routers" / "cto_projects.py").read_text())


def test_orchestrator_parses_clean():
    ast.parse((BACKEND_DIR / "services" / "orchestrator.py").read_text())


# ── Get/full read fault-tolerance ────────────────────────────────────

def test_get_graph_returns_empty_on_none_db():
    from services.graph_builder import get_graph
    out = asyncio.run(get_graph(None, "p_x", "u_x"))
    assert out == {}


def test_get_graph_for_agent_returns_empty_on_none_db():
    from services.graph_builder import get_graph_for_agent
    out = asyncio.run(get_graph_for_agent(None, "p_x", "u_x"))
    assert out == ""
