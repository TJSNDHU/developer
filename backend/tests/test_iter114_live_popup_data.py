"""Iter 114 — Live task popup data contract.

Tests:
  - build_files_read returns correct {name, lines_count} list
  - build_files_changed line-level diff includes:
      lines_added, lines_removed, line_number, old_value, new_value
  - shape_vanguard_findings normalises to popup contract
  - Empty case → vanguard clean ([] findings)
  - GET /cto/tasks/{id} returns the new fields when present in the doc
"""
import os
import asyncio
import pytest

os.environ.setdefault("APP_URL", "https://auremcto.com")

from services.task_diff import (
    build_files_read,
    build_files_changed,
    shape_vanguard_findings,
)


# ── build_files_read ────────────────────────────────────────────
def test_files_read_counts_lines_per_file():
    out = build_files_read({"a.py": "line1\nline2\nline3", "b.py": "x"})
    assert {"name": "a.py", "lines_count": 3} in out
    assert {"name": "b.py", "lines_count": 1} in out


def test_files_read_skips_none_bodies():
    """Files that 404'd return None — must NOT appear in files_read."""
    out = build_files_read({"a.py": "x\n", "b.py": None, "c.py": ""})
    names = [r["name"] for r in out]
    assert "a.py" in names
    assert "b.py" not in names
    # empty-string body still counts as a read attempt (0 lines)
    assert any(r["name"] == "c.py" and r["lines_count"] == 0 for r in out)


# ── build_files_changed ─────────────────────────────────────────
def test_changed_detects_first_line_diff():
    before = {"a.py": "x = 1\ny = 2\nz = 3\n"}
    after  = {"a.py": "x = 1\ny = 99\nz = 3\n"}
    rows = build_files_changed(before, after)
    row = rows[0]
    assert row["name"] == "a.py"
    assert row["line_number"] == 2
    assert row["old_value"] == "y = 2"
    assert row["new_value"] == "y = 99"
    assert row["lines_added"]   >= 1
    assert row["lines_removed"] >= 1


def test_changed_treats_brand_new_file():
    rows = build_files_changed({}, {"new.py": "print('hi')\n"})
    row = rows[0]
    assert row["name"] == "new.py"
    assert row["line_number"] == 1
    assert row["old_value"] is None
    assert row["new_value"] == "print('hi')"


def test_changed_counts_added_lines():
    before = {"a.py": "x = 1\n"}
    after  = {"a.py": "x = 1\ny = 2\nz = 3\n"}
    rows = build_files_changed(before, after)
    assert rows[0]["lines_added"]   >= 2
    assert rows[0]["lines_removed"] == 0


def test_changed_counts_removed_lines():
    before = {"a.py": "x = 1\ny = 2\nz = 3\n"}
    after  = {"a.py": "x = 1\n"}
    rows = build_files_changed(before, after)
    assert rows[0]["lines_added"]   == 0
    assert rows[0]["lines_removed"] >= 2


def test_changed_truncates_long_lines():
    """Old/new value preview must not exceed ~240 chars to keep popup
    payload light."""
    long_line = "x = " + "A" * 1000
    before = {"a.py": long_line}
    after  = {"a.py": "x = 'short'"}
    row = build_files_changed(before, after)[0]
    assert len(row["old_value"]) <= 240
    assert row["new_value"] == "x = 'short'"


# ── shape_vanguard_findings ─────────────────────────────────────
def test_shape_vanguard_normalises_keys():
    raw = [{"rule": "hardcoded_secret", "file": "auth.py", "line": 12,
            "severity": "CRITICAL", "message": "AWS key"}]
    out = shape_vanguard_findings(raw, status="blocked")
    assert len(out) == 1
    f = out[0]
    assert f["rule"] == "hardcoded_secret"
    assert f["file"] == "auth.py"
    assert f["line"] == 12
    assert f["severity"] == "CRITICAL"
    assert f["status"] == "blocked"


def test_shape_vanguard_uses_name_or_type_fallback():
    raw = [{"name": "missing_auth", "severity": "high"}]
    out = shape_vanguard_findings(raw)
    assert out[0]["rule"] == "missing_auth"
    assert out[0]["severity"] == "HIGH"


def test_shape_vanguard_empty_in_empty_out():
    assert shape_vanguard_findings([]) == []
    assert shape_vanguard_findings(None) == []


# ── popup contract via GET /cto/tasks/{id} ─────────────────────
@pytest.mark.asyncio
async def test_task_endpoint_returns_popup_fields_when_present(monkeypatch):
    """GET /cto/tasks/{id} must surface the new popup fields verbatim
    when they exist on the doc. Tests via direct route module rather
    than spinning up FastAPI — the response body shape is what matters."""
    import importlib
    cto_projects = importlib.import_module("routers.cto_projects")

    # Build a fake task document
    task_doc = {
        "task_id":  "test-task-xyz",
        "user_id":  "u-1",
        "status":   "done",
        "files_read": [{"name": "auth.py", "lines_count": 142},
                        {"name": "main.py", "lines_count": 89}],
        "files_changed": [{
            "name": "auth.py", "lines_added": 1, "lines_removed": 1,
            "line_number": 112, "old_value": "verify_exp: False",
            "new_value": "verify_exp: True"}],
        "vanguard_findings": [],
        "commit_sha": "af14facabc123",
        "github_url": "https://github.com/o/r/commit/af14facabc123",
        "tokens_used":         4200,
        "agent_used":          "deepseek",
        "time_taken_seconds":  17,
        "steps": [],
    }

    # Stub the auth dependency + DB call
    async def fake_current_dev(_authz):
        return {"user_id": "u-1"}

    class _Coll:
        async def find_one(self, _q, _proj):
            return task_doc
    class _DB:
        cto_tasks = _Coll()
    monkeypatch.setattr(cto_projects, "current_dev", fake_current_dev)
    monkeypatch.setattr(cto_projects, "require_db", lambda: _DB())

    res = await cto_projects.get_task("test-task-xyz", authorization="Bearer x")
    assert res["ok"] is True
    t = res["task"]
    # All four popup-required fields present
    assert t["files_read"][0]["lines_count"] == 142
    assert t["files_changed"][0]["line_number"] == 112
    assert t["files_changed"][0]["new_value"] == "verify_exp: True"
    assert t["vanguard_findings"] == []          # clean
    assert t["commit_sha"] == "af14facabc123"
    assert t["github_url"].endswith("af14facabc123")
    assert t["time_taken_seconds"] == 17
    assert t["agent_used"] == "deepseek"
    assert t["tokens_used"] == 4200
