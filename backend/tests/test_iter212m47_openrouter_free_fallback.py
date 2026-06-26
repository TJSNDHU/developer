"""Iter 212m-47 — OpenRouter free-model fallback chain.

These are AST-level / config-level guards (no network) so they run
fast and catch regressions when someone:
  • Removes the fallback chain wiring from `_call_deepseek`.
  • Swaps the default free model list to a slug that isn't on
    OpenRouter's free tier.
  • Forgets to mark 402 (insufficient credits) as fallback-worthy.

Live-network behaviour is covered by a manual smoke step (the user
reproduces 402 by exhausting OpenRouter credits) — those don't belong
in CI.
"""
from __future__ import annotations

import importlib.util
import os
import pathlib


LLM_PY = pathlib.Path(__file__).resolve().parent.parent / "services" / "llm.py"


def _load_llm_module():
    spec = importlib.util.spec_from_file_location("llm_module_for_test", LLM_PY)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_fallback_status_set_includes_402_and_404() -> None:
    """402 (insufficient credits) is the most common failure when
    OpenRouter "stops in the middle of a task". 404 (model slug
    drift) must also walk forward, not abort the chain."""
    mod = _load_llm_module()
    assert 402 in mod._FALLBACK_STATUSES, (
        "402 Payment Required must trigger free-model fallback; that's "
        "the entire reason this layer exists."
    )
    assert 404 in mod._FALLBACK_STATUSES
    # And the long-tail transient codes:
    for code in (429, 500, 502, 503, 504):
        assert code in mod._FALLBACK_STATUSES, f"{code} should be fallback-worthy"
    # 400/401/422 (prompt-level errors) MUST NOT walk the chain.
    for code in (400, 401, 422):
        assert code not in mod._FALLBACK_STATUSES, (
            f"{code} is a prompt-level error — retrying on free models would "
            f"just waste their quota for no win."
        )


def test_default_free_models_are_actually_free() -> None:
    """Every default fallback slug must end with `:free` so it never
    consumes the user's OpenRouter credits."""
    mod = _load_llm_module()
    chain = mod._DEFAULT_FREE_MODELS
    assert isinstance(chain, list) and len(chain) >= 3
    for slug in chain:
        assert slug.endswith(":free"), (
            f"Default free-model slug {slug!r} doesn't end with ':free' — "
            "it would silently bill the user's OpenRouter account if the "
            "primary fails."
        )


def test_env_override_parses() -> None:
    """OPENROUTER_FREE_MODELS env var must override the default chain
    with a comma-separated list."""
    mod = _load_llm_module()
    old = os.environ.get("OPENROUTER_FREE_MODELS")
    try:
        os.environ["OPENROUTER_FREE_MODELS"] = "a/b:free, c/d:free ,e/f:free"
        chain = mod._free_fallback_models()
        assert chain == ["a/b:free", "c/d:free", "e/f:free"]
    finally:
        if old is None:
            os.environ.pop("OPENROUTER_FREE_MODELS", None)
        else:
            os.environ["OPENROUTER_FREE_MODELS"] = old


def test_call_deepseek_walks_fallback_chain() -> None:
    """The `_call_deepseek` body must build a candidate list that
    prepends the primary model to the free chain — proving the
    fallback is actually wired and not just a dead helper."""
    src = LLM_PY.read_text()
    assert "candidates: list[tuple[str, bool]] = [(_deepseek_model(), True)]" in src
    assert "for fm in _free_fallback_models():" in src
    # And the corresponding `call_openrouter_model` walks the same chain.
    assert (
        "candidates = [model] + [m for m in _free_fallback_models() if m != model]"
        in src
    )


def test_is_fallback_worthy_rejects_prompt_errors() -> None:
    """The helper must reject prompt-level errors so we don't burn
    free quota retrying a broken prompt against every free model."""
    mod = _load_llm_module()
    import httpx
    # Synthetic 400 response — should NOT fall back.
    req = httpx.Request("POST", "https://example.com")
    resp_400 = httpx.Response(400, request=req)
    exc_400 = httpx.HTTPStatusError("bad request", request=req, response=resp_400)
    assert mod._is_fallback_worthy(exc_400) is False
    # 402 → must walk forward.
    resp_402 = httpx.Response(402, request=req)
    exc_402 = httpx.HTTPStatusError("payment", request=req, response=resp_402)
    assert mod._is_fallback_worthy(exc_402) is True
