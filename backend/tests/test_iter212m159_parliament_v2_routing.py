"""
Iter 212m-159 — Parliament V2 Routing contract tests.

Verifies:
  • Feature flags exist and reflect env-loaded values.
  • Council A primary swaps GLM-5.2 → LongCat-2.0 when LONGCAT_ENABLED=true.
  • Council B uses mode="analysis" (not mode="chat").
  • llm.py routes mode="analysis" to GLM-5.2 + DeepSeek rescue when
    COUNCIL_B_GLM_ENABLED=true, else falls through to mode="chat" (legacy).
  • CEO judge has a rescue wrapper that times-out the primary on
    CEO_PRIMARY_TIMEOUT_S and switches to DeepSeek.
  • Langfuse traces emit `primary_model` metadata at every Council vote.
  • Source guards: no hard-coded "z-ai/glm-5.2" or "meituan/longcat-2.0"
    string outside services/llm.py (model strings live in one place).
"""

import asyncio
import importlib
import os
import pathlib
import sys
import time

import pytest

# Ensure /app/backend is on the import path so `services.llm` resolves
# when pytest is invoked from /app.
BACKEND = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _reload_llm(monkeypatch, **env):
    """Reload services.llm with the given env vars set so module-level
    flag constants reflect the test's expected state."""
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    import services.llm as llm_mod
    importlib.reload(llm_mod)
    return llm_mod


# ─── Tests ──────────────────────────────────────────────────────────────────

def test_v2_flags_exist():
    """All three flags must be attributes on services.llm so callers and
    Langfuse metadata can read them in one place."""
    import services.llm as llm
    importlib.reload(llm)
    assert hasattr(llm, "LONGCAT_ENABLED")
    assert hasattr(llm, "COUNCIL_B_GLM_ENABLED")
    assert hasattr(llm, "CEO_RESCUE_ENABLED")
    assert hasattr(llm, "CEO_PRIMARY_TIMEOUT_S")
    assert hasattr(llm, "CEO_RESCUE_MODEL")
    assert hasattr(llm, "_LONGCAT_MODEL")


def test_longcat_model_string_is_meituan(monkeypatch):
    """LongCat default model string must match the OpenRouter slug
    documented in PARLIAMENT_V2_ROUTING_ROADMAP.md."""
    monkeypatch.delenv("LONGCAT_MODEL", raising=False)
    llm = _reload_llm(monkeypatch)
    assert llm._LONGCAT_MODEL == "meituan/longcat-2.0"


def test_council_a_primary_swaps_to_longcat_when_enabled(monkeypatch):
    llm = _reload_llm(monkeypatch, LONGCAT_ENABLED="true")
    assert llm.LONGCAT_ENABLED is True
    assert llm.council_a_primary_model() == "meituan/longcat-2.0"


def test_council_a_primary_stays_glm_when_flag_off(monkeypatch):
    llm = _reload_llm(monkeypatch, LONGCAT_ENABLED="false")
    assert llm.LONGCAT_ENABLED is False
    assert llm.council_a_primary_model() == "z-ai/glm-5.2"


def test_council_b_primary_swaps_to_glm_when_enabled(monkeypatch):
    llm = _reload_llm(monkeypatch, COUNCIL_B_GLM_ENABLED="true")
    assert llm.COUNCIL_B_GLM_ENABLED is True
    assert llm.council_b_primary_model() == "z-ai/glm-5.2"


def test_council_b_primary_stays_deepseek_when_flag_off(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "deepseek/deepseek-chat")
    llm = _reload_llm(monkeypatch, COUNCIL_B_GLM_ENABLED="false")
    assert llm.COUNCIL_B_GLM_ENABLED is False
    assert llm.council_b_primary_model() == "deepseek/deepseek-chat"


def test_council_b_members_use_analysis_mode():
    """Parliament Council B members must declare mode='analysis' (Iter
    212m-159) so llm.py can route them through the V2 primary+rescue
    chain.  Previously was mode='chat' (DeepSeek)."""
    from core.parliament import CouncilB
    modes = {m.mode for m in CouncilB.members}
    assert modes == {"analysis"}, f"Council B mode mismatch: {modes}"


def test_council_c_members_unchanged():
    """Council C is explicitly UNTOUCHED by V2 routing — still
    mode='chat' (DeepSeek)."""
    from core.parliament import CouncilC
    modes = {m.mode for m in CouncilC.members}
    assert modes == {"chat"}, f"Council C mode must stay 'chat', got {modes}"


def test_council_a_members_unchanged():
    """Council A still uses mode='code', review_mode='pro' — the LongCat
    swap happens INSIDE llm.py's review_mode router, not in the member
    declaration."""
    from core.parliament import CouncilA
    for m in CouncilA.members:
        assert m.mode == "code"
        assert m.review_mode == "pro"


