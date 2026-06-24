"""Iter 212m-18 — GLM-5.2 primary + Claude watchdog + SSE step streaming.

Tests live in three layers:

  • llm.py routing — unit tests with `monkeypatch` over `_call_glm` and
    `_call_claude` so we can deterministically force GLM empty / GLM
    raise / Claude empty / Claude raise paths.
  • orchestrator.py wiring — static-source pin that the orchestrator
    plumbs `review_mode` + `step_hook` through to call_llm_with_meta.
  • chat.py SSE wiring — static pin that the SSE worker registers a
    `_step` callback and forwards `step` queue events as
    `data: {"type":"step", "text":"…", "done":bool}`.
"""
import asyncio
from pathlib import Path

import pytest

from services import llm as llm_mod
from services.llm import call_llm_with_meta, _GLM_MODEL, _CLAUDE_MODEL

BACKEND = Path(__file__).resolve().parents[1]


# ── llm.py: GLM model ID ───────────────────────────────────────────


def test_glm_model_id_default_is_z_ai_glm_52():
    """Default model must be z-ai/glm-5.2 unless GLM_MODEL env is set."""
    assert _GLM_MODEL == "z-ai/glm-5.2"


def test_call_glm_function_exists():
    assert callable(getattr(llm_mod, "_call_glm", None))


# ── Swift mode → GLM only ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_swift_mode_uses_glm_only(monkeypatch):
    glm_calls = []

    async def fake_glm(system, user, max_tokens=3500, temperature=0.0):
        glm_calls.append((system[:20], user[:20], max_tokens, temperature))
        return "Hello from GLM"

    async def fake_claude(*a, **k):
        raise AssertionError("Claude must NOT be called in Swift mode")

    monkeypatch.setattr(llm_mod, "_call_glm",    fake_glm)
    monkeypatch.setattr(llm_mod, "_call_claude", fake_claude)

    res = await call_llm_with_meta(
        system="You are helpful.", user="hi", review_mode="swift",
    )
    assert res["ok"] is True
    assert res["provider"] == "glm-5.2"
    assert res["model"] == _GLM_MODEL
    assert res["content"] == "Hello from GLM"
    assert res["fallback_chain"] == ["glm-5.2"]
    assert len(glm_calls) == 1


# ── Pro mode → GLM, Claude fallback when GLM empty / errors ───────


@pytest.mark.asyncio
async def test_pro_mode_uses_glm_when_non_empty(monkeypatch):
    async def fake_glm(*a, **k): return "real glm reply"
    async def fake_claude(*a, **k):
        raise AssertionError("Claude must NOT be called when GLM ok")
    monkeypatch.setattr(llm_mod, "_call_glm", fake_glm)
    monkeypatch.setattr(llm_mod, "_call_claude", fake_claude)

    res = await call_llm_with_meta(
        system="s", user="u", review_mode="pro",
    )
    assert res["provider"] == "glm-5.2"
    assert res["content"] == "real glm reply"


@pytest.mark.asyncio
async def test_pro_mode_falls_back_to_claude_when_glm_empty(monkeypatch):
    async def fake_glm(system="", user="", max_tokens=3500, temperature=0.0):
        return ""        # GLM returned empty
    async def fake_claude(system="", user="", max_tokens=3500, temperature=0.0):
        return "claude saved the day"
    monkeypatch.setattr(llm_mod, "_call_glm", fake_glm)
    monkeypatch.setattr(llm_mod, "_call_claude", fake_claude)

    res = await call_llm_with_meta(
        system="s", user="u", review_mode="pro",
    )
    assert res["provider"] == "claude-sonnet-pro-fallback"
    assert res["model"] == _CLAUDE_MODEL
    assert res["content"] == "claude saved the day"
    assert res["fallback_chain"] == ["glm-5.2", "claude-sonnet"]


@pytest.mark.asyncio
async def test_pro_mode_falls_back_to_claude_when_glm_raises(monkeypatch):
    async def fake_glm(*a, **k): raise RuntimeError("GLM upstream down")
    async def fake_claude(*a, **k): return "claude recovered"
    monkeypatch.setattr(llm_mod, "_call_glm", fake_glm)
    monkeypatch.setattr(llm_mod, "_call_claude", fake_claude)
    res = await call_llm_with_meta(
        system="s", user="u", review_mode="pro",
    )
    assert res["provider"] == "claude-sonnet-pro-fallback"


# ── Maxx mode → GLM then Claude review+improve ────────────────────


@pytest.mark.asyncio
async def test_maxx_mode_calls_both_glm_then_claude_review(monkeypatch):
    calls = {"glm": 0, "claude": 0, "claude_user": ""}

    async def fake_glm(system="", user="", max_tokens=3500, temperature=0.0):
        calls["glm"] += 1
        return "draft from glm"

    async def fake_claude(system="", user="", max_tokens=3500, temperature=0.0):
        calls["claude"] += 1
        calls["claude_user"] = user
        return "improved by claude"

    monkeypatch.setattr(llm_mod, "_call_glm",    fake_glm)
    monkeypatch.setattr(llm_mod, "_call_claude", fake_claude)

    res = await call_llm_with_meta(
        system="s", user="u", review_mode="maxx",
    )
    assert calls["glm"] == 1
    assert calls["claude"] == 1
    # Claude must receive the GLM draft inside its user prompt — that's
    # the "review and improve" handoff the user spec explicitly asks for.
    assert "draft from glm" in calls["claude_user"]
    assert "review" in calls["claude_user"].lower() or \
           "improve" in calls["claude_user"].lower()
    assert res["provider"] == "glm-5.2+claude-review"
    assert res["content"] == "improved by claude"
    assert res["fallback_chain"] == ["glm-5.2", "claude-sonnet-review"]


