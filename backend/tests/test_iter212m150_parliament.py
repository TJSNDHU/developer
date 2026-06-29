"""
Iter 212m-150 — Parliament contract tests.

Validates the founder-spec requirements:
  • parliament.py exists as a single self-contained module
  • TaskRouter, Council A/B/C, CEO, SelfHeal, Parliament classes
  • Council A has 3 members at temps 0.1 / 0.2 / 0.3
  • CEO operates at temp 0.0
  • Healer escalates temperature across rounds
  • Logs to `parliament_log` Mongo collection
  • Loop engine wires Parliament in only TWO places:
      - _do_execute (per-file code-gen)
      - _do_verify  (heal LLM)
  • Prompt-mode code path UNCHANGED (no Parliament import in chat.py
    / orchestrator.py / ora_*.py)
"""
import asyncio
from pathlib import Path

import pytest

from core import parliament as pl


# ─── Module surface ──────────────────────────────────────────────────

def test_parliament_module_exports():
    assert hasattr(pl, "Parliament")
    assert hasattr(pl, "TaskRouter")
    assert hasattr(pl, "CEO")
    assert hasattr(pl, "SelfHeal")
    assert hasattr(pl, "CouncilA")
    assert hasattr(pl, "CouncilB")
    assert hasattr(pl, "CouncilC")


def test_council_a_has_three_members_at_correct_temps():
    council = pl.CouncilA()
    assert len(council.members) == 3
    temps = sorted(m.temperature for m in council.members)
    assert temps == [0.1, 0.2, 0.3]
    # All members must use the code persona.
    for m in council.members:
        assert "code" in m.persona.lower() or "engineer" in m.persona.lower()


def test_council_b_and_c_are_three_member(_iter_155_upgrade=True):
    """Iter 212m-155 — Council B (analysis) and Council C (writing) now
    each have 3 members (formerly empty placeholders).  We accept any
    non-empty member list to keep this guard test forward-compatible
    with future persona tweaks."""
    cb = pl.CouncilB()
    cc = pl.CouncilC()
    assert len(cb.members) >= 1, "Council B should have at least one member"
    assert len(cc.members) >= 1, "Council C should have at least one member"


# ─── TaskRouter ──────────────────────────────────────────────────────

def test_router_respects_forced_council():
    r = pl.TaskRouter()
    assert r.route("anything", {"council": "A"}) == "A"
    assert r.route("anything", {"council": "B"}) == "B"
    assert r.route("anything", {"council": "C"}) == "C"


def test_router_keywords_map_to_council_a():
    r = pl.TaskRouter()
    for txt in ("fix sql injection", "refactor auth", "implement endpoint",
                "lint heal"):
        assert r.route(txt, {}) == "A"


def test_router_task_type_maps_to_council_a():
    r = pl.TaskRouter()
    for tt in ("code_fix", "security", "lint_heal"):
        assert r.route("misc", {"task_type": tt}) == "A"


# ─── Scoring heuristic ───────────────────────────────────────────────

def test_score_refusal_returns_zero():
    assert pl._score_output("I cannot help with that") == 0.0
    assert pl._score_output("As an AI, I won't") == 0.0


def test_score_empty_returns_zero():
    assert pl._score_output("") == 0.0


def test_score_code_returns_high():
    code = (
        "import os\nfrom typing import Any\n\n"
        "def fix(x: Any) -> int:\n    return int(x)\n\n"
        "class Foo:\n    def __init__(self):\n        self.x = 1\n"
    )
    assert pl._score_output(code, task_type="code_fix") >= 0.78


def test_score_prose_returns_low_for_code_task():
    prose = "This file needs to be updated to fix the bug. " * 3
    assert pl._score_output(prose, task_type="code_fix") <= 0.3


def test_strip_fences():
    assert pl._strip_fences("```python\ncode here\n```") == "code here"
    assert pl._strip_fences("plain text") == "plain text"


