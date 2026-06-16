"""
Iter 169 — Orchestrator budget guard buffer regression (revised).

User-visible bug: after Iter 165's smart-router + warm-start ship, a
legit 13-tool-call repo sweep got guillotined at 90s with a
"runaway tool-loop" message.

Root cause: chat.py kept `HARD_TIMEOUT_S=90` from Iter 160, while
orch had been bumped to 82s. Real deep-dives need more headroom now
that warm start + smart router make each step faster but also enable
broader sweeps in a single turn.

This test pins the new Iter 169 numbers AND adds a cross-file
invariant so a future tightening can't desync the two timers again.
"""
from pathlib import Path
import re


ORCH_PATH = Path(__file__).parent.parent / "services" / "orchestrator.py"
CHAT_PATH = Path(__file__).parent.parent / "routers" / "chat.py"


def _read_int(src: str, pattern: str) -> int:
    m = re.search(pattern, src)
    assert m is not None, f"pattern not found: {pattern}"
    return int(m.group(1))


def test_budget_default_is_150s():
    """Per-turn budget default must be 150s (Iter 169 bump from 82s)."""
    src = ORCH_PATH.read_text()
    val = _read_int(
        src,
        r'_ORCH_BUDGET_S\s*=\s*float\(os\.getenv\(\s*"ORCH_PER_TURN_BUDGET_S"\s*,\s*"(\d+)"\s*\)\)',
    )
    assert val == 150, (
        f"expected default budget 150s, got {val}s — regression of iter 169"
    )


def test_final_round_reserve_is_25s():
    """Final-round reserve default must be 25s (Iter 169 bump from 18s)."""
    src = ORCH_PATH.read_text()
    val = _read_int(
        src,
        r'_ORCH_FINAL_ROUND_RESERVE_S\s*=\s*float\(\s*'
        r'os\.getenv\(\s*"ORCH_FINAL_ROUND_RESERVE_S"\s*,\s*"(\d+)"\s*\)',
    )
    assert val == 25, (
        f"expected default reserve 25s, got {val}s — regression of iter 169"
    )


def test_guard_uses_named_reserve_not_magic_number():
    src = ORCH_PATH.read_text()
    assert "_ORCH_BUDGET_S - _ORCH_FINAL_ROUND_RESERVE_S" in src
    assert "_ORCH_BUDGET_S - 40" not in src
    assert "_ORCH_BUDGET_S - 18" not in src


def test_effective_window_is_at_least_100s():
    """Effective working window must comfortably absorb a 13-tool-call repo sweep."""
    src = ORCH_PATH.read_text()
    budget = _read_int(
        src,
        r'_ORCH_BUDGET_S\s*=\s*float\(os\.getenv\(\s*"ORCH_PER_TURN_BUDGET_S"\s*,\s*"(\d+)"\s*\)\)',
    )
    reserve = _read_int(
        src,
        r'_ORCH_FINAL_ROUND_RESERVE_S\s*=\s*float\(\s*'
        r'os\.getenv\(\s*"ORCH_FINAL_ROUND_RESERVE_S"\s*,\s*"(\d+)"\s*\)',
    )
    effective = budget - reserve
    assert effective >= 100, (
        f"effective window {effective}s < 100s — would regress the deep-scan UX"
    )


def test_chat_hard_timeout_is_180s():
    """chat.py wall clock must give orch room to breathe (Iter 169 bump from 90s)."""
    src = CHAT_PATH.read_text()
    val = _read_int(
        src,
        r'HARD_TIMEOUT_S\s*=\s*float\(\s*os\.getenv\(\s*'
        r'"CHAT_HARD_TIMEOUT_S"\s*,\s*"(\d+)"\s*\)\)',
    )
    assert val == 180, (
        f"expected chat hard timeout 180s, got {val}s — regression of iter 169"
    )


def test_orch_budget_strictly_below_wall_clock():
    """Cross-file invariant — the bug we keep regressing.

    If orch budget >= chat wall clock, the orch's friendly per-turn
    synth summary never gets a chance to run; the user instead sees
    the wall-clock "I cut myself off" message.
    Iter 169 needs orch budget < wall clock by at least 20s so the
    final-round reserve actually completes inside wall clock.
    """
    orch_budget = _read_int(
        ORCH_PATH.read_text(),
        r'_ORCH_BUDGET_S\s*=\s*float\(os\.getenv\(\s*"ORCH_PER_TURN_BUDGET_S"\s*,\s*"(\d+)"\s*\)\)',
    )
    wall = _read_int(
        CHAT_PATH.read_text(),
        r'HARD_TIMEOUT_S\s*=\s*float\(\s*os\.getenv\(\s*'
        r'"CHAT_HARD_TIMEOUT_S"\s*,\s*"(\d+)"\s*\)\)',
    )
    assert orch_budget < wall - 20, (
        f"orch budget {orch_budget}s >= wall {wall}s - 20s safety margin — "
        f"the per-turn synth summary will never fire before wall-clock kills the turn"
    )
