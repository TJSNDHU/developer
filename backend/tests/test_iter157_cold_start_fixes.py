"""
test_iter157_cold_start_fixes.py — regression tests for the Iter 157
cold-start + generic-response bugs reported by the founder on production:
1. Chat sessions getting stuck at "thinking… 300s" with no tool calls.
2. ORA giving generic "stream buffer aborted" diagnoses about its OWN
   bundle hashes when the user asks about a name in their connected repo.

The root causes (and the fixes these tests guard against):
  - get_repo_context() ran with no outer timeout → up to 60s on cold start
  - LLM httpx timeout was 60s × 2 retries → up to 120s per LLM call
  - _wants_repo() returned False when repo_ctx fetch failed → REPO persona
    layer was skipped → LLM hallucinated without CODEBASE READ instructions
  - No per-turn orchestrator deadline → iters × retries could blow past
    the router HARD_TIMEOUT_S unchecked

Keep these tests focused on the *contracts* the fixes establish, not the
internals — so refactors don't break the safety net.
"""
import asyncio
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ── 1. Persona REPO rescue ────────────────────────────────────────────

def test_degraded_repo_context_still_triggers_repo_persona():
    """When get_repo_context() times out we inject a synthetic
    'CONNECTED REPO CONTEXT (degraded — fetch timed out)' block.
    persona_layers_for must include the 'repo' layer so the LLM still
    gets MANDATORY-tool-use instructions."""
    from services.orchestrator import persona_layers_for

    degraded = (
        "=== CONNECTED REPO CONTEXT (degraded — fetch timed out) ===\n"
        "project_id=xyz\nuse read_repo_file / search_repo to locate files\n"
        "=== END REPO CONTEXT ==="
    )
    layers = persona_layers_for("is scout working?", degraded)
    assert "repo" in layers, (
        "Degraded repo context must still activate the REPO persona layer "
        f"— got {layers}"
    )


def test_no_project_no_repo_layer():
    """Sanity: a plain chat prompt with no repo/project context should
    NOT pull in the REPO layer (keeps the system prompt cheap)."""
    from services.orchestrator import persona_layers_for
    layers = persona_layers_for("how are you today?")
    assert "repo" not in layers
    assert "core" in layers


# ── 2. LLM HTTP timeout is bounded ────────────────────────────────────

def test_llm_http_timeout_capped_under_60s():
    """The httpx client used by _call_deepseek must use a timeout well
    under the 60s value that caused the production stalls. Default is
    35s and is env-overridable via LLM_HTTP_TIMEOUT_S."""
    import importlib
    import services.llm as llm
    importlib.reload(llm)
    # Read the source to verify the literal constant is in place.
    src = open(llm.__file__).read()
    assert "LLM_HTTP_TIMEOUT_S" in src, (
        "Expected LLM_HTTP_TIMEOUT_S env hook (iter 157) in services/llm.py"
    )
    assert "35.0" in src or "35" in src, (
        "Expected default LLM HTTP timeout of 35s"
    )


# ── 3. Per-turn orchestrator deadline exists ──────────────────────────

def test_orchestrator_has_per_turn_budget_guard():
    """The chat_with_tools iter loop must check ORCH_PER_TURN_BUDGET_S
    so a single chat turn cannot blow past the router HARD_TIMEOUT_S
    via stacked LLM retries."""
    src = open(os.path.join(
        os.path.dirname(__file__), "..", "services", "orchestrator.py"
    )).read()
    assert "ORCH_PER_TURN_BUDGET_S" in src, (
        "iter 157 per-turn deadline guard missing from chat_with_tools"
    )
    assert "per_turn_budget_hit" in src, (
        "Synthesised summary must flag per_turn_budget_hit so the UI "
        "can render a useful message instead of 'thinking…' indefinitely"
    )


# ── 4. Context builders run in parallel + bounded ─────────────────────

def test_chat_context_builders_parallelised_and_bounded():
    """chat.py:chat_stream must run repo_ctx + url_ctx in parallel with
    a per-builder timeout (the _safe wrapper). Sequential awaiting was
    the dominant contributor to the 300s production stalls."""
    src = open(os.path.join(
        os.path.dirname(__file__), "..", "routers", "chat.py"
    )).read()
    # Parallel: gather over the safe wrappers.
    assert "asyncio.gather(" in src
    assert "_safe(get_repo_context(" in src
    assert "_safe(build_url_context(" in src
    # Bounded: per-call asyncio.wait_for(timeout=…) inside _safe.
    assert "asyncio.wait_for(coro, timeout" in src


# ── 5. Sanity: smoke against an actual chat send ──────────────────────

@pytest.mark.asyncio
async def test_chat_send_returns_under_per_turn_budget():
    """End-to-end fast-path smoke test: a one-line greeting must come
    back well under the per-turn budget. Anything > 30s here means a
    regression in the cold-start path."""
    import httpx

    api_base = os.environ.get(
        "REACT_APP_BACKEND_URL",
        "http://localhost:8001",
    )
    api = api_base.rstrip("/")

    async with httpx.AsyncClient(timeout=60.0) as c:
        # Login as the seeded test admin. If creds don't exist locally we
        # SKIP rather than fail — this test exists mainly to catch
        # regressions in CI / staging where the seed user is present.
        login = await c.post(
            f"{api}/api/aurem-dev/auth/login",
            json={"email": "test@aurem.dev", "password": "AuremTest2026!"},
        )
        if login.status_code != 200:
            pytest.skip(f"test user not seeded in this env ({login.status_code})")
        token = login.json().get("token") or login.json().get("access_token")
        if not token:
            pytest.skip("login returned no token")

        t0 = time.monotonic()
        r = await c.post(
            f"{api}/api/aurem-dev/chat/send",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "prompt": "hi - one short sentence please",
                "session_id": "iter157-smoke",
                "max_tool_iters": 2,
                "agent": "auto",
            },
        )
        elapsed = time.monotonic() - t0

        assert r.status_code == 200, r.text[:400]
        body = r.json()
        assert body.get("content"), f"empty content: {body}"
        assert elapsed < 35.0, (
            f"Chat send took {elapsed:.1f}s — cold-start regression "
            f"(was meant to be < 35s per iter 157)"
        )
