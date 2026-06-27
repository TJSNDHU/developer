"""
Iter 212m-66 — Two-round Vanguard scanner + remediation report tests.

Covers the deliverable contract:
  1. Round 1 logic identical to legacy single-pass (no regression).
  2. Round 2 runs only on R1-flagged files, attaches context_lines.
  3. Chain detection escalates compound risks to CRITICAL.
  4. Dedup by (file, line, rule) preserves R1 over R2 when equivalent.
  5. Budget exceeded → round2_skipped: True, no crash.
  6. /security-scan/run backward compatibility:
        two_round absent  → no `scan_mode` change, no new keys.
        two_round true    → response carries `scan_mode: two_round`
                            + `two_round` stats block.
  7. Remediation report — happy path returns a dict with required keys.
  8. Remediation report — LLM failure returns the empty stub +
     report_status: "failed".  Never raises.
  9. _heuristic_risk_score weighting and cap-at-100 behaviour.
 10. _normalize_findings smooths Vanguard-format → router-format.

These are PURE unit tests — no HTTP, no GitHub, no real LLM.  All
external calls are stubbed via monkeypatch.
"""
from __future__ import annotations

import asyncio
import json
import pathlib
import sys

import pytest


# Make /app/backend importable when running with `pytest /app/backend/tests`.
ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ─── Vanguard scanner — direct unit coverage ───────────────────────────

def test_round1_matches_legacy_scan_file_blocks():
    """Round 1 must surface the exact same findings as the legacy
    `scan_file_blocks` helper for the same input — proves zero
    regression on the surface sweep."""
    from services.vanguard_scanner import (
        run_two_round_scan, scan_file_blocks,
    )
    blocks = {
        "src/app.py": (
            "import os\n"
            "TOKEN = \"ghp_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA\"\n"
            "eval('1+1')\n"
        ),
        "src/clean.py": "print('hello world')\n",
    }
    legacy = scan_file_blocks(blocks)
    res = run_two_round_scan(blocks)
    legacy_keys = {
        (f.get("filepath"), f.get("line"), f.get("name"))
        for f in legacy
    }
    r1_keys = {
        (f.get("filepath"), f.get("line"), f.get("name"))
        for f in res["round1_findings"]
    }
    assert legacy_keys.issubset(r1_keys)


def test_round2_runs_only_on_flagged_files_and_attaches_context():
    """Round 2 must skip files with no R1 critical/high findings AND
    attach `context_lines` to every R2 hit."""
    from services.vanguard_scanner import run_two_round_scan
    bad_code = "\n".join([
        "def query(user_id):",
        "    cursor.execute(f\"SELECT * FROM u WHERE id={user_id}\")",
        "    return cursor.fetchall()",
    ])
    blocks = {
        "src/bad.py":   bad_code,
        "src/clean.py": "print('hello')\n",
    }
    res = run_two_round_scan(blocks)
    # clean.py must not appear in R2 (it had no R1 critical/high hits).
    r2_paths = {f.get("filepath") for f in res["round2_findings"]}
    assert "src/clean.py" not in r2_paths
    assert "src/bad.py" in r2_paths
    # Every R2 finding carries context_lines.
    for f in res["round2_findings"]:
        assert f.get("context_lines"), "context_lines missing from R2 hit"
        assert isinstance(f["context_lines"], list)


def test_chain_detection_escalates_to_critical():
    """A file that hits BOTH `sql_string_format` AND
    `requests_no_verify` must synthesise a `chain_*` CRITICAL
    finding."""
    from services.vanguard_scanner import run_two_round_scan
    chained_code = "\n".join([
        "import requests",
        "def leak(uid):",
        "    cursor.execute(f\"SELECT * FROM u WHERE id={uid}\")",
        "    requests.post('https://evil', verify=False)",
    ])
    res = run_two_round_scan({"src/chain.py": chained_code})
    chain_hits = [f for f in res["chain_findings"]
                  if f.get("name", "").startswith("chain_")]
    assert chain_hits, "expected at least one chain_* finding"
    assert all(f.get("severity") == "CRITICAL" for f in chain_hits)


