"""Iter 334 — Auto-QA agent (founder charter) behavioral tests.

Anti-mock rule verification: every function has a REAL implementation;
these tests execute them for real (real replay buffer, real pytest
subprocess, real report file writes, real GitHub-verify logic with a
monkeypatched HTTP layer only where an external sandbox repo is
founder-blocked — stated, not hidden).
"""
import asyncio
import json
import os
from pathlib import Path

import pytest

from services import qa_matrix as qm

# The REAL changed-file list of the iter-332 ship-gate-fix commit.
SHIP_GATE_FIX_FILES = [
    "backend/services/loop_engine.py",
    "backend/routers/loop.py",
    "frontend/src/components/LoopActionCards.jsx",
    "frontend/src/components/ChatPanel.jsx",
    "backend/tests/test_iter332_ship_gate_skip.py",
    "frontend/src/components/__tests__/LoopActionCards.iter332_ship_gate.test.jsx",
]
SHIP_GATE_FIX_MESSAGE = (
    "fix: ship human-review gate — add Approve & Ship button, "
    "skip at ship gate now terminates instead of infinite re-execute"
)


class TestDecideScope:
    def test_todays_real_ship_gate_fix_commit(self):
        """DONE-criterion: decide_scope against the actual commit that
        fixed the ship-gate bug must include ship_gate_approval +
        full_loop_lifecycle."""
        out = qm.decide_scope(SHIP_GATE_FIX_MESSAGE, SHIP_GATE_FIX_FILES)
        assert "ship_gate_approval" in out["scenarios"]
        assert "full_loop_lifecycle" in out["scenarios"]
        assert out["run_backend"] is True
        assert out["run_ui"] is True

    def test_empty_diff_falls_back_to_smoke(self):
        out = qm.decide_scope("chore: bump docs", ["README.md"])
        assert out["scenarios"] == ["smoke_baseline"]

    def test_keyword_rollback(self):
        out = qm.decide_scope("fix rollback regression", ["docs/x.md"])
        assert "rollback_cycle" in out["scenarios"]

    def test_reasoning_is_populated(self):
        out = qm.decide_scope(SHIP_GATE_FIX_MESSAGE, SHIP_GATE_FIX_FILES)
        assert "files_matched" in out["reasoning"]
        assert "loop_engine.py" in out["reasoning"]


class TestStallDetection:
    def _seed(self, loop_id, texts):
        from services import sse_replay_buffer as buf
        for t in texts:
            buf.record(loop_id, {
                "loop_id": loop_id, "state": "executing",
                "phase": "execute", "message": t,
                "data": {"type": "narration", "narration_text": t},
            })

    def test_synthetic_3x_repeat_returns_true(self):
        """DONE-criterion: synthetic 3-message sequence repeated
        twice must flag a stall."""
        loop_id = "qa_stall_synthetic_1"
        seq = ["Writing app.py", "Verifying app.py", "Retrying app.py"]
        self._seed(loop_id, seq + seq)
        assert qm.detect_stall_from_replay_buffer(loop_id) is True

    def test_non_repeating_returns_false(self):
        loop_id = "qa_stall_synthetic_2"
        self._seed(loop_id, [f"Writing file {i}" for i in range(8)])
        assert qm.detect_stall_from_replay_buffer(loop_id) is False

    def test_too_few_events_returns_false(self):
        loop_id = "qa_stall_synthetic_3"
        self._seed(loop_id, ["a", "b"])
        assert qm.detect_stall_from_replay_buffer(loop_id) is False

    def test_unknown_loop_returns_false(self):
        assert qm.detect_stall_from_replay_buffer("no_such_loop") is False


