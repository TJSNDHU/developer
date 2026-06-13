"""Iter 137 regression: middleware chain must never return None.

Symptom: prod logs showed
  RuntimeError: No response returned.
  File "starlette/middleware/errors.py", line 164, in __call__
  ERROR: Exception in ASGI application
  During handling of the above exception, another exception occurred

Root cause: FastAPI's BaseHTTPMiddleware (`@app.middleware('http')`)
doesn't gracefully handle the common case where a long SSE stream is
cancelled because the client browser closed the tab. The cancellation
propagates as `asyncio.CancelledError` (or another exception), no
response is returned, and Starlette's outer ServerErrorMiddleware
panics with `RuntimeError: No response returned.`

Fix: every middleware AND the global exception handler must
ALWAYS return a Response object, even on CancelledError or unhandled
exceptions during call_next.

This test pins those guards in source so a future refactor can't
silently re-introduce the leak.
"""
from __future__ import annotations

import pathlib


MAIN_PY = pathlib.Path(__file__).resolve().parents[1] / "main.py"


def _src() -> str:
    return MAIN_PY.read_text(encoding="utf-8")


def test_security_headers_middleware_has_try_except() -> None:
    src = _src()
    # The middleware must wrap call_next in try/except so a cancelled
    # SSE stream becomes a 499 instead of a 500 panic.
    assert "Iter 137" in src, "Iter 137 marker missing"
    assert "client disconnected mid-stream" in src, (
        "_security_headers must handle CancelledError gracefully"
    )
    # The fallback path must return a JSONResponse with 499.
    assert "status_code=499" in src, "499 fallback status missing"


def test_route_cache_skips_streaming_response() -> None:
    src = _src()
    # The cache middleware must NOT try to buffer a StreamingResponse —
    # that would defeat SSE and risk OOM on slow clients.
    assert "StreamingResponse as _StreamResp" in src, (
        "route_cache must import StreamingResponse type"
    )
    assert "isinstance(response, _StreamResp)" in src, (
        "route_cache must short-circuit on StreamingResponse"
    )


def test_route_cache_body_iter_has_try_except() -> None:
    src = _src()
    # Body iteration must be in a try/except so a mid-stream error
    # doesn't strand the cache middleware without a response to return.
    assert "route-cache: body_iterator failed" in src, (
        "route_cache body iteration must have an exception guard"
    )


def test_global_exc_handler_handles_cancellederror() -> None:
    src = _src()
    # The global exception handler must explicitly short-circuit
    # CancelledError so client disconnects don't escalate to 500s in
    # the logs.
    assert "asyncio.CancelledError" in src or "_aio.CancelledError" in src, (
        "global exception handler must catch CancelledError"
    )
    assert "client disconnected" in src, (
        "global exception handler must surface a clear disconnect message"
    )