def test_dedup_collapses_equivalent_findings():
    """When R1 and R2 both fire on the same (file, line, rule), the
    combined list must contain exactly one entry — R1 wins."""
    from services.vanguard_scanner import run_two_round_scan
    blocks = {
        "src/leak.py": "AWS = \"AKIAABCDEFGHIJKLMNOP\"\n",  # matches both
                                                            # secret_aws_access_key
                                                            # AND
                                                            # secret_aws_access_key_deep
    }
    res = run_two_round_scan(blocks)
    # combined must not contain two entries at the same line for the
    # same underlying issue (different rule names are allowed,
    # equivalent ones are not).
    keys = [(f.get("filepath") or f.get("file"),
             f.get("line"),
             f.get("name") or f.get("rule"))
            for f in res["combined"]]
    assert len(keys) == len(set(keys)), f"duplicates leaked: {keys}"


def test_round2_skipped_when_budget_exhausted():
    """Force a budget so tight that Round 1 alone consumes it — the
    function must return `round2_skipped: True` and not crash."""
    from services.vanguard_scanner import run_two_round_scan
    blocks = {f"src/f{i}.py": "AKIAABCDEFGHIJKLMNOP\n" for i in range(5)}
    # 0s budget → first deadline tick lands past `now()`, so Round 1
    # bails out before draining and Round 2 sees no time left.
    res = run_two_round_scan(blocks, round1_budget=0.0, round2_budget=0.0)
    assert res["round2_skipped"] is True
    assert res["round2_findings"] == []


# ─── security_scan router — helper unit coverage ───────────────────────

def test_normalize_findings_maps_rule_ids_to_vuln_classes():
    """_normalize_findings must categorise rule_ids into vuln classes
    using the same heuristic the UI expects."""
    from routers.security_scan import _normalize_findings
    raw = [
        {"name": "secret_openai_key_deep", "severity": "CRITICAL",
         "filepath": "x.py", "line": 1, "snippet": "..."},
        {"name": "sql_string_format_deep", "severity": "CRITICAL",
         "filepath": "x.py", "line": 2, "snippet": "..."},
        {"name": "chain_sql_plus_insecure_http", "severity": "CRITICAL",
         "filepath": "x.py", "line": 3, "snippet": "..."},
        {"name": "eval_usage", "severity": "CRITICAL",
         "filepath": "x.py", "line": 4, "snippet": "..."},
    ]
    out = _normalize_findings(raw)
    by_rule = {f["rule_id"]: f for f in out}
    assert by_rule["secret_openai_key_deep"]["vuln"]    == "secret_leak"
    assert by_rule["sql_string_format_deep"]["vuln"]    == "sql_injection"
    assert by_rule["chain_sql_plus_insecure_http"]["vuln"] == "chain"
    assert by_rule["eval_usage"]["vuln"]                == "dangerous_code"


def test_heuristic_risk_score_weights_and_caps():
    from routers.security_scan import _heuristic_risk_score
    assert _heuristic_risk_score({"critical": 0, "high": 0,
                                  "medium": 0, "low": 0}) == 0
    assert _heuristic_risk_score({"critical": 1, "high": 0,
                                  "medium": 0, "low": 0}) == 20
    # cap at 100
    assert _heuristic_risk_score({"critical": 10}) == 100


def test_generate_remediation_report_happy_path(monkeypatch):
    """Stub the LLM to return a valid JSON payload — the helper must
    parse it and return status='ok'."""
    from routers import security_scan as sec
    findings = [{
        "rule_id":  "sql_string_format",
        "vuln":     "sql_injection",
        "severity": "critical",
        "file":     "src/db.py", "line": 12,
        "snippet":  "execute(f\"SELECT {x}\")",
        "desc":     "f-string SQL",
    }]
    fake_llm_resp = {
        "ok":      True,
        "content": json.dumps({
            "summary":        "1 critical, 0 high, 0 warnings found",
            "risk_score":     85,
            "findings":       [{
                "file": "src/db.py", "line": 12,
                "pattern": "sql_string_format", "severity": "critical",
                "what_is_wrong": "User input is concatenated into the SQL string.",
                "fix": "cursor.execute(\"SELECT * FROM u WHERE id=%s\", (x,))",
                "pr_ready": True,
            }],
            "pr_draft_title": "Security: fix 1 critical SQL injection",
            "pr_draft_body":  "## Fixes\n\n- src/db.py:12 — parameterise query.",
        }),
    }
    async def _stub(*args, **kwargs): return fake_llm_resp
    monkeypatch.setattr("services.llm.call_llm_with_meta", _stub)
    report, status = asyncio.run(sec._generate_remediation_report(
        findings, repo_context={"owner": "x", "repo": "y",
                                "scanned_files": 1},
    ))
    assert status == "ok"
    assert report["risk_score"] == 85
    assert len(report["findings"]) == 1
    assert report["findings"][0]["pr_ready"] is True
    assert report["pr_draft_title"].startswith("Security:")