class TestVerifyPassIsReal:
    async def test_shipped_negative_case_no_new_commit(self, monkeypatch):
        """DONE-criterion (negative case): claimed SHIPPED but head sha
        unchanged → genuinely_verified must be False. GitHub layer is
        monkeypatched because the Section-0 sandbox repo + PAT are
        founder-blocked — stated openly, verify logic itself is real."""
        async def fake_head(client, owner, repo, branch, token):
            return "same_sha_123"
        monkeypatch.setattr(
            "services.github_api_writer._get_branch_head", fake_head)
        out = await qm.verify_pass_is_real(
            "SHIPPED", owner="o", repo="r", branch="main", token="t",
            pre_state_sha="same_sha_123")
        assert out["checks"]["github_commit_exists"] is False
        assert out["genuinely_verified"] is False

    async def test_shipped_positive_case_new_commit(self, monkeypatch):
        async def fake_head(client, owner, repo, branch, token):
            return "NEW_sha_456"
        monkeypatch.setattr(
            "services.github_api_writer._get_branch_head", fake_head)
        out = await qm.verify_pass_is_real(
            "SHIPPED", owner="o", repo="r", branch="main", token="t",
            pre_state_sha="old_sha_123")
        assert out["genuinely_verified"] is True

    async def test_unknown_state_returns_null_not_verified(self):
        out = await qm.verify_pass_is_real(
            "SOME_FUTURE_STATE", owner="o", repo="r", branch="main",
            token="t")
        assert out["checks"] == {}
        assert out["genuinely_verified"] is None
        assert "no independent check defined" in out["note"]


class TestChatToolCallAdversarial:
    async def test_very_long_and_empty_inputs_no_crash(self):
        """Direct tool_executor.execute() with the real adversarial
        inputs — structured result required, no exception escapes."""
        rows = await qm._run_chat_tool_call_variants()
        by_label = {r["variant"]: r for r in rows}
        assert set(by_label) == {"normal", "very_long", "empty"}
        for label, row in by_label.items():
            assert row["result"] in ("PASS", "SUSPICIOUS"), (
                f"{label} crashed: {row['detail']}")


class TestReportWriter:
    def test_report_written_with_regressions_and_overall(self, tmp_path,
                                                          monkeypatch):
        monkeypatch.setattr(qm, "_REPORT_PATH",
                            str(tmp_path / "latest-qa-report.md"))
        monkeypatch.setattr(qm, "_HISTORY_DIR", str(tmp_path / "hist"))
        scope = qm.decide_scope(SHIP_GATE_FIX_MESSAGE,
                                SHIP_GATE_FIX_FILES)
        results = [{"scenario": "smoke_baseline", "layer": "backend",
                     "rows": [{"variant": "health", "result": "PASS",
                                "detail": "GET ok"}]}]
        path = qm.write_report(scope, results, SHIP_GATE_FIX_MESSAGE,
                               sha="abc1234")
        content = Path(path).read_text()
        assert "# QA Report — commit abc1234" in content
        assert "| smoke_baseline | health | PASS |" in content
        assert "regression-20260728-ship-gate-infinite-loop" in content
        assert "## Overall: PASS" in content
        assert list(Path(qm._HISTORY_DIR).glob("qa-report-*.md"))

    def test_overall_precedence(self):
        mk = lambda s: [{"scenario": "x", "layer": "backend",
                          "rows": [{"variant": "v", "result": s,
                                     "detail": ""}]}]
        assert qm._overall(mk("PASS")) == "PASS"
        assert qm._overall(mk("FAIL") + mk("PASS")) == "FAIL"
        assert qm._overall(mk("SUSPICIOUS") + mk("PASS")) == "SUSPICIOUS"
        assert qm._overall(mk("INCONCLUSIVE") + mk("PASS")) == "INCONCLUSIVE"


class TestRegressionLibrary:
    def test_library_exists_with_todays_bug(self):
        lib = json.loads(Path(
            "/app/.emergent/qa-history/regression_library.json").read_text())
        assert lib[0]["id"] == "regression-20260728-ship-gate-infinite-loop"
        assert lib[0]["status"] == "open"
        assert lib[0]["fixed_in_commit"] is None


class TestNoPlaywrightInBackendPath:
    def test_backend_scenario_code_has_no_playwright(self):
        """DONE-criterion: grep-verify no Playwright in the backend-only
        scenario code path."""
        src = Path("/app/backend/services/qa_matrix.py").read_text()
        backend_seg = src.split("async def run_backend_scenario")[1]
        backend_seg = backend_seg.split("async def run_ui_scenario")[0]
        assert "playwright" not in backend_seg.lower()
        helpers = src.split("def _run_pytest")[1].split(
            "async def run_backend_scenario")[0]
        assert "playwright" not in helpers.lower()
