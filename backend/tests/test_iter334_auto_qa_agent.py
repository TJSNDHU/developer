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


# ── Iter 338 — response secret-leak scanner (born from iter337) ─────
class TestSecretLeakScanner:
    def test_catches_the_exact_iter337_leak_shape(self):
        leak = {"ok": True, "user": {
            "email": "x", "mfa_secret": "ZNWU",
            "mfa_backup_codes": ["a", "b"],
            "github": {"login": "foo", "access_token": "gho_xxx",
                       "avatar_url": "u"}}, "token": "jwt_session"}
        hits = qm.scan_response_for_secrets(leak)
        assert "user.mfa_secret" in hits
        assert "user.mfa_backup_codes" in hits
        assert "user.github.access_token" in hits

    def test_clean_response_no_hits(self):
        clean = {"ok": True, "user": {
            "email": "x", "mfa_enabled": True,
            "github": {"login": "foo", "avatar_url": "u",
                       "connected_at": 1}}, "token": "jwt_session"}
        assert qm.scan_response_for_secrets(clean) == []

    def test_toplevel_session_token_not_flagged(self):
        # bare top-level `token`/`mfa_token` are the login flow's own
        # session/challenge tokens — by design, not a leak.
        assert qm.scan_response_for_secrets({"token": "abc"}) == []
        assert qm.scan_response_for_secrets({"mfa_token": "abc"}) == []

    def test_nested_access_token_still_flagged(self):
        assert "a.b.access_token" in qm.scan_response_for_secrets(
            {"a": {"b": {"access_token": "gho_x"}}})

    def test_empty_secret_value_not_flagged(self):
        assert qm.scan_response_for_secrets({"password": ""}) == []
        assert qm.scan_response_for_secrets({"access_token": None}) == []

    async def test_verify_pass_flags_leak_and_logs_regression(self, tmp_path,
                                                              monkeypatch):
        monkeypatch.setattr(qm, "_HISTORY_DIR", str(tmp_path))
        monkeypatch.setattr(qm, "_REGRESSION_LIB",
                            str(tmp_path / "regression_library.json"))
        Path(qm._REGRESSION_LIB).write_text("[]")
        out = await qm.verify_pass_is_real(
            "RESPONSE_SCAN", owner="", repo="", branch="", token="",
            response_payload={"user": {"mfa_secret": "ZZ"}},
            response_source="/auth/me")
        assert out["checks"]["no_secret_leak"] is False
        assert out["genuinely_verified"] is False
        lib = json.loads(Path(qm._REGRESSION_LIB).read_text())
        assert any("secretleak" in e["id"] for e in lib)
        assert lib[-1]["status"] == "open"

    async def test_verify_pass_clean_response_passes(self, tmp_path,
                                                     monkeypatch):
        monkeypatch.setattr(qm, "_REGRESSION_LIB",
                            str(tmp_path / "rl.json"))
        Path(qm._REGRESSION_LIB).write_text("[]")
        out = await qm.verify_pass_is_real(
            "RESPONSE_SCAN", owner="", repo="", branch="", token="",
            response_payload={"user": {"email": "x", "mfa_enabled": True}},
            response_source="/auth/me")
        assert out["checks"]["no_secret_leak"] is True
        assert json.loads(Path(qm._REGRESSION_LIB).read_text()) == []

    async def test_live_auth_me_is_clean_on_this_backend(self):
        """Live scan against the REAL running backend /auth/me — proves
        the iter337 fix holds and the scanner works end-to-end."""
        out = await qm.run_backend_scenario("secret_leak_scan")
        me_rows = [r for r in out["rows"] if r["variant"] == "/auth/me"]
        assert me_rows, "scan did not reach /auth/me (login may have failed)"
        assert me_rows[0]["result"] == "PASS", me_rows[0]["detail"]

    def test_regression_dedup(self, tmp_path, monkeypatch):
        monkeypatch.setattr(qm, "_HISTORY_DIR", str(tmp_path))
        monkeypatch.setattr(qm, "_REGRESSION_LIB",
                            str(tmp_path / "rl.json"))
        Path(qm._REGRESSION_LIB).write_text("[]")
        assert qm._append_regression_entry("regression-x", "d", {}) is True
        assert qm._append_regression_entry("regression-x", "d", {}) is False
        assert len(json.loads(Path(qm._REGRESSION_LIB).read_text())) == 1

    def test_decide_scope_triggers_secret_scan_on_auth_change(self):
        out = qm.decide_scope("fix auth token handling",
                              ["backend/routers/auth.py"])
        assert "secret_leak_scan" in out["scenarios"]
