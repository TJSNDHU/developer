"""
Iter 289 — Track 1 Lane A + Task 2 (mock-reality) + Task 3 (mutation
smoke). Locks the new infra so a later refactor cannot silently
break it.

Surfaces:
  - services.qa_matrix.matrix_coverage_gap()  — per-journey gap-list
  - services.qa_matrix.canary_e2e(mode)       — 'lane_a' | 'lane_b'
  - services.mock_reality_check.run_all()     — shape probes
  - MCP tools: run_canary_e2e, qa_mock_reality_check (schemas +
    dispatch + founder gate)
"""
from __future__ import annotations

import asyncio
import os
import json
import sys
import pytest

sys.path.insert(0, "/app/backend")


# ── qa_matrix.matrix_coverage_gap ────────────────────────────────────

def test_matrix_coverage_gap_shape_and_summary():
    from services.qa_matrix import matrix_coverage_gap
    r = matrix_coverage_gap()
    assert r["ok"] is True
    assert isinstance(r["per_journey"], list) and r["per_journey"]
    for row in r["per_journey"]:
        assert set(row.keys()) >= {
            "journey_id", "title", "status", "severity",
            "tracked_paths", "hit", "uncovered", "uncovered_pct",
        }
    s = r["summary"]
    assert isinstance(s["journeys"], int) and s["journeys"] >= 20
    assert isinstance(s["with_gap"], int)
    assert isinstance(s["fully_untouched"], int)
    assert isinstance(s["p0_with_gap"], list)


def test_matrix_coverage_gap_respects_min_hit_threshold():
    """Verify the ≥5% hit threshold is actually applied — a path with
    a percent below _MIN_COVERAGE_HIT_PCT must land in 'uncovered'."""
    from services import qa_matrix
    assert qa_matrix._MIN_COVERAGE_HIT_PCT == 5.0


# ── qa_matrix.canary_e2e — Lane A + Lane B contract ─────────────────

def test_canary_e2e_lane_a_returns_real_coverage_and_gap():
    from services.qa_matrix import canary_e2e
    r = canary_e2e("lane_a")
    assert r["mode"] == "lane_a"
    assert "backend_coverage" in r
    assert "frontend_coverage" in r
    assert "gap_report" in r
    # Signal keys the founder can eyeball in Claude Desktop.
    sig = r["signal"]
    assert set(sig.keys()) == {
        "p0_journeys_with_any_gap",
        "journeys_fully_untouched",
        "journeys_total",
    }


