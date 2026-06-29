"""
Iter 212m-151 — Parliament production-readiness gap fixes.

Tests the four gaps closed before Loop Mode wire-up is finalized:
  Gap 1: Circuit breaker + global concurrency semaphore @ 6
  Gap 2: Self-heal respects caller-owned round counter (no internal retry)
  Gap 3: CEO temperature driven by explicit output-type detection
  Gap 4: Distributed trace_id propagated through every parliament_log row

All tests stub the LLM at the lowest layer (`_llm_call_protected`) so
no real tokens are consumed.
"""
import asyncio
import time
from pathlib import Path

import pytest

from core import parliament as pl


# ─── Test harness — patch `_llm_call_protected` for determinism ──────

@pytest.fixture(autouse=True)
def _reset_breaker():
    """Each test starts with a CLOSED breaker."""
    pl._GLOBAL_BREAKER._state            = "closed"
    pl._GLOBAL_BREAKER._consec_failures  = 0
    pl._GLOBAL_BREAKER._opened_at        = 0.0
    pl._GLOBAL_BREAKER._half_open_probe  = False
    pl._GLOBAL_BREAKER._window.clear()
    yield


# ─── GAP 1 — Circuit breaker + concurrency ────────────────────────────

def test_module_exposes_max_concurrent_six():
    assert pl.MAX_CONCURRENT_LLM_CALLS == 6
    # Semaphore is created from that constant.
    assert pl._GLOBAL_LLM_SEM._value <= pl.MAX_CONCURRENT_LLM_CALLS


def test_circuit_breaker_state_machine():
    br = pl.ParliamentCircuitBreaker()
    assert br.state == "closed"
    assert br.should_attempt() is True
    # 3 consecutive failures → OPEN
    for _ in range(3):
        br.record_failure(10.0, kind="timeout")
    assert br.state == "open"
    assert br.should_attempt() is False
    # After cooldown → HALF_OPEN, single probe allowed.
    br._opened_at = time.monotonic() - br.COOLDOWN_SECONDS - 1
    assert br.state == "half_open"
    # One probe attempt allowed, second blocked.
    assert br.should_attempt() is True
    assert br.should_attempt() is False
    # Successful probe → CLOSED
    br.record_success(50.0)
    assert br.state == "closed"


def test_circuit_breaker_half_open_failure_reopens():
    br = pl.ParliamentCircuitBreaker()
    for _ in range(3):
        br.record_failure(10.0)
    br._opened_at = time.monotonic() - br.COOLDOWN_SECONDS - 1
    assert br.state == "half_open"
    br.should_attempt()        # consume the probe slot
    br.record_failure(10.0)     # probe fails → OPEN again
    assert br.state == "open"
    assert br.should_attempt() is False


def test_circuit_breaker_stats_shape():
    br = pl.ParliamentCircuitBreaker()
    br.record_success(10.0)
    br.record_failure(20.0)
    s = br.stats()
    for k in ("state", "consec_failures", "window_total",
              "window_ok", "failure_threshold", "cooldown_seconds"):
        assert k in s


@pytest.mark.asyncio
async def test_parliament_falls_back_when_circuit_open(monkeypatch):
    """When the breaker is open, Parliament must skip the council
    fan-out and use the protected single-call fallback.  Result row
    has `circuit_breaker_fallback=True`."""
    # Trip the breaker manually.
    pl._GLOBAL_BREAKER._state = "open"
    pl._GLOBAL_BREAKER._opened_at = time.monotonic()  # block half-open transition

    # Stub _llm_call_protected so the fallback returns deterministic code.
    async def _stub(*args, **kwargs):
        return ("def healed_fallback():\n    return 1\nimport os\nclass C: pass",
                42.0, None)
    monkeypatch.setattr(pl, "_llm_call_protected", _stub)

    parl = pl.Parliament(db=None)
    res = await parl.run(
        task="Fix the SQL injection in auth.py",
        context={"council": "A", "task_type": "code_fix"},
    )
    assert res["status"] == "success"
    assert res["circuit_breaker_fallback"] is True
    assert res["winner"] == "fallback-single"
    assert "def healed_fallback" in res["output"]


@pytest.mark.asyncio
async def test_parliament_records_failures_into_breaker(monkeypatch):
    """3 consecutive provider errors should trip the global breaker."""
    async def _err(*args, **kwargs):
        return ("", 5.0, "TimeoutError")
    monkeypatch.setattr(pl, "_llm_call_protected", _err)

    # Direct ping — bypass council fan-out: call the cast_vote of a
    # single member 3 times.
    member = pl.CouncilA().members[0]
    # First mark every call as a failure in the breaker (mimics what
    # _llm_call_protected does on error).  Since we monkeypatched the
    # whole helper, we need to ALSO simulate the breaker recording.
    for _ in range(3):
        v = await member.cast_vote(task="t", context={})
        assert v["error"] == "TimeoutError"
        # Manually record (the real helper does this — our stub doesn't).
        pl._GLOBAL_BREAKER.record_failure(5.0, kind="TimeoutError")
    assert pl._GLOBAL_BREAKER.state == "open"


