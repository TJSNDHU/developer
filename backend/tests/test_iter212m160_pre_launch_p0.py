"""
Iter 212m-160 — Pre-launch P0 contract tests.

Verifies:
  • TaskRouter routes analysis/report/insight/summarize → Council B.
  • TaskRouter routes email/copy/write/draft           → Council C.
  • TaskRouter falls back to Council A on unknown task_type + keyword path.
  • `context["council"]` override still wins (back-compat).
  • loop_engine no longer hardcodes council="A" but still resolves to A
    via task_type="code_fix".
  • probe_longcat_availability() exists, sets LONGCAT_LIVE,
    short-circuits when LONGCAT_ENABLED=false.
  • _call_longcat fast-paths to GLM-5.2 when LONGCAT_LIVE=False.
"""

import asyncio
import importlib
import pathlib
import sys

# Path bootstrap so pytest can import `services.*` and `core.*` whether
# invoked from /app or /app/backend.
BACKEND = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


# ─── TaskRouter ─────────────────────────────────────────────────────────────

def test_task_router_analysis_routes_to_b():
    from core.parliament import TaskRouter
    r = TaskRouter()
    for ttype in ("analysis", "report", "insight", "summarize"):
        assert r.route("any task text", {"task_type": ttype}) == "B", (
            f"task_type={ttype!r} must route to Council B, got {r.route('x', {'task_type': ttype})!r}"
        )


def test_task_router_creative_routes_to_c():
    from core.parliament import TaskRouter
    r = TaskRouter()
    for ttype in ("email", "copy", "write", "draft"):
        assert r.route("any task text", {"task_type": ttype}) == "C", (
            f"task_type={ttype!r} must route to Council C, got {r.route('x', {'task_type': ttype})!r}"
        )


def test_task_router_code_routes_to_a():
    from core.parliament import TaskRouter
    r = TaskRouter()
    for ttype in ("code_fix", "code_review", "security", "lint_heal"):
        assert r.route("any task text", {"task_type": ttype}) == "A"


def test_task_router_unknown_task_type_keyword_fallback():
    """Unknown task_type → falls through to keyword scan.  Code-like
    keywords still land on A; everything else also lands on A (current
    safe default until B/C have richer entry points)."""
    from core.parliament import TaskRouter
    r = TaskRouter()
    assert r.route("please patch this bug", {"task_type": "wat"}) == "A"
    assert r.route("hi how are you", {}) == "A"


def test_task_router_explicit_council_override_wins():
    """A caller that passes context['council'] still gets that exact
    council, even if task_type would route differently. Preserves the
    Iter 212m-152 escape hatch for tests and ad-hoc tooling."""
    from core.parliament import TaskRouter
    r = TaskRouter()
    assert r.route("summarize x", {"council": "A", "task_type": "summarize"}) == "A"
    assert r.route("code patch",  {"council": "B", "task_type": "code_fix"})  == "B"
    assert r.route("draft email", {"council": "A", "task_type": "draft"})     == "A"


def test_task_router_map_covers_all_documented_types():
    """Spec sheet (Iter 212m-160 ask_human): the map MUST contain the
    11 task_types declared in the P0 task brief."""
    from core.parliament import TaskRouter
    expected = {
        "code_fix", "code_review", "security", "lint_heal",
        "analysis", "report", "insight", "summarize",
        "email", "copy", "write", "draft",
    }
    assert expected.issubset(TaskRouter._TASK_TYPE_TO_COUNCIL.keys())


# ─── loop_engine.py — hardcode removal ──────────────────────────────────────

def test_loop_engine_no_longer_hardcodes_council_a():
    """The `council="A"` literal must not appear in loop_engine.py's
    parliament context (verified via source scan because the actual
    code path requires a live repo/PR flow)."""
    src = pathlib.Path("/app/backend/services/loop_engine.py").read_text()
    # The exact key/value pair we deleted.
    assert '"council":         "A"' not in src
    assert '"council": "A"' not in src
    # task_type="code_fix" must still be there — that's how loop_engine
    # still routes to Council A via TaskRouter.
    assert '"task_type":       "code_fix"' in src


# ─── LongCat live probe ─────────────────────────────────────────────────────