def test_analysis_mode_has_token_and_temperature_settings(monkeypatch):
    llm = _reload_llm(monkeypatch)
    assert "analysis" in llm.MAX_TOKENS
    assert "analysis" in llm.TEMPERATURE
    assert llm.cap_for("analysis") == llm.MAX_TOKENS["analysis"]
    assert llm.temperature_for("analysis") == llm.TEMPERATURE["analysis"]


def test_ceo_rescue_wrapper_exists():
    """The CEO rescue helper must exist in core.parliament so the CEO
    judge no longer has a single point of failure."""
    from core import parliament
    assert hasattr(parliament, "_ceo_judge_call_with_rescue")


def test_ceo_judge_uses_rescue_wrapper():
    """CEO._llm_judge must delegate to the rescue wrapper, not call
    _llm_call_protected directly."""
    src = pathlib.Path("/app/backend/core/parliament.py").read_text()
    # The _llm_judge function should reference the rescue wrapper.
    assert "_ceo_judge_call_with_rescue(" in src
    # And the OLD pattern (direct _llm_call_protected from _llm_judge with
    # trace_name="parliament.ceo.judge") must NOT exist anymore.
    assert "_llm_call_protected(" not in src.split("class SelfHeal")[0].split("async def _llm_judge")[1].split("async def _ceo_judge_call_with_rescue")[0]


def test_ceo_rescue_disabled_uses_single_call(monkeypatch):
    """When CEO_RESCUE_ENABLED=false, the wrapper must issue a single
    _llm_call_protected with the legacy GLM-via-swift params and zero
    rescue overhead."""
    monkeypatch.setenv("CEO_RESCUE_ENABLED", "false")
    import services.llm as llm
    importlib.reload(llm)
    from core import parliament
    importlib.reload(parliament)

    calls = []

    async def fake_llm_call_protected(**kwargs):
        calls.append(kwargs)
        return ("0", 50.0, None)

    monkeypatch.setattr(parliament, "_llm_call_protected", fake_llm_call_protected)
    content, latency, err = asyncio.run(
        parliament._ceo_judge_call_with_rescue(
            system="s", user="u", max_tokens=8,
            user_id=None, temperature=0.0, trace_metadata={"council": "A"},
        )
    )
    assert content == "0"
    assert err is None
    assert len(calls) == 1, f"flag-off path must issue exactly 1 LLM call, got {len(calls)}"
    assert calls[0]["trace_name"] == "parliament.ceo.judge"
    assert calls[0]["review_mode"] == "swift"


def test_ceo_rescue_fires_on_timeout(monkeypatch):
    """With the flag ON, if the primary GLM-5.2 call exceeds
    CEO_PRIMARY_TIMEOUT_S, the rescue must fire under the
    'parliament.ceo.rescue' trace name."""
    monkeypatch.setenv("CEO_RESCUE_ENABLED", "true")
    monkeypatch.setenv("CEO_PRIMARY_TIMEOUT_S", "0.05")
    import services.llm as llm
    importlib.reload(llm)
    from core import parliament
    importlib.reload(parliament)

    calls = []

    async def fake_llm_call_protected(**kwargs):
        calls.append(kwargs)
        if kwargs["trace_name"] == "parliament.ceo.judge":
            # primary: sleep past the timeout so the wrapper's wait_for fires
            await asyncio.sleep(0.5)
            return ("X", 500.0, None)
        # rescue
        return ("1", 80.0, None)

    monkeypatch.setattr(parliament, "_llm_call_protected", fake_llm_call_protected)
    content, latency, err = asyncio.run(
        parliament._ceo_judge_call_with_rescue(
            system="s", user="u", max_tokens=8,
            user_id=None, temperature=0.0,
            trace_metadata={"council": "A"},
        )
    )
    assert content == "1"
    assert err is None
    # 2 calls: primary (timed out) + rescue
    assert len(calls) == 2
    trace_names = [c["trace_name"] for c in calls]
    assert trace_names == ["parliament.ceo.judge", "parliament.ceo.rescue"]
    # Rescue used mode="chat" with empty review_mode → DeepSeek
    assert calls[1]["mode"] == "chat"
    assert calls[1]["review_mode"] == ""


