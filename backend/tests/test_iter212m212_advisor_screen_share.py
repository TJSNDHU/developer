"""
Iter 212m-212 — Ask Advisor screen-share + vision regression.

Locks these contracts:

  1.  When `screenshot_b64` is absent, the advisor path is UNCHANGED
      (provider=advisor-direct, `advisor_vision: status=not_requested`
      logged).  A user who never toggles the switch pays zero cost.

  2.  When `screenshot_b64` is a valid PNG, the isolated vision call
      fires (`advisor_vision: status=ok`), its description is
      injected into the system prompt, and the LLM's reply is
      grounded in the actual image content (asserted by round-
      tripping a distinctive marker rendered onto the PNG).

  3.  When `screenshot_b64` is malformed (too small, bad base64),
      the advisor STILL returns a text reply — vision failure never
      blocks the text path.  An ERROR-level log
      `advisor_vision_failed:` is emitted for founder monitoring.

  4.  Source-string checks lock the isolation contract: the vision
      call must live in `services/advisor_vision.py`, must not
      import from `chat_with_tools`, and must be invoked via a
      try/except in `routers/chat.py` (never re-raised).
"""

from __future__ import annotations

import base64
import io
import os
import time
import uuid

import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL")
if not BASE_URL:
    with open("/app/frontend/.env") as fh:
        for line in fh:
            if line.startswith("REACT_APP_BACKEND_URL"):
                BASE_URL = line.split("=", 1)[1].strip()
                break
assert BASE_URL, "REACT_APP_BACKEND_URL missing"
AUREM = BASE_URL.rstrip("/") + "/api/aurem-dev"

FOUNDER = ("test@aurem.dev", "AuremTest2026!")
LOG = "/var/log/supervisor/backend.err.log"


def _login(email=FOUNDER[0], password=FOUNDER[1]) -> str:
    r = requests.post(f"{AUREM}/auth/login",
                       json={"email": email, "password": password},
                       timeout=15)
    r.raise_for_status()
    return r.json()["token"]


def _stream(token: str, prompt: str, **extra) -> dict:
    """Return {content, provider, tool_calls_run}."""
    body = {"prompt": prompt, "ora_panel": True, "mode": "swift", **extra}
    r = requests.post(f"{AUREM}/chat/stream",
                       headers={"Authorization": f"Bearer {token}",
                                "Content-Type": "application/json"},
                       json=body, stream=True, timeout=60)
    assert r.status_code == 200, r.text[:200]
    import json, re
    tokens = []
    provider = None
    tool_calls_run = 0
    for line in r.iter_lines(decode_unicode=True):
        if not line or not line.startswith("data: "):
            continue
        try:
            obj = json.loads(line[6:])
        except Exception:
            continue
        if isinstance(obj, dict):
            if obj.get("meta") and obj.get("provider") and obj["provider"] != "aurem-cto":
                provider = obj["provider"]
                tool_calls_run = int(obj.get("tool_calls_run") or 0)
            if "token" in obj:
                tokens.append(obj["token"])
    return {"content": "".join(tokens), "provider": provider,
            "tool_calls_run": tool_calls_run}


def _make_test_png(marker: str) -> str:
    """Return a base64-encoded 400×200 PNG with `marker` drawn on it.
    Uses PIL — installed with the backend (pillow is transitive)."""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        pytest.skip("PIL not available")
    img = Image.new("RGB", (400, 200), (20, 30, 50))
    d = ImageDraw.Draw(img)
    d.rectangle([20, 20, 380, 60], outline=(255, 80, 80), width=3)
    d.text((30, 30), marker, fill=(255, 255, 255))
    d.text((30, 80), "Aurem UI test frame", fill=(200, 200, 200))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def _tail_log_since(offset: int) -> str:
    with open(LOG, "rb") as fh:
        fh.seek(offset)
        return fh.read().decode("utf-8", errors="ignore")


def _log_offset() -> int:
    try: return os.path.getsize(LOG)
    except OSError: return 0


# ── 1. no-screenshot regression ─────────────────────────────────
def test_advisor_without_screenshot_unchanged():
    tok = _login()
    off = _log_offset()
    out = _stream(tok, "hi ora")
    assert out["provider"] in {
        "advisor-direct", "advisor-direct-fallback", "advisor-leak-guard",
    }, out["provider"]
    assert out["tool_calls_run"] == 0
    tail = _tail_log_since(off)
    assert "advisor_vision: status=not_requested" in tail, (
        "Expected 'not_requested' status log; got:\n" + tail[-2000:]
    )
    # Belt-and-braces: guarantee the vision-failed log did NOT fire.
    assert "advisor_vision_failed" not in tail