def test_longcat_live_flag_exists():
    import services.llm as llm
    importlib.reload(llm)
    assert hasattr(llm, "LONGCAT_LIVE")
    # Default optimistic — flipped by the boot probe.
    assert llm.LONGCAT_LIVE is True


def test_probe_longcat_availability_skips_when_flag_off(monkeypatch):
    monkeypatch.setenv("LONGCAT_ENABLED", "false")
    import services.llm as llm
    importlib.reload(llm)
    assert llm.LONGCAT_ENABLED is False
    # When the flag is off the probe must NOT toggle the live flag.
    result = asyncio.run(llm.probe_longcat_availability())
    assert result is True  # stays True (no-op)
    assert llm.LONGCAT_LIVE is True


def test_probe_longcat_flips_live_false_on_400(monkeypatch):
    monkeypatch.setenv("LONGCAT_ENABLED", "true")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    import services.llm as llm
    importlib.reload(llm)

    class _Resp:
        def __init__(self): self.status_code = 400; self.text = ""
        def json(self):
            return {"error": {"message": "model id invalid"}}

    class _Client:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, *a, **kw): return _Resp()

    monkeypatch.setattr(llm.httpx, "AsyncClient", _Client)
    result = asyncio.run(llm.probe_longcat_availability())
    assert result is False
    assert llm.LONGCAT_LIVE is False


def test_probe_longcat_keeps_live_true_on_200(monkeypatch):
    monkeypatch.setenv("LONGCAT_ENABLED", "true")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    import services.llm as llm
    importlib.reload(llm)
    # Pretend the flag was already flipped False by a previous boot.
    llm.LONGCAT_LIVE = False

    class _Resp:
        def __init__(self): self.status_code = 200; self.text = ""
        def json(self): return {}

    class _Client:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, *a, **kw): return _Resp()

    monkeypatch.setattr(llm.httpx, "AsyncClient", _Client)
    result = asyncio.run(llm.probe_longcat_availability())
    assert result is True
    assert llm.LONGCAT_LIVE is True


def test_council_a_primary_falls_back_to_glm_when_live_false(monkeypatch):
    monkeypatch.setenv("LONGCAT_ENABLED", "true")
    import services.llm as llm
    importlib.reload(llm)
    llm.LONGCAT_LIVE = False
    assert llm.council_a_primary_model() == "z-ai/glm-5.2"
    llm.LONGCAT_LIVE = True
    assert llm.council_a_primary_model() == "meituan/longcat-2.0"


def test_call_longcat_fast_paths_to_glm_when_dead(monkeypatch):
    """If the boot probe has already flipped LONGCAT_LIVE=False, the
    actual call must NOT hit OpenRouter — it must go straight to GLM."""
    monkeypatch.setenv("LONGCAT_ENABLED", "true")
    import services.llm as llm
    importlib.reload(llm)
    llm.LONGCAT_LIVE = False

    or_calls = []

    async def fake_openrouter(**kwargs):
        or_calls.append(kwargs)
        return "should-never-be-called"

    async def fake_glm(**kwargs):
        return "via-glm"

    monkeypatch.setattr(llm, "call_openrouter_model", fake_openrouter)
    monkeypatch.setattr(llm, "_call_glm", fake_glm)
    out = asyncio.run(llm._call_longcat(system="s", user="u", max_tokens=10))
    assert out == "via-glm"
    assert or_calls == [], "OpenRouter must NOT be called when LONGCAT_LIVE=False"


def test_call_longcat_flips_live_false_on_mid_session_empty(monkeypatch):
    """If LongCat was live at boot but a call returns empty, the flag
    must flip so subsequent calls skip the round-trip."""
    monkeypatch.setenv("LONGCAT_ENABLED", "true")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    import services.llm as llm
    importlib.reload(llm)
    llm.LONGCAT_LIVE = True

    async def fake_openrouter(**kwargs):
        return ""  # LongCat suddenly returned empty

    async def fake_glm(**kwargs):
        return "via-glm"

    monkeypatch.setattr(llm, "call_openrouter_model", fake_openrouter)
    monkeypatch.setattr(llm, "_call_glm", fake_glm)
    out = asyncio.run(llm._call_longcat(system="s", user="u", max_tokens=10))
    assert out == "via-glm"
    assert llm.LONGCAT_LIVE is False, (
        "Mid-session empty must flip the live flag to short-circuit "
        "subsequent calls"
    )
