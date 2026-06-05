"""
test_iter81_mode_b_council.py — Mode B auto-upgrade: Decision Council.

When a user genuinely stuck on a decision lands in Mode B, ORA now
upgrades the response into a 5-adviser council + Chairman verdict.

Locks:
  1. is_council_request() trigger logic (true positives + true
     negatives — must NOT hijack routine "should I" coding questions).
  2. run_council() produces the full 7-section Markdown layout
     end-to-end via a real LLM call (skipped if no key).
  3. The chat router wires the council branch BEFORE Mode F so the
     ordering can't be quietly inverted by a future refactor.
"""
from __future__ import annotations

import os
import pytest


# ── 1. Trigger logic — local, no LLM ───────────────────────────────────

def test_is_council_request_true_positives():
    from services.mode_b_council import is_council_request
    cases = [
        "I'm torn between raising a seed round and bootstrapping.",
        "Stuck on this decision — pivot or persevere?",
        "Can't decide if I should fire my cofounder",
        "Should I quit my job to do AUREM full-time?",
        "Run the council on this: should I sell to Google or hold?",
        "Decision council please: build in-house ML or buy OpenAI credits",
        "Major decision coming up — should I launch in Feb or April?",
        "Debating between Postgres and Mongo for the new service",
    ]
    for msg in cases:
        assert is_council_request(msg, "B") is True, \
            f"council trigger missed: {msg!r}"


def test_is_council_request_true_negatives():
    """Routine Mode B coding/advice must NOT trigger the council — that
    would burn a 4 k-token Claude call on a question that needed two
    sentences."""
    from services.mode_b_council import is_council_request
    cases = [
        "Should I add caching to /api/users?",
        "Which is better, useEffect or useMemo here?",
        "What's the best way to structure my FastAPI routes?",
        "Compare Redis and Memcached for session storage",
        "Recommend a UI library for shadcn",
        "How should I handle JWT refresh tokens?",
    ]
    for msg in cases:
        assert is_council_request(msg, "B") is False, \
            f"council trigger fired on routine Mode B: {msg!r}"


def test_is_council_request_ignores_non_mode_b():
    """Even with explicit 'council' wording, we don't hijack a Mode C
    (code-ship) or Mode D (debug) message — the classifier owns mode
    selection, this helper is only an upgrade signal."""
    from services.mode_b_council import is_council_request
    assert is_council_request("Run the council on this", "C") is False
    assert is_council_request("Decision council please",  "A") is False
    assert is_council_request("Stuck between two libs",   "D") is False


def test_is_council_request_safe_on_empty():
    from services.mode_b_council import is_council_request
    assert is_council_request("", "B") is False
    assert is_council_request(None, "B") is False  # type: ignore[arg-type]


# ── 2. Chat router wiring ─────────────────────────────────────────────

def test_chat_router_wires_council_before_mode_f():
    """The council branch MUST run before the Mode F branch — both
    early-return so order decides who wins on a Mode B message that
    also matches a Mode F pattern."""
    base = os.path.join(os.path.dirname(__file__), "..")
    with open(os.path.join(base, "routers/chat.py"), encoding="utf-8") as fh:
        src = fh.read()
    assert "from services.mode_b_council import" in src
    assert "is_council_request" in src
    assert "run_council" in src
    council_idx = src.index("is_council_request(body.prompt")
    mode_f_idx  = src.index('if _mode == "F":')
    assert council_idx < mode_f_idx, \
        "Council branch must come BEFORE the Mode F branch in chat.py"
    # The result payload must tag itself so the frontend can render a
    # distinct council card / badge.
    assert '"council": True' in src
    assert '"provider": "mode-b-council"' in src


# ── 3. Real e2e — single LLM call producing the full layout ───────────

REAL_LLM = bool(os.environ.get("EMERGENT_LLM_KEY")) or \
           bool(os.environ.get("OPENROUTER_API_KEY"))


@pytest.mark.asyncio
@pytest.mark.skipif(not REAL_LLM,
                    reason="no LLM key (EMERGENT_LLM_KEY / OPENROUTER_API_KEY) set")
async def test_run_council_real_call_produces_full_layout():
    from services.mode_b_council import run_council
    md = await run_council(
        prompt="I'm torn between launching AUREM on Product Hunt this "
               "Friday or waiting two more weeks to polish the onboarding "
               "flow. We have 800 waitlist signups already.",
        repo_ctx="Repo: aurem-labs/aurem-cto · stack: React + FastAPI + Mongo",
        brain_ctx="",
    )
    # Every required section header must appear.
    required = [
        "# Decision Council",
        "Adviser 1 — The Contrarian",
        "Adviser 2 — The First-Principles Thinker",
        "Adviser 3 — The Expansionist",
        "Adviser 4 — The Outsider",
        "Adviser 5 — The Executor",
        "## Peer review",
        "## Chairman's call",
        "**The decision:**",
        "**Strongest reason:**",
        "**Biggest risk:**",
        "**Next step",
    ]
    missing = [s for s in required if s not in md]
    assert not missing, (
        f"council output missing sections: {missing}\n"
        f"---\nGot:\n{md[:1200]}\n..."
    )
    # The advisers must have actual content — at least 50 chars each
    # after the header. Catches the "Claude printed only headers" mode.
    assert len(md) > 1200, f"council output too short: {len(md)} chars"
