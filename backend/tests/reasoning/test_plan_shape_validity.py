"""
test_plan_shape_validity.py — Iter 301 (Track 3 v1 — deterministic)

Feeds 5 curated plan JSONs to `validate_plan_shape` and asserts the
deterministic evaluator catches every real regression class:

    1. valid plan  →  ok=True, violations=[]
    2. missing required key  →  ok=False, violations name the key
    3. placeholder marker in a step  →  ok=False
    4. non-list steps (wrong type)  →  ok=False
    5. hallucinated files_to_change (2+ paths not in repo map) →  ok=False

Zero LLM calls. Zero source-string grep. Pure schema + grounding
logic against real fixtures — trustworthy as a regression gate,
sub-second to run, free.
"""
from __future__ import annotations

import asyncio

from services.reasoning_evals import validate_plan_shape


# The set of paths the repo map would report for these tests. Kept
# small so we can test hallucination detection with 1-2 unknown paths.
KNOWN_PATHS = {
    "backend/routers/health.py",
    "backend/services/loop_engine.py",
    "frontend/src/App.jsx",
    "frontend/src/components/ChatPanel.jsx",
}


def test_plan_valid_shape_passes():
    plan = {
        "title": "add /api/health endpoint",
        "steps": [
            "- create a new route in backend/routers/health.py",
            "- add unit test coverage",
            "- wire it up in main.py",
        ],
        "files_to_change": ["backend/routers/health.py"],
    }
    r = validate_plan_shape(plan, known_paths=KNOWN_PATHS)
    assert r["ok"] is True, f"valid plan flagged: {r['violations']}"
    assert r["violations"] == []


def test_plan_missing_required_key_fails():
    """`files_to_change` missing entirely — the WORM freeze step
    downstream depends on it being present, so this MUST fail."""
    plan = {
        "title": "hmm",
        "steps": ["- do a thing"],
        # files_to_change is MISSING
    }
    r = validate_plan_shape(plan, known_paths=KNOWN_PATHS)
    assert r["ok"] is False
    assert any("files_to_change" in v for v in r["violations"]), (
        f"missing-key violation should name the key; got {r['violations']}"
    )


def test_plan_placeholder_in_step_fails():
    """A TODO/FIXME/<PLACEHOLDER> marker in a step means the LLM
    forgot to finish the plan. Approval UI must never see this."""
    plan = {
        "title": "wire the thing",
        "steps": [
            "- create the endpoint",
            "- <ADD IMPLEMENTATION HERE>",   # placeholder leak
            "- ship",
        ],
        "files_to_change": ["backend/routers/health.py"],
    }
    r = validate_plan_shape(plan, known_paths=KNOWN_PATHS)
    assert r["ok"] is False
    assert any("placeholder" in v.lower() for v in r["violations"]), (
        f"placeholder violation should be flagged; got {r['violations']}"
    )


def test_plan_wrong_type_steps_fails():
    """steps=42 (int) is a shape crime the parser must catch."""
    plan = {
        "title":  "x",
        "steps":  42,
        "files_to_change": [],
    }
    r = validate_plan_shape(plan, known_paths=KNOWN_PATHS)
    assert r["ok"] is False
    assert any("steps" in v for v in r["violations"])


def test_plan_hallucinated_files_flag_when_multiple_ungrounded():
    """A single ungrounded path is legit (net-new file). TWO or
    more is a hallucination signal — the diagnostic layer (`_do_plan`
    already writes `ungrounded_paths`) should be actionable here."""
    plan = {
        "title": "add stuff",
        "steps": [
            "- create some files",
            "- ??? and more files",
        ],
        "files_to_change": [
            "backend/routers/health.py",             # known
            "backend/services/nonexistent_a.py",     # UNGROUNDED
            "backend/services/nonexistent_b.py",     # UNGROUNDED
        ],
    }
    r = validate_plan_shape(plan, known_paths=KNOWN_PATHS)
    assert r["ok"] is False
    # Violation must name the ungrounded ratio + the offending paths.
    reason = next((v for v in r["violations"] if "hallucination" in v), None)
    assert reason is not None, f"no hallucination violation; got {r['violations']}"
    assert "nonexistent_a.py" in reason or "nonexistent_b.py" in reason
