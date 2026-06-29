"""
Iter 212m-119 — Langfuse cloud tracing for every LLM call.

Verifies:
  • langfuse_tracing.trace_llm_call() degrades to a no-op shim when
    LANGFUSE_*_KEY env vars are missing (production-safe).
  • The contextmanager exposes .success() + .fail() callbacks that
    callers invoke unconditionally — no feature-flag plumbing needed.
  • services/llm.py wraps call_llm_with_meta() with the tracer at the
    public entry point — every legacy + router-path LLM call is auto-
    traced.
  • Langfuse init failures NEVER break the LLM call (defensive
    swallowing in the contextmanager).
"""
from __future__ import annotations

import os
import pytest


# ─── 1. No-op shim when keys missing ──────────────────────────────────
@pytest.mark.asyncio
async def test_trace_llm_call_is_noop_without_keys(monkeypatch):
    from services import langfuse_tracing as lf
    for k in ("LANGFUSE_SECRET_KEY", "LANGFUSE_PUBLIC_KEY"):
        monkeypatch.delenv(k, raising=False)
    # Reset the singleton so the test sees the missing-keys path.
    lf._lf_client = None
    lf._lf_disabled_reason = None
    assert lf.is_enabled() is False
    with lf.trace_llm_call(
        name="t1", mode="chat", user_id="u1",
        system_prompt="sys", user_prompt="usr",
    ) as t:
        # Both callbacks must exist on the shim and accept arbitrary args.
        t["success"]({"content": "x", "tokens_used": 5, "model": "stub"})
        t["fail"](RuntimeError("dummy"))


# ─── 2. Real client returned when keys set ────────────────────────────
def test_client_lazy_init_with_keys(monkeypatch):
    from services import langfuse_tracing as lf
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test-key")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test-key")
    monkeypatch.setenv("LANGFUSE_BASE_URL", "https://us.cloud.langfuse.com")
    lf._lf_client = None
    lf._lf_disabled_reason = None

    class _FakeLangfuse:
        def __init__(self, **kw):
            self.kw = kw
        def trace(self, **kw): return type("T", (), {"update": lambda *a, **k: None})()
        def flush(self): pass
    import langfuse
    monkeypatch.setattr(langfuse, "Langfuse", _FakeLangfuse)
    client = lf._client()
    assert client is not None
    # Constructor args were threaded through.
    assert client.kw["secret_key"] == "sk-test-key"
    assert client.kw["public_key"] == "pk-test-key"
    assert client.kw["host"]       == "https://us.cloud.langfuse.com"


# ─── 3. Tracer never breaks the LLM call on Langfuse outage ───────────
@pytest.mark.asyncio
async def test_trace_llm_call_swallows_langfuse_errors(monkeypatch):
    """Even if Langfuse internals raise, the contextmanager must let
    the wrapped call complete normally."""
    from services import langfuse_tracing as lf
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    lf._lf_client = None
    lf._lf_disabled_reason = None

    class _BrokenClient:
        def trace(self, **kw):
            raise RuntimeError("langfuse upstream down")
        def flush(self): pass
    monkeypatch.setattr(lf, "_client", lambda: _BrokenClient())

    with lf.trace_llm_call(
        name="t1", mode="chat", user_id="u1",
        system_prompt="s", user_prompt="u",
    ) as t:
        # Tracer is broken → shim fallback path. Both callbacks must
        # be callable no-ops.
        t["success"]({"content": "x", "tokens_used": 10, "model": "stub"})


# ─── 4. llm.py wires the tracer at the public entry point ─────────────
def test_call_llm_with_meta_wraps_inner_with_trace():
    src = open("/app/backend/services/llm.py").read()
    # The split into wrapper + _call_llm_with_meta_inner must exist.
    assert "async def _call_llm_with_meta_inner(" in src
    assert "trace_llm_call" in src
    # The wrapper must call .success() on the returned result.
    cl_block = src.split("async def call_llm_with_meta(", 1)[1].split("async def _call_llm_with_meta_inner(", 1)[0]
    assert "trace_llm_call" in cl_block
    assert '_lf["success"](result)' in cl_block
    # The router short-circuit (iter 118) must live INSIDE the inner.
    inner_block = src.split("async def _call_llm_with_meta_inner(", 1)[1].split("async def ", 1)[0]
    assert "call_via_router" in inner_block


def test_langfuse_dependency_is_in_requirements():
    req = open("/app/backend/requirements.txt").read().lower()
    assert "langfuse" in req


# ─── 5. Module exposes the documented surface ─────────────────────────
def test_langfuse_tracing_module_api():
    from services import langfuse_tracing as lf
    assert callable(lf.is_enabled)
    assert callable(lf.trace_llm_call)
    assert callable(lf._client)
    # MAX_PROMPT_CHARS guards Langfuse storage cost.
    assert lf._MAX_PROMPT_CHARS == 8_000


# ─── 6. Truncates long prompts before sending to Langfuse ─────────────
@pytest.mark.asyncio
async def test_trace_truncates_long_prompts(monkeypatch):
    from services import langfuse_tracing as lf
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-x")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-x")
    lf._lf_client = None
    lf._lf_disabled_reason = None

    captured: list[dict] = []
    class _T:
        def update(self, **kw):
            captured.append(kw)
    class _FakeClient:
        def trace(self, **kw):
            captured.append({"trace_kw": kw})
            return _T()
        def flush(self): pass
    monkeypatch.setattr(lf, "_client", lambda: _FakeClient())

    huge_prompt = "x" * 50_000
    with lf.trace_llm_call(
        name="t", mode="code", user_id="u",
        system_prompt=huge_prompt, user_prompt=huge_prompt,
    ) as t:
        t["success"]({"content": "y" * 50_000, "tokens_used": 1, "model": "z"})

    init = next(c for c in captured if "trace_kw" in c)["trace_kw"]
    assert len(init["input"]["system"]) == lf._MAX_PROMPT_CHARS
    assert len(init["input"]["user"])   == lf._MAX_PROMPT_CHARS
    # Output completion also truncated.
    upd = [c for c in captured if "output" in c]
    assert upd
    assert len(upd[0]["output"]["completion"]) == lf._MAX_PROMPT_CHARS
