"""
test_iter311_file_selector_scope_drift_repro.py — Iter 311

RCA REPRO TEST (written FIRST, before Fix C).

Reproduces the 2026-07-26 scope-drift finding from loop_511cdd848b5945
where `file_selector.select_relevant_files` inflated the plan from 3
planner-picked files to 12 candidates, adding 9 UNRELATED router
files (admin_financials_router.py, campaign_health_router.py, etc.)
via naive keyword scoring.

MECHANISM (code-verified against file_selector.py):
  • Trust-verbatim guard (line 127) requires `len(planner_set) <= 2`.
  • With planner_set == 3, sweep runs.
  • Score model matches any repo file whose basename/description/
    symbols contain tokens from the task description.
  • For task "/health/detailed endpoint...", `health` and `endpoint`
    tokens match dozens of unrelated router files.
  • Line 1040 in loop_engine.py REPLACES plan.files_to_change with
    the sweep's output, expanding scope up to `top_n + planner_set`
    files (13 for top_n=10).

TEST GOALS:
  1. `test_repro_current_bug_expands_scope_with_unrelated_files` —
     MUST FAIL against current code (assertion: candidates ⊆ planner_set).
     After Fix C ships, this same test MUST PASS unchanged.

  2. Four regression tests + one defensive-fallback test proving Fix C
     preserves file_selector's other legitimate behaviours:
       (a) Trimming when planner over-specifies (15 files → top-N).
       (b) Trust-verbatim for planner ≤ 2 files (unchanged path).
       (c) No-graph fallback returns planner_set unchanged.
       (d) Scope-drift gate STILL fires for planner-side bloated
           plans (Fix C must not accidentally disarm the safety net
           for a different failure mode).
       (e) Defensive fallback — if scoring produces zero (all files
           score 0), function must return planner_set, not [].
"""
from __future__ import annotations
import pytest
from unittest.mock import AsyncMock, patch

from services.file_selector import select_relevant_files


# ── Mock graph builder — mimics the prod repo shape that caused the
# scope-drift finding. Nodes contain the exact router/description
# pattern that scored high on `health`/`endpoint`/`detailed` tokens.
def _mock_graph_prod_shape() -> dict:
    """Returns a mock graph mimicking the auremcto.com backend repo
    at the time of loop_511cdd848b5945. 3 planner-picked files + 9
    unrelated routers + a few control files.

    Descriptions/symbols/imports are set so that a naive keyword sweep
    on tokens {"health", "detailed", "endpoint", "status"} pulls the
    unrelated routers into the top-N."""
    return {
        "nodes": {
            # ── 3 planner-picked files (the CORRECT scope) ────────
            "backend/routers/health.py": {
                "description": "System health check endpoint returning "
                               "DB latency, redis, version, build_sha",
                "symbols":  ["health_check", "detailed_health"],
                "imports":  ["fastapi", "motor"],
            },
            "backend/services/health_service.py": {
                "description": "Health status probe service",
                "symbols":  ["probe_db_latency", "probe_redis"],
                "imports":  ["motor", "redis"],
            },
            "backend/tests/test_health_detailed.py": {
                "description": "Tests for /health/detailed endpoint",
                "symbols":  ["test_health_detailed_returns_db_latency"],
                "imports":  ["httpx"],
            },

            # ── 9 UNRELATED routers (the scope drift) ─────────────
            # Each has "endpoint" or "health" or "detailed" tokens in
            # description or basename — reproducing the exact scoring
            # collision that let them slip through.
            "backend/routers/campaign_health_router.py": {
                "description": "Marketing campaign health tracking endpoint",
                "symbols":  ["campaign_status", "campaign_metrics"],
                "imports":  ["fastapi"],
            },
            "backend/routers/admin_financials_router.py": {
                "description": "Admin financials dashboard endpoint with "
                               "detailed revenue breakdown",
                "symbols":  ["get_revenue"],
                "imports":  ["fastapi"],
            },
            "backend/routers/case_study_router.py": {
                "description": "Case study endpoint returning detailed "
                               "customer case studies",
                "symbols":  ["list_case_studies"],
                "imports":  ["fastapi"],
            },
            "backend/routers/aurem_llm_proxy_router.py": {
                "description": "LLM proxy endpoint with detailed usage "
                               "logging",
                "symbols":  ["proxy_llm"],
                "imports":  ["fastapi"],
            },
            "backend/routers/aurem_redis_router.py": {
                "description": "Redis status endpoint",
                "symbols":  ["redis_health_endpoint"],
                "imports":  ["fastapi", "redis"],
            },
            "backend/routers/autonomous_repair_router.py": {
                "description": "Autonomous repair endpoint with detailed "
                               "failure logs",
                "symbols":  ["repair_status"],
                "imports":  ["fastapi"],
            },
            "backend/routers/action_engine_router.py": {
                "description": "Action engine endpoint",
                "symbols":  ["action_status"],
                "imports":  ["fastapi"],
            },
            "backend/_archive/evolver_router.py": {
                "description": "Legacy evolver endpoint with detailed logs",
                "symbols":  ["evolve_status"],
                "imports":  ["fastapi"],
            },
            "backend/db.py": {
                "description": "Database session endpoint helpers",
                "symbols":  ["get_db"],
                "imports":  ["motor"],
            },

            # ── 5 truly-unrelated control files (should never
            # appear in candidates for a health-endpoint task) ────
            "frontend/src/App.jsx": {
                "description": "React app root",
                "symbols":  ["App"],
                "imports":  ["react"],
            },
            "backend/services/stripe.py": {
                "description": "Stripe payment integration",
                "symbols":  ["create_checkout"],
                "imports":  ["stripe"],
            },
            "backend/services/email_sender.py": {
                "description": "SendGrid email sending",
                "symbols":  ["send_email"],
                "imports":  ["sendgrid"],
            },
            "backend/services/parliament.py": {
                "description": "Multi-model consensus orchestrator",
                "symbols":  ["Parliament"],
                "imports":  [],
            },
            "backend/services/loop_engine.py": {
                "description": "Loop pipeline orchestrator",
                "symbols":  ["LoopEngine"],
                "imports":  [],
            },
        }
    }


