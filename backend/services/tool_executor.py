"""
tool_executor.py — Core 2 of the verification foundation.

Wraps every tool dispatch in a uniform try/except that maps backend
exceptions (especially GitHub API errors) into structured `system_signal`
records. The LLM only ever sees a neutral *"Tool X could not complete"*
message — never the raw status code or hallucination-bait error text.

The structured signal is bubbled up to the SSE stream so the frontend
can render a typed banner via `SystemSignalBanner.jsx` instead of relying
on the LLM to explain the failure.

Iter 209 — Aurem CTO core architecture.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

# ─── Status → signal mapping ─────────────────────────────────────────
TOOL_ERROR_MAP: dict[int, dict[str, str]] = {
    401: {"signal": "github_auth_failed",       "severity": "error"},
    403: {"signal": "github_permission_denied", "severity": "error"},
    404: {"signal": "repo_not_found",           "severity": "warning"},
    422: {"signal": "invalid_request",          "severity": "warning"},
    429: {"signal": "github_rate_limited",      "severity": "warning"},
    500: {"signal": "github_server_error",      "severity": "info"},
    502: {"signal": "github_server_error",      "severity": "info"},
    503: {"signal": "github_server_error",      "severity": "info"},
}

_STATUS_RE = re.compile(r"\b(?:HTTP|status)[\s:]*?(\d{3})\b", re.IGNORECASE)


def _extract_status_code(exc: Exception) -> int | None:
    """Best-effort: pull an HTTP status from common exception shapes."""
    # httpx.HTTPStatusError → .response.status_code
    rsp = getattr(exc, "response", None)
    if rsp is not None:
        sc = getattr(rsp, "status_code", None)
        if isinstance(sc, int):
            return sc
    # Custom GitHubAPIError → .status_code attribute
    sc = getattr(exc, "status_code", None)
    if isinstance(sc, int):
        return sc
    # FastAPI HTTPException → .status_code
    sc = getattr(exc, "code", None)
    if isinstance(sc, int):
        return sc
    # Last resort — parse the message
    m = _STATUS_RE.search(str(exc))
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None
    return None


def _classify(exc: Exception) -> dict[str, str]:
    """Map an exception to a signal record. Falls back to `unknown_error`."""
    code = _extract_status_code(exc)
    if code is not None and code in TOOL_ERROR_MAP:
        return {**TOOL_ERROR_MAP[code], "http_status": str(code)}
    if code is not None:
        return {
            "signal":      "unknown_http_error",
            "severity":    "error",
            "http_status": str(code),
        }
    return {
        "signal":   "unknown_error",
        "severity": "error",
    }


async def execute(
    tool_name: str,
    runner:    Callable[..., Awaitable[dict]],
    *args,
    **kwargs,
) -> dict[str, Any]:
    """Run any tool function under uniform error capture.

    Returns the *unwrapped* tool result on success::
        {"ok": True, "data": {...tool result...}}

    Or a structured failure on error::
        {"ok": False,
         "tool": tool_name,
         "system_signal": "github_auth_failed",
         "severity": "error",
         "http_status": "401",
         "error_class": "HTTPStatusError",
         "error_message": "Bad credentials",
         "llm_facing":   "Tool read_repo_file could not complete."}

    `llm_facing` is the ONLY string the LLM should ever read; all
    error specifics travel through `system_signal` for the frontend.
    """
    try:
        result = await runner(*args, **kwargs)
    except Exception as e:                                    # noqa: BLE001
        sig = _classify(e)
        logger.warning(
            "ToolExecutor: %s failed (signal=%s status=%s): %r",
            tool_name, sig.get("signal"), sig.get("http_status", "-"), e,
        )
        return {
            "ok":            False,
            "tool":          tool_name,
            "system_signal": sig["signal"],
            "severity":      sig["severity"],
            "http_status":   sig.get("http_status"),
            "error_class":   type(e).__name__,
            "error_message": str(e)[:240],
            # Neutral message the LLM is allowed to relay verbatim:
            "llm_facing":    f"Tool {tool_name} could not complete.",
        }
    return {"ok": True, "tool": tool_name, "data": result}


def collect_signals(executor_results: list[dict]) -> list[dict]:
    """Filter a list of executor results to just the failure signals,
    in the canonical shape expected by `SystemSignalBanner.jsx`."""
    return [
        {
            "signal":      r["system_signal"],
            "severity":    r["severity"],
            "tool":        r["tool"],
            "http_status": r.get("http_status"),
        }
        for r in executor_results or ()
        if not r.get("ok") and r.get("system_signal")
    ]
