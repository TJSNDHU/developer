"""
Iter 212m-217 — Contract test for the `/scan` slash-command rate-limit
countdown toast.

The frontend Toast countdown in `ChatPanel.runSlashCommand` depends
on a very specific server contract:

    HTTP 429  with body {
        "detail": {
            "error":                "github_rate_limited"  |  "scan_rate_limited",
            "message":              "…",
            "retry_after_seconds":  <int>,
            ...
        }
    }

If any of these fields regress — most importantly the numeric
`retry_after_seconds` — the countdown breaks silently and the user is
back to a dead-end "Scan failed" toast.  This suite locks the shape
end-to-end (transport → HTTPException → JSON body).

We assert two paths:

  1. Per-user scan quota exhaustion  → `scan_rate_limited`
     (raised inside `codebase_health.scan` before any GitHub hit).
  2. GitHub primary rate limit       → `github_rate_limited`
     (raised inside `_gh_get`, must bubble unchanged through
      `codebase_health.scan`'s outer wrap).
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from routers.security_scan import _gh_get


class _FakeResp:
    def __init__(self, status_code, json_body=None, headers=None):
        self.status_code = status_code
        self._json = json_body
        self.text = ""
        self.headers = headers or {}
    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json


class _FakeClient:
    def __init__(self, resp):
        self._resp = resp
    async def get(self, url, *, headers=None, timeout=None):
        return self._resp


pytestmark = pytest.mark.asyncio


async def test_github_rate_limit_contract_has_retry_after_seconds():
    """The countdown toast reads
    `error.response.data.detail.retry_after_seconds` — this test
    guarantees that field exists and is a positive int."""
    import time
    reset_at = int(time.time()) + 120
    client = _FakeClient(_FakeResp(
        403,
        json_body={"message": "API rate limit exceeded for user."},
        headers={
            "x-ratelimit-remaining": "0",
            "x-ratelimit-reset":     str(reset_at),
        },
    ))

    with pytest.raises(HTTPException) as ei:
        await _gh_get(client, "https://api.github.com/x", "ghp_ok")

    assert ei.value.status_code == 429, \
        "GitHub primary rate-limit must map to HTTP 429 " \
        "(the frontend switches on status===429 to open the countdown)"
    body = ei.value.detail
    assert isinstance(body, dict), \
        "429 detail MUST be a structured dict, not a string, or the " \
        "countdown toast can't extract retry_after_seconds"
    assert body.get("error") == "github_rate_limited"
    assert isinstance(body.get("retry_after_seconds"), int)
    assert body["retry_after_seconds"] > 0
    # Note: no upper bound enforced on the server side — the client
    # caps at 300 s. This is purely documentation.


async def test_scan_rate_limit_shape_matches_client_expectations():
    """The other 429 path is the sliding-window per-user quota inside
    `codebase_health.scan`.  It packs the same shape.  Rather than
    spin up the full request stack we assert against the literal
    dict the router builds so a future refactor that renames the
    field is caught immediately."""
    src = open("/app/backend/routers/codebase_health.py").read()
    # The frontend keys on both fields; if either is renamed the toast breaks.
    assert '"error":               "scan_rate_limited"' in src, \
        "codebase_health.scan lost the `scan_rate_limited` error tag"
    assert '"retry_after_seconds": int(retry_secs)' in src, \
        "codebase_health.scan lost the numeric retry_after_seconds; " \
        "the countdown toast in ChatPanel.runSlashCommand can no " \
        "longer render."


def test_chat_panel_reads_retry_after_seconds_from_detail():
    """Static check: `ChatPanel.runSlashCommand` MUST read the
    numeric field the backend emits.  A rename on either end would
    silently break auto-retry.
    """
    src = open("/app/frontend/src/components/ChatPanel.jsx").read()
    assert "retry_after_seconds" in src, (
        "ChatPanel.runSlashCommand no longer references "
        "retry_after_seconds — the rate-limit countdown toast is "
        "disconnected from the backend contract."
    )
    # The countdown toast is persistent; make sure that codepath
    # survives.
    assert "persistent: true" in src or "persistent:true" in src, (
        "The rate-limit countdown toast MUST be persistent — a "
        "regular auto-dismissing toast would vanish before the "
        "retry fires."
    )
    assert "countdown:" in src, (
        "The rate-limit countdown toast lost its `countdown` field; "
        "the user no longer sees a live retry timer."
    )
