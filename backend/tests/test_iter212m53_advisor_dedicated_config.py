"""Iter 212m-53 — Ask Advisor dedicated rules + LLM selector.

These tests cover schema contracts (no DB, no LLM, no network) so
they run in <100 ms and lock the public surface that the admin UI
depends on. Live behaviour is verified by curl smoke at deploy time.
"""
from __future__ import annotations

import pathlib
import re


HR_PY     = pathlib.Path(__file__).resolve().parent.parent / "services" / "house_rules.py"
ADMIN_PY  = pathlib.Path(__file__).resolve().parent.parent / "routers"  / "admin.py"
CHAT_PY   = pathlib.Path(__file__).resolve().parent.parent / "routers"  / "chat.py"


def test_advisor_fields_present_in_singleton_default() -> None:
    """The default house_rules doc must carry the three new fields
    so the admin GET endpoint always returns them, even on a cold DB."""
    src = HR_PY.read_text()
    assert '"advisor_prompt":' in src
    assert '"advisor_prompt_enabled":' in src
    assert '"advisor_llm":' in src


def test_llm_choices_constant_has_all_five_models() -> None:
    """Spec from founder: 'all LLMs we are already using'.
       That's OR primary (GLM, Claude, DeepSeek-OR), DeepSeek direct,
       and Groq. Five entries — no more, no less, until we add a
       new LLM provider."""
    src = HR_PY.read_text()
    # The constant must exist and list each id literally.
    m = re.search(r"ADVISOR_LLM_CHOICES.*?=\s*\[(.*?)\n\]", src, re.S)
    assert m, "ADVISOR_LLM_CHOICES constant missing"
    block = m.group(1)
    for needed in [
        '"glm-5.2"', '"claude-sonnet-4.5"', '"deepseek-chat"',
        '"deepseek-direct"', '"groq-llama-3.3-70b"',
    ]:
        assert needed in block, f"missing LLM id {needed}"


def test_invalid_llm_clamps_to_glm_default() -> None:
    """`_valid_advisor_llm` must reject unknown ids so the admin UI
    can't poison the field with a typo / future-deprecated slug."""
    src = HR_PY.read_text()
    # Function exists.
    assert "def _valid_advisor_llm(" in src
    # Default fallback must be the spec-pinned glm-5.2.
    assert 'return value if value in valid_ids else "glm-5.2"' in src


def test_admin_endpoint_exposes_llm_choices() -> None:
    """The GET /admin/house-rules response must include the choice
    list so the admin UI doesn't have to hard-code model slugs."""
    src = ADMIN_PY.read_text()
    assert "advisor_llm_choices" in src
    assert "from services.house_rules import get_house_rules_doc, ADVISOR_LLM_CHOICES" in src


def test_admin_put_accepts_new_payload_fields() -> None:
    """`HouseRulesPayload` must whitelist the three new fields so
    Pydantic doesn't strip them silently."""
    src = ADMIN_PY.read_text()
    m = re.search(r"class HouseRulesPayload\(BaseModel\):.*?\n\n", src, re.S)
    assert m, "HouseRulesPayload model missing"
    block = m.group(0)
    assert "advisor_prompt:" in block
    assert "advisor_prompt_enabled:" in block
    assert "advisor_llm:" in block


def test_chat_router_dispatches_on_admin_llm_choice() -> None:
    """The Ask Advisor branch in chat.py MUST honour the admin's
    LLM selection (5 branches: glm / claude / deepseek-chat /
    deepseek-direct / groq) — not always call _call_glm."""
    src = CHAT_PY.read_text()
    # Find the Ask Advisor branch (agent='ora').
    assert "Iter 212m-53 — Ask Advisor dedicated config" in src
    # All five LLM branches must be present.
    for branch in [
        'if _adv_llm == "claude-sonnet-4.5":',
        'elif _adv_llm == "deepseek-chat":',
        'elif _adv_llm == "deepseek-direct":',
        'elif _adv_llm == "groq-llama-3.3-70b":',
        '# "glm-5.2" (default) or any unrecognised value',
    ]:
        assert branch in src, f"missing branch: {branch}"


def test_chat_router_injects_dedicated_advisor_prompt() -> None:
    """The admin-set advisor prompt must be prepended to the system
    prompt FIRST (highest priority), independent of the combined
    house rules block."""
    src = CHAT_PY.read_text()
    assert "get_active_advisor_prompt" in src
    assert "get_active_advisor_llm" in src
    # The advisor header must come BEFORE extra_sys + ORA_PANEL_TONE
    # so admin rules truly override persona.
    m = re.search(
        r"_adv_header\s*=\s*\(\s*format_house_rules_block\(_adv_prompt\)",
        src,
    )
    assert m, "advisor header injection block missing"


def test_helper_functions_added_to_house_rules_service() -> None:
    """Two new helpers must exist so chat.py can read the admin
    config without re-implementing the validation logic."""
    src = HR_PY.read_text()
    assert "async def get_active_advisor_prompt() -> str:" in src
    assert "async def get_active_advisor_llm() -> str:" in src


def test_advisor_prompt_max_length_enforced() -> None:
    """`set_house_rules_doc` must trim the advisor prompt to MAX_PROMPT_LEN
    so a malicious admin can't blow up the system prompt and OOM the LLM."""
    src = HR_PY.read_text()
    # The trimming line for advisor_prompt must exist.
    assert "advisor_prompt = advisor_prompt[:_MAX_PROMPT_LEN]" in src