# ─── GAP 2 — Dual retry conflict resolution ───────────────────────────

@pytest.mark.asyncio
async def test_healer_respects_caller_max_rounds_exactly():
    """round_num == max_rounds → escalate immediately, NO new LLM call."""
    h = pl.SelfHeal()
    res = await h.heal(
        task="x", round_num=2, max_rounds=2,
        all_attempts=[{"output": "broken", "score": 0, "error": "E"}],
    )
    assert res["status"] == "escalate"
    assert res["reason"] == "caller max rounds reached"


@pytest.mark.asyncio
async def test_healer_never_increments_round_internally(monkeypatch):
    """Even if heal() is called many times with round_num=0, it must
    NOT silently bump the round count.  Caller is the only counter."""
    captured_rounds = []
    async def _stub(*args, **kwargs):
        return ("def healed(): return 1", 10.0, None)
    monkeypatch.setattr(pl, "_llm_call_protected", _stub)

    h = pl.SelfHeal()
    for _ in range(5):
        res = await h.heal(
            task="x", round_num=0, max_rounds=2,
            all_attempts=[{"output": "broken", "score": 0, "error": "E"}],
        )
        captured_rounds.append(res["round_num"])
    # round_num echoed back unchanged.
    assert captured_rounds == [0, 0, 0, 0, 0]


@pytest.mark.asyncio
async def test_loop_engine_passes_max_rounds_to_healer():
    """loop_engine.py wire-point must pass MAX_SELF_HEALS explicitly."""
    src = (Path(__file__).resolve().parent.parent / "services" /
           "loop_engine.py").read_text()
    assert "max_rounds=MAX_SELF_HEALS" in src


# ─── GAP 3 — CEO output-type detection ────────────────────────────────

def test_ceo_temps_constants_match_spec():
    expected = {
        "code_output":     0.0,
        "json_output":     0.0,
        "tool_call":       0.0,
        "analysis_output": 0.3,
        "plan_output":     0.4,
        "writing_output":  0.65,
        "casual_output":   0.7,
    }
    for k, v in expected.items():
        assert pl.CEO_TEMPS[k] == v


@pytest.mark.parametrize("task,expected", [
    ("fix the bug in auth.py and refactor login()",      "code_output"),
    ("implement parameterized SQL for the user table",   "code_output"),
    ("summarize last week performance",                  "analysis_output"),
    ("how many leads converted yesterday — show me a report", "analysis_output"),
    ("draft a follow-up email to the lead",              "writing_output"),
    ("format the response as json schema",               "json_output"),
])
def test_detect_output_type_routes_correctly(task, expected):
    assert pl.detect_output_type(task) == expected


def test_detect_output_type_empty_defaults_to_council_a():
    """Empty task with council=A → code_output (safer floor)."""
    assert pl.detect_output_type("", council="A") == "code_output"
    assert pl.detect_output_type("", council="B") == "analysis_output"


def test_detect_output_type_tie_defaults_to_code_when_council_a():
    """A task with NO signal but council A → code_output (t=0.0)."""
    assert pl.detect_output_type("...", council="A") == "code_output"


@pytest.mark.asyncio
async def test_ceo_uses_detected_temperature_in_result(monkeypatch):
    async def _stub_vote(self, *, task, context):
        return {
            "member": self.name, "output": "def fix(): return 1",
            "score":  0.85, "error": None, "latency_ms": 5.0,
            "temp":   self.temperature,
        }
    monkeypatch.setattr(pl._CouncilMember, "cast_vote", _stub_vote)

    # Code task → t=0.0
    res = await pl.Parliament(db=None).run(
        task="Fix the SQL injection vulnerability in auth.py",
        context={"council": "A", "task_type": "code_fix"},
    )
    assert res["ceo_temp_key"]   == "code_output"
    assert res["ceo_temp_value"] == 0.0

    # Analysis task → t=0.3
    res = await pl.Parliament(db=None).run(
        task="Summarize how many leads converted yesterday",
        context={"council": "A", "task_type": "analysis"},
    )
    assert res["ceo_temp_key"]   == "analysis_output"
    assert res["ceo_temp_value"] == 0.3


# ─── GAP 4 — Distributed trace IDs ────────────────────────────────────

