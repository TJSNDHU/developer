"""
Iter 212m-24 — Admin House Rules backend tests.

Admin can set a single global "house rules" prompt that ORA reads
FIRST, with green/red toggles per target (chat, advisor) and per
chat mode (swift, pro, maxx). Verifies:
  - GET/PUT /admin/house-rules endpoints exist and require admin.
  - The service helpers respect the toggles (off → "", on → prompt).
  - Live injection into chat.py prepends a clearly-marked block at
    the top of extra_sys, with HIGHEST priority wording.
"""
from __future__ import annotations

import os
import asyncio
import pytest

CHAT_PY = os.path.join(
    os.path.dirname(__file__), "..", "routers", "chat.py"
)
ADMIN_PY = os.path.join(
    os.path.dirname(__file__), "..", "routers", "admin.py"
)
SVC_PY = os.path.join(
    os.path.dirname(__file__), "..", "services", "house_rules.py"
)


# ── 1. service module exports the expected API ────────────────────────

def test_service_exports_full_api():
    from services import house_rules as hr
    for name in (
        "get_house_rules_doc", "set_house_rules_doc",
        "get_active_house_rules", "format_house_rules_block",
    ):
        assert hasattr(hr, name), f"missing export {name}"
        assert callable(getattr(hr, name)), f"{name} must be callable"


def test_format_house_rules_block_has_highest_priority_header():
    from services.house_rules import format_house_rules_block
    out = format_house_rules_block("be polite")
    assert "ADMIN HOUSE RULES" in out
    assert "HIGHEST PRIORITY" in out
    assert "be polite" in out
    # Empty input → empty output (defence-in-depth).
    assert format_house_rules_block("") == ""
    assert format_house_rules_block("   ") == ""


def test_get_active_house_rules_off_when_no_db(monkeypatch):
    """When DB is unreachable, the helper degrades to "" (OFF) — chat
    must keep working even if Mongo is down."""
    from services import house_rules as hr
    monkeypatch.setattr(hr, "get_db", lambda: None)
    hr._invalidate_cache()
    out = asyncio.run(hr.get_active_house_rules("chat", "swift"))
    assert out == ""


def test_get_active_house_rules_respects_toggles(monkeypatch):
    """When all toggles are GREEN + prompt has text, returns prompt.
    Flip any required toggle to RED → returns ""."""
    from services import house_rules as hr

    fake_doc = {
        "_id": "singleton", "prompt": "BE TERSE",
        "enabled_chat": True, "enabled_advisor": True,
        "enabled_swift": True, "enabled_pro": True, "enabled_maxx": True,
        "updated_at": None, "updated_by": None, "_source": "test",
    }

    async def fake_get():
        return dict(fake_doc)

    monkeypatch.setattr(hr, "get_house_rules_doc", fake_get)

    # All on → prompt returned for every chat mode + advisor.
    assert asyncio.run(hr.get_active_house_rules("chat", "swift")) == "BE TERSE"
    assert asyncio.run(hr.get_active_house_rules("chat", "pro"))   == "BE TERSE"
    assert asyncio.run(hr.get_active_house_rules("chat", "maxx"))  == "BE TERSE"
    assert asyncio.run(hr.get_active_house_rules("advisor", None)) == "BE TERSE"

    # Flip swift OFF → swift returns "", others still return prompt.
    fake_doc["enabled_swift"] = False
    assert asyncio.run(hr.get_active_house_rules("chat", "swift")) == ""
    assert asyncio.run(hr.get_active_house_rules("chat", "pro"))   == "BE TERSE"

    # Flip chat target OFF → all chat modes return ""; advisor stays.
    fake_doc["enabled_chat"] = False
    fake_doc["enabled_swift"] = True
    assert asyncio.run(hr.get_active_house_rules("chat", "swift")) == ""
    assert asyncio.run(hr.get_active_house_rules("advisor", None)) == "BE TERSE"

    # Flip advisor OFF → advisor also returns "".
    fake_doc["enabled_advisor"] = False
    assert asyncio.run(hr.get_active_house_rules("advisor", None)) == ""


def test_get_active_house_rules_unknown_target_returns_empty(monkeypatch):
    from services import house_rules as hr

    async def fake_get():
        return {
            "_id": "singleton", "prompt": "X",
            "enabled_chat": True, "enabled_advisor": True,
            "enabled_swift": True, "enabled_pro": True, "enabled_maxx": True,
        }
    monkeypatch.setattr(hr, "get_house_rules_doc", fake_get)
    assert asyncio.run(hr.get_active_house_rules("unknown", "swift")) == ""


def test_empty_prompt_short_circuits(monkeypatch):
    """Even if every toggle is green, an empty prompt yields ""."""
    from services import house_rules as hr

    async def fake_get():
        return {
            "_id": "singleton", "prompt": "   ",  # whitespace only
            "enabled_chat": True, "enabled_advisor": True,
            "enabled_swift": True, "enabled_pro": True, "enabled_maxx": True,
        }
    monkeypatch.setattr(hr, "get_house_rules_doc", fake_get)
    assert asyncio.run(hr.get_active_house_rules("chat", "swift")) == ""


# ── 2. admin router exposes the endpoints behind _require_admin ───────

def test_admin_router_has_house_rules_routes():
    src = open(ADMIN_PY).read()
    # GET handler
    assert "@router.get(\"/house-rules\")" in src
    # PUT handler
    assert "@router.put(\"/house-rules\")" in src
    # Both must go through the admin guard.
    block_start = src.find("@router.get(\"/house-rules\")")
    block_end = src.find("@router.put(\"/house-rules\")") + 800
    block = src[block_start:block_end]
    assert "_require_admin(authorization)" in block
    assert block.count("_require_admin(authorization)") >= 2


def test_admin_router_uses_pydantic_payload():
    """The PUT endpoint must validate the body with a pydantic model
    so toggles arrive as bools, not arbitrary truthy values."""
    src = open(ADMIN_PY).read()
    assert "class HouseRulesPayload(BaseModel):" in src
    for field in ("prompt:", "enabled_chat:", "enabled_advisor:",
                  "enabled_swift:", "enabled_pro:", "enabled_maxx:"):
        assert field in src


# ── 3. chat.py injects the block at the TOP of extra_sys ──────────────

def test_chat_send_injects_house_rules_at_top():
    src = open(CHAT_PY).read()
    # The injection block is tagged with the iter marker.
    assert "Iter 212m-24" in src
    assert "from services.house_rules import" in src
    # The block must call get_active_house_rules with ("chat", mode) and
    # prepend (not append) via format_house_rules_block.
    assert 'get_active_house_rules(\n            "chat"' in src \
        or 'get_active_house_rules("chat"' in src
    assert "format_house_rules_block(_hr_prompt)" in src


def test_chat_stream_injects_house_rules_for_main_and_advisor():
    src = open(CHAT_PY).read()
    # Stream path injects for `target="chat"` when ora_panel is False.
    assert 'if not body.ora_panel:' in src
    # And re-resolves for `target="advisor"` when ora_panel is True.
    assert 'get_active_house_rules("advisor", None)' in src


def test_house_rules_block_uses_prepend_not_append():
    """The rules MUST land BEFORE the rest of extra_sys, otherwise
    they're not "read first" — the whole point of the feature."""
    src = open(CHAT_PY).read()
    # The format helper output goes on the LEFT side of the concatenation.
    assert "format_house_rules_block(_hr_prompt)\n                + (\"\\n\\n\" + extra_sys" in src