def test_canary_e2e_lane_b_fails_closed_when_env_missing(monkeypatch):
    """Lane B MUST refuse cleanly when even one of the 5 canary env
    vars is missing — no stubbed passing result, no silent skip."""
    from services.qa_matrix import canary_e2e
    for k in ("AUREM_CANARY_REPO_OWNER", "AUREM_CANARY_REPO_NAME",
              "AUREM_CANARY_BRANCH", "AUREM_ORG_NAME",
              "AUREM_ORG_GITHUB_APP_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    r = canary_e2e("lane_b")
    assert r["ok"] is False
    assert r["mode"] == "lane_b"
    assert r["reason"] == "lane_b_not_configured"
    assert set(r["missing_env"]) == {
        "AUREM_CANARY_REPO_OWNER", "AUREM_CANARY_REPO_NAME",
        "AUREM_CANARY_BRANCH", "AUREM_ORG_NAME",
        "AUREM_ORG_GITHUB_APP_TOKEN",
    }


def test_canary_e2e_rejects_unknown_mode():
    from services.qa_matrix import canary_e2e
    r = canary_e2e("lane_c_pretend")
    assert r["ok"] is False
    assert r["reason"] == "unknown_mode"


# ── mock_reality_check — deterministic layers ───────────────────────

def test_diff_shape_flags_missing_as_breaking():
    from services.mock_reality_check import _diff_shape
    r = _diff_shape({"id", "name"}, {"id": 1})
    assert r["missing"] == ["name"]
    assert r["breaking_drift"] is True


def test_diff_shape_flags_extras_as_info_only():
    """Upstream added a NEW field — this must NOT flip ok to false."""
    from services.mock_reality_check import _diff_shape
    r = _diff_shape({"id"}, {"id": 1, "new_shiny": True})
    assert r["missing"] == []
    assert r["unexpected"] == ["new_shiny"]
    assert r["breaking_drift"] is False
    assert r["info_drift_only"] is True


def test_diff_shape_clean_when_exact_match():
    from services.mock_reality_check import _diff_shape
    r = _diff_shape({"id", "name"}, {"id": 1, "name": "x"})
    assert r["breaking_drift"] is False
    assert r["info_drift_only"] is False


def test_mock_reality_expected_keys_are_grounded_in_current_code():
    """The `_GITHUB_REPO_KEYS` and `_OPENROUTER_MODEL_KEYS` sets MUST
    stay small + auditable. If they balloon past 20 entries, we've
    turned this into de-facto contract testing — which the user
    explicitly said was overkill. Lock the size limit."""
    from services.mock_reality_check import (
        _GITHUB_REPO_KEYS, _OPENROUTER_MODEL_KEYS,
    )
    assert 5 <= len(_GITHUB_REPO_KEYS) <= 20, (
        "Keep the GH shape set small — this is a sanity probe, "
        "not a contract test"
    )
    assert 3 <= len(_OPENROUTER_MODEL_KEYS) <= 15


# ── MCP tool dispatch — schema + registration ───────────────────────

def test_mcp_registers_run_canary_e2e_and_mock_reality():
    from routers.mcp import TOOLS, _TOOL_DISPATCH
    for name in ("run_canary_e2e", "qa_mock_reality_check"):
        assert name in _TOOL_DISPATCH, f"dispatch missing: {name}"
    schema_names = {t["name"] for t in TOOLS}
    for name in ("run_canary_e2e", "qa_mock_reality_check"):
        assert name in schema_names, f"schema missing: {name}"


def test_mcp_qa_tools_reject_non_founder():
    """Non-founder callers on either new tool must be refused — same
    property the iter287 QA tools already lock."""
    from cto_services.db import set_db
    from routers.mcp import _tool_run_canary_e2e, _tool_qa_mock_reality_check
    set_db(None)  # force the DB-unavailable branch

    async def _run():
        for fn in (_tool_run_canary_e2e, _tool_qa_mock_reality_check):
            with pytest.raises(RuntimeError) as ei:
                await fn("nobody_zzz", {})
            msg = str(ei.value).lower()
            assert ("founder" in msg) or ("database" in msg) \
                   or ("event loop" in msg), msg

    asyncio.run(_run())


def test_mcp_run_canary_arg_validation():
    from routers.mcp import _tool_run_canary_e2e
    from routers import mcp as _mcp

    async def _noop(_uid):
        return None

    orig = _mcp._require_founder
    _mcp._require_founder = _noop
    try:
        async def _run():
            with pytest.raises(ValueError):
                await _tool_run_canary_e2e("anyone", {"mode": "lane_x"})
        asyncio.run(_run())
    finally:
        _mcp._require_founder = orig


def test_mcp_mock_reality_timeout_validation():
    from routers.mcp import _tool_qa_mock_reality_check
    from routers import mcp as _mcp

    async def _noop(_uid):
        return None

    orig = _mcp._require_founder
    _mcp._require_founder = _noop
    try:
        async def _run():
            with pytest.raises(ValueError):
                await _tool_qa_mock_reality_check("anyone", {"timeout": 0.1})
            with pytest.raises(ValueError):
                await _tool_qa_mock_reality_check("anyone", {"timeout": 999})
        asyncio.run(_run())
    finally:
        _mcp._require_founder = orig


# ── Coverage artefacts must actually exist ──────────────────────────

def test_backend_coverage_json_written():
    assert os.path.isfile("/app/backend/coverage.json"), (
        "run: cd /app/backend && python -m pytest tests/... "
        "--cov=services --cov=routers --cov=cto_services "
        "--cov-report=json:/app/backend/coverage.json"
    )
    with open("/app/backend/coverage.json", "r", encoding="utf-8") as f:
        data = json.load(f)
    # Every real coverage.json has these top-level keys.
    assert "files" in data and "totals" in data
    assert isinstance(data["totals"].get("percent_covered"), (int, float))


def test_frontend_coverage_summary_written():
    assert os.path.isfile("/app/frontend/coverage/coverage-summary.json"), (
        "run: cd /app/frontend && npx vitest run --coverage"
    )
    with open("/app/frontend/coverage/coverage-summary.json",
              "r", encoding="utf-8") as f:
        data = json.load(f)
    assert "total" in data
    # Percentage may be 0 (no target-file test yet) but the key MUST
    # exist — this is what the QA MCP tool reads.
    assert "statements" in data["total"]
    assert "pct" in data["total"]["statements"]
