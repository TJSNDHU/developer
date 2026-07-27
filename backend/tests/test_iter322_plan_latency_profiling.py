"""
test_iter322_plan_latency_profiling.py — Iter 322

Item 1 RCA close-out: loop_678eea28436c4e took 21.6s to plan a
one-line README edit (planning 04:30:55.219 → awaiting_confirmation
04:31:16.815). Speed-diagnostic showed n:0 sample_calls because
plan-phase timings were never persisted.

Fix: `_generate_plan` now records per-segment wall-clock into a
`_profile` dict and returns it inside the plan; `_do_plan` strips
it and writes to `loop_run_log` under kind='plan_latency_profile'.
The speed-diagnostic aggregator can then attribute time between
graph-refresh, repo-map-read, LLM call, and JSON parse.
"""
from __future__ import annotations

import re
from pathlib import Path


_ENGINE_SRC = Path("/app/backend/services/loop_engine.py").read_text()


def test_generate_plan_records_profile_segments():
    """`_generate_plan` must record per-segment wall-clock for the
    four segments the founder needs to distinguish: graph refresh
    (potentially the slowest), repo map read, LLM call, JSON parse.
    Live evidence: 21.6s total for a trivial README edit."""
    m = re.search(
        r"async def _generate_plan\(.*?(?=\nasync def |\Z)",
        _ENGINE_SRC, re.DOTALL,
    )
    assert m, "_generate_plan not found in loop_engine.py"
    body = m.group(0)
    assert "_profile" in body, (
        "Iter 322: `_generate_plan` must build a `_profile` dict "
        "with per-segment wall-clock timings."
    )
    for segment in (
        "graph_refresh_s", "repo_map_read_s", "llm_call_s",
        "total_s",
    ):
        assert segment in body, (
            f"Iter 322: _generate_plan profile must include "
            f"`{segment}` — needed to isolate the 21.6s cost "
            f"driver in speed-diagnostic reports."
        )
    # A monotonic wall-clock is required — datetime.now() drift
    # makes millisecond-scale segments unreliable.
    assert "time.monotonic" in body or "_time.monotonic" in body, (
        "Iter 322: _generate_plan must use time.monotonic() (not "
        "datetime.now()) for the profile so sub-second segments "
        "are accurate under clock drift."
    )


def test_do_plan_persists_latency_profile_to_loop_run_log():
    """`_do_plan` must strip `_profile` off the plan dict and
    persist it to `loop_run_log` under a distinct `kind` so the
    speed-diagnostic aggregator can query it without pulling the
    entire plan payload."""
    m = re.search(
        r"    async def _do_plan\(.*?(?=\n    async def |\n    def )",
        _ENGINE_SRC, re.DOTALL,
    )
    assert m, "_do_plan not found in loop_engine.py"
    body = m.group(0)
    assert "plan_latency_profile" in body, (
        "Iter 322: _do_plan must write the plan latency profile to "
        "loop_run_log under kind='plan_latency_profile' so the "
        "speed-diagnostic dashboard can attribute the wall-clock."
    )
    assert '"kind":        "plan_latency_profile"' in body or \
        "'kind':        'plan_latency_profile'" in body or \
        '"kind": "plan_latency_profile"' in body or \
        re.search(r'"kind"\s*:\s*"plan_latency_profile"', body), (
        "Iter 322: the persistence write must set kind exactly to "
        "'plan_latency_profile' (matches speed-diagnostic aggregator "
        "convention)."
    )


def test_profile_stripped_before_frontend_delivery():
    """The `_profile` internal field must be popped off the plan
    before the approval card sees it — the frontend approval UI
    should not carry latency-profile telemetry in its payload."""
    m = re.search(
        r"    async def _do_plan\(.*?(?=\n    async def |\n    def )",
        _ENGINE_SRC, re.DOTALL,
    )
    body = m.group(0)
    assert "plan.pop(\"_profile\"" in body or \
        "plan.pop('_profile'" in body, (
        "Iter 322: _do_plan must plan.pop('_profile', None) so the "
        "frontend approval card never sees the internal telemetry."
    )
