"""
Iter 212m-164 — Health-score curve + ChatBody task_type wiring.

Verifies:
  • _score_for_findings uses the exponential-decay curve, not the
    legacy linear `100 - sum(weights)` (which cliff-edged at 0
    on 4+ critical findings).
  • Key reference points hold:  0 issues → 100; 5 mediums → ~78;
    2 criticals → ~44; 4 criticals → ~19; 9 criticals → ~2.
  • _category_label thresholds re-tuned for the new compression.
  • ChatBody accepts an optional `task_type` field.
  • Unknown task_type values are dropped to None (whitelist-only).
  • chat_with_tools propagates task_type → llm_mode + council letter
    and returns the council in its result payload.
"""

import pathlib
import sys

BACKEND = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


# ─── Health scoring curve ───────────────────────────────────────────────────

def _make_findings(severities: list[str]) -> list[dict]:
    return [{"severity": s, "file": "x.py", "line": 1} for s in severities]


def test_score_zero_issues_is_100():
    from routers.codebase_health import _score_for_findings
    assert _score_for_findings([]) == 100


def test_score_5_mediums_is_in_good_band():
    """5 medium findings (raw 15) → ~78 score → GOOD band."""
    from routers.codebase_health import _score_for_findings, _category_label
    s = _score_for_findings(_make_findings(["medium"] * 5))
    assert 70 <= s <= 85, f"5 mediums must land in GOOD band, got {s}"
    label, _ = _category_label(s)
    assert label == "GOOD"


def test_score_2_criticals_is_needs_attention():
    """2 critical findings (raw 50) → ~44 → NEEDS ATTENTION band."""
    from routers.codebase_health import _score_for_findings, _category_label
    s = _score_for_findings(_make_findings(["critical"] * 2))
    assert 30 <= s <= 55, f"2 criticals must land near NEEDS ATTENTION, got {s}"
    label, _ = _category_label(s)
    assert label in ("NEEDS ATTENTION", "CRITICAL RISK")


def test_score_4_criticals_no_longer_cliff_at_zero():
    """The whole point of Iter 212m-164: 4 criticals used to give
    score=0 (4 × 25 = 100 weight). The new curve must give >0 so
    the user sees movement when they fix one."""
    from routers.codebase_health import _score_for_findings
    s = _score_for_findings(_make_findings(["critical"] * 4))
    assert 10 <= s <= 30, (
        f"4 criticals must NOT clip to 0 — must show movement room. "
        f"Got {s}"
    )


def test_score_9_criticals_still_critical():
    """9 criticals (the production data) → near-zero, but the curve
    floors at 0 so the score stays a real number."""
    from routers.codebase_health import _score_for_findings, _category_label
    s = _score_for_findings(_make_findings(["critical"] * 9))
    assert 0 <= s <= 10, f"9 criticals must remain near zero, got {s}"
    label, _ = _category_label(s)
    assert label == "CRITICAL RISK"


def test_score_monotonic_in_severity_count():
    """Adding more issues must never INCREASE the score (sanity)."""
    from routers.codebase_health import _score_for_findings
    s0 = _score_for_findings([])
    s1 = _score_for_findings(_make_findings(["low"]))
    s5 = _score_for_findings(_make_findings(["low"] * 5))
    s10 = _score_for_findings(_make_findings(["critical"] * 10))
    assert s0 >= s1 >= s5 >= s10


def test_category_label_thresholds_match_curve():
    """The re-tuned label bands must split the score space sensibly."""
    from routers.codebase_health import _category_label
    assert _category_label(95)[0] == "HEALTHY"
    assert _category_label(70)[0] == "GOOD"
    assert _category_label(35)[0] == "NEEDS ATTENTION"
    assert _category_label(10)[0] == "CRITICAL RISK"


# ─── ChatBody task_type field ──────────────────────────────────────────────

def test_chat_body_accepts_task_type():
    from routers.chat import ChatBody
    body = ChatBody(prompt="analyze this", task_type="analysis")
    assert body.task_type == "analysis"


def test_chat_body_drops_unknown_task_type():
    """Unknown values must silently degrade to None so a typo
    doesn't accidentally change routing semantics."""
    from routers.chat import ChatBody
    body = ChatBody(prompt="hi", task_type="zzz_invalid")
    assert body.task_type is None


def test_chat_body_accepts_all_12_task_types():
    from routers.chat import ChatBody
    expected = [
        "code_fix", "code_review", "security", "lint_heal",
        "analysis", "report", "insight", "summarize",
        "email", "copy", "write", "draft",
    ]
    for tt in expected:
        body = ChatBody(prompt="x", task_type=tt)
        assert body.task_type == tt, f"task_type={tt!r} was dropped"


def test_chat_body_task_type_optional():
    from routers.chat import ChatBody
    body = ChatBody(prompt="x")
    assert body.task_type is None


# ─── Orchestrator routing wiring (source-level) ─────────────────────────────

def test_orchestrator_chat_with_tools_accepts_task_type():
    import inspect
    from services.orchestrator import chat_with_tools
    sig = inspect.signature(chat_with_tools)
    assert "task_type" in sig.parameters
    # Default must be None so existing callers are unaffected.
    assert sig.parameters["task_type"].default is None


def test_orchestrator_task_type_maps_to_llm_mode():
    """Source-level guard — orchestrator.py must contain the task_type
    → llm_mode + council_letter mapping per Iter 212m-164."""
    src = pathlib.Path("/app/backend/services/orchestrator.py").read_text()
    # analysis bucket
    assert '"analysis", "report", "insight", "summarize"' in src
    assert 'llm_mode       = "analysis"' in src
    assert 'council_letter = "B"' in src
    # writing bucket
    assert '"email", "copy", "write", "draft"' in src
    assert 'council_letter = "C"' in src
    # code bucket
    assert '"code_fix", "code_review", "security", "lint_heal"' in src
    assert 'council_letter = "A"' in src


def test_orchestrator_returns_council_in_result():
    """The final result payload must surface `council` so curl/test
    assertions can verify routing without scraping parliament_log."""
    src = pathlib.Path("/app/backend/services/orchestrator.py").read_text()
    assert '"council": council_letter' in src


def test_chat_router_threads_task_type_into_chat_with_tools():
    """Both /chat and /chat/stream call sites must pass body.task_type."""
    src = pathlib.Path("/app/backend/routers/chat.py").read_text()
    # Two chat_with_tools(...) call sites both pass task_type.
    assert src.count("task_type=body.task_type") >= 2, (
        f"expected ≥2 task_type=body.task_type call sites, "
        f"got {src.count('task_type=body.task_type')}"
    )
