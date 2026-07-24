"""
test_verify_verdict_calibration.py — Iter 301 (Track 3 v1 — deterministic)

Asserts the verify phase's verdict is ALWAYS consistent with the
peak severity of the evidence — a "pass" verdict on a diff carrying
a HIGH finding is a calibration bug, not a judgment call.

Fixed mapping the verifier MUST honour:
    critical / high  →  fail
    medium           →  needs_revision
    low / info       →  pass

Zero LLM calls. Deterministic mapping check.
"""
from __future__ import annotations

from services.reasoning_evals import calibrate_verdict


def test_verdict_pass_on_low_severity_evidence():
    evidence = {"findings": [
        {"severity": "info",  "rule_id": "style_hint"},
        {"severity": "low",   "rule_id": "minor_lint"},
    ]}
    r = calibrate_verdict("pass", evidence)
    assert r["ok"] is True
    assert r["peak_severity"] == "low"
    assert r["expected"] == "pass"


def test_verdict_fail_when_high_severity_present():
    evidence = {"findings": [
        {"severity": "low",  "rule_id": "trivial"},
        {"severity": "high", "rule_id": "auth_bypass"},
    ]}
    r = calibrate_verdict("fail", evidence)
    assert r["ok"] is True
    assert r["peak_severity"] == "high"
    assert r["expected"] == "fail"


def test_verdict_needs_revision_on_medium():
    evidence = {"findings": [
        {"severity": "medium", "rule_id": "insecure_deserialisation"},
    ]}
    r = calibrate_verdict("needs_revision", evidence)
    assert r["ok"] is True
    assert r["expected"] == "needs_revision"


def test_verdict_miscalibration_is_caught_pass_on_high():
    """The REGRESSION we care about: verifier returns 'pass' but
    evidence has a HIGH finding. calibrate_verdict MUST fail."""
    evidence = {"findings": [
        {"severity": "high", "rule_id": "sql_injection"},
    ]}
    r = calibrate_verdict("pass", evidence)
    assert r["ok"] is False, (
        "MISCALIBRATION: verdict='pass' on high-severity evidence "
        "must be caught — this is the loop's worst-case regression"
    )
    assert r["expected"] == "fail"
    assert r["actual"]   == "pass"
    assert "high" in r["reason"] and "fail" in r["reason"]


def test_verdict_unknown_string_rejected():
    """Verdict enum is strict — 'maybe', 'ok', 'looks good' etc.
    are all rejects. Otherwise a jailbroken model could emit
    something ambiguous that downstream code coerces to 'pass'."""
    r = calibrate_verdict("looks_good_to_me", {"findings": []})
    assert r["ok"] is False
    assert r["actual"] == "looks_good_to_me"
    assert "not in" in r["reason"]
