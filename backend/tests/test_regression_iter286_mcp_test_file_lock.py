"""
Iter 286 (Track 0) — MCP write-path test-file lock.

Bug (session audit): both `write_repo_file` (MCP local_tools) and
`ship_code` (Mode-C task pipeline in cto_projects.py) previously
committed any file the LLM produced, gated only by Vanguard's
regex secrets scan. The Loop-pipeline test-file lock
(services/loop_diff_classifier) was NOT enforced on these paths —
an MCP client (Claude Desktop, Cursor) or the ship_code tool could
silently overwrite test_*.py / *.test.jsx to make a failing test
pass, defeating the whole purpose of the regression suite.

Fix: both paths now call `is_test_or_fixture(path)`. If any target
is a test file, the write/commit is blocked unless
`allow_test_file_change=true` is set — and that flag is READ from
the task record itself, never from LLM-generated content, so the
model cannot self-grant.

These tests lock:
  1. `write_repo_file` blocks a test-file path by default.
  2. `write_repo_file` allows a test-file path when
     `allow_test_file_change=True` is set in args.
  3. `write_repo_file` still allows non-test paths as before.
  4. `ship_code` / cto_projects.py::_run_task has the source-level
     gate in place before the `gh_api_commit` call — regression
     covers the exact lines that were missing.
"""
from __future__ import annotations
import asyncio


def test_regression_iter286_write_repo_file_blocks_test_files():
    """
    write_repo_file MUST return an error with `gate="test_file_lock"`
    when the target path matches a test-file heuristic.
    """
    from services.local_tools import write_repo_file

    async def _run():
        result = await write_repo_file(
            ctx={"user_id": "u1", "project_id": "p1"},
            args={
                "path":    "backend/tests/test_regression_iter999.py",
                "content": "def test_x(): assert True",
            },
        )
        return result

    result = asyncio.run(_run())
    assert result["ok"] is False, (
        "test-file write MUST be blocked by default. Got: " + str(result)
    )
    assert result.get("gate") == "test_file_lock"


def test_regression_iter286_write_repo_file_allows_test_files_with_override():
    """
    With `allow_test_file_change=True`, the same call must pass the
    test-file gate (and then hit whatever downstream check exists —
    for this unit test we're only asserting the gate step doesn't
    reject).
    """
    from services.local_tools import write_repo_file

    async def _run():
        result = await write_repo_file(
            ctx={"user_id": "u1", "project_id": "p1"},
            args={
                "path":    "backend/tests/test_regression_iter999.py",
                "content": "def test_x(): assert True",
                "allow_test_file_change": True,
            },
        )
        return result

    result = asyncio.run(_run())
    # Downstream checks (project not found, no PAT, etc.) will still
    # reject in unit-test isolation — but the *gate* must not be the
    # rejection reason.
    assert result.get("gate") != "test_file_lock", (
        "override MUST let the call past the test-file gate. Got: "
        + str(result)
    )


def test_regression_iter286_write_repo_file_allows_normal_paths():
    """
    Non-test paths must not be affected by the new gate.
    """
    from services.local_tools import write_repo_file

    async def _run():
        return await write_repo_file(
            ctx={"user_id": "u1", "project_id": "p1"},
            args={
                "path":    "src/components/Widget.jsx",
                "content": "export default () => null;",
            },
        )

    result = asyncio.run(_run())
    assert result.get("gate") != "test_file_lock", (
        "non-test paths must not trigger the test-file gate"
    )


def test_regression_iter286_ship_code_has_test_file_gate_in_source():
    """
    cto_projects.py::_run_task MUST perform the same test-file
    classification before invoking gh_api_commit. Source-level check
    so the gate can't be silently removed by a refactor.
    """
    src = open("/app/backend/routers/cto_projects.py").read()

    # The gate must sit between the "Committing to GitHub…" _emit
    # and the actual gh_api_commit call.
    commit_emit_idx = src.find('"Committing to GitHub…"')
    assert commit_emit_idx > -1
    gh_commit_idx = src.find("gh_api_commit(", commit_emit_idx)
    assert gh_commit_idx > commit_emit_idx
    gate_region = src[commit_emit_idx: gh_commit_idx]

    assert "is_test_or_fixture" in gate_region, (
        "test-file classifier import must live between the "
        "phase_commit emit and the gh_api_commit call"
    )
    assert "test_file_lock" in gate_region, (
        "blocked_reason='test_file_lock' must be persisted on reject"
    )
    assert "allow_test_file_change" in gate_region, (
        "override flag must be read from task record"
    )


def test_regression_iter286_ship_code_override_not_llm_grantable():
    """
    The `allow_test_file_change` flag MUST be read from the task
    record (`db.cto_tasks.find_one`), NEVER from `edits` or any
    LLM-produced payload. Enforce at source level.
    """
    src = open("/app/backend/routers/cto_projects.py").read()

    # Find the gate block and confirm the flag is read via find_one.
    idx = src.find("_test_touched = [e for e in edits")
    assert idx > -1
    block = src[idx: idx + 1200]
    assert "cto_tasks.find_one(" in block, (
        "allow_test_file_change must be loaded from the DB task row"
    )
    # Guard-rail: the flag must NOT be pulled from `edits` (LLM output).
    # Simple heuristic — no line in the block should say
    # `edits[...].get("allow_test_file_change")`.
    for line in block.splitlines():
        if "allow_test_file_change" in line and "edits" in line:
            assert False, (
                "SECURITY: allow_test_file_change must NEVER be read "
                f"from `edits` (LLM output). Offending line: {line}"
            )
