"""
Iter 212m-24 — LIVE E2E tests for Admin House Rules against PREVIEW.

Covers what the unit suite cannot: real HTTP auth guard, persistence,
and end-to-end injection visible in the chat response. Resets the
house_rules doc to OFF/empty at session teardown.
"""
from __future__ import annotations

import json
import os
import time
from typing import Optional

import pytest
import requests

BASE = os.environ.get(
    "REACT_APP_BACKEND_URL", "https://launch-pad-237.preview.emergentagent.com"
).rstrip("/")
API = f"{BASE}/api/aurem-dev"

FOUNDER_EMAIL = "teji.ss1986@gmail.com"
FOUNDER_PASSWORD = "FounderOwn123!"
QA_USER_EMAIL = "qa-prod@aurem.dev"
QA_USER_PASSWORD = "qq*U71r#ZQ*fnB1BqRIKBQLt"

HR_URL = f"{API}/admin/house-rules"
MARKER = "[HOUSE-RULE-OK]"
PROMPT = (
    "When you reply, the FIRST 17 characters of your reply MUST be the "
    "literal token [HOUSE-RULE-OK] followed by a space. Do not omit the "
    "brackets or change the case. Then answer normally."
)


def _login(email: str, password: str) -> Optional[str]:
    r = requests.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=20)
    if r.status_code != 200:
        return None
    return r.json().get("access_token") or r.json().get("token")


@pytest.fixture(scope="session")
def founder_token():
    tok = _login(FOUNDER_EMAIL, FOUNDER_PASSWORD)
    if not tok:
        pytest.skip("Founder login failed")
    return tok


@pytest.fixture(scope="session")
def non_admin_token():
    tok = _login(QA_USER_EMAIL, QA_USER_PASSWORD)
    return tok  # may be None — handled per-test


@pytest.fixture(scope="session", autouse=True)
def _reset_house_rules(founder_token):
    """Ensure clean OFF state before and after the whole suite."""
    off = {
        "prompt": "",
        "enabled_chat": False, "enabled_advisor": False,
        "enabled_swift": False, "enabled_pro": False, "enabled_maxx": False,
    }
    requests.put(HR_URL, json=off, headers={"Authorization": f"Bearer {founder_token}"}, timeout=15)
    yield
    requests.put(HR_URL, json=off, headers={"Authorization": f"Bearer {founder_token}"}, timeout=15)


# ── 1. Auth guard ────────────────────────────────────────────────────
def test_get_house_rules_requires_auth():
    r = requests.get(HR_URL, timeout=15)
    assert r.status_code in (401, 403), f"expected 401/403, got {r.status_code} {r.text[:200]}"


def test_put_house_rules_requires_auth():
    r = requests.put(HR_URL, json={"prompt": "x"}, timeout=15)
    assert r.status_code in (401, 403)


def test_get_house_rules_rejects_non_admin(non_admin_token):
    if not non_admin_token:
        pytest.skip("qa-prod login unavailable on preview")
    r = requests.get(HR_URL, headers={"Authorization": f"Bearer {non_admin_token}"}, timeout=15)
    assert r.status_code == 403, f"expected 403 for non-admin, got {r.status_code}"


# ── 2. Default OFF doc ───────────────────────────────────────────────
def test_default_state_off_and_empty(founder_token):
    r = requests.get(HR_URL, headers={"Authorization": f"Bearer {founder_token}"}, timeout=15)
    assert r.status_code == 200, r.text
    doc = r.json()
    assert (doc.get("prompt") or "") == ""
    for k in ("enabled_chat", "enabled_advisor", "enabled_swift",
              "enabled_pro", "enabled_maxx"):
        assert doc.get(k) is False, f"{k} should be False by default"


# ── 3. PUT persists + GET reflects ───────────────────────────────────
def test_put_then_get_round_trip(founder_token):
    payload = {
        "prompt": PROMPT,
        "enabled_chat": True, "enabled_advisor": False,
        "enabled_swift": True, "enabled_pro": False, "enabled_maxx": False,
    }
    r = requests.put(HR_URL, json=payload,
                     headers={"Authorization": f"Bearer {founder_token}"}, timeout=15)
    assert r.status_code == 200, r.text

    g = requests.get(HR_URL, headers={"Authorization": f"Bearer {founder_token}"}, timeout=15).json()
    assert g.get("prompt") == PROMPT
    assert g.get("enabled_chat") is True
    assert g.get("enabled_swift") is True
    assert g.get("enabled_pro") is False
    assert g.get("enabled_advisor") is False