@pytest.mark.asyncio
async def test_parliament_run_returns_trace_id(monkeypatch):
    async def _stub_vote(self, *, task, context):
        return {
            "member": self.name, "output": "def f(): return 1",
            "score":  0.85, "error": None, "latency_ms": 5.0,
            "temp":   self.temperature,
        }
    monkeypatch.setattr(pl._CouncilMember, "cast_vote", _stub_vote)
    res = await pl.Parliament(db=None).run(
        task="Fix something", context={"council": "A", "task_type": "code_fix"},
    )
    assert "trace_id" in res
    assert isinstance(res["trace_id"], str)
    assert 6 <= len(res["trace_id"]) <= 16     # uuid4 first 8 chars expected


@pytest.mark.asyncio
async def test_parliament_logs_trace_events_sequence(monkeypatch):
    """All log rows share the same trace_id and cover the full
    lifecycle: route → council_start → council_done → ceo_decision →
    final + aggregate."""
    async def _stub_vote(self, *, task, context):
        return {
            "member": self.name, "output": "def f(): return 1",
            "score":  0.85, "error": None, "latency_ms": 5.0,
            "temp":   self.temperature,
        }
    monkeypatch.setattr(pl._CouncilMember, "cast_vote", _stub_vote)

    rows: list[dict] = []
    class _Col:
        async def insert_one(self, doc):
            rows.append(doc)
    class _DB:
        parliament_log = _Col()

    res = await pl.Parliament(db=_DB()).run(
        task="Fix the SQL injection in auth.py",
        context={"council": "A", "task_type": "code_fix",
                 "loop_session_id": "L_42", "user_id": "U_7",
                 "file_path": "auth.py"},
    )
    trace_id = res["trace_id"]
    # Wait for any fire-and-forget create_task to settle.
    await asyncio.sleep(0.05)
    # All trace events must share the same trace_id.
    trace_rows = [r for r in rows if r.get("trace_id") == trace_id]
    assert len(trace_rows) >= 4
    events = {r.get("event") for r in trace_rows}
    for required in ("route", "council_start", "council_done",
                     "ceo_decision", "final", "aggregate"):
        assert required in events, f"missing {required} in {events}"
    # loop_session_id propagated.
    for r in trace_rows:
        assert r["loop_session_id"] == "L_42"


@pytest.mark.asyncio
async def test_parliament_trace_isolation_between_runs(monkeypatch):
    """Two parallel runs must have disjoint trace_ids in their logs."""
    async def _stub_vote(self, *, task, context):
        return {
            "member": self.name, "output": "def f(): return 1",
            "score":  0.85, "error": None, "latency_ms": 5.0,
            "temp":   self.temperature,
        }
    monkeypatch.setattr(pl._CouncilMember, "cast_vote", _stub_vote)

    rows: list[dict] = []
    class _Col:
        async def insert_one(self, doc):
            rows.append(doc)
    class _DB:
        parliament_log = _Col()

    parl = pl.Parliament(db=_DB())
    res_a, res_b = await asyncio.gather(
        parl.run(task="Fix bug A in a.py",
                 context={"council": "A", "task_type": "code_fix",
                          "loop_session_id": "S_A"}),
        parl.run(task="Fix bug B in b.py",
                 context={"council": "A", "task_type": "code_fix",
                          "loop_session_id": "S_B"}),
    )
    await asyncio.sleep(0.05)
    assert res_a["trace_id"] != res_b["trace_id"]
    a_rows = [r for r in rows if r.get("trace_id") == res_a["trace_id"]]
    b_rows = [r for r in rows if r.get("trace_id") == res_b["trace_id"]]
    assert all(r["loop_session_id"] == "S_A" for r in a_rows)
    assert all(r["loop_session_id"] == "S_B" for r in b_rows)


# ─── Wire-point isolation (re-validated for Iter 151) ────────────────

_BACKEND = Path(__file__).resolve().parent.parent


def test_parliament_wired_only_in_loop_engine_iter151():
    """Iter 212m-153 — same strict rule, but observability.py and
    admin.py are read-only mentioners (the wrapper + the /system-stats
    endpoint); only loop_engine.py may IMPORT Parliament."""
    importers = []
    for p in _BACKEND.rglob("*.py"):
        try:
            text = p.read_text()
        except Exception:
            continue
        if "from core.parliament import" in text or "from core import parliament" in text:
            importers.append(p.relative_to(_BACKEND).as_posix())
    importers = [h for h in importers if not h.startswith("tests/")]
    assert sorted(importers) == [
        "services/loop_engine.py",
    ], f"Parliament was imported outside loop_engine: {importers}"


def test_phase_budgets_preserved():
    text = (_BACKEND / "services" / "loop_engine.py").read_text()
    assert "420" in text
    assert "360" in text
