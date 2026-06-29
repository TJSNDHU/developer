"""
Iter 212m-118 — Tests for:
  1. Diagnose-first (RepairAgent pattern) in loop_execute._localize_change_target
  2. litellm.Router wiring (services/llm_router.py)
"""
from __future__ import annotations

import os
import pytest


# ─── 1. Diagnose-first localization ───────────────────────────────────
@pytest.mark.asyncio
async def test_localize_change_target_returns_context_block():
    """When the localizer LLM returns valid JSON pointing at a line +
    function, the helper must produce a 20-line context block."""
    from services import loop_execute as ge

    content = "\n".join(f"line_{i}" for i in range(1, 51))

    async def fake_llm(*, system, user, **kw):
        return {"content": '{"line": 25, "function": "do_thing", "reason": "old impl"}'}

    res = await ge._localize_change_target(
        path="src/x.py",
        current=content,
        plan={"title": "T", "bullets": ["a"]},
        user_message="fix the thing",
        user_id="u1",
        call_llm_with_meta=fake_llm,
    )
    assert res is not None
    assert res["has_localization"] is True
    assert res["line"] == 25
    assert res["function"] == "do_thing"
    # 20-line context window around line 25.
    assert res["snippet_end"] - res["snippet_start"] <= 21
    # Block contains the function name + reason.
    assert "do_thing" in res["context_block"]
    assert "old impl" in res["context_block"]


@pytest.mark.asyncio
async def test_localize_change_target_returns_none_on_entire_file_signal():
    """ENTIRE_FILE signal must skip extra context (fall back to
    full-file rewrite path)."""
    from services import loop_execute as ge

    async def fake_llm(*, system, user, **kw):
        return {"content": '{"line": 0, "function": "ENTIRE_FILE", "reason": "broad"}'}

    res = await ge._localize_change_target(
        path="x.py", current="x = 1\n" * 100,
        plan={"bullets": []}, user_message="rewrite all",
        user_id="u1", call_llm_with_meta=fake_llm,
    )
    assert res is None


@pytest.mark.asyncio
async def test_localize_change_target_returns_none_on_tiny_file():
    """Files <100 bytes don't need localization."""
    from services.loop_execute import _localize_change_target
    async def fake_llm(**kw): return {"content": ""}
    res = await _localize_change_target(
        path="x.py", current="x=1", plan={}, user_message="x",
        user_id="u", call_llm_with_meta=fake_llm,
    )
    assert res is None


@pytest.mark.asyncio
async def test_localize_change_target_returns_none_on_invalid_json():
    """Malformed JSON from the LLM must NOT crash — fall back to None
    so the caller goes through full-file rewrite."""
    from services.loop_execute import _localize_change_target
    async def fake_llm(**kw):
        return {"content": "not json at all"}
    res = await _localize_change_target(
        path="x.py", current="line\n" * 50, plan={},
        user_message="x", user_id="u", call_llm_with_meta=fake_llm,
    )
    assert res is None


@pytest.mark.asyncio
async def test_localize_change_target_strips_code_fences():
    """LLMs love wrapping JSON in ``` despite the system prompt — must
    still parse correctly."""
    from services.loop_execute import _localize_change_target
    async def fake_llm(**kw):
        return {"content": '```json\n{"line": 5, "function": "f", "reason": "r"}\n```'}
    res = await _localize_change_target(
        path="x.py", current="x\n" * 100, plan={},
        user_message="x", user_id="u", call_llm_with_meta=fake_llm,
    )
    assert res is not None
    assert res["line"] == 5
    assert res["function"] == "f"


