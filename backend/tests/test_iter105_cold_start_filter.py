"""Iter 105 — cold-start hardening for F12 capture / Mode-D trigger.

Bug: On `auremcto.com` first page load, Cloudflare returns 520 on the
JWT-validation request (origin cold-start). F12ErrorCapture stored that
520, and ChatPanel auto-flushed the buffer with the user's first chat
("hi"). Backend's `_f12_has_real_signal` saw the 520+URL → routed to
Mode D → LLM had no real file context → produced the spammy:

    🟠 Root cause: 520 origin timeout
    Files to check: (unknown — error context too thin)

Fix: drop transient proxy/gateway codes (408, 502, 503, 504, 520-530)
with an HTML body from the signal-detection layer. They are
infrastructure noise, not application errors.

This test exercises the BACKEND filter. The mirror filter in
F12ErrorCapture.js is browser-only and is covered manually.
"""
from routers.chat import (
    _f12_has_real_signal,
    _is_transient_proxy_error,
    _TRANSIENT_PROXY_CODES,
)


# ── transient proxy code helper ─────────────────────────────────
def test_proxy_code_set_includes_cloudflare_and_gateway():
    # Cloudflare-specific
    for c in (520, 521, 522, 523, 524, 525, 526, 527, 530):
        assert c in _TRANSIENT_PROXY_CODES, f"missing {c}"
    # Gateway family
    for c in (502, 503, 504):
        assert c in _TRANSIENT_PROXY_CODES, f"missing {c}"
    # Request timeout
    assert 408 in _TRANSIENT_PROXY_CODES


def test_transient_when_status_in_set_and_html_body():
    body = "<!DOCTYPE html><html><body>Error 520: Origin Timeout</body></html>"
    assert _is_transient_proxy_error(520, body) is True
    assert _is_transient_proxy_error(503, "<html>service unavailable</html>") is True


def test_transient_when_status_in_set_and_empty_body():
    # Cloudflare sometimes returns empty body on cold-start
    assert _is_transient_proxy_error(520, "") is True
    assert _is_transient_proxy_error(504, None) is True


def test_transient_recognises_cloudflare_marker():
    body = '{"error":"upstream","via":"cloudflare"}'
    assert _is_transient_proxy_error(520, body) is True


def test_NOT_transient_when_real_app_500_with_json_body():
    # A real backend 500 (JSON body, app trace) — must NOT be filtered.
    body = '{"detail":"NameError: foo not defined","trace":"..."}'
    assert _is_transient_proxy_error(500, body) is False
    # 500 isn't in the proxy set either way
    assert 500 not in _TRANSIENT_PROXY_CODES


def test_NOT_transient_when_status_not_in_set():
    # 401, 403, 404, 422 are real application responses → keep them
    for st in (401, 403, 404, 422, 429):
        assert _is_transient_proxy_error(st, "<html>x</html>") is False


# ── _f12_has_real_signal with payload ─────────────────────────
def test_cold_start_520_alone_does_not_trigger_signal():
    payload = {
        "console_errors": [],
        "network_errors": [{
            "url": "/api/aurem-dev/auth/tokens",
            "method": "GET",
            "status": 520,
            "response_body": "<!DOCTYPE html><html><body>Error 520</body></html>",
        }],
        "stack_traces": [],
    }
    assert _f12_has_real_signal(payload) is False


def test_cold_start_502_with_empty_body_does_not_trigger():
    payload = {
        "network_errors": [{
            "url": "/api/anything",
            "method": "POST",
            "status": 502,
            "response_body": "",
        }],
    }
    assert _f12_has_real_signal(payload) is False


def test_real_app_500_with_json_body_DOES_trigger():
    payload = {
        "network_errors": [{
            "url": "/api/aurem-dev/chat/send",
            "method": "POST",
            "status": 500,
            "response_body": '{"detail":"NameError: foo"}',
        }],
    }
    assert _f12_has_real_signal(payload) is True


def test_real_404_DOES_trigger():
    payload = {
        "network_errors": [{
            "url": "/api/aurem-dev/repo/nonexistent",
            "method": "GET",
            "status": 404,
            "response_body": '{"detail":"Not Found"}',
        }],
    }
    assert _f12_has_real_signal(payload) is True


def test_real_console_error_still_triggers_signal():
    payload = {
        "console_errors": [{
            "type": "error",
            "message": "TypeError: Cannot read property 'foo' of undefined",
        }],
    }
    assert _f12_has_real_signal(payload) is True


def test_stack_trace_still_triggers_signal():
    payload = {"stack_traces": ["TypeError at App.jsx:88"]}
    assert _f12_has_real_signal(payload) is True


def test_mixed_payload_real_signal_wins():
    """If the buffer contains BOTH a cold-start 520 AND a real 500, we
    must still trigger debug for the real 500."""
    payload = {
        "network_errors": [
            {
                "url": "/api/auth/tokens",
                "method": "GET",
                "status": 520,
                "response_body": "<html>cloudflare</html>",
            },
            {
                "url": "/api/aurem-dev/chat/send",
                "method": "POST",
                "status": 500,
                "response_body": '{"detail":"real bug"}',
            },
        ],
    }
    assert _f12_has_real_signal(payload) is True


def test_empty_payload_no_signal():
    assert _f12_has_real_signal({}) is False
    assert _f12_has_real_signal({"console_errors": [], "network_errors": [], "stack_traces": []}) is False