@pytest.mark.asyncio
async def test_maxx_mode_returns_glm_draft_when_claude_review_fails(
        monkeypatch):
    async def fake_glm(*a, **k): return "glm draft survives"
    async def fake_claude(*a, **k): raise RuntimeError("Claude down")
    monkeypatch.setattr(llm_mod, "_call_glm",    fake_glm)
    monkeypatch.setattr(llm_mod, "_call_claude", fake_claude)
    res = await call_llm_with_meta(
        system="s", user="u", review_mode="maxx",
    )
    assert res["provider"] == "glm-5.2-no-review"
    assert res["content"] == "glm draft survives"


@pytest.mark.asyncio
async def test_maxx_mode_falls_back_to_claude_direct_when_glm_empty(
        monkeypatch):
    async def fake_glm(*a, **k): return ""
    async def fake_claude(*a, **k): return "claude direct"
    monkeypatch.setattr(llm_mod, "_call_glm",    fake_glm)
    monkeypatch.setattr(llm_mod, "_call_claude", fake_claude)
    res = await call_llm_with_meta(
        system="s", user="u", review_mode="maxx",
    )
    assert res["provider"] == "claude-sonnet-maxx-direct"
    assert res["content"] == "claude direct"


# ── Legacy path untouched ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_legacy_chat_mode_still_uses_deepseek(monkeypatch):
    """When no `review_mode` is passed, behaviour must be identical to
    pre-iter-212m-18 (DeepSeek for chat, Claude for code)."""
    async def fake_deepseek(messages, system, max_tokens, temperature):
        return "deepseek says hi"
    async def fake_glm(*a, **k):
        raise AssertionError("GLM must NOT be called on legacy path")
    monkeypatch.setattr(llm_mod, "_call_deepseek", fake_deepseek)
    monkeypatch.setattr(llm_mod, "_call_glm", fake_glm)

    res = await call_llm_with_meta(
        system="s", user="u", mode="chat",
    )
    assert res["provider"] == "deepseek"
    assert res["content"] == "deepseek says hi"


# ── step_hook contract ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_step_hook_fires_thinking_label_in_swift_mode(monkeypatch):
    async def fake_glm(*a, **k): return "ok"
    monkeypatch.setattr(llm_mod, "_call_glm", fake_glm)
    seen = []
    await call_llm_with_meta(
        system="s", user="u", review_mode="swift",
        step_hook=lambda txt, done=False: seen.append((txt, done)),
    )
    assert any("🤔" in s[0] or "Thinking" in s[0] for s in seen)


@pytest.mark.asyncio
async def test_step_hook_fires_review_label_in_maxx_mode(monkeypatch):
    async def fake_glm(*a, **k): return "draft"
    async def fake_claude(*a, **k): return "reviewed"
    monkeypatch.setattr(llm_mod, "_call_glm", fake_glm)
    monkeypatch.setattr(llm_mod, "_call_claude", fake_claude)
    seen = []
    await call_llm_with_meta(
        system="s", user="u", review_mode="maxx",
        step_hook=lambda txt, done=False: seen.append((txt, done)),
    )
    assert any("🔍" in s[0] or "review" in s[0].lower() for s in seen)


# ── Orchestrator wiring pins ───────────────────────────────────────


def test_orchestrator_plumbs_review_mode_to_llm():
    src = (BACKEND / "services" / "orchestrator.py").read_text(
        encoding="utf-8")
    assert "step_hook=None" in src
    # The orchestrator must pass `review_mode=mode if ...` down to
    # call_llm_with_meta for the main iter, not silently drop it.
    assert 'review_mode=(mode if mode in {"swift", "pro", "maxx"} else None)' \
        in src
    # Step labels for each phase must be defined.
    assert "_STEP_LABELS" in src
    assert "📖 Reading repo…" in src
    assert "✍️ Writing files…" in src
    assert "🚀 Committing…" in src
    assert "✅ Done" in src


def test_orchestrator_fires_step_hook_on_tool_dispatch():
    src = (BACKEND / "services" / "orchestrator.py").read_text(
        encoding="utf-8")
    # The tool-execution path must call step_hook with the per-tool
    # label so the SSE consumer sees a real phase event when a tool
    # actually fires (not a fake delay).
    assert "_step_label_for_tool(tool_name)" in src


# ── chat.py SSE wiring pins ────────────────────────────────────────


def test_chat_sse_registers_step_callback():
    src = (BACKEND / "routers" / "chat.py").read_text(encoding="utf-8")
    # SSE worker must define a `_step` callback that pushes to the queue
    # AND pass step_hook=_step into chat_with_tools.
    assert "def _step(" in src
    assert 'q.put_nowait({' in src
    assert '"type": "step"' in src
    assert "step_hook=_step" in src


def test_chat_sse_forwards_step_events_to_client():
    src = (BACKEND / "routers" / "chat.py").read_text(encoding="utf-8")
    # The consumer-side `while True:` loop must have a branch that
    # ships the step event back out as an SSE frame.
    assert 'ev["type"] == "step"' in src
    assert '"text": ev.get("text"' in src
    assert '"done":' in src
