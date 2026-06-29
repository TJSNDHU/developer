"""
core/observability.py — Iter 212m-153 (Langfuse safe wrapper).

Single goal: expose `trace_llm(...)` as an **async context manager** that
any LLM call inside Parliament can wrap itself with — without ever
crashing if Langfuse keys are missing, the network is down, or the
SDK rejects a payload.

Why a wrapper instead of using Langfuse directly?
  1. Silent no-op when `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY`
     are not set.  In dev/CI we don't want Parliament to fail because
     of an optional observability service.
  2. Same call-site shape across the codebase.  `_llm_call_protected`,
     `Parliament.run`, and future LLM call sites all use one helper.
  3. End-on-exit semantics: latency, output, model, error — all in
     a single `with` block.

The wrapper is safe to import even without keys:

    with trace_llm("parliament.member", input=task) as span:
        ...
        span.set_output(content)
        span.set_metadata({"latency_ms": 412})

If the SDK is disabled, `span` is a no-op stub with the same API
shape (set_output / set_metadata / record_error).
"""
from __future__ import annotations

import logging
import os
import threading
from contextlib import contextmanager
from typing import Any, Iterator, Optional

logger = logging.getLogger("aurem-dev.observability")

# Module-level singletons.  We resolve the client exactly once on
# first use — subsequent calls reuse the same handle.
_lock          = threading.Lock()
_initialised   = False
_lf_client: Optional[Any] = None
_enabled: bool = False


def _resolve_client() -> Optional[Any]:
    """Lazily build a Langfuse client.  Returns None if either of the
    two required keys is missing or initialisation fails."""
    global _initialised, _lf_client, _enabled
    if _initialised:
        return _lf_client
    with _lock:
        if _initialised:
            return _lf_client
        _initialised = True
        pub = os.environ.get("LANGFUSE_PUBLIC_KEY", "").strip()
        sec = os.environ.get("LANGFUSE_SECRET_KEY", "").strip()
        if not pub or not sec:
            logger.info("Langfuse disabled — missing PUBLIC/SECRET keys")
            _enabled = False
            _lf_client = None
            return None
        try:
            from langfuse import Langfuse  # type: ignore
            base_url = (
                os.environ.get("LANGFUSE_BASE_URL")
                or os.environ.get("LANGFUSE_HOST")
                or "https://us.cloud.langfuse.com"
            )
            _lf_client = Langfuse(
                public_key=pub,
                secret_key=sec,
                host=base_url,
                # Background flush — never block the event loop.
                flush_interval=5.0,
            )
            _enabled = True
            logger.info("Langfuse initialised host=%s", base_url)
        except Exception as e:                                  # noqa: BLE001
            logger.warning("Langfuse init failed (silent no-op): %r", e)
            _enabled = False
            _lf_client = None
        return _lf_client


def is_enabled() -> bool:
    """True iff Langfuse is configured and the client built ok."""
    _resolve_client()
    return _enabled


# ─────────────────────────────────────────────────────────────────────
#  No-op span — used when Langfuse is disabled OR a span build fails.
# ─────────────────────────────────────────────────────────────────────

class _NoopSpan:
    __slots__ = ()

    def set_output(self, output: Any) -> None:                # noqa: D401
        return None

    def set_metadata(self, metadata: dict) -> None:           # noqa: D401
        return None

    def record_error(self, err: str | Exception) -> None:     # noqa: D401
        return None


class _RealSpan:
    """Thin adapter over a Langfuse observation handle.

    The Langfuse v4 SDK exposes `update()` on the observation handle
    returned by `start_as_current_observation()`.  We expose a stable
    3-method API so the call sites don't need to know whether they
    received a real or stub span."""

    __slots__ = ("_handle",)

    def __init__(self, handle: Any):
        self._handle = handle

    def set_output(self, output: Any) -> None:
        try:
            self._handle.update(output=output)
        except Exception as e:                                  # noqa: BLE001
            logger.debug("langfuse span.set_output failed: %r", e)

    def set_metadata(self, metadata: dict) -> None:
        try:
            self._handle.update(metadata=metadata)
        except Exception as e:                                  # noqa: BLE001
            logger.debug("langfuse span.set_metadata failed: %r", e)

    def record_error(self, err: str | Exception) -> None:
        try:
            self._handle.update(
                level="ERROR",
                status_message=str(err)[:240],
            )
        except Exception as e:                                  # noqa: BLE001
            logger.debug("langfuse span.record_error failed: %r", e)


_NOOP = _NoopSpan()


# ─────────────────────────────────────────────────────────────────────
#  Public API
# ─────────────────────────────────────────────────────────────────────

@contextmanager
def trace_llm(
    name: str,
    *,
    input: Any = None,
    metadata: Optional[dict] = None,
    model: Optional[str] = None,
    as_type: str = "generation",
) -> Iterator[Any]:
    """Wrap an LLM call in a Langfuse observation.  Silent no-op if
    Langfuse is disabled.

    Usage::

        with trace_llm("parliament.member", input=task,
                       metadata={"council": "A"}) as span:
            content = await call_llm(...)
            span.set_output(content)
            span.set_metadata({"latency_ms": ms})

    On any exception inside the `with` block, the span is marked as
    ERROR and the exception is re-raised.  Failure to talk to
    Langfuse itself never raises — we degrade to a no-op."""
    client = _resolve_client()
    if client is None or not _enabled:
        yield _NOOP
        return
    try:
        cm = client.start_as_current_observation(
            name=name,
            as_type=as_type,
            input=input,
            metadata=metadata or {},
            model=model,
        )
    except Exception as e:                                      # noqa: BLE001
        logger.debug("langfuse start_as_current_observation failed: %r", e)
        yield _NOOP
        return
    try:
        with cm as handle:
            span = _RealSpan(handle)
            try:
                yield span
            except Exception as e:                              # noqa: BLE001
                span.record_error(e)
                raise
    except Exception as e:                                      # noqa: BLE001
        # Either the context manager itself blew up (unlikely) or
        # the user's code raised — we already recorded it above.
        if not isinstance(e, RuntimeError):
            raise


def flush() -> None:
    """Best-effort flush of the Langfuse client buffer.  Safe to call
    when disabled."""
    client = _resolve_client()
    if client is None:
        return
    try:
        client.flush()
    except Exception as e:                                      # noqa: BLE001
        logger.debug("langfuse flush failed: %r", e)


__all__ = ["trace_llm", "flush", "is_enabled"]