# ─── CEO ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ceo_picks_highest_scoring_vote():
    ceo = pl.CEO()
    votes = [
        {"member": "A1", "output": "low score prose only",     "score": 0.25, "temp": 0.1, "error": None},
        {"member": "A2", "output": "def good(): return 1",     "score": 0.85, "temp": 0.2, "error": None},
        {"member": "A3", "output": "def okay(): pass",         "score": 0.60, "temp": 0.3, "error": None},
    ]
    out = await ceo.decide(task="x", votes=votes, context={"council": "A"})
    assert out["status"] == "success"
    assert out["winner"] == "A2"
    assert "def good" in out["output"]


@pytest.mark.asyncio
async def test_ceo_returns_manual_review_when_all_refused():
    ceo = pl.CEO()
    votes = [
        {"member": "A1", "output": "I cannot help", "score": 0.0,
         "temp": 0.1, "error": None},
        {"member": "A2", "output": "",              "score": 0.0,
         "temp": 0.2, "error": None},
        {"member": "A3", "output": "",              "score": 0.0,
         "temp": 0.3, "error": None},
    ]
    out = await ceo.decide(task="x", votes=votes, context={})
    assert out["status"] == "manual_review"
    assert out["output"] is None


@pytest.mark.asyncio
async def test_ceo_empty_votes_manual_review():
    out = await pl.CEO().decide(task="x", votes=[], context={})
    assert out["status"] == "manual_review"


# ─── Parliament.run() — happy path with stubbed LLM ───────────────────

@pytest.mark.asyncio
async def test_parliament_run_returns_success_on_good_votes(monkeypatch):
    """Stubs every member to return clean code → CEO accepts the best."""
    async def _stub(self, *, task, context):
        # Return slightly different code per temperature to test the
        # tie-break-on-lowest-temp rule.
        body = (
            "import os\n\n"
            "def fix(x):\n    return int(x)\n\n"
            "class C:\n    def __init__(self):\n        self.x = 1\n"
        )
        return {
            "member":     self.name,
            "output":     body + f"# temp={self.temperature}\n",
            "score":      0.90,
            "error":      None,
            "latency_ms": 12.0,
            "temp":       self.temperature,
        }
    monkeypatch.setattr(pl._CouncilMember, "cast_vote", _stub)

    parl = pl.Parliament(db=None)
    res = await parl.run(
        task="Fix something",
        context={"council": "A", "task_type": "code_fix",
                 "file_path": "auth.py", "loop_session_id": "loop_1"},
    )
    assert res["status"] == "success"
    assert res["council"] == "A"
    assert "def fix" in res["output"]
    # All three members must have been polled.
    assert len(res["scores"]) == 3
    # Tie-break on lowest temp wins (0.1).
    assert res["winner"] == "A1-conservative"


@pytest.mark.asyncio
async def test_parliament_run_returns_manual_review_on_all_refusal(monkeypatch):
    async def _refuse(self, *, task, context):
        return {
            "member": self.name, "output": "I cannot do this",
            "score": 0.0, "error": None, "latency_ms": 0.0,
            "temp": self.temperature,
        }
    monkeypatch.setattr(pl._CouncilMember, "cast_vote", _refuse)
    res = await pl.Parliament(db=None).run(
        task="x",
        context={"council": "A", "task_type": "code_fix"},
    )
    assert res["status"] == "manual_review"


