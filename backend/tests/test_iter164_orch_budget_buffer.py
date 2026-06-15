"""
Iter 164 — Orchestrator budget guard buffer regression.

Production founder reported customer-repo deep-scan queries getting the
"I cut myself off / hit reasoning-step budget" wall too aggressively.
Root cause: the guard reserved 40s for "one final LLM round worst case"
but median LLM round latency is 8-15s — so out of a 75s budget the
orchestrator effectively had only 35s of working window.

This test pins the new numbers so a future tightening can't regress
the production fix without explicit failure:
  - ORCH_PER_TURN_BUDGET_S default = 82s
  - ORCH_FINAL_ROUND_RESERVE_S default = 18s
  - Effective working window = 64s (was 35s before this iter)
"""
from pathlib import Path
import re


ORCH_PATH = Path(__file__).parent.parent / "services" / "orchestrator.py"


def test_budget_default_is_82s():
    """Per-turn budget default must be 82s (loosened from 75s)."""
    src = ORCH_PATH.read_text()
    m = re.search(
        r'_ORCH_BUDGET_S\s*=\s*float\(os\.getenv\(\s*"ORCH_PER_TURN_BUDGET_S"\s*,\s*"(\d+)"\s*\)\)',
        src,
    )
    assert m is not None, "budget env-lookup missing"
    assert int(m.group(1)) == 82, (
        f"expected default budget 82s, got {m.group(1)}s — regression of iter 164"
    )


def test_final_round_reserve_is_18s():
    """Final-round reserve default must be 18s (was implicitly 40s)."""
    src = ORCH_PATH.read_text()
    m = re.search(
        r'_ORCH_FINAL_ROUND_RESERVE_S\s*=\s*float\(\s*'
        r'os\.getenv\(\s*"ORCH_FINAL_ROUND_RESERVE_S"\s*,\s*"(\d+)"\s*\)',
        src,
    )
    assert m is not None, "final-round reserve env-lookup missing"
    assert int(m.group(1)) == 18, (
        f"expected default reserve 18s, got {m.group(1)}s — regression of iter 164"
    )


def test_guard_uses_named_reserve_not_magic_40():
    """Budget guard must reference the named reserve, not a literal `-40`.

    The literal `_ORCH_BUDGET_S - 40` was the bug in iter 160 — it gave
    the orchestrator only 35s of working window out of a 75s budget.
    """
    src = ORCH_PATH.read_text()
    assert "_ORCH_BUDGET_S - _ORCH_FINAL_ROUND_RESERVE_S" in src, (
        "guard must use named reserve constant"
    )
    # And no stray `_ORCH_BUDGET_S - 40` left behind
    assert "_ORCH_BUDGET_S - 40" not in src, (
        "stale literal `_ORCH_BUDGET_S - 40` still in code — must use named reserve"
    )


def test_effective_window_is_at_least_60s():
    """Effective working window (budget - reserve) must be >= 60s.

    Below 60s the orchestrator can't reliably finish multi-file reads
    on customer repos within a single turn.
    """
    src = ORCH_PATH.read_text()
    budget_m = re.search(
        r'_ORCH_BUDGET_S\s*=\s*float\(os\.getenv\(\s*"ORCH_PER_TURN_BUDGET_S"\s*,\s*"(\d+)"\s*\)\)',
        src,
    )
    reserve_m = re.search(
        r'_ORCH_FINAL_ROUND_RESERVE_S\s*=\s*float\(\s*'
        r'os\.getenv\(\s*"ORCH_FINAL_ROUND_RESERVE_S"\s*,\s*"(\d+)"\s*\)',
        src,
    )
    assert budget_m and reserve_m
    effective = int(budget_m.group(1)) - int(reserve_m.group(1))
    assert effective >= 60, (
        f"effective working window {effective}s < 60s — would regress "
        f"the customer-repo deep-scan UX"
    )


def test_budget_stays_below_wall_clock_90s():
    """Per-turn budget must stay below the 90s wall-clock ceiling.

    The router (chat.py) enforces a 90s wall-clock ceiling. If the
    orchestrator budget approaches or exceeds 90s, the user sees the
    "I cut myself off" message instead of the per-turn synthesised
    summary — defeating the whole budget design.
    """
    src = ORCH_PATH.read_text()
    m = re.search(
        r'_ORCH_BUDGET_S\s*=\s*float\(os\.getenv\(\s*"ORCH_PER_TURN_BUDGET_S"\s*,\s*"(\d+)"\s*\)\)',
        src,
    )
    assert m is not None
    assert int(m.group(1)) < 90, (
        f"budget {m.group(1)}s >= 90s wall-clock — guard will never trip "
        f"because chat.py kills first"
    )
