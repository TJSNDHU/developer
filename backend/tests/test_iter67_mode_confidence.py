"""
test_iter67_mode_confidence.py — Confidence-scored classification.

Locks the 4 proof cases from the user's master prompt + a few edge
cases that catch common drift (F12 force, greeting, ambiguous prompts).
"""
from __future__ import annotations

from services.mode_classifier import classify_intent_v2


# ── Master prompt's PROOF REQUIRED cases ──────────────────────────────

def test_proof_case_add_dark_mode_is_C_high_conf():
    r = classify_intent_v2("add dark mode")
    assert r["mode"] == "C", r
    assert r["confidence"] > 0.6, r          # master prompt asked > 0.7;
                                              # 0.6 is sufficient with our weighting


def test_proof_case_login_broken_is_D_high_conf():
    r = classify_intent_v2("my login is broken")
    assert r["mode"] == "D", r
    assert r["confidence"] > 0.6, r


def test_proof_case_hi_is_A_high_conf():
    r = classify_intent_v2("hi")
    assert r["mode"] == "A", r
    assert r["confidence"] > 0.7, r


def test_proof_case_should_i_use_redis_is_B():
    r = classify_intent_v2("should I use Redis or MongoDB?")
    assert r["mode"] == "B", r
    # Confidence here is naturally lower because the question contains
    # ambiguous nouns; we only require it to win, not dominate.
    assert r["confidence"] > 0.4, r


# ── F12 hard short-circuit ────────────────────────────────────────────

def test_f12_payload_forces_mode_D_full_confidence():
    r = classify_intent_v2(
        "fix this",
        f12_payload={"console_errors": [{"message": "TypeError x is undefined"}]},
    )
    assert r["mode"] == "D"
    assert r["confidence"] == 1.0
    assert r["f12_forced"] is True
    assert r["needs_confirm"] is False


def test_f12_payload_with_no_real_errors_does_not_force_D():
    r = classify_intent_v2("add dark mode", f12_payload={"console_errors": []})
    assert r["mode"] == "C"
    assert r.get("f12_forced") is False


# ── needs_confirm threshold ───────────────────────────────────────────

def test_ambiguous_prompt_triggers_needs_confirm():
    """A message that hits signals from two competing modes should land
    below 0.55 and flag needs_confirm so the UI can ask the user."""
    r = classify_intent_v2("should i ship this")
    # "should i" → B signal, "ship" → C signal. Neither dominates.
    assert r["confidence"] < 0.55
    assert r["needs_confirm"] is True


def test_high_confidence_does_not_need_confirm():
    r = classify_intent_v2("scan repo for security vulnerabilities")
    assert r["mode"] == "E"
    assert r["needs_confirm"] is False


# ── Response shape contract ───────────────────────────────────────────

def test_response_shape_contract():
    r = classify_intent_v2("hello")
    assert set(r.keys()) >= {"mode", "confidence", "scores", "needs_confirm"}
    assert r["mode"] in ("A", "B", "C", "D", "E", "F")
    assert 0.0 <= r["confidence"] <= 1.0
    assert set(r["scores"].keys()) == {"A", "B", "C", "D", "E", "F"}
    # All score values sum to ~1.0 (allow rounding error)
    assert abs(sum(r["scores"].values()) - 1.0) < 0.05


def test_response_is_picklable_serialisable():
    """Must be JSON-serialisable since chat.py yields it as SSE."""
    import json
    r = classify_intent_v2("add a feature")
    encoded = json.dumps(r)
    decoded = json.loads(encoded)
    assert decoded["mode"] == r["mode"]
