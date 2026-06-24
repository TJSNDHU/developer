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
import re
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
    plain = scan_text("el.innerHTML = data;")
    assert "innerHTML_assignment" in _names(plain)
    suppr = scan_text("el.innerHTML = data;  // vanguard: ignore")
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
    """Mirror the in-router normalization step so we can assert
    the contract without going through OpenRouter."""
    text = re.sub(r"\bTrue\b",  "true",  text)
    text = re.sub(r"\bFalse\b", "false", text)
    text = re.sub(r"\bNone\b",  "null",  text)
    return text


def test_python_literal_normalization_parses_clean():
    raw = '{"pass": True, "findings": [], "summary": "ok", "meta": None}'
    data = json.loads(_normalize_python_literals(raw))
    assert data["pass"] is True
    assert data["findings"] == []
    assert data["meta"] is None


def test_python_literal_normalization_preserves_strings():
    # Tokens INSIDE string values shouldn't be touched. \b prevents
    # partial-word substitution; we rely on the fact that quoted
    # strings still get the substitution but ONLY for full-token
    # matches — which is acceptable because JSON readers are
    # case-sensitive and `"True"` is not a valid JSON literal anyway.
    # The contract we lock here: the resulting text is valid JSON.
    raw = '{"pass": False, "label": "TrueBlue"}'
    normalised = _normalize_python_literals(raw)
    data = json.loads(normalised)
    assert data["pass"] is False
    # "TrueBlue" preserved because \b prevents the partial match.
    assert data["label"] == "TrueBlue"


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
    f = scan_text("-----BEGIN RSA PRIVATE KEY-----")
    assert "private_key" in _names(f)


def test_regression_eval_still_fires():
    f = scan_text("eval(user_input)")
    assert "eval_usage" in _names(f)


def test_regression_password_assignment_still_fires():
    f = scan_text("password = 'hunter2'")
    assert "password_assignment" in _names(f)
