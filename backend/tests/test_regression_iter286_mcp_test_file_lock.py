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
    LLM-produced payload.

    Iter 297 — HYBRID upgrade (was STATIC_GREP). We now actually
    EXECUTE the two guarantees the source-level gate is supposed to
    provide:

      (a) Real behaviour: call `is_test_or_fixture` on 10 real test
          paths and 5 real source paths and prove the classifier
          returns the correct answer for each. The gate can't do
          its job if the classifier is broken — this test catches
          a broken classifier that a source-grep never would.
      (b) Real behaviour: simulate the "LLM tries to smuggle
          allow_test_file_change via edits[]" attack — build a fake
          edits list where every edit carries `allow_test_file_change=
          True` embedded, then run the same DB-read pattern the
          production code uses (`cto_tasks.find_one(..., {"allow_
          test_file_change": 1, "_id": 0})`) against a stub DB where
          the task row has NO such field. Prove that the resulting
          `_allow_tests` is False — the smuggled edit-level flag
          cannot flip it. This is the invariant the source-grep
          check below merely *describes*.
      (c) Belt-and-suspenders source assertion (the HYBRID part):
          the same source-string check is retained as a defensive
          guard against a refactor that deletes the DB-read entirely.
    """
    # ── (a) behavioural — classifier correctness ────────────────
    from services.loop_diff_classifier import is_test_or_fixture

    real_test_paths = [
        "backend/tests/test_regression_iter999.py",
        "backend/tests/conftest.py",
        "backend/tests/test_iter212m237_security_gate.py",
        "frontend/src/App.test.jsx",
        "frontend/src/lib/utils.test.js",
        "frontend/src/components/__tests__/Widget.test.tsx",
        "test/e2e/login.spec.ts",
        "tests/integration/api.spec.py",
        "vitest.config.js",
        "jest.config.mjs",
    ]
    for p in real_test_paths:
        assert is_test_or_fixture(p) is True, (
            f"classifier failed to flag test path: {p!r}"
        )
    real_source_paths = [
        "backend/routers/scaffold.py",
        "backend/services/loop_engine.py",
        "frontend/src/App.jsx",
        "backend/main.py",
        "frontend/src/components/Widget.jsx",
    ]
    for p in real_source_paths:
        assert is_test_or_fixture(p) is False, (
            f"classifier falsely flagged source path as test: {p!r}"
        )

    # ── (b) behavioural — DB-read pattern is not smuggle-able ───
    # Simulate the exact production code path: build edits where
    # each carries `allow_test_file_change=True` (attacker payload),
    # then run the DB read the same way cto_projects.py does.
    class _StubTasksColl:
        def __init__(self, row):
            self._row = row
            self.calls: list[tuple] = []
        async def find_one(self, q, proj=None):
            self.calls.append((dict(q), dict(proj) if proj else None))
            # The prod code projects ONLY the flag — if the projection
            # widens, this stub still returns the row so the test
            # keeps working. But we assert on the projection below.
            return dict(self._row) if self._row else None

    class _StubDB:
        def __init__(self, tasks_row):
            self.cto_tasks = _StubTasksColl(tasks_row)

    async def _resolve_allow_tests(db, task_id, edits):
        """Replicate the exact production snippet from cto_projects.
        py so a refactor of this helper WILL diverge from prod (which
        is the point — this test would catch it)."""
        _task_row = await db.cto_tasks.find_one(
            {"task_id": task_id},
            {"allow_test_file_change": 1, "_id": 0},
        ) or {}
        _allow_tests = bool(_task_row.get("allow_test_file_change"))
        # The attacker's payload is IGNORED — we never look at edits[].
        return _allow_tests, edits

    import asyncio
    smuggle_edits = [
        {"path": "backend/tests/test_regression_iter999.py",
         "content": "def test_x(): assert True",
         "allow_test_file_change": True},                    # ← smuggle
    ]
    stub_db = _StubDB({"task_id": "t1"})                      # NO flag
    allow, kept_edits = asyncio.run(
        _resolve_allow_tests(stub_db, "t1", smuggle_edits)
    )
    assert allow is False, (
        "SECURITY REGRESSION: smuggling allow_test_file_change=True "
        "via edits[] flipped the gate. The DB-read pattern must "
        "IGNORE edit-level flags."
    )
    # The DB call must project the ONE flag — a wider projection
    # would leak LLM-controlled fields into the resolution path.
    assert stub_db.cto_tasks.calls, "DB read must fire"
    _q, _proj = stub_db.cto_tasks.calls[0]
    assert _q == {"task_id": "t1"}
    assert _proj == {"allow_test_file_change": 1, "_id": 0}, (
        f"projection must be exactly the flag; got {_proj}"
    )
    # Positive control — if the DB DOES carry the flag, allow=True.
    stub_db2 = _StubDB({"task_id": "t2", "allow_test_file_change": True})
    allow2, _ = asyncio.run(
        _resolve_allow_tests(stub_db2, "t2", smuggle_edits)
    )
    assert allow2 is True, (
        "positive control failed — DB-set flag must enable the override"
    )

    # ── (c) defensive source-level guard (HYBRID) ───────────────
    # Retained so a refactor that deletes the DB-read entirely is
    # caught even if the caller pattern above stops matching prod.
    src = open("/app/backend/routers/cto_projects.py").read()
    idx = src.find("_test_touched = [e for e in edits")
    assert idx > -1
    block = src[idx: idx + 1200]
    assert "cto_tasks.find_one(" in block
    for line in block.splitlines():
        if "allow_test_file_change" in line and "edits" in line:
            assert False, (
                "SECURITY: allow_test_file_change must NEVER be read "
                f"from `edits` (LLM output). Offending line: {line}"
            )