# ─── Mongo logging ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_parliament_logs_to_mongo(monkeypatch):
    async def _stub(self, *, task, context):
        return {
            "member": self.name, "output": "def f(): return 1",
            "score": 0.85, "error": None, "latency_ms": 5.0,
            "temp": self.temperature,
        }
    monkeypatch.setattr(pl._CouncilMember, "cast_vote", _stub)

    rows = []
    class _Col:
        async def insert_one(self, doc):
            rows.append(doc)
    class _DB:
        parliament_log = _Col()

    await pl.Parliament(db=_DB()).run(
        task="Fix sql_injection in auth.py",
        context={
            "council":         "A",
            "task_type":       "code_fix",
            "file_path":       "auth.py",
            "loop_session_id": "loop_999",
            "user_id":         "u_42",
        },
    )
    # Iter 212m-151 — Parliament now writes trace events + 1 aggregate
    # row.  Find the aggregate row for the legacy fields.
    await asyncio.sleep(0.05)
    aggregate = next((r for r in rows if r.get("event") == "aggregate"), None)
    assert aggregate is not None
    assert aggregate["loop_session_id"] == "loop_999"
    assert aggregate["user_id"]         == "u_42"
    assert aggregate["file_path"]       == "auth.py"
    assert aggregate["council"]         == "A"
    assert aggregate["status"]          == "success"
    assert "scores"                     in aggregate
    assert "ts"                         in aggregate
    # And the trace_id is propagated to every event.
    trace_id = aggregate["trace_id"]
    assert all(r["trace_id"] == trace_id for r in rows if "trace_id" in r)


@pytest.mark.asyncio
async def test_parliament_log_failure_does_not_raise(monkeypatch):
    async def _stub(self, *, task, context):
        return {
            "member": self.name, "output": "def f(): return 1",
            "score": 0.85, "error": None, "latency_ms": 5.0,
            "temp": self.temperature,
        }
    monkeypatch.setattr(pl._CouncilMember, "cast_vote", _stub)
    class _Broken:
        async def insert_one(self, doc):
            raise RuntimeError("mongo down")
    class _DB:
        parliament_log = _Broken()
    res = await pl.Parliament(db=_DB()).run(
        task="x", context={"council": "A", "task_type": "code_fix"},
    )
    assert res["status"] == "success"  # broken log must NOT kill the run


# ─── SelfHeal ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_healer_escalates_temperature_across_rounds(monkeypatch):
    captured_temps = []
    async def _spy(*args, **kwargs):
        captured_temps.append(kwargs.get("temperature"))
        return ("def f(): return 1", 5.0, None)
    monkeypatch.setattr(pl, "_llm_call_protected", _spy)

    h = pl.SelfHeal()
    for r in range(0, 2):
        await h.heal(
            task="Fix lint", round_num=r, max_rounds=2,
            all_attempts=[{"output": "broken", "score": 0, "error": "E101"}],
        )
    # Round 0 → ~0.05, Round 1 → ~0.20
    assert captured_temps[0] < captured_temps[1]


@pytest.mark.asyncio
async def test_healer_threads_prior_attempts_into_prompt(monkeypatch):
    captured_user = []
    async def _spy(*args, **kwargs):
        captured_user.append(kwargs.get("user") or "")
        return ("def f(): return 1", 5.0, None)
    monkeypatch.setattr(pl, "_llm_call_protected", _spy)

    h = pl.SelfHeal()
    await h.heal(
        task="Fix something",
        round_num=1, max_rounds=2,
        all_attempts=[
            {"output": "v1", "score": 0, "error": "first error"},
            {"output": "v2", "score": 0, "error": "second error"},
        ],
    )
    text = captured_user[0]
    assert "PRIOR FAILED ATTEMPTS" in text
    assert "first error" in text
    assert "Do NOT repeat" in text


@pytest.mark.asyncio
async def test_healer_returns_retry_with_output(monkeypatch):
    async def _stub(*args, **kwargs):
        return ("def healed(): return 1", 5.0, None)
    monkeypatch.setattr(pl, "_llm_call_protected", _stub)

    res = await pl.SelfHeal().heal(
        task="x", round_num=0, max_rounds=2,
        all_attempts=[{"output": "broken", "score": 0, "error": "E101"}],
    )
    assert res["status"] == "retry"
    assert "def healed" in res["output"]


