"""
services/langfuse_tracing.py — Iter 212m-119

Cloud-hosted Langfuse (https://us.cloud.langfuse.com) observability for
every ORA LLM call. Each `services.llm.call_llm_with_meta()` invocation
emits a trace with:

  • model          (claude / deepseek / openrouter / groq / glm)
  • mode           (chat / code / review)
  • user_id        (so per-user spend + latency dashboards work)
  • review_mode    (swift / pro / maxx)
  • prompt + completion lengths
  • latency        (ms)
  • tokens_used    (when the model returns usage data)
  • is_emergency   (Groq fallback used)
  • error          (on exceptions)

Safety:
  • Initialised lazily on first call. Missing keys → tracing silently
    disabled (no crash, no startup blocking).
  • All work wrapped in try/except — a Langfuse outage NEVER breaks an
    LLM call. Logging only.
  • Prompts are truncated to 8 KB each in the trace metadata to keep
    Langfuse storage cheap. Full bodies stay in our app logs.
"""
from __future__ import annotations

import logging
import os
import time
from contextlib import contextmanager
from typing import Any, Optional

logger = logging.getLogger("aurem-dev.langfuse_tracing")

_lf_client: Optional[Any] = None
_lf_disabled_reason: Optional[str] = None
_MAX_PROMPT_CHARS = 8_000


def _client() -> Optional[Any]:
    """Lazy singleton. Returns None if Langfuse isn't configured —
    callers must handle that gracefully (already done in the contextmanager)."""
    global _lf_client, _lf_disabled_reason
    if _lf_client is not None:
        return _lf_client
    if _lf_disabled_reason:
        return None
    sk = os.getenv("LANGFUSE_SECRET_KEY")
    pk = os.getenv("LANGFUSE_PUBLIC_KEY")
    host = os.getenv("LANGFUSE_BASE_URL") or "https://us.cloud.langfuse.com"
    if not (sk and pk):
        _lf_disabled_reason = "missing LANGFUSE_SECRET_KEY/PUBLIC_KEY env vars"
        logger.info("langfuse disabled — %s", _lf_disabled_reason)
        return None
    try:
        from langfuse import Langfuse
        _lf_client = Langfuse(
            secret_key=sk,
            public_key=pk,
            host=host,
        )
        logger.info("langfuse initialised — host=%s public_key=%s…",
                    host, pk[:12])
        return _lf_client
    except Exception as e:                              # noqa: BLE001
        _lf_disabled_reason = f"init_failed: {e}"
        logger.warning("langfuse init failed, tracing disabled: %r", e)
        return None


def is_enabled() -> bool:
    return _client() is not None


@contextmanager
def trace_llm_call(
    *,
    name: str = "llm.call",
    mode: Optional[str] = None,
    review_mode: Optional[str] = None,
    user_id: Optional[str] = None,
    system_prompt: str = "",
    user_prompt: str = "",
    extra_metadata: Optional[dict] = None,
):
    """Wrap a call_llm_with_meta() invocation. Use as:

        with trace_llm_call(...) as t:
            result = await call_llm_with_meta(...)
            t.success(result)

    Any exception raised inside the block is automatically logged on
    the trace as an error and re-raised. If Langfuse isn't configured,
    this is a no-op shim so callers don't need any feature flags."""
    started = time.time()
    client = _client()
    span_handle: dict[str, Any] = {"_started": started}

    if client is None:
        # No-op shim. Define handle.success() so callers can call it
        # unconditionally.
        def _noop(*_a, **_kw): return None
        span_handle["success"] = _noop
        span_handle["fail"]    = _noop
        try:
            yield span_handle
        finally:
            pass
        return

    trace = None
    try:
        # Langfuse v3+ unified API.
        trace = client.trace(
            name=name,
            user_id=user_id or "anonymous",
            metadata={
                "mode":        mode,
                "review_mode": review_mode,
                **(extra_metadata or {}),
            },
            input={
                "system": (system_prompt or "")[:_MAX_PROMPT_CHARS],
                "user":   (user_prompt or "")[:_MAX_PROMPT_CHARS],
            },
        )
    except Exception as e:                              # noqa: BLE001
        # Older v2 API → fall back to simple log + return shim.
        logger.debug("langfuse trace() failed, using noop: %r", e)
        def _noop(*_a, **_kw): return None
        span_handle["success"] = _noop
        span_handle["fail"]    = _noop
        try:
            yield span_handle
        finally:
            pass
        return

    def _on_success(result: dict) -> None:
        try:
            latency_ms = int((time.time() - started) * 1000)
            tokens = 0
            try:
                tokens = int((result or {}).get("tokens_used") or 0)
            except (TypeError, ValueError):
                tokens = 0
            content = (result or {}).get("content") or ""
            model   = (result or {}).get("model") or "unknown"
            trace.update(
                output={
                    "model":         model,
                    "completion":    content[:_MAX_PROMPT_CHARS],
                    "tokens_used":   tokens,
                    "is_emergency":  bool((result or {}).get("is_emergency")),
                    "latency_ms":    latency_ms,
                },
            )
        except Exception as e:                              # noqa: BLE001
            logger.debug("langfuse on_success failed: %r", e)

    def _on_fail(err: BaseException) -> None:
        try:
            latency_ms = int((time.time() - started) * 1000)
            trace.update(
                output={"error": repr(err)[:1000], "latency_ms": latency_ms},
                level="ERROR",
            )
        except Exception as e:                              # noqa: BLE001
            logger.debug("langfuse on_fail failed: %r", e)

    span_handle["success"] = _on_success
    span_handle["fail"]    = _on_fail

    try:
        yield span_handle
    except BaseException as e:
        _on_fail(e)
        raise
    finally:
        # Flush only on best-effort basis. Langfuse client batches in
        # background, so this is just a hint.
        try:
            if hasattr(client, "flush"):
                client.flush()
        except Exception:
            pass
