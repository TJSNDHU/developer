"""Iter 110 — two production bugs from user transcript.

Bug A: Decision Council fired when user attached a dashboard screenshot
       and typed "fix". The OCR'd screenshot text contained phrases that
       coincidentally matched the council triggers ("stuck", "rules
       disabled", etc.) — the user actually wanted a code fix.

Bug B: Vision OCR returned 400 on some screenshots because the pinned
       Gemini model rejects certain images. Production ended up returning
       the "vision unavailable" placeholder → ORA said "I cannot see the
       screenshot". Fixed by adding a fallback model chain.
"""
import re
from services.mode_b_council import (
    is_council_request,
    _strip_attachment_blocks,
    _COUNCIL_RE,
)


# ── Bug A: attachment blocks must be stripped before council detect ──
def test_pure_user_text_with_council_trigger_still_fires():
    assert is_council_request("I'm stuck on a major decision", "B") is True
    assert is_council_request("run the council on whether to pivot", "B") is True


def test_message_with_only_attachment_block_does_not_fire_council():
    """User typed nothing, just dragged a dashboard screenshot in. The
    OCR'd content mentions 'stuck queue' but user didn't ask for council."""
    msg = (
        "[🖼️ dashboard.png · 245.3 KB → 1.2 KB]\n\n"
        "Dashboard shows: leads pool empty, rules disabled, "
        "user appears stuck on this dashboard for hours.\n\n"
        "fix"  # user actually typed "fix"
    )
    # "fix" alone is NOT a council trigger; the OCR text should NOT count
    assert is_council_request(msg, "B") is False


def test_attachment_block_stripped_correctly():
    msg = (
        "[🖼️ shot.png · 250 KB]\n"
        "Visual description: looks like a stuck decision diagram.\n"
        "Extracted text: pivot or persevere?\n\n"
        "what should I do here?"
    )
    cleaned = _strip_attachment_blocks(msg)
    # The OCR body and header are gone; only user's last sentence remains
    assert "Visual description" not in cleaned
    assert "pivot or persevere" not in cleaned
    assert "what should I do" in cleaned


def test_project_context_prefix_stripped():
    msg = "[Working on project: foo — repo a/b@main]\n\nfix the bug"
    cleaned = _strip_attachment_blocks(msg)
    assert "Working on project" not in cleaned
    assert cleaned.strip() == "fix the bug"


def test_council_fires_even_with_attachment_if_user_explicitly_asks():
    """If the user attaches a file AND types a council-trigger phrase,
    the council should STILL fire — the attachment didn't accidentally
    trigger it; the user's intent did."""
    msg = (
        "[🖼️ chart.png · 100 KB]\n"
        "Visual description: revenue chart.\n\n"
        "run the council — should I pivot or persevere?"
    )
    assert is_council_request(msg, "B") is True


def test_council_NOT_fired_if_mode_not_B():
    assert is_council_request("run the council", "A") is False
    assert is_council_request("run the council", "D") is False
    assert is_council_request("run the council", "F") is False


def test_council_NOT_fired_on_empty_or_whitespace_only_user_text():
    msg = "[🖼️ s.png · 100 KB]\nText extracted: stuck pivot decision council\n"
    # After stripping attachment, the user typed NOTHING. Council must not fire.
    cleaned = _strip_attachment_blocks(msg)
    assert cleaned.strip() == ""
    assert is_council_request(msg, "B") is False


# ── Bug B: vision fallback model ──────────────────────────────────
def test_vision_module_has_primary_and_fallback_models():
    from routers import upload as up
    assert hasattr(up, "_PRIMARY_VISION_MODEL")
    assert hasattr(up, "_FALLBACK_VISION_MODEL")
    # Fallback must be a different model than primary, otherwise no point
    assert up._PRIMARY_VISION_MODEL != up._FALLBACK_VISION_MODEL


def test_vision_fallback_default_is_a_known_vision_model():
    from routers import upload as up
    # Reasonable defaults — must be an OpenRouter-format slug "<vendor>/<model>"
    assert "/" in up._FALLBACK_VISION_MODEL


def test_describe_image_uses_fallback_when_primary_returns_empty(monkeypatch):
    """Mock OpenRouter: primary returns empty content, fallback returns
    a useful description. The helper should return the fallback's text."""
    import asyncio
    from routers import upload as up
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    calls = []
    class MockResp:
        def __init__(self, status, body):
            self.status_code = status
            self._body = body
            self.text = str(body)
        def json(self):
            return self._body

    class MockClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def post(self, url, headers=None, json=None):
            model = json["model"]
            calls.append(model)
            # Primary returns 400 — empty content
            if model == up._PRIMARY_VISION_MODEL:
                return MockResp(400, {"error": "bad image"})
            # Fallback returns real description
            return MockResp(200, {"choices": [{"message": {
                "content": "**Visual description**\nA test image showing a red square.\n"
            }}]})

    monkeypatch.setattr(up.httpx, "AsyncClient", MockClient)
    raw = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
    out = asyncio.run(up._describe_image_via_vision(raw, "image/png", "x.png"))
    assert "Visual description" in out
    assert "red square" in out
    # Confirm fallback was actually tried
    assert up._FALLBACK_VISION_MODEL in calls


def test_describe_image_returns_primary_when_primary_succeeds(monkeypatch):
    """When primary works, fallback should NOT be called (save API spend)."""
    import asyncio
    from routers import upload as up
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    calls = []

    class MockResp:
        def __init__(self, body):
            self.status_code = 200
            self._body = body
            self.text = str(body)
        def json(self):
            return self._body

    class MockClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def post(self, url, headers=None, json=None):
            calls.append(json["model"])
            return MockResp({"choices": [{"message": {"content": "from primary OK"}}]})

    monkeypatch.setattr(up.httpx, "AsyncClient", MockClient)
    raw = b"\x89PNG" + b"\x00" * 100
    out = asyncio.run(up._describe_image_via_vision(raw, "image/png", "x.png"))
    assert "primary" in out
    assert calls == [up._PRIMARY_VISION_MODEL]  # fallback never tried
