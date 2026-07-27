#!/usr/bin/env python3
"""Focused runtime probe for Iter 311 Fix C.

Verifies file_selector.select_relevant_files cannot introduce files outside
planner_files for a realistic 3-file planner scope and keyword-collision graph.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, "/app/backend")

from services.file_selector import select_relevant_files  # noqa: E402


TASK = (
    "Add a /health/detailed endpoint returning DB latency, redis status, "
    "version, and build_sha, with 3 pytest tests"
)
PLANNER_FILES = [
    "backend/routers/health.py",
    "backend/services/health_service.py",
    "backend/tests/test_health_detailed.py",
]


def mock_graph() -> dict:
    return {
        "nodes": {
            "backend/routers/health.py": {
                "description": "System health check endpoint returning DB latency, redis, version, build_sha",
                "symbols": ["health_check", "detailed_health"],
                "imports": ["fastapi", "motor"],
            },
            "backend/services/health_service.py": {
                "description": "Health status probe service",
                "symbols": ["probe_db_latency", "probe_redis"],
                "imports": ["motor", "redis"],
            },
            "backend/tests/test_health_detailed.py": {
                "description": "Tests for /health/detailed endpoint",
                "symbols": ["test_health_detailed_returns_db_latency"],
                "imports": ["httpx"],
            },
            # Exact unrelated files from the reported scope-drift class.
            "backend/routers/campaign_health_router.py": {
                "description": "Marketing campaign health tracking endpoint",
                "symbols": ["campaign_status", "campaign_metrics"],
                "imports": ["fastapi"],
            },
            "backend/routers/admin_financials_router.py": {
                "description": "Admin financials dashboard endpoint with detailed revenue breakdown",
                "symbols": ["get_revenue"],
                "imports": ["fastapi"],
            },
            "backend/routers/case_study_router.py": {
                "description": "Case study endpoint returning detailed customer case studies",
                "symbols": ["list_case_studies"],
                "imports": ["fastapi"],
            },
            "backend/routers/aurem_llm_proxy_router.py": {
                "description": "LLM proxy endpoint with detailed usage logging",
                "symbols": ["proxy_llm"],
                "imports": ["fastapi"],
            },
            "backend/routers/aurem_redis_router.py": {
                "description": "Redis status endpoint",
                "symbols": ["redis_health_endpoint"],
                "imports": ["fastapi", "redis"],
            },
            "backend/routers/autonomous_repair_router.py": {
                "description": "Autonomous repair endpoint with detailed failure logs",
                "symbols": ["repair_status"],
                "imports": ["fastapi"],
            },
            "backend/routers/action_engine_router.py": {
                "description": "Action engine endpoint",
                "symbols": ["action_status"],
                "imports": ["fastapi"],
            },
            "backend/_archive/evolver_router.py": {
                "description": "Legacy evolver endpoint with detailed logs",
                "symbols": ["evolve_status"],
                "imports": ["fastapi"],
            },
            "backend/db.py": {
                "description": "Database session endpoint helpers",
                "symbols": ["get_db"],
                "imports": ["motor"],
            },
        }
    }


async def main() -> int:
    results = {}
    with patch("services.graph_builder.get_graph_full", new=AsyncMock(return_value=mock_graph())):
        top10 = await select_relevant_files(
            db=None,
            project_id="proj_iter311_probe",
            user_id="user_iter311_probe",
            task_description=TASK,
            planner_files=PLANNER_FILES,
            top_n=10,
        )
        top2 = await select_relevant_files(
            db=None,
            project_id="proj_iter311_probe",
            user_id="user_iter311_probe",
            task_description=TASK,
            planner_files=PLANNER_FILES,
            top_n=2,
        )

    planner_set = set(PLANNER_FILES)
    top10_extras = sorted(set(top10["candidates"]) - planner_set)
    top2_extras = sorted(set(top2["candidates"]) - planner_set)
    top2_is_proper_subset = set(top2["candidates"]) < planner_set

    assert top10["ok"] is True and top10["has_graph"] is True
    assert not top10_extras, top10_extras
    assert top10["total_scored"] == len(PLANNER_FILES)
    assert top2_is_proper_subset, top2["candidates"]
    assert not top2_extras, top2_extras

    results = {
        "ok": True,
        "top10_candidates": top10["candidates"],
        "top10_extras": top10_extras,
        "top10_total_scored": top10["total_scored"],
        "top2_candidates": top2["candidates"],
        "top2_is_proper_subset_of_planner_files": top2_is_proper_subset,
        "top2_extras": top2_extras,
    }
    out = Path("/app/test_reports/bug_verification_artifacts/iter311_file_selector_probe_result.json")
    out.write_text(json.dumps(results, indent=2, sort_keys=True) + "\n")
    print(json.dumps(results, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))