"""
test_iter36_anti_hallucination.py — guards the 3 P0/P1 fixes from
Iter 36 (production trust crisis):

1. _retry exists and retries-then-raises (the original crash was
   `name '_retry' is not defined` while attempting Ship via CTO).
2. detect_unsourced_citations flags fabricated line numbers / metrics /
   unread file paths in AI replies.
3. Persona contains the ANTI-HALLUCINATION CONTRACT clause.
4. /cto/tasks/{id}/retry endpoint exists and validates state.
"""
from __future__ import annotations

import asyncio
import os
import time
import uuid

import httpx
import pytest
from motor.motor_asyncio import AsyncIOMotorClient

from routers.cto_projects import _retry
from services.orchestrator import AUREM_CTO_PERSONA
from services.tools_bridge import detect_unsourced_citations


API = "http://localhost:8001/api/aurem-dev"


# ── _retry ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_retry_succeeds_on_first_try():
    async def ok():
        return "ok"
    out = await _retry(ok, what="x", task_id="t1", attempts=3, base_sleep=0.01)
    assert out == "ok"


@pytest.mark.asyncio
async def test_retry_eventually_succeeds():
    state = {"n": 0}
    async def flaky():
        state["n"] += 1
        if state["n"] < 3:
            raise RuntimeError("transient")
        return "victory"
    out = await _retry(flaky, what="x", task_id="t1",
                       attempts=3, base_sleep=0.01)
    assert out == "victory"
    assert state["n"] == 3


@pytest.mark.asyncio
async def test_retry_raises_after_exhausting_attempts():
    state = {"n": 0}
    async def always_fail():
        state["n"] += 1
        raise ValueError(f"attempt {state['n']}")
    with pytest.raises(ValueError, match="attempt 3"):
        await _retry(always_fail, what="x", task_id="t1",
                     attempts=3, base_sleep=0.01)
    assert state["n"] == 3


# ── Hallucination scanner ─────────────────────────────────────────────

def test_flags_unread_file_paths():
    reply = "Issue is at `backend/middleware/health_probe.py` line 80."
    flags = detect_unsourced_citations(reply, tool_paths_read=set())
    assert any("health_probe.py" in f for f in flags)


def test_flags_line_numbers_without_any_fetched_file():
    reply = "Look at line 476 — that's where the bug is."
    flags = detect_unsourced_citations(reply, tool_paths_read=set())
    assert any("line citation" in f for f in flags)


def test_flags_fabricated_metrics():
    reply = "Stress test shows 83% improvement and 92% fewer failures."
    flags = detect_unsourced_citations(reply, tool_paths_read=set())
    metric_flags = [f for f in flags if "metric" in f]
    assert len(metric_flags) >= 1


def test_clean_when_path_actually_fetched():
    reply = "Read `routers/auth.py` — the bug is at line 47."
    # Path was actually fetched this turn → no path flag, but line-number
    # citation is allowed because A file WAS fetched
    flags = detect_unsourced_citations(
        reply, tool_paths_read={"routers/auth.py"},
    )
    # No path-related flag
    assert not any("routers/auth.py" in f for f in flags)


def test_no_flags_for_clean_text():
    reply = "I haven't read worker.py yet — let me fetch it now."
    flags = detect_unsourced_citations(reply, tool_paths_read=set())
    assert flags == []


def test_caps_flag_count_to_6():
    reply = "\n".join(f"problem at `mod_{i}.py`" for i in range(20))
    flags = detect_unsourced_citations(reply, tool_paths_read=set())
    assert len(flags) <= 6


# ── Persona contract ──────────────────────────────────────────────────

def test_persona_has_anti_hallucination_contract():
    assert "ANTI-HALLUCINATION CONTRACT" in AUREM_CTO_PERSONA
    assert "fabricated" in AUREM_CTO_PERSONA.lower() or \
        "fabrication" in AUREM_CTO_PERSONA.lower()


def test_persona_forbids_metric_invention():
    """The exact pattern the user caught the AI doing: '83%', '92%'.
    The persona must explicitly forbid this."""
    assert "83%" in AUREM_CTO_PERSONA or "92%" in AUREM_CTO_PERSONA or \
        "metric" in AUREM_CTO_PERSONA.lower()


def test_persona_keeps_iter32_rules():
    """Don't accidentally drop the older rules when adding new ones."""
    assert "EXECUTE ON FIRST COMMAND" in AUREM_CTO_PERSONA
    assert "Reply 'go' and I'll start" not in AUREM_CTO_PERSONA


# ── Retry endpoint ────────────────────────────────────────────────────

async def _login() -> str:
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.post(f"{API}/auth/login",
                         json={"email": "test@aurem.dev",
                               "password": "testpass123"})
    return r.json()["token"]


@pytest.mark.asyncio
async def test_retry_endpoint_404_for_unknown_task():
    tok = await _login()
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.post(f"{API}/cto/tasks/nope/retry",
                         headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_retry_endpoint_400_when_task_not_failed():
    """Can only retry failed tasks. Seed a 'done' task and confirm 400."""
    tok = await _login()
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[
        os.environ.get("DB_NAME", "aurem_dev")
    ]
    user = await db.dev_users.find_one({"email": "test@aurem.dev"},
                                        {"user_id": 1})
    task_id = f"t_test_retry_{uuid.uuid4().hex[:8]}"
    await db.cto_tasks.insert_one({
        "task_id": task_id, "user_id": user["user_id"],
        "project_id": "p_test", "status": "done", "created_at": time.time(),
    })
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.post(f"{API}/cto/tasks/{task_id}/retry",
                             headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 400
        assert "Only failed tasks" in r.text
    finally:
        await db.cto_tasks.delete_one({"task_id": task_id})