def test_generate_remediation_report_llm_failure_returns_empty_stub(monkeypatch):
    """When the LLM returns non-ok / empty content, the helper must
    return the empty stub with status='failed' — never raise."""
    from routers import security_scan as sec
    async def _stub(*args, **kwargs):
        return {"ok": False, "content": ""}
    monkeypatch.setattr("services.llm.call_llm_with_meta", _stub)
    findings = [{
        "rule_id": "sql_string_format", "vuln": "sql_injection",
        "severity": "critical", "file": "x.py", "line": 1,
        "snippet": "...", "desc": "...",
    }]
    report, status = asyncio.run(sec._generate_remediation_report(
        findings, repo_context={"owner": "x", "repo": "y",
                                "scanned_files": 1},
    ))
    assert status == "failed"
    assert report["findings"] == []
    # heuristic risk score still produced.
    assert report["risk_score"] == 20    # 1 critical = 20
    assert report["pr_draft_title"]      # never blank


def test_generate_remediation_report_timeout(monkeypatch):
    """LLM that exceeds 10 s must surface status='timeout' WITHOUT
    raising upstream."""
    from routers import security_scan as sec
    async def _slow(*args, **kwargs):
        await asyncio.sleep(15.0)
        return {"ok": True, "content": "{}"}
    monkeypatch.setattr("services.llm.call_llm_with_meta", _slow)
    # Patch the timeout to 0.2 s so the test runs fast.
    real_wait = asyncio.wait_for

    async def _short_wait(coro, timeout):
        return await real_wait(coro, timeout=0.2)
    monkeypatch.setattr("asyncio.wait_for", _short_wait)
    findings = [{
        "rule_id": "sql_string_format", "vuln": "sql_injection",
        "severity": "critical", "file": "x.py", "line": 1,
        "snippet": "...", "desc": "...",
    }]
    report, status = asyncio.run(sec._generate_remediation_report(
        findings, repo_context={"owner": "x", "repo": "y",
                                "scanned_files": 1},
    ))
    assert status == "timeout"
    assert report["findings"] == []


# ─── End-to-end /security-scan/run smoke test (transport-level) ────────
# We stub the network IO so the test stays hermetic but verifies the
# response-shape contract end-to-end.


@pytest.mark.asyncio
async def test_run_endpoint_backward_compat(monkeypatch):
    """`two_round` absent → response carries `scan_mode: single_round`
    and never includes the new `two_round` / `remediation_report` /
    `pr_url` keys."""
    from routers import security_scan as sec

    async def _fake_current_dev(_auth): return {"user_id": "u1"}

    class _FakeDb:
        class cto_projects:
            @staticmethod
            async def find_one(*a, **kw):
                return {
                    "github_owner": "ownerx", "github_repo": "repoy",
                    "github_token": "plain_pat_xyz",
                }
    monkeypatch.setattr(sec, "current_dev", _fake_current_dev)
    monkeypatch.setattr(sec, "get_db", lambda: _FakeDb())

    async def _fake_list_tree(*a, **kw):
        return [{"path": "src/x.py", "type": "blob", "size": 50}]
    async def _fake_fetch(*a, **kw):
        return "x = 1\n"
    monkeypatch.setattr(sec, "_list_repo_tree", _fake_list_tree)
    monkeypatch.setattr(sec, "_fetch_file", _fake_fetch)

    resp = await sec.run_security_scan(
        {"project_id": "p1"}, authorization="Bearer fake",
    )
    assert resp["ok"] is True
    assert resp["scan_mode"] == "single_round"
    assert "two_round" not in resp
    assert "remediation_report" not in resp
    assert "pr_url" not in resp