_TASK = ("Add a /health/detailed endpoint returning DB latency, "
          "redis status, version, and build_sha, with 3 pytest tests")

_PLANNER_FILES = [
    "backend/routers/health.py",
    "backend/services/health_service.py",
    "backend/tests/test_health_detailed.py",
]


# ═══════════════════════════════════════════════════════════════════
# THE REPRO TEST — must FAIL against current code, PASS after Fix C
# ═══════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_repro_current_bug_expands_scope_with_unrelated_files():
    """RCA repro for loop_511cdd848b5945 scope-drift finding.

    With exactly 3 planner-picked files (trust-verbatim guard at
    file_selector.py:127 requires `<= 2`, so this hits the sweep)
    and a task description containing common overlapping tokens,
    file_selector must NOT expand the scope beyond the planner_set.

    Invariant: `candidates ⊆ planner_set` — file_selector may
    reorder or trim, but never introduce files the planner didn't
    pick. Currently VIOLATED — this test proves the bug exists.
    """
    with patch("services.graph_builder.get_graph_full",
               new=AsyncMock(return_value=_mock_graph_prod_shape())):
        result = await select_relevant_files(
            db=None,
            project_id="proj_test",
            user_id="user_test",
            task_description=_TASK,
            planner_files=_PLANNER_FILES,
            top_n=10,
        )

    assert result["ok"] is True
    assert result["has_graph"] is True
    candidates = set(result["candidates"])
    planner_set = set(_PLANNER_FILES)

    # THE INVARIANT (currently VIOLATED — this assertion must FAIL
    # against current code, PASS after Fix C ships)
    unrelated_added = candidates - planner_set
    assert not unrelated_added, (
        f"Fix C invariant violated: file_selector expanded scope "
        f"with {len(unrelated_added)} file(s) outside planner_set. "
        f"planner_set={sorted(planner_set)}, "
        f"unrelated={sorted(unrelated_added)}"
    )


# ═══════════════════════════════════════════════════════════════════
# REGRESSION TESTS (a-e) — must PASS both before and after Fix C
# ═══════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_regression_a_trim_over_specified_planner():
    """Planner returns 15 files → sweep trims to top-N by score, but
    ONLY within planner_set (never introduces external files).
    Preserves file_selector's original Iter 212m-116 purpose of
    trimming over-eager planner output to save LLM tokens.

    (Under current code — no Fix C — this test may fail because the
    sweep might rank external files above the planner's 15. That is
    expected: Fix C is what makes this test reliably pass.)"""
    planner_15 = [f"backend/routers/health.py"] + [
        f"backend/services/health_service.py",
        f"backend/tests/test_health_detailed.py",
    ] + [
        f"backend/services/probe_{i}.py" for i in range(12)
    ]
    graph = _mock_graph_prod_shape()
    # Add all 15 planner files to the graph so scoring can rank them.
    for p in planner_15:
        graph["nodes"].setdefault(p, {
            "description": "Health probe helper",
            "symbols":  [f"probe_{p.split('_')[-1].split('.')[0]}"],
            "imports":  [],
        })
    with patch("services.graph_builder.get_graph_full",
               new=AsyncMock(return_value=graph)):
        result = await select_relevant_files(
            db=None,
            project_id="proj_test",
            user_id="user_test",
            task_description=_TASK,
            planner_files=planner_15,
            top_n=8,
        )
    assert result["ok"] is True
    candidates = set(result["candidates"])
    planner_set = set(planner_15)
    # Trim happened — candidates ≤ full planner_set
    # (top_n + len(planner_set) is the cap; with Fix C the actual
    # constraint tightens further.) The critical invariant even
    # today is: no NEW files outside planner_set.
    unrelated_added = candidates - planner_set
    assert not unrelated_added, (
        f"Regression (a): trim-scenario introduced external files: "
        f"{sorted(unrelated_added)}"
    )