@pytest.mark.asyncio
async def test_generate_one_inner_uses_localization_when_available(monkeypatch):
    """When localization succeeds, the rewrite prompt must contain the
    'DIAGNOSE-FIRST LOCALIZATION' marker."""
    from services import loop_execute as ge

    captured_prompt: list[str] = []
    async def fake_fetch(client, owner, repo, path, token):
        return "\n".join(f"line_{i}" for i in range(1, 80))
    async def fake_llm(*, system, user, max_tokens=None, mode=None,
                       user_id=None, review_mode=None):
        if review_mode == "swift":      # localizer call
            return {"content": '{"line": 40, "function": "g", "reason": "old"}'}
        captured_prompt.append(user)    # rewrite call
        return {"content": "rewritten content\n"}

    res = await ge._generate_one_inner(
        client=None, idx=1, total=1, path="src/a.py",
        plan={"title": "T", "bullets": []},
        user_message="fix",
        owner="o", repo="r", branch="main", token="t",
        user_id="u1",
        fetch_file=fake_fetch,
        call_llm_with_meta=fake_llm,
    )
    assert res is not None
    assert res["path"] == "src/a.py"
    # The rewrite prompt must include the localization block.
    assert captured_prompt, "rewrite LLM must have been called"
    assert "DIAGNOSE-FIRST LOCALIZATION" in captured_prompt[0]
    assert "function: g" in captured_prompt[0]
    assert "line:     40" in captured_prompt[0]


@pytest.mark.asyncio
async def test_generate_one_inner_falls_back_when_localizer_fails(monkeypatch):
    """Localizer failure must NOT break the rewrite — just skip the
    extra context block."""
    from services import loop_execute as ge

    captured_prompt: list[str] = []
    async def fake_fetch(*a, **k):
        return "\n".join(f"line_{i}" for i in range(1, 80))
    async def fake_llm(*, system, user, max_tokens=None, mode=None,
                       user_id=None, review_mode=None):
        if review_mode == "swift":
            raise RuntimeError("localizer down")
        captured_prompt.append(user)
        return {"content": "ok\n"}

    res = await ge._generate_one_inner(
        client=None, idx=1, total=1, path="src/a.py",
        plan={"title": "T", "bullets": []},
        user_message="fix",
        owner="o", repo="r", branch="main", token="t",
        user_id="u1",
        fetch_file=fake_fetch,
        call_llm_with_meta=fake_llm,
    )
    assert res is not None
    # Rewrite happened despite localizer failure.
    assert "DIAGNOSE-FIRST LOCALIZATION" not in captured_prompt[0]


# ─── 2. litellm router ────────────────────────────────────────────────
def test_llm_router_is_disabled_by_default(monkeypatch):
    from services import llm_router
    monkeypatch.delenv("LITELLM_ROUTER_ENABLED", raising=False)
    assert llm_router.is_enabled() is False


def test_llm_router_is_enabled_with_env_flag(monkeypatch):
    from services import llm_router
    monkeypatch.setenv("LITELLM_ROUTER_ENABLED", "1")
    assert llm_router.is_enabled() is True
    monkeypatch.setenv("LITELLM_ROUTER_ENABLED", "0")
    assert llm_router.is_enabled() is False


def test_llm_router_module_exposes_required_api():
    from services import llm_router
    assert callable(llm_router.is_enabled)
    assert callable(llm_router.get_router)
    assert callable(llm_router.call_via_router)
    assert callable(llm_router._build_model_list)


def test_llm_py_short_circuits_via_router_when_enabled():
    """The legacy 4-hop chain in services/llm.py must check the router
    flag at the top of call_llm_with_meta and delegate when enabled."""
    src = open("/app/backend/services/llm.py").read()
    cl_block = src.split("async def call_llm_with_meta(", 1)[1].split("async def ", 1)[0] if src.count("async def ") > 2 else src
    assert "LITELLM_ROUTER_ENABLED" in src or "is_enabled" in cl_block
    assert "call_via_router" in src
    # On router init failure, must fall through to legacy chain.
    assert "falling back to legacy chain" in src


def test_llm_router_build_model_list_skips_when_no_keys(monkeypatch):
    from services import llm_router
    for k in ("ANTHROPIC_API_KEY", "EMERGENT_LLM_KEY", "DEEPSEEK_API_KEY",
              "OPENROUTER_API_KEY", "GROQ_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    models = llm_router._build_model_list()
    assert models == []


def test_llm_router_build_model_list_includes_configured_keys(monkeypatch):
    from services import llm_router
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    for k in ("ANTHROPIC_API_KEY", "EMERGENT_LLM_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    models = llm_router._build_model_list()
    model_strs = [m["litellm_params"]["model"] for m in models]
    assert any("deepseek" in m for m in model_strs)
    assert any("groq" in m for m in model_strs)
    assert not any("anthropic" in m for m in model_strs)
