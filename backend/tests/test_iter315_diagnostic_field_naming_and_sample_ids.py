"""
test_iter315_diagnostic_field_naming_and_sample_ids.py — Iter 315

Fix 2 + option-(a) invariants for the speed-diagnostic honesty
class. These tests protect two contracts:

  1. `llm_calls_by_phase` in the diagnostic report must NOT use
     `avg_s` / `median_s` / `p95_s` field names — those units imply
     seconds. The values are COUNTS (number of loop.<phase> rows
     per loop). The founder-visible field must be renamed to
     `avg_calls` / `median_calls` / etc. so the report never lies
     about units again.

  2. The diagnostic response must include per-loop metadata
     (`sample_loop_ids` with created_at per loop) so we can
     retroactively verify whether an "n:10, avg:0" result is from
     loops that predate the instrumentation vs a genuine write bug.
"""
from __future__ import annotations
import re
from pathlib import Path

_DIAG_SRC = Path("/app/backend/services/loop_speed_diagnostic.py").read_text()


def test_llm_calls_by_phase_section_uses_calls_units():
    """
    In the llm_calls_by_phase computation, `_stats_line` is called
    over integer counts. The rename must (a) either not use the
    _stats_line helper for that section (build a call-specific stats
    dict instead), OR (b) rewrite _stats_line to accept a `unit`
    param and produce `avg_calls` when called from that section.
    Contract: after Fix 2, the returned llm_call_stats dict must
    contain the key `avg_calls` for at least one phase's line.
    """
    # Locate the section that builds llm_call_stats.
    m = re.search(
        r"llm_call_stats\s*=\s*\{[^}]+\}", _DIAG_SRC, re.DOTALL,
    )
    assert m, "llm_call_stats construction not found in diagnostic source"
    section = m.group(0)
    # After Fix 2 either the section uses a dedicated helper
    # (e.g., _calls_stats_line) OR passes unit='calls'.
    assert (
        "_calls_stats_line" in section
        or "unit='calls'" in section
        or 'unit="calls"' in section
        or "avg_calls" in section
    ), (
        "Fix 2: llm_call_stats section must produce COUNT-labelled "
        "fields (avg_calls / median_calls / etc.), not seconds-"
        "labelled ones. The old code called _stats_line which "
        "returned avg_s — that lied about units."
    )


def test_sample_loop_ids_field_present_in_result():
    """
    Option (a): the diagnostic response must include
    `sample_loop_ids` with per-loop {loop_id, created_at, state}
    so predates-instrumentation ambiguity can be resolved just
    by reading the JSON.
    """
    # The report dict is constructed in the final `return {...}`
    # of compute_speed_report. We just check the source contains
    # the key literal.
    assert '"sample_loop_ids"' in _DIAG_SRC, (
        "Option (a): compute_speed_report() must include "
        "'sample_loop_ids' in its returned dict. This is the "
        "founder-approved addition so 'n:10, avg:0' can be "
        "verified against per-loop created_at timestamps."
    )


def test_no_bare_avg_s_survives_in_llm_calls_by_phase_section():
    """
    Guardrail against a future refactor accidentally re-labelling
    counts as seconds. Nothing under llm_call_stats can carry the
    literal `avg_s` key.
    """
    m = re.search(
        r"llm_call_stats\s*=\s*\{(.*?)\}",
        _DIAG_SRC, re.DOTALL,
    )
    if not m:
        # Refactor may have removed the dict-comprehension shape
        # entirely; that's fine as long as the top-level return
        # dict doesn't say avg_s for calls.
        return
    section = m.group(1)
    assert "avg_s" not in section, (
        "Fix 2: llm_call_stats section must not reintroduce "
        "the misleading avg_s label. Use avg_calls."
    )
