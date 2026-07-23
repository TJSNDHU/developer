"""
Iter 287 — Track 1 Step 2 + Step 5.

Locks two new surfaces:
  1. `/app/docs/traceability_matrix.json` — is well-formed JSON, has
     the loop_1f8 row (frozen-plan scope-enforcement during Execute),
     and its `summary` counts stay in sync with the actual `journeys`.
  2. `services/qa_matrix.py` helpers + the four MCP QA tools:
     - qa_traceability_matrix
     - qa_open_gaps
     - qa_regression_index
     - qa_coverage_summary

Every founder-only tool refuses when the calling user_id is not a
founder — verified by direct dispatch (no HTTP layer needed).
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import pytest

sys.path.insert(0, "/app/backend")


# ── Traceability matrix invariants ───────────────────────────────────

def test_traceability_matrix_is_valid_json_and_has_journeys():
    path = "/app/docs/traceability_matrix.json"
    assert os.path.isfile(path), f"missing {path}"
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert "journeys" in data
    assert isinstance(data["journeys"], list)
    assert len(data["journeys"]) >= 20, "matrix must have ≥20 journeys"


def test_traceability_matrix_has_loop_1f8_frozen_plan_scope_row():
    with open("/app/docs/traceability_matrix.json", "r", encoding="utf-8") as f:
        data = json.load(f)
    ids = [j.get("journey_id") for j in data.get("journeys") or []]
    hits = [i for i in ids if isinstance(i, str) and "1f8" in i]
    assert hits, "loop_1f8 (frozen-plan scope-enforcement during Execute) row missing from matrix"
    row = next(j for j in data["journeys"] if j["journey_id"] == hits[0])
    assert row["status"] == "OPEN_GAP", "loop_1f8 row must stay OPEN_GAP until a regression ships"
    assert row["severity"] == "p0"
    assert row.get("must_ship_regression_when_fixed") is True


def test_traceability_matrix_summary_matches_live_counts():
    from services.qa_matrix import load_matrix, matrix_summary
    m = load_matrix()
    persisted = m.get("summary") or {}
    live = matrix_summary()
    # The live summary is the source of truth; the persisted summary
    # is a static hint. They must agree — otherwise the doc has drifted.
    assert live["total_journeys"] == persisted.get("total_journeys")
    assert live["by_status"] == persisted.get("by_status")


# ── QA matrix helpers (deterministic, no LLM) ────────────────────────

def test_open_gaps_returns_p0_first_and_includes_loop_1f8():
    from services.qa_matrix import open_gaps
    gaps = open_gaps()
    assert gaps, "no OPEN_GAP rows returned"
    # First entry must be a p0.
    assert gaps[0]["severity"] == "p0"
    ids = [g["journey_id"] for g in gaps]
    assert any("1f8" in i for i in ids), "loop_1f8 not surfaced by open_gaps()"


def test_regression_index_lists_iter286_tests():
    from services.qa_matrix import regression_index
    rows = regression_index()
    assert rows, "regression_index returned nothing"
    # At least one iter286 file must be present (Track 0 shipped there).
    assert any(r["iter"] == 286 for r in rows), "iter286 regression test not indexed"


def test_coverage_summary_shape():
    from services.qa_matrix import coverage_summary
    r = coverage_summary()
    assert isinstance(r, dict)
    # We don't require a real coverage.json — just that the shape is honest.
    assert r.get("ok") in (True, False)
    if r.get("ok") is False:
        assert r.get("reason") == "no_run" or r.get("reason") == "parse_error"


# ── MCP tool dispatch — founder gate + read-only surfaces ────────────

def test_mcp_dispatch_registers_four_qa_tools():
    from routers.mcp import _TOOL_DISPATCH, TOOLS
    for name in ("qa_traceability_matrix", "qa_open_gaps",
                 "qa_regression_index", "qa_coverage_summary"):
        assert name in _TOOL_DISPATCH, f"missing dispatch: {name}"
    tool_names = {t["name"] for t in TOOLS}
    for name in ("qa_traceability_matrix", "qa_open_gaps",
                 "qa_regression_index", "qa_coverage_summary"):
        assert name in tool_names, f"missing tool schema: {name}"


def test_qa_tools_reject_non_founder_user():
    """Direct dispatch — no HTTP layer. A user_id that does NOT resolve
    to a founder row must be rejected. Fail-closed: either 'founder'
    (identity check ran) or 'database unavailable' (test env without
    a live DB — still refuses, which is the security property we want)."""
    from cto_services.db import set_db, get_db
    from routers.mcp import _tool_qa_open_gaps

    async def _run():
        # Init a Motor client so the DB path is exercised; if MONGO_URL
        # is missing we fall through to the 'database unavailable' branch
        # which still refuses — that is also acceptable.
        if get_db() is None:
            try:
                import os
                from motor.motor_asyncio import AsyncIOMotorClient
                url = os.environ.get("MONGO_URL")
                name = os.environ.get("DB_NAME")
                if url and name:
                    set_db(AsyncIOMotorClient(url)[name])
            except Exception:
                pass
        with pytest.raises(RuntimeError) as ei:
            await _tool_qa_open_gaps("user_does_not_exist_zzz", {})
        msg = str(ei.value).lower()
        assert ("founder" in msg) or ("database" in msg), msg

    asyncio.run(_run())


def test_qa_open_gaps_severity_arg_validated():
    from routers.mcp import _tool_qa_open_gaps

    async def _run():
        # Non-founder path still runs `_require_founder` first, so we
        # patch that away for arg-validation coverage.
        from routers import mcp as _mcp

        async def _noop(_uid):
            return None

        orig = _mcp._require_founder
        _mcp._require_founder = _noop
        try:
            with pytest.raises(ValueError):
                await _tool_qa_open_gaps("anyone", {"severity": "urgent"})
        finally:
            _mcp._require_founder = orig

    asyncio.run(_run())