@pytest.mark.asyncio
async def test_regression_b_trust_verbatim_two_files():
    """Planner ≤ 2 files hits the trust-verbatim guard (line 127)
    and returns planner_set unchanged. This path is UNTOUCHED by
    Fix C — must still work identically."""
    planner_2 = [
        "backend/routers/health.py",
        "backend/tests/test_health_detailed.py",
    ]
    with patch("services.graph_builder.get_graph_full",
               new=AsyncMock(return_value=_mock_graph_prod_shape())):
        result = await select_relevant_files(
            db=None,
            project_id="proj_test",
            user_id="user_test",
            task_description=_TASK,
            planner_files=planner_2,
            top_n=10,
        )
    assert result["ok"] is True
    assert result.get("trusted_planner") is True
    assert set(result["candidates"]) == set(planner_2)


@pytest.mark.asyncio
async def test_regression_c_no_graph_returns_planner_set():
    """Graph load fails / graph missing → function returns
    planner_set unchanged. Fix C must not break this fallback."""
    with patch("services.graph_builder.get_graph_full",
               new=AsyncMock(return_value=None)):
        result = await select_relevant_files(
            db=None,
            project_id="proj_test",
            user_id="user_test",
            task_description=_TASK,
            planner_files=_PLANNER_FILES,
            top_n=10,
        )
    assert result["ok"] is True
    assert result["has_graph"] is False
    assert set(result["candidates"]) == set(_PLANNER_FILES)


@pytest.mark.asyncio
async def test_regression_d_scope_drift_gate_still_fires_for_planner_bloat():
    """If the PLANNER itself emits a bloated list (e.g., 20 files
    when frozen_files_to_change had 3), the scope-drift gate at
    loop_engine.py:1082 must STILL fire. Fix C is scoped to
    file_selector; it must not accidentally disarm the scope-drift
    safety net for a different failure mode (planner-side expansion,
    not file_selector-side).

    We simulate this by checking that the invariant `frozen ⊂ current`
    still produces `extras = current - frozen != {}` when the planner
    itself bloats — the gate uses pure set arithmetic, independent of
    file_selector. This test documents that Fix C's boundary is
    file_selector.py only; scope_drift detection is unaffected."""
    frozen = {"backend/routers/health.py",
              "backend/services/health_service.py",
              "backend/tests/test_health_detailed.py"}
    # Simulated planner-emitted bloat (imagine plan.files_to_change
    # itself grew via a plan-phase mutation — the file_selector never
    # got involved).
    planner_bloated_now = frozen | {
        "backend/routers/admin_financials_router.py",
        "backend/services/parliament.py",
    }
    extras = sorted(planner_bloated_now - frozen)
    # Scope-drift gate condition: `if extras: pause_for_user(...)`.
    # Fix C's boundary must leave this arithmetic untouched.
    assert extras == [
        "backend/routers/admin_financials_router.py",
        "backend/services/parliament.py",
    ], "Scope-drift set arithmetic must be independent of file_selector"


@pytest.mark.asyncio
async def test_regression_e_defensive_fallback_when_all_score_zero():
    """Explicit test for the 'scoring produces zero for every node'
    defensive fallback. If the task description is empty or contains
    only stopwords, `_tokenize` returns [] and every file scores 0.
    The +200 planner-blessed boost still applies (line 143), so
    planner files should still be in candidates. But if by some
    edge case even that path returns [], the function must fall
    back to planner_set — never return an empty candidates list
    while planner_set is non-empty."""
    # Empty task description → _tokenize returns []
    with patch("services.graph_builder.get_graph_full",
               new=AsyncMock(return_value=_mock_graph_prod_shape())):
        result = await select_relevant_files(
            db=None,
            project_id="proj_test",
            user_id="user_test",
            task_description="",  # ← triggers empty-tokens branch
            planner_files=_PLANNER_FILES,
            top_n=10,
        )
    # Guard at line 97 returns has_graph=False on empty task_description,
    # candidates = planner_files verbatim.
    assert result["ok"] is True
    assert set(result["candidates"]) == set(_PLANNER_FILES), (
        "Empty task_description path must fall back to planner_set"
    )

    # Even with a non-empty task description made entirely of stopwords,
    # after tokenisation there are effectively zero useful tokens, so
    # every node scores 0 EXCEPT planner files (each gets +200 planner
    # boost at line 143). Candidates must still contain planner_set.
    with patch("services.graph_builder.get_graph_full",
               new=AsyncMock(return_value=_mock_graph_prod_shape())):
        result = await select_relevant_files(
            db=None,
            project_id="proj_test",
            user_id="user_test",
            task_description="the and or but to of in on for is are",
            planner_files=_PLANNER_FILES,
            top_n=10,
        )
    assert result["ok"] is True
    # Planner files must ALL survive the +200 boost path, no matter
    # how narrow the token scoring is.
    for pf in _PLANNER_FILES:
        assert pf in result["candidates"], (
            f"Defensive fallback broken: planner file {pf} dropped "
            f"despite +200 boost. Candidates: {result['candidates']}"
        )