# ── 4. Live chat injection ───────────────────────────────────────────
def _chat_stream_reply(token: str, mode: str, prompt: str,
                       ora_panel: bool = False, timeout: int = 60) -> str:
    """Stream /chat/stream and return the concatenated text payload."""
    body = {
        "prompt": prompt, "message": prompt,
        "mode": mode, "ora_panel": ora_panel,
        "session_id": f"hr-e2e-{int(time.time() * 1000)}",
    }
    headers = {"Authorization": f"Bearer {token}", "Accept": "text/event-stream"}
    out: list[str] = []
    with requests.post(f"{API}/chat/stream", json=body, headers=headers,
                       stream=True, timeout=timeout) as r:
        assert r.status_code == 200, f"chat stream HTTP {r.status_code}"
        for line in r.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if not payload or payload == "[DONE]":
                continue
            try:
                obj = json.loads(payload)
            except Exception:
                continue
            # Token frames may use various shapes — collect any string text.
            for key in ("token", "delta", "content", "text"):
                v = obj.get(key)
                if isinstance(v, str):
                    out.append(v)
            if obj.get("type") == "final" and isinstance(obj.get("message"), str):
                out.append(obj["message"])
    return "".join(out)


def test_swift_mode_sees_house_rule_when_enabled(founder_token):
    # PUT chat+swift ON only (matches test_put_then_get_round_trip state)
    requests.put(HR_URL, json={
        "prompt": PROMPT,
        "enabled_chat": True, "enabled_advisor": False,
        "enabled_swift": True, "enabled_pro": False, "enabled_maxx": False,
    }, headers={"Authorization": f"Bearer {founder_token}"}, timeout=15)
    time.sleep(1.0)  # absorb 30s cache invalidation
    reply = _chat_stream_reply(founder_token, "swift", "What is 2+2?")
    assert MARKER in reply[:200], (
        f"Swift reply should contain {MARKER!r} in first 200 chars; got: {reply[:300]!r}"
    )


def test_pro_mode_does_not_see_house_rule_when_pro_toggle_off(founder_token):
    # Same state as above — pro toggle is OFF.
    reply = _chat_stream_reply(founder_token, "pro", "What is 2+2?")
    assert MARKER not in reply, (
        f"Pro reply MUST NOT contain {MARKER!r} when pro toggle is off; got: {reply[:300]!r}"
    )


def test_advisor_scoping_isolated_from_chat(founder_token):
    # Now: chat ON + maxx ON only, advisor OFF, swift OFF
    requests.put(HR_URL, json={
        "prompt": PROMPT,
        "enabled_chat": True, "enabled_advisor": False,
        "enabled_swift": False, "enabled_pro": False, "enabled_maxx": True,
    }, headers={"Authorization": f"Bearer {founder_token}"}, timeout=15)
    time.sleep(1.0)
    # Swift normal chat → must NOT see rule (swift toggle off)
    r1 = _chat_stream_reply(founder_token, "swift", "What is 2+2?")
    assert MARKER not in r1, f"swift saw rule but swift toggle is OFF: {r1[:200]!r}"


def test_advisor_only_when_advisor_toggle_on(founder_token):
    # advisor only ON
    requests.put(HR_URL, json={
        "prompt": PROMPT,
        "enabled_chat": False, "enabled_advisor": True,
        "enabled_swift": False, "enabled_pro": False, "enabled_maxx": False,
    }, headers={"Authorization": f"Bearer {founder_token}"}, timeout=15)
    time.sleep(1.0)
    # Normal chat (not advisor) must NOT see rule
    r_chat = _chat_stream_reply(founder_token, "swift", "What is 2+2?", ora_panel=False)
    assert MARKER not in r_chat, f"chat saw advisor-only rule: {r_chat[:200]!r}"
    # Advisor path (ora_panel=True) SHOULD see the rule
    r_adv = _chat_stream_reply(founder_token, "swift", "What is 2+2?", ora_panel=True)
    assert MARKER in r_adv[:200], f"advisor missed rule: {r_adv[:300]!r}"