def test_ceo_rescue_fires_on_empty_primary(monkeypatch):
    """Rescue must also fire if the primary returns empty content
    (not just on timeout)."""
    monkeypatch.setenv("CEO_RESCUE_ENABLED", "true")
    import services.llm as llm
    importlib.reload(llm)
    from core import parliament
    importlib.reload(parliament)

    calls = []

    async def fake_llm_call_protected(**kwargs):
        calls.append(kwargs)
        if kwargs["trace_name"] == "parliament.ceo.judge":
            return ("", 30.0, "empty")  # primary failed
        return ("2", 40.0, None)

    monkeypatch.setattr(parliament, "_llm_call_protected", fake_llm_call_protected)
    content, latency, err = asyncio.run(
        parliament._ceo_judge_call_with_rescue(
            system="s", user="u", max_tokens=8,
            user_id=None, temperature=0.0,
            trace_metadata={"council": "A"},
        )
    )
    assert content == "2"
    assert err is None
    assert [c["trace_name"] for c in calls] == ["parliament.ceo.judge", "parliament.ceo.rescue"]


def test_council_vote_trace_metadata_includes_primary_model(monkeypatch):
    """Every Council member vote must surface primary_model in the
    Langfuse trace metadata so the dashboard can filter by router
    version."""
    monkeypatch.setenv("LONGCAT_ENABLED", "true")
    monkeypatch.setenv("COUNCIL_B_GLM_ENABLED", "true")
    import services.llm as llm
    importlib.reload(llm)
    from core import parliament
    importlib.reload(parliament)

    captured = {}

    async def fake_llm_call_protected(**kwargs):
        captured.update(kwargs)
        return ("dummy", 10.0, None)

    monkeypatch.setattr(parliament, "_llm_call_protected", fake_llm_call_protected)
    member = parliament.CouncilA.members[0]
    asyncio.run(member.cast_vote(task="task", context={"council": "A", "user_id": "u"}))
    md = captured.get("trace_metadata") or {}
    assert md.get("primary_model") == "meituan/longcat-2.0"
    assert md.get("v2_longcat") is True
    assert md.get("v2_council_b_glm") is True

    # Council B trace
    captured.clear()
    b_member = parliament.CouncilB.members[0]
    asyncio.run(b_member.cast_vote(task="task", context={"council": "B", "user_id": "u"}))
    md_b = captured.get("trace_metadata") or {}
    assert md_b.get("primary_model") == "z-ai/glm-5.2"


def test_council_a_review_mode_pro_still_falls_back_to_claude(monkeypatch):
    """When the Council A primary (GLM or LongCat) returns empty, Pro
    mode must still fall through to Claude — Iter 212m-18 contract
    preserved by V2."""
    src = pathlib.Path("/app/backend/services/llm.py").read_text()
    # The pro branch must still reference _call_claude as a fallback
    assert "primary_caller" in src       # the new variable lives in the swift/pro/maxx block
    assert "_call_claude(" in src        # Claude rescue path unchanged
    assert "claude-sonnet-pro-fallback" in src


def test_no_hardcoded_longcat_string_outside_llm_py():
    """The literal string 'meituan/longcat-2.0' must only appear inside
    services/llm.py (single source of truth) and tests."""
    backend = pathlib.Path("/app/backend")
    offenders = []
    for path in backend.rglob("*.py"):
        if path.name.startswith("test_"):
            continue
        if path.name == "llm.py" and path.parent.name == "services":
            continue
        try:
            txt = path.read_text(errors="ignore")
        except Exception:
            continue
        if "meituan/longcat-2.0" in txt:
            offenders.append(str(path))
    assert not offenders, f"hard-coded LongCat string leaked into: {offenders}"


def test_analysis_mode_routing_block_in_llm_py():
    """The analysis-mode routing block must exist in services/llm.py
    (Council B's V2 path)."""
    src = pathlib.Path("/app/backend/services/llm.py").read_text()
    assert 'if mode == "analysis":' in src
    assert "COUNCIL_B_GLM_ENABLED" in src
    assert "Council B GLM-5.2 raised" in src
    assert "deepseek-v3-council-b-rescue" in src


def test_ceo_rescue_trace_name_distinct():
    """Rescue calls must use trace_name='parliament.ceo.rescue', NOT
    'parliament.ceo.judge', so Langfuse can compute rescue_rate."""
    src = pathlib.Path("/app/backend/core/parliament.py").read_text()
    assert 'trace_name="parliament.ceo.rescue"' in src
    assert 'trace_name="parliament.ceo.judge"' in src


def test_v2_flags_in_env_file():
    """All 3 V2 flags + LongCat model + CEO rescue config must be in
    backend/.env so the runtime picks them up."""
    env = pathlib.Path("/app/backend/.env").read_text()
    assert "LONGCAT_ENABLED=" in env
    assert "COUNCIL_B_GLM_ENABLED=" in env
    assert "CEO_RESCUE_ENABLED=" in env
    assert "CEO_PRIMARY_TIMEOUT_S=" in env
    assert "CEO_RESCUE_MODEL=" in env
    assert "LONGCAT_MODEL=" in env