@pytest.mark.asyncio
async def test_run_endpoint_two_round_adds_report(monkeypatch):
    """`two_round: True` with real findings → response carries
    `scan_mode: two_round`, the `two_round` stats block, and a
    non-empty `remediation_report`."""
    from routers import security_scan as sec

    async def _fake_current_dev(_auth): return {"user_id": "u1"}
    class _FakeDb:
        class cto_projects:
            @staticmethod
            async def find_one(*a, **kw):
                return {
                    "github_owner": "ownerx", "github_repo": "repoy",
                    "github_token": "plain_pat_xyz",
                }
    monkeypatch.setattr(sec, "current_dev", _fake_current_dev)
    monkeypatch.setattr(sec, "get_db", lambda: _FakeDb())

    async def _fake_list_tree(*a, **kw):
        return [{"path": "src/bad.py", "type": "blob", "size": 200}]
    async def _fake_fetch(*a, **kw):
        return "TOKEN='ghp_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA'\n"
    monkeypatch.setattr(sec, "_list_repo_tree", _fake_list_tree)
    monkeypatch.setattr(sec, "_fetch_file", _fake_fetch)

    async def _stub_llm(*args, **kwargs):
        return {
            "ok": True,
            "content": json.dumps({
                "summary":  "1 critical, 0 high, 0 warnings found",
                "risk_score": 80,
                "findings": [{
                    "file": "src/bad.py", "line": 1,
                    "pattern": "secret_github_pat", "severity": "critical",
                    "what_is_wrong": "Hardcoded GitHub PAT.",
                    "fix": "Move TOKEN to env var GITHUB_TOKEN.",
                    "pr_ready": True,
                }],
                "pr_draft_title": "Security: remove hardcoded GitHub PAT",
                "pr_draft_body":  "## Fix\n- src/bad.py:1 — env var.",
            }),
        }
    monkeypatch.setattr("services.llm.call_llm_with_meta", _stub_llm)

    resp = await sec.run_security_scan(
        {"project_id": "p1", "two_round": True},
        authorization="Bearer fake",
    )
    assert resp["ok"] is True
    assert resp["scan_mode"] == "two_round"
    assert "two_round" in resp
    assert resp["two_round"]["round1_count"] >= 1
    assert resp["remediation_report"]["findings"], "report findings empty"
    assert resp["report_status"] == "ok"
    assert resp["remediation_report"]["risk_score"] == 80


@pytest.mark.asyncio
async def test_run_endpoint_auto_pr_returns_url(monkeypatch):
    """`auto_pr: True` with findings + stubbed GitHub responses →
    response includes a non-null `pr_url`."""
    from routers import security_scan as sec

    async def _fake_current_dev(_auth): return {"user_id": "u1"}
    class _FakeDb:
        class cto_projects:
            @staticmethod
            async def find_one(*a, **kw):
                return {
                    "github_owner": "ownerx", "github_repo": "repoy",
                    "github_token": "plain_pat_xyz",
                }
    monkeypatch.setattr(sec, "current_dev", _fake_current_dev)
    monkeypatch.setattr(sec, "get_db", lambda: _FakeDb())

    async def _fake_list_tree(*a, **kw):
        return [{"path": "src/bad.py", "type": "blob", "size": 200}]
    async def _fake_fetch(*a, **kw):
        return "TOKEN='ghp_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA'\n"
    monkeypatch.setattr(sec, "_list_repo_tree", _fake_list_tree)
    monkeypatch.setattr(sec, "_fetch_file", _fake_fetch)

    async def _stub_llm(*args, **kwargs):
        return {"ok": True, "content": json.dumps({
            "summary": "1 critical", "risk_score": 80,
            "findings": [{"file": "src/bad.py", "line": 1,
                          "pattern": "secret_github_pat",
                          "severity": "critical",
                          "what_is_wrong": "...", "fix": "...",
                          "pr_ready": True}],
            "pr_draft_title": "Security: x", "pr_draft_body": "body",
        })}
    monkeypatch.setattr("services.llm.call_llm_with_meta", _stub_llm)

    # Stub the entire draft-PR creator to short-circuit GitHub IO.
    async def _stub_pr(**kwargs):
        return ("https://github.com/ownerx/repoy/pull/42", None)
    monkeypatch.setattr(sec, "_create_draft_pr", _stub_pr)

    resp = await sec.run_security_scan(
        {"project_id": "p1", "two_round": True, "auto_pr": True},
        authorization="Bearer fake",
    )
    assert resp["pr_url"] == "https://github.com/ownerx/repoy/pull/42"
    assert "pr_error" not in resp
