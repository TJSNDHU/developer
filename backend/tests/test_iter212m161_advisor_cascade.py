"""
Iter 212m-161 — Ask Advisor cascade contract tests.

Verifies:
  • TEMPERATURE["advisor"] and MAX_TOKENS["advisor"] are defined in
    services/llm.py (no more hard-coded 0.2 / 2500 in routers/chat.py).
  • cap_for("advisor") + temperature_for("advisor") return the configured
    values.
  • routers/chat.py source no longer contains the literal
    `max_tokens=2500` for the advisor primary (moved to config maps).
  • routers/chat.py source no longer contains the literal
    `temperature=0.2` for the advisor primary (moved to config maps).
  • Advisor cascade wires Groq → DeepSeek in the source (no Claude rescue).
  • Self-rescue guards exist: when primary IS Groq, no Groq-rescue line;
    when primary IS DeepSeek, no DeepSeek-rescue line.
"""

import pathlib
import sys

BACKEND = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


def test_advisor_max_tokens_in_config_map():
    import importlib
    import services.llm as llm
    importlib.reload(llm)
    assert "advisor" in llm.MAX_TOKENS
    assert llm.MAX_TOKENS["advisor"] == 2500
    assert llm.cap_for("advisor") == 2500


def test_advisor_temperature_in_config_map():
    import importlib
    import services.llm as llm
    importlib.reload(llm)
    assert "advisor" in llm.TEMPERATURE
    assert llm.TEMPERATURE["advisor"] == 0.2
    assert llm.temperature_for("advisor") == 0.2


def test_advisor_no_hardcoded_max_tokens_2500_in_chat_router():
    """The literal `max_tokens=2500` must no longer appear in the
    advisor block — every call must read from `cap_for('advisor')`."""
    src = pathlib.Path("/app/backend/routers/chat.py").read_text()
    # Slice the advisor block so we don't get false positives from
    # other parts of the file (which may legitimately use 2500
    # elsewhere — defensive).
    start = src.find("Ask Advisor multi-model cascade")
    end   = src.find("activity[\"label\"] = \"thinking…\"", start)
    assert start != -1 and end != -1, "advisor cascade block not found"
    advisor_block = src[start:end]
    assert "max_tokens=2500" not in advisor_block, (
        "advisor block must use cap_for('advisor') instead of hardcoded 2500"
    )
    assert "temperature=0.2" not in advisor_block, (
        "advisor block must use temperature_for('advisor') instead of hardcoded 0.2"
    )


def test_advisor_block_imports_cap_and_temperature_helpers():
    src = pathlib.Path("/app/backend/routers/chat.py").read_text()
    # The new imports must be present in the advisor block.
    assert "cap_for, temperature_for" in src
    assert "_adv_max_tokens  = cap_for(\"advisor\")" in src
    assert "_adv_temperature = temperature_for(\"advisor\")" in src


def test_advisor_cascade_uses_groq_then_deepseek_no_claude_rescue():
    """The cascade must wire Groq (free) FIRST, then DeepSeek V3
    (cheap).  Claude must NOT appear as a rescue step (too expensive)."""
    src = pathlib.Path("/app/backend/routers/chat.py").read_text()
    start = src.find("Ask Advisor multi-model cascade")
    end   = src.find("activity[\"label\"] = \"thinking…\"", start)
    block = src[start:end]
    # Groq rescue must be wired
    assert "groq rescue" in block.lower() or "Groq rescue" in block
    assert "_call_groq(" in block
    # DeepSeek last-resort must be wired
    assert "DeepSeek rescue" in block or "deepseek rescue" in block
    assert "_call_deepseek(" in block
    # Order: Groq must appear before DeepSeek
    g_idx = block.find("_call_groq(")
    d_idx = block.find("_call_deepseek(", g_idx)
    assert g_idx < d_idx, "Groq must be wired BEFORE DeepSeek in the cascade"
    # Claude must NOT be used as a rescue tag
    assert "claude-rescue" not in block
    assert "claude-sonnet-rescue" not in block


def test_advisor_self_rescue_guards():
    """When the admin-selected primary IS Groq, the Groq-rescue step
    must skip (no self-rescue).  Same for DeepSeek as primary."""
    src = pathlib.Path("/app/backend/routers/chat.py").read_text()
    start = src.find("Ask Advisor multi-model cascade")
    end   = src.find("activity[\"label\"] = \"thinking…\"", start)
    block = src[start:end]
    # Guard against self-rescue for Groq
    assert "_adv_llm != \"groq-llama-3.3-70b\"" in block
    # Guard against self-rescue for DeepSeek (either flavour)
    assert "_adv_llm not in (" in block
    assert "deepseek-chat" in block and "deepseek-direct" in block


def test_advisor_fallback_chain_field_populated():
    """The result emitted must include `fallback_chain` as a real list
    (not just `[provider_tag]`), so the SSE meta frame and Langfuse
    span surface the actual cascade walked at runtime."""
    src = pathlib.Path("/app/backend/routers/chat.py").read_text()
    start = src.find("Ask Advisor multi-model cascade")
    end   = src.find("activity[\"label\"] = \"thinking…\"", start)
    block = src[start:end]
    # The result dict must use `_adv_chain` for fallback_chain
    assert "\"fallback_chain\":  _adv_chain" in block
    # _adv_chain must collect rescue providers
    assert "_adv_chain.append" in block


def test_advisor_provider_tag_marks_rescue():
    """Provider/model tags must distinguish a primary-success from a
    rescue-success so dashboards can compute rescue rate per primary."""
    src = pathlib.Path("/app/backend/routers/chat.py").read_text()
    start = src.find("Ask Advisor multi-model cascade")
    end   = src.find("activity[\"label\"] = \"thinking…\"", start)
    block = src[start:end]
    assert "groq-llama-3.3-70b-rescue" in block
    assert "deepseek-v3-rescue" in block
