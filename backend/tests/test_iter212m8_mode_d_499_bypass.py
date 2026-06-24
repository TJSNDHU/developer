"""Iter 212m-8 — Mode-D classifier 499 bypass.

Bug surfaced on production (auremcto.com): ORA was ignoring tool-call
requests like "Read backend/routers/deploy.py" and instead returning
canned "Root cause: Client disconnected" Mode-D diagnoses.

Root cause: the browser's F12 capture buffer held a stale HTTP 499
(client-closed-request) network entry from a previous request. The
chat router's `_f12_has_real_signal()` did NOT filter 499 as a
transient proxy error → Mode D fired on every subsequent prompt →
the user's actual "Read deploy.py" intent never reached the
orchestrator.

This test locks the fix: 499 must be classified as transient so it
NEVER routes a chat to Mode D again, even if it lingers in the F12
buffer for the entire session.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from routers.chat import (
    _is_transient_proxy_error,
    _f12_has_real_signal,
    _TRANSIENT_PROXY_CODES,
    classify_intent,
)


# ──────────────────────────────────────────────────────────────────
# Direct unit on the helpers
# ──────────────────────────────────────────────────────────────────


def test_499_is_in_transient_codes():
    """The set membership is the first line of defence."""
    assert 499 in _TRANSIENT_PROXY_CODES


def test_499_with_json_body_is_transient():
    """Our backend returns JSON `{"detail": "client disconnected"}` on
    499, NOT HTML. The function must still treat it as transient — 499
    is by definition client-side, body shape is irrelevant."""
    assert _is_transient_proxy_error(
        499, b'{"detail":"client disconnected"}',
    ) is True
    assert _is_transient_proxy_error(
        499, '{"detail":"client disconnected"}',
    ) is True
    assert _is_transient_proxy_error(499, "") is True
    assert _is_transient_proxy_error(499, None) is True


def test_real_500_with_app_body_is_not_transient():
    """A genuine application 500 must STILL route to Mode D — we only
    drop 499 unconditionally, not other 5xx codes."""
    assert _is_transient_proxy_error(500, '{"detail":"ValueError"}') is False


def test_502_with_html_body_is_transient_unchanged():
    """Existing behaviour for proxy 502 must not regress."""
    assert _is_transient_proxy_error(
        502, "<!doctype html><body>Bad Gateway</body>",
    ) is True


# ──────────────────────────────────────────────────────────────────
# F12 payload — must NOT fire Mode D on stale 499
# ──────────────────────────────────────────────────────────────────


def test_f12_with_only_a_499_returns_no_signal():
    """The exact production bug: a buffer holding one 499 must NOT
    light up `_f12_has_real_signal` — otherwise Mode D hijacks the
    user's next chat turn."""
    payload = {
        "network_errors": [{
            "url": "/api/aurem-dev/chat/stream",
            "method": "POST",
            "status": 499,
            "response_body": '{"detail":"client disconnected"}',
            "timestamp": "2026-02-24T10:00:00Z",
        }],
    }
    assert _f12_has_real_signal(payload) is False


def test_f12_with_499_plus_real_error_still_signals():
    """A stale 499 alongside a REAL error (e.g. a 500 from our backend)
    must still fire Mode D — we only filter 499 specifically."""
    payload = {
        "network_errors": [
            {"url": "/api/x", "status": 499, "response_body": ""},
            {"url": "/api/y", "status": 500, "response_body": '{"detail":"crash"}'},
        ],
    }
    assert _f12_has_real_signal(payload) is True


def test_f12_with_499_plus_console_error_signals():
    """A real console.error in the SAME payload must still reach Mode D."""
    payload = {
        "network_errors": [{"url": "/api/x", "status": 499, "response_body": ""}],
        "console_errors": [{"message": "TypeError: x is undefined at App.jsx:42"}],
    }
    assert _f12_has_real_signal(payload) is True


def test_classify_intent_read_request_with_stale_499_routes_to_codegen():
    """End-to-end intent classifier: 'Read backend/routers/deploy.py'
    with a stale 499 in F12 must NOT route to Mode D. It can route to
    A/B/C — any of those is fine, but never D (which is the
    diagnostic mode that hijacked production)."""
    stale_499_payload = {
        "network_errors": [{
            "url": "/api/aurem-dev/chat/stream",
            "status": 499,
            "response_body": '{"detail":"client disconnected"}',
        }],
    }
    intent = classify_intent(
        "Read backend/routers/deploy.py — show me all endpoints",
        stale_499_payload,
    )
    assert intent != "D", (
        "Read-request must not route to Mode D when F12 only carries a "
        "stale 499. Got intent=%r" % intent
    )
