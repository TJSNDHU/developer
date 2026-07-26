"""Iter 212m-11 — Vanguard false-positive fixes.

Six surgical fixes to silence high-noise false-positives in the
pre-push security scanner:

1. `openai_key` — exclude `sk-aurem[-_]*` and `sk-test[-_]*` test
   creds via negative lookahead.
2. `requests_no_verify` — only flag when prefixed by a real
   HTTP client (`requests` / `httpx` / `urllib`), not arbitrary
   `verify=False` kwargs.
3. `token_assignment` — drop bare `token` (matches everything
   from `csrf_token` to `pagination_token`), require 16+ char
   literal.
4. `generic_secret` — exclude `client_secret`, require 16+ char
   literal, negative-lookbehind so `*_secret` variants don't fire.
5. `# vanguard: ignore` per-line suppression marker (works for
   both Python and JS comment styles).
6. `vanguard_verify_agent._llm_review` — pre-normalise Python
   `True/False/None` to JSON `true/false/null` so single-literal
   slips from open-weight models don't blow the entire review.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.vanguard_scanner import scan_text  # noqa: E402


def _names(findings: list[dict]) -> list[str]:
    return [f["name"] for f in findings]


# ── Fix 1 — openai_key negative lookahead ─────────────────────────


def test_openai_key_flags_real_sk():
    f = scan_text("OPENAI_KEY = 'sk-proj1234567890abcdefghij'")
    assert "openai_key" in _names(f)


@pytest.mark.parametrize("placeholder", [
    "sk-aurem-1234567890abcdefghij",
    "sk-aurem_1234567890abcdefghij",
    "sk-test-1234567890abcdefghij",
    "sk-test_1234567890abcdefghij",
])
def test_openai_key_skips_aurem_and_test_placeholders(placeholder):
    f = scan_text(f"key = '{placeholder}'")
    assert "openai_key" not in _names(f), placeholder


# ── Fix 2 — requests_no_verify narrowed ───────────────────────────


@pytest.mark.parametrize("snippet", [
    "requests.get(url, verify=False)",
    "httpx.AsyncClient(verify=False)",
    "urllib.request.urlopen(url, context=ctx, verify=False)",
])
def test_requests_no_verify_flags_real_http_clients(snippet):
    f = scan_text(snippet)
    assert "requests_no_verify" in _names(f)


@pytest.mark.parametrize("snippet", [
    'config = {"verify": False}',
    "my_validate(verify=False)",
    "pydantic_field = Field(verify=False)",
    "schema.validate(verify=False)",
])
def test_requests_no_verify_skips_unrelated_kwargs(snippet):
    f = scan_text(snippet)
    assert "requests_no_verify" not in _names(f), snippet


# ── Fix 3 — token_assignment: no bare 'token', 16+ chars ──────────


@pytest.mark.parametrize("snippet", [
    "token = 'abc12345'",
    "token = 'abcdefghijklmnop1234'",
    "csrf_token = 'abcdefghijklmnop'",
    "pagination_token: 'abcdefghijklmnop'",
])
def test_token_assignment_drops_bare_token(snippet):
    f = scan_text(snippet)
    assert "token_assignment" not in _names(f), snippet


def test_token_assignment_8_char_does_not_fire():
    f = scan_text("bearer = 'abc12345'")
    assert "token_assignment" not in _names(f)


@pytest.mark.parametrize("snippet", [
    "bearer = 'abc1234567890abcdef'",
    "refresh_token: 'abc1234567890abcdef'",
    "access_token = 'abc1234567890abcdef'",
    "auth_token = 'abc1234567890abcdef'",
])
def test_token_assignment_fires_on_16char_real_token_names(snippet):
    f = scan_text(snippet)
    assert "token_assignment" in _names(f), snippet


# ── Fix 4 — generic_secret: 16+ chars, no client_secret ───────────


@pytest.mark.parametrize("snippet", [
    "client_secret = 'abcdefghijklmnop'",
    "CLIENT_SECRET = 'abcdefghijklmnop'",
    "api_secret = 'abcdefghijklmnop'",
    "secret = 'short'",
    "secret = 'abc12345'",
])
def test_generic_secret_skips_short_or_prefixed(snippet):
    f = scan_text(snippet)
    assert "generic_secret" not in _names(f), snippet


@pytest.mark.parametrize("snippet", [
    "secret = 'abcdefghijklmnop'",
    "signing_key = 'abcdefghijklmnop'",
    "encryption_key = 'abcdefghijklmnop'",
])
def test_generic_secret_fires_on_bare_secret_with_16char_literal(snippet):
    f = scan_text(snippet)
    assert "generic_secret" in _names(f), snippet


# ── Fix 5 — # vanguard: ignore line suppression ──────────────────


def test_vanguard_ignore_marker_suppresses_python_finding():
    plain = scan_text("requests.get(url, verify=False)")
    assert "requests_no_verify" in _names(plain)
    suppr = scan_text("requests.get(url, verify=False)  # vanguard: ignore")
    assert "requests_no_verify" not in _names(suppr)


def test_vanguard_ignore_marker_suppresses_js_finding():
    # Iter 212m-226 — innerHTML_assignment is a CODE-ONLY rule so
    # it now requires a file extension (.js/.jsx/.ts/.tsx) to fire.
    # The test passes a `.js` filepath explicitly so the assertion
    # exercises the suppression marker rather than the code-only
    # gate.
    plain = scan_text("el.innerHTML = data;", filepath="app.js")
    assert "innerHTML_assignment" in _names(plain)
    suppr = scan_text("el.innerHTML = data;  // vanguard: ignore",
                      filepath="app.js")
    assert "innerHTML_assignment" not in _names(suppr)


def test_vanguard_ignore_marker_per_line_not_per_file():
    # Marker on line 1 suppresses line 1 but the second occurrence
    # on line 2 still fires.
    code = (
        "requests.get(url, verify=False)  # vanguard: ignore\n"
        "httpx.AsyncClient(verify=False)\n"
    )
    f = scan_text(code)
    flagged = [x for x in f if x["name"] == "requests_no_verify"]
    assert len(flagged) == 1
    assert flagged[0]["line"] == 2


# ── Fix 6 — Python True/False/None → JSON true/false/null ─────────


def _normalize_python_literals(text: str) -> str:
    """Mirror the in-router normalization step (naive .replace())
    so we can assert the contract without going through OpenRouter."""
    text = text.replace("True", "true")
    text = text.replace("False", "false")
    text = text.replace("None", "null")
    return text


def test_python_literal_normalization_parses_clean():
    raw = '{"pass": True, "findings": [], "summary": "ok", "meta": None}'
    data = json.loads(_normalize_python_literals(raw))
    assert data["pass"] is True
    assert data["findings"] == []
    assert data["meta"] is None


def test_python_literal_normalization_naive_replace_known_tradeoff():
    """Locked behaviour: naive `.replace()` will also rewrite the
    tokens inside JSON string VALUES. This is a deliberate trade-off
    per the spec — JSON.loads success on the common case (top-level
    bools/None emitted by Claude) matters more than preserving rare
    string content like the literal word 'True' inside a message."""
    raw = '{"pass": False, "label": "TrueBlue"}'
    normalised = _normalize_python_literals(raw)
    data = json.loads(normalised)
    assert data["pass"] is False
    # Naive replace DOES rewrite inside string values — locked here
    # so a future "fix" doesn't silently change the contract.
    assert data["label"] == "trueBlue"


def test_python_literal_normalization_handles_nested():
    raw = ('{"pass": True, "findings": ['
           '{"file": "x.py", "line": 42, "vuln": True, "meta": None}]}')
    data = json.loads(_normalize_python_literals(raw))
    assert data["pass"] is True
    assert data["findings"][0]["vuln"] is True
    assert data["findings"][0]["meta"] is None


# ── Regression: existing high-value patterns still fire ───────────


def test_regression_aws_access_key_still_fires():
    f = scan_text("AWS_KEY = 'AKIAIOSFODNN7EXAMPLE'")
    assert "aws_access_key" in _names(f)


def test_regression_github_token_still_fires():
    f = scan_text("GITHUB = 'ghp_abcdefghijklmnopqrstuvwxyz0123456789ab'")
    assert "github_token" in _names(f)


def test_regression_private_key_still_fires():
    # Iter 212m-224 — the private_key regex was tightened to require
    # actual base64 key material on the line(s) following the header
    # (`\n[A-Za-z0-9+/=]{20,}`). Bare `-----BEGIN … PRIVATE KEY-----`
    # lines with no body were surfacing on placeholder JSX / form
    # strings and documentation. This test asserts the rule STILL
    # fires when real key material is present, which is the
    # security-relevant case.
    f = scan_text(
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQC7VJTUt9Us8cKj\n"
    )
    assert "private_key" in _names(f)


def test_regression_eval_still_fires():
    # Iter 212m-226 — eval_usage is now a CODE-ONLY rule so it only
    # fires on files with real source-code extensions (.py, .js,
    # .ts, …). Without a filepath the scanner conservatively skips
    # eval_usage because plain-text prose (`Running promptfoo
    # eval …` in run.sh, markdown examples) was surfacing as
    # CRITICAL. Passing a `.py` filepath restores the previous
    # behaviour for the security-relevant case.
    f = scan_text("eval(user_input)", filepath="app.py")
    assert "eval_usage" in _names(f)


def test_regression_password_assignment_still_fires():
    f = scan_text("password = 'hunter2'")
    assert "password_assignment" in _names(f)