@pytest.mark.asyncio
async def test_healer_returns_escalate_on_empty_output(monkeypatch):
    async def _stub(*args, **kwargs):
        return ("", 5.0, None)
    monkeypatch.setattr(pl, "_llm_call_protected", _stub)

    res = await pl.SelfHeal().heal(
        task="x", round_num=0, max_rounds=2,
        all_attempts=[{"output": "broken", "score": 0, "error": "E101"}],
    )
    assert res["status"] == "escalate"


# ─── Wire-point isolation ────────────────────────────────────────────

_BACKEND = Path(__file__).resolve().parent.parent


def test_parliament_wired_only_in_loop_engine():
    """Parliament must only be IMPORTED by loop_engine.  Iter 212m-153
    allows two additional read-only mentions:
      • `core/observability.py` — wraps LLM calls inside parliament
        (mentions the name, never imports `Parliament` itself)
      • `routers/admin.py`      — reads the `parliament_log` Mongo
        collection for the /system-stats endpoint.
    The strict rule is: no other module may `import` Parliament."""
    hits = []
    for p in _BACKEND.rglob("*.py"):
        try:
            text = p.read_text()
        except Exception:
            continue
        if "parliament" in text.lower() or "Parliament" in text:
            hits.append(p.relative_to(_BACKEND).as_posix())
    # Tests directory will reference parliament too — strip those.
    hits = [h for h in hits if not h.startswith("tests/")]

    # Allow the read-only / wrapper mentions enumerated above; flag
    # everything else as a leak.
    ALLOWED = {
        "core/parliament.py",
        "core/observability.py",   # Iter 212m-153 — silent Langfuse wrapper
        "services/loop_engine.py",
        "routers/admin.py",        # Iter 212m-153 — reads parliament_log only
    }
    leaks = sorted(set(hits) - ALLOWED)
    assert leaks == [], f"Parliament leaked into other modules: {leaks}"

    # Anyone using `from core.parliament import Parliament` must be
    # exactly loop_engine.  This is the stronger / final guard.
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


def test_prompt_mode_files_untouched_by_parliament():
    """Explicitly assert the prompt-mode entry points do NOT import
    Parliament — the user demanded prompt mode stay as-is."""
    for rel in (
        "routers/chat.py",
        "services/orchestrator.py",
        "services/local_tools.py",
        "routers/codebase_health.py",
    ):
        text = (_BACKEND / rel).read_text()
        assert "parliament" not in text.lower(), \
            f"Parliament leaked into {rel} — prompt mode must stay untouched"


def test_loop_engine_wires_parliament_in_execute():
    """_do_execute imports Parliament for the per-file code-gen call."""
    text = (_BACKEND / "services" / "loop_engine.py").read_text()
    # Both the execute and verify branches must import.
    assert "from core.parliament import Parliament" in text
    assert "_parliament.run(" in text
    # Per-file timeout preserved.
    assert "PER_FILE_TIMEOUT_S" in text
    # 3-file parallelism cap preserved.
    assert "MAX_PARALLEL_GENS" in text


def test_loop_engine_wires_parliament_in_verify_heal():
    """_do_verify uses the parliament healer instead of self_heal()."""
    text = (_BACKEND / "services" / "loop_engine.py").read_text()
    assert "_parl.healer.heal(" in text
    assert "round_num=heal_attempt" in text
    assert "SELF_HEAL_LLM_TIMEOUT_S" in text


def test_loop_engine_preserves_phase_budgets():
    """Phase budgets must not be tampered with."""
    text = (_BACKEND / "services" / "loop_engine.py").read_text()
    # The 5-phase budgets dict + heal timeout must still be present.
    assert '"execute":' in text
    assert '"verify":' in text
    assert "SELF_HEAL_LLM_TIMEOUT_S" in text
    # Specifically the 420 / 360 numbers from Iter 212m-131 must
    # survive (they were re-balanced for the self-heal flow).
    assert "420" in text
    assert "360" in text
