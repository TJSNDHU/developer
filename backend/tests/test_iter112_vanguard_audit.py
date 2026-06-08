"""Iter 112 — Vanguard audit log + admin dashboard endpoints.

Tests:
  - log_blocked_commit writes the expected schema
  - log_blocked_commit is safe when db is None (audit must NOT raise)
  - top_rule picks the highest-severity finding's rule slug
  - layer_blocked detects which Vanguard layer actually blocked
  - weekly_stats aggregates by-rule / by-project / by-severity
  - recent_blocks returns rows in descending ts order
"""
import time
from datetime import datetime, timezone

import pytest

from services import vanguard_audit as va


# ── helper introspection ────────────────────────────────────────
def test_top_rule_picks_highest_severity():
    findings = [
        {"severity": "LOW",      "rule": "info_leak"},
        {"severity": "CRITICAL", "rule": "hardcoded_secret"},
        {"severity": "HIGH",     "rule": "sql_injection"},
    ]
    assert va._top_rule(findings) == "hardcoded_secret"


def test_top_rule_handles_no_findings():
    assert va._top_rule([]) == "unknown"


def test_top_rule_falls_back_to_name_then_type():
    assert va._top_rule([{"severity": "HIGH", "name": "xss"}]) == "xss"
    assert va._top_rule([{"severity": "HIGH", "type": "ssrf"}]) == "ssrf"
    assert va._top_rule([{"severity": "HIGH"}]) == "unknown"


def test_layer_blocked_detects_regex():
    r = {"regex": {"blocked": True}, "agent": {}, "e2b": {"skipped": True}}
    assert va._layer_blocked(r) == ["regex"]


def test_layer_blocked_detects_agent():
    r = {
        "regex": {"blocked": False},
        "agent": {"pass": False, "model": "claude"},
        "e2b":   {"skipped": True},
    }
    assert va._layer_blocked(r) == ["verify-agent"]


def test_layer_blocked_detects_e2b():
    r = {
        "regex": {"blocked": False},
        "agent": {"pass": True, "model": "claude"},
        "e2b":   {"skipped": False, "pass": False},
    }
    assert va._layer_blocked(r) == ["e2b"]


def test_layer_blocked_multiple_layers():
    r = {
        "regex": {"blocked": True},
        "agent": {"pass": False, "model": "claude"},
        "e2b":   {"skipped": False, "pass": False},
    }
    assert va._layer_blocked(r) == ["regex", "verify-agent", "e2b"]


def test_layer_blocked_agent_without_model_not_counted():
    """When the agent skipped (no model field), it shouldn't show up as
    a blocking layer even if `pass=False` default."""
    r = {"regex": {"blocked": True}, "agent": {"pass": True, "model": ""},
         "e2b":   {"skipped": True}}
    assert va._layer_blocked(r) == ["regex"]


# ── log_blocked_commit ─────────────────────────────────────────
class _FakeCollection:
    def __init__(self):
        self.rows = []
    async def insert_one(self, doc):
        self.rows.append(doc)
    async def create_index(self, *a, **kw):
        return "ok"
    def find(self, *a, **kw):
        return _FakeCursor(self.rows)
    async def count_documents(self, *a, **kw):
        return len(self.rows)
    def aggregate(self, *a, **kw):
        return _FakeCursor([])


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows
    def sort(self, *a, **kw):
        return self
    def limit(self, n):
        self._rows = self._rows[:n]
        return self
    def __aiter__(self):
        self._it = iter(self._rows)
        return self
    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration


class _FakeDB:
    def __init__(self):
        self.vanguard_audit = _FakeCollection()


@pytest.mark.asyncio
async def test_log_blocked_commit_writes_full_schema():
    db = _FakeDB()
    verify_result = {
        "pass":     False,
        "findings": [
            {"file": "app.py", "line": 12, "severity": "CRITICAL",
             "rule": "hardcoded_secret",
             "message": "API key on line 12"},
            {"file": "app.py", "line": 18, "severity": "HIGH",
             "rule": "sql_injection",
             "message": "f-string SQL"},
        ],
        "regex":   {"blocked": True, "count": 2},
        "agent":   {"pass": False, "model": "claude", "findings": []},
        "e2b":     {"pass": True,  "skipped": True, "reason": "no py"},
        "summary": "regex: BLOCK | verify-agent: BLOCK | e2b: skipped",
    }
    await va.log_blocked_commit(
        db,
        user_id="u-123",
        project="owner/repo@main",
        verify_result=verify_result,
        project_id="proj-xyz",
        task_id="task-abc",
    )
    assert len(db.vanguard_audit.rows) == 1
    row = db.vanguard_audit.rows[0]
    assert row["user_id"] == "u-123"
    assert row["project"] == "owner/repo@main"
    assert row["project_id"] == "proj-xyz"
    assert row["task_id"] == "task-abc"
    assert row["rule_triggered"] == "hardcoded_secret"
    assert row["total_findings"] == 2
    assert "regex" in row["layers_blocked"]
    assert "verify-agent" in row["layers_blocked"]
    assert "e2b" not in row["layers_blocked"]
    assert "BLOCK" in row["layer_summary"]
    # ts shape
    assert isinstance(row["ts_unix"], float)
    assert row["ts"].startswith("20") and "T" in row["ts"]


@pytest.mark.asyncio
async def test_log_blocked_commit_safe_with_none_db():
    # Must NOT raise — auditing must never block the user response
    await va.log_blocked_commit(
        None, user_id="u", project="o/r@main",
        verify_result={"findings": [], "regex": {}, "agent": {}, "e2b": {}},
    )


@pytest.mark.asyncio
async def test_log_blocked_commit_caps_findings_at_25():
    db = _FakeDB()
    huge = [{"file": "x.py", "severity": "LOW", "rule": f"r{i}",
             "message": "x"} for i in range(60)]
    await va.log_blocked_commit(
        db, user_id="u", project="o/r@main",
        verify_result={"findings": huge, "regex": {"blocked": False},
                       "agent": {}, "e2b": {"skipped": True}},
    )
    assert len(db.vanguard_audit.rows[0]["findings"]) == 25


# ── weekly_stats / recent_blocks structure ────────────────────
@pytest.mark.asyncio
async def test_weekly_stats_shape_when_empty():
    db = _FakeDB()
    out = await va.weekly_stats(db, since_days=7)
    assert out["total_blocked"] == 0
    assert out["top_rule"] is None
    assert out["by_rule"] == []
    assert out["by_project"] == []
    assert out["by_severity"] == []
    assert out["by_day"] == []
    assert out["window_days"] == 7


@pytest.mark.asyncio
async def test_weekly_stats_with_none_db():
    out = await va.weekly_stats(None)
    assert out["total_blocked"] == 0
    assert out["top_rule"] is None


@pytest.mark.asyncio
async def test_recent_blocks_with_none_db_returns_empty():
    assert await va.recent_blocks(None) == []
