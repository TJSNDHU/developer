"""
Iter 165 — Smart Router + Multi-Agent System

Pins the production-critical routing decisions so a future refactor
can't accidentally regress costs or quality:
  - Swift code  → Kimi K2.7 Code
  - Swift review→ Kimi K2.5  (cheapest review)
  - Pro code    → Kimi K2.7 Code
  - Pro review  → Kimi K2 Thinking
  - Maxx code   → Claude Sonnet
  - Maxx review → Kimi K2 Thinking (used only by direct .run pipeline)
  - Security    → ALWAYS Claude Sonnet (non-negotiable)
  - Read        → Kimi K2 (all modes)
  - Fallback    → DeepSeek

Also verifies the orchestrator no longer references the removed legacy
review functions, and that CoordinatorAgent's review_tail is the
single integration surface.
"""
from __future__ import annotations
import ast
import asyncio
import pathlib
import sys

import pytest

BACKEND_DIR = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


# ── smart_router model routing ───────────────────────────────────────

def test_router_swift_code_is_kimi_k27():
    from services.smart_router import get_model
    assert get_model("code", "swift") == "moonshotai/kimi-k2.7-code"


def test_router_swift_review_is_kimi_k25():
    from services.smart_router import get_model
    assert get_model("review", "swift") == "moonshotai/kimi-k2.5"


def test_router_pro_review_is_kimi_thinking():
    from services.smart_router import get_model
    assert get_model("review", "pro") == "moonshotai/kimi-k2-thinking"


def test_router_maxx_code_is_claude():
    from services.smart_router import get_model
    assert get_model("code", "maxx") == "anthropic/claude-sonnet-4-5-20250929"


def test_router_security_is_always_claude():
    from services.smart_router import get_model
    for mode in ("swift", "pro", "maxx"):
        assert get_model("security", mode) == "anthropic/claude-sonnet-4-5-20250929", \
            f"security must use Claude in mode={mode}"


def test_router_read_uses_kimi_k2():
    from services.smart_router import get_model
    assert get_model("read") == "moonshotai/kimi-k2"


def test_router_budget_review_is_tight():
    """Swift review budget MUST stay tight — it's the cheap diff path."""
    from services.smart_router import get_budget
    assert get_budget("review", "swift") <= 500


def test_router_provider_name_is_humanized():
    from services.smart_router import get_provider_name
    assert get_provider_name("code", "swift") == "Kimi K2.7"
    assert get_provider_name("code", "maxx") == "Claude Sonnet"
    assert get_provider_name("security") == "Claude Sonnet"


# ── agents.py structure ──────────────────────────────────────────────

def test_agents_module_exports_all_five():
    from services import agents
    for name in (
        "ReaderAgent", "CoderAgent", "ReviewerAgent",
        "SecurityAgent", "CoordinatorAgent",
    ):
        assert hasattr(agents, name), f"agents.{name} missing"


def test_agents_uses_asyncio_gather_for_parallel_tail():
    src = (BACKEND_DIR / "services" / "agents.py").read_text()
    assert "asyncio.gather" in src, "review_tail must run reviewer + security in parallel"


def test_coordinator_review_tail_signature():
    from services.agents import CoordinatorAgent
    coord = CoordinatorAgent(mode="swift")
    assert hasattr(coord, "review_tail")
    assert hasattr(coord, "run")


# ── orchestrator wiring ──────────────────────────────────────────────

def test_orchestrator_removed_legacy_review_funcs():
    src = (BACKEND_DIR / "services" / "orchestrator.py").read_text()
    # Tail must no longer DEFINE the legacy helpers
    assert "async def _swift_diff_review" not in src
    assert "async def _pro_parallel_review" not in src


def test_orchestrator_wires_coordinator_agent():
    src = (BACKEND_DIR / "services" / "orchestrator.py").read_text()
    assert "CoordinatorAgent" in src, \
        "orchestrator must use CoordinatorAgent for review tail"
    assert "review_tail" in src, \
        "orchestrator must call coordinator.review_tail()"


def test_orchestrator_surfaces_agent_meta():
    """The chat response must include agent provider info so the UI
    transparency chip can render the right labels."""
    src = (BACKEND_DIR / "services" / "orchestrator.py").read_text()
    assert "agent_providers" in src
    assert "agent_security_findings" in src


# ── llm.py wiring ────────────────────────────────────────────────────

def test_llm_exports_call_openrouter_model():
    from services.llm import call_openrouter_model  # noqa: F401


def test_llm_call_openrouter_model_is_async():
    from services.llm import call_openrouter_model
    assert asyncio.iscoroutinefunction(call_openrouter_model)


# ── Syntax sanity ────────────────────────────────────────────────────

@pytest.mark.parametrize("relpath", [
    "services/smart_router.py",
    "services/agents.py",
    "services/orchestrator.py",
    "services/llm.py",
])
def test_files_parse_clean(relpath: str):
    src = (BACKEND_DIR / relpath).read_text()
    ast.parse(src)


# ── Agent fault-tolerance contracts ──────────────────────────────────

def test_reviewer_returns_original_on_short_input():
    """Tiny snippets MUST not be sent to a reviewer — guard at 150 chars."""
    from services.agents import ReviewerAgent
    agent = ReviewerAgent()
    short = "x = 1"
    out, was = asyncio.run(agent.review(code=short, task="t", mode="swift"))
    assert out == short
    assert was is False


def test_reviewer_maxx_skips_review():
    """Maxx mode MUST never call a reviewer — Claude wrote the code."""
    from services.agents import ReviewerAgent
    agent = ReviewerAgent()
    code = "def foo():\n    return 42\n" * 20  # well over 150 chars
    out, was = asyncio.run(agent.review(code=code, task="t", mode="maxx"))
    assert out == code
    assert was is False


def test_security_agent_empty_on_tiny_input():
    """Trivial code MUST not waste a Claude security scan call."""
    from services.agents import SecurityAgent
    agent = SecurityAgent()
    findings = asyncio.run(agent.scan(code="x=1", file_path="t.py"))
    assert findings == []
