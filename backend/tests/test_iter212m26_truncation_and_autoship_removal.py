"""
Iter 212m-26 — Production fixes:

  1. ORA chat reply truncation
     The chat token cap was 1500, causing GLM-5.2 to truncate
     multi-paragraph replies. Raised to 4000 (env override
     `LLM_CHAT_MAX_TOKENS`) and the orchestrator's non-code
     `token_budget` lifted from 1500 → 4000.

  2. "SHIP VIA CTO" button auto-trigger
     `_maybe_ship_shortcut` silently fired a CTO task whenever the
     user typed a short confirmation ("yes", "ok", "fix", "go") after
     an assistant turn with an aurem-handoff fence. This bypassed the
     manual button click. The shortcut + clarify-short-fix helpers
     are GONE. Manual button click in MessageBubble.jsx → ShipDialog
     is now the ONLY ship path. The shell-handoff guard
     (orthogonal, non-shipping) stays.
"""
from __future__ import annotations

import os

ROOT = os.path.join(os.path.dirname(__file__), "..")
LLM_PY   = os.path.join(ROOT, "services", "llm.py")
ORCH_PY  = os.path.join(ROOT, "services", "orchestrator.py")
CHAT_PY  = os.path.join(ROOT, "routers", "chat.py")


# ── 1. Token cap raise ──────────────────────────────────────────────

def test_chat_max_tokens_raised_above_old_1500_cap():
    """MAX_TOKENS['chat'] must default to ≥ 4000 so multi-paragraph
    replies don't truncate. Honors LLM_CHAT_MAX_TOKENS env override."""
    src = open(LLM_PY).read()
    # Single-source default + env-override pattern.
    assert 'LLM_CHAT_MAX_TOKENS' in src
    assert '"chat":    int(os.getenv("LLM_CHAT_MAX_TOKENS", "4000"))' in src
    # The old hard-coded 1500 line is gone.
    assert '"chat":    1500,' not in src


def test_orchestrator_token_budget_raised_for_chat():
    """Orchestrator's non-code path used to set token_budget=1500 which
    re-clamped the chat reply downstream even if MAX_TOKENS was raised.
    Now uses the same env override."""
    src = open(ORCH_PY).read()
    assert 'LLM_CHAT_MAX_TOKENS' in src
    # Old hard-coded line is gone.
    assert "token_budget = 3500 if use_code_model else 1500" not in src
    # New env-driven line.
    assert 'os.getenv("LLM_CHAT_MAX_TOKENS", "4000")' in src


def test_chat_max_tokens_runtime_value_at_least_4000():
    """Actually import llm.py and assert MAX_TOKENS['chat'] is at
    least 4000 under default env (no override set)."""
    import importlib, sys
    # Reload to pick up env-driven values fresh.
    if "services.llm" in sys.modules:
        importlib.reload(sys.modules["services.llm"])
    from services.llm import MAX_TOKENS, cap_for
    assert MAX_TOKENS["chat"] >= 4000, (
        f"chat max_tokens must be ≥ 4000, got {MAX_TOKENS['chat']}"
    )
    assert cap_for("chat") >= 4000


# ── 2. Ship-shortcut / auto-trigger fully removed ────────────────────

def test_maybe_ship_shortcut_function_is_gone():
    """The auto-shipping helper must not exist anywhere in chat.py.
    Only mentions allowed are in NOTE comments documenting the
    removal."""
    src = open(CHAT_PY).read()
    # No live function definition.
    assert "async def _maybe_ship_shortcut" not in src
    # No call site (any occurrence outside a `#` comment line).
    live_refs = [
        ln for ln in src.splitlines()
        if "_maybe_ship_shortcut(" in ln and not ln.lstrip().startswith("#")
    ]
    assert live_refs == [], (
        f"Expected NO live _maybe_ship_shortcut references — found: {live_refs}"
    )
    assert "shipped_via_shortcut = " not in src


def test_ship_confirmations_keyword_set_is_gone():
    """The hard-coded keyword set (yes / ok / fix / ship / go…) that
    triggered auto-ship is removed."""
    src = open(CHAT_PY).read()
    assert "_SHIP_CONFIRMATIONS = {" not in src
    assert "_looks_like_ship_confirmation" not in src
    assert "_normalise_confirmation" not in src


def test_clarify_short_fix_guard_is_gone():
    """The sibling clarify guard depended on the same keyword
    detection and is also gone."""
    src = open(CHAT_PY).read()
    assert "async def _maybe_clarify_short_fix" not in src
    assert "_maybe_clarify_short_fix(" not in src


def test_shell_handoff_guard_still_present():
    """The shell-command handoff guard is orthogonal (non-shipping)
    and must remain so the worker doesn't hang on pip-install briefs."""
    src = open(CHAT_PY).read()
    assert "async def _maybe_guard_shell_handoff_followup" in src
    assert "_maybe_guard_shell_handoff_followup(" in src


def test_handoff_fence_regex_still_present():
    """The shell guard reuses _HANDOFF_FENCE_RE; it must survive."""
    src = open(CHAT_PY).read()
    assert "_HANDOFF_FENCE_RE = re.compile(" in src
    assert "aurem-handoff" in src


def test_messagebubble_still_has_manual_ship_button():
    """The manual ship path on the frontend is the ONLY ship path now.
    `shipViaCTO()` + the ShipDialog onClick handler must still exist."""
    mb_path = os.path.join(
        ROOT, "..", "frontend", "src", "components", "MessageBubble.jsx",
    )
    src = open(mb_path).read()
    # The async ship handler.
    assert "async function shipViaCTO()" in src
    # window.confirm gate — explicit user consent.
    assert "window.confirm(" in src
    # Wired to ShipDialog.
    assert "onShip={shipViaCTO}" in src


def test_no_orphan_auto_ship_imports():
    """Sanity: nothing in chat.py imports from a non-existent
    `_maybe_ship_shortcut` module (defensive against partial
    revert)."""
    src = open(CHAT_PY).read()
    assert "from .ship_shortcut" not in src
    assert "import _maybe_ship_shortcut" not in src