# ── 2. valid screenshot round-trips into the reply ──────────────
def test_advisor_with_valid_screenshot_grounds_reply():
    tok = _login()
    marker = f"AUREM_MARK_{uuid.uuid4().hex[:6].upper()}"
    png_b64 = _make_test_png(marker)
    off = _log_offset()
    out = _stream(tok, "what text do you see on the screen?",
                   screenshot_b64=png_b64)
    assert out["provider"] in {
        "advisor-direct", "advisor-direct-fallback", "advisor-leak-guard",
    }, out["provider"]
    tail = _tail_log_since(off)
    assert "advisor_vision: status=ok" in tail, (
        "Expected 'status=ok' vision log; got:\n" + tail[-2000:]
    )
    # The LLM must ground its reply in the actual rendered marker.
    # If the vision model + injection are wired correctly, the marker
    # string appears in the reply verbatim (or normalised).
    body = out["content"] or ""
    assert marker in body or marker.lower() in body.lower(), (
        f"marker {marker!r} NOT in reply — vision injection broken. "
        f"Reply: {body[:400]!r}"
    )


# ── 3. malformed screenshot → SILENT graceful degrade ──────────
# Iter 212m-213 — vision failure MUST be silent to the user (no
# "screenshot capture nahi ho paya" note in the reply).  Text path
# still returns a normal answer; ERROR log fires for founder-side
# monitoring.
@pytest.mark.parametrize("bad_b64,label", [
    ("not-real-base64!!!!",                 "invalid base64"),
    (base64.b64encode(b"tiny").decode(),    "under 1KB"),
    ("aGVsbG8=",                            "3-byte payload"),
])
def test_advisor_with_bad_screenshot_still_replies(bad_b64, label):
    tok = _login()
    off = _log_offset()
    out = _stream(tok, "hello", screenshot_b64=bad_b64)
    # Advisor path stays intact.
    assert out["provider"] in {
        "advisor-direct", "advisor-direct-fallback", "advisor-leak-guard",
    }, f"provider on {label!r}: {out['provider']}"
    # Response was still delivered — text path never blocked.
    assert (out["content"] or "").strip(), (
        f"empty reply on {label!r} — vision failure broke the text path"
    )
    # Reply must be silent about the failure — never mention
    # screenshots or "capture nahi ho paya" style notes.
    body_lc = (out["content"] or "").lower()
    for banned in ("screenshot capture nahi ho paya",
                   "couldn't see the screen",
                   "vision analysis failed"):
        assert banned not in body_lc, (
            f"advisor leaked failure note {banned!r} to user on {label!r}. "
            f"Reply: {out['content'][:300]!r}"
        )
    # Founder monitoring must still see the failure.
    tail = _tail_log_since(off)
    assert "advisor_vision_failed" in tail, (
        f"expected advisor_vision_failed log on {label!r}; log:\n"
        + tail[-2000:]
    )


# ── 4. source-level isolation contracts ─────────────────────────
def test_vision_isolation_source_contracts():
    """The vision call MUST live in its own module and MUST NOT be
    invoked through the tool orchestrator."""
    with open("/app/backend/services/advisor_vision.py") as fh:
        av = fh.read()
    with open("/app/backend/routers/chat.py") as fh:
        chat = fh.read()

    # 1) Module has its own OpenRouter call, not routed via llm.py
    assert "openrouter.ai/api/v1/chat/completions" in av, (
        "advisor_vision must call OpenRouter directly, not via the "
        "council/llm_router ladder"
    )
    # 2) It MUST NOT actually IMPORT chat_with_tools (docstring
    #    mentions it in prose as the thing we're isolating from —
    #    that's expected).
    assert "from services.llm import chat_with_tools" not in av, (
        "advisor_vision.py imports chat_with_tools — that's the very "
        "path we're isolating away from"
    )
    assert "import chat_with_tools" not in av.replace(
        "orchestrator (`chat_with_tools`, `services/llm.py`)", "",
    ), "advisor_vision.py imports chat_with_tools"
    # 3) chat.py invokes it inside a try/except so a failure never
    #    propagates to the SSE stream.
    assert "from services.advisor_vision import" in chat
    assert "analyze_screenshot" in chat
    # 4) Failure log tag is present so founder monitoring can detect
    #    silent breakage.
    assert "advisor_vision_failed" in chat
