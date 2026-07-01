"""
tests/test_iter212m172_five_fixes_and_timeout.py

Iter 212m-172 batch:
  1. dev_skills.py — 7 tools use `_repo_ctx_from(ctx)` (lazy, no _resolve_project)
  2. Vanguard verify — Claude → DeepSeek rescue model fallback
  3. smart_router.py — Claude/DeepSeek IDs imported from services.llm
  4. FeatureWindow — Swift label uses council_a_primary_model()
  5. SidebarBound — mobile bottom-sheet UserDropdown
  6. Loop awaiting_confirmation auto-expiry sweeper
  7. main.py lifespan — auto-install ruff + eslint at runtime
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, "/app/backend")

BACKEND_ROOT = Path("/app/backend")


# ────────────────────────────────────────────────────────────
# FIX 1 — dev_skills tools use _repo_ctx_from(ctx)
# ────────────────────────────────────────────────────────────

def test_dev_skills_no_direct_resolve_project_in_tools():
    """The 7 ORA tools MUST not call _resolve_project directly inside
    their body — they must go through _repo_ctx_from(ctx) lazily.

    The shim `async def _resolve_project` at the top of the file IS
    allowed (delegates to local_tools for backward compat), but no
    tool body should call it.
    """
    src = (BACKEND_ROOT / "services/dev_skills.py").read_text()
    lines = src.splitlines()
    # Only the module-level shim (near top of file) is allowed to
    # mention `_resolve_project`.
    hit_indexes = [
        i for i, ln in enumerate(lines, 1)
        if "_resolve_project" in ln
    ]
    # Anything past line 65 (past the shim + delegate) is a violation.
    violations = [i for i in hit_indexes if i > 65]
    assert not violations, (
        f"dev_skills.py still calls _resolve_project inside tool bodies at "
        f"lines {violations} — should use _repo_ctx_from(ctx) instead."
    )


def test_dev_skills_uses_lazy_repo_ctx_from_import():
    """Every tool that needs a repo must lazy-import _repo_ctx_from
    inside the function (not at module top) to avoid circular import.
    Expect ≥ 7 occurrences of the lazy import pattern.
    """
    src = (BACKEND_ROOT / "services/dev_skills.py").read_text()
    n = src.count(
        "from .local_tools import _repo_ctx_from as _lt_repo_ctx_from, "
        "_NO_BIN_CTX_ERROR as _lt_no_ctx"
    )
    assert n >= 7, f"expected ≥7 lazy imports; found {n}"


def test_dev_skills_module_imports_no_circular_dependency():
    """The module must import cleanly with no circular import errors."""
    if "services.dev_skills" in sys.modules:
        del sys.modules["services.dev_skills"]
    import importlib
    mod = importlib.import_module("services.dev_skills")
    assert hasattr(mod, "find_usages")
    assert hasattr(mod, "get_dependencies")
    assert hasattr(mod, "get_env_vars")
    assert hasattr(mod, "detect_framework")
    assert hasattr(mod, "get_commit_history")
    assert hasattr(mod, "list_issues")
    assert hasattr(mod, "get_pr_comments")


@pytest.mark.asyncio
async def test_dev_skills_tools_hard_block_without_bin_ctx():
    """When ctx has no bin_ctx (Home surface), every repo tool must
    hard-refuse cleanly with a `no_bin_ctx` shaped error — never
    silently fall back to a DB lookup."""
    from services import dev_skills as ds
    ctx = {"user_id": "u1", "project_id": None, "bin_ctx": None}
    args = {"symbol": "foo", "max": 5}
    r = await ds.find_usages(ctx, args)
    assert r.get("ok") is False
    # Either the sentinel error_class or a "no repo" message must fire.
    err_class = r.get("error_class", "")
    msg = (r.get("error") or "").lower()
    assert err_class == "no_bin_ctx" or "no project" in msg or "no repo" in msg


# ────────────────────────────────────────────────────────────
# FIX 2 — Vanguard verify agent has rescue model
# ────────────────────────────────────────────────────────────

def test_vanguard_rescue_model_constant_exists():
    src = (BACKEND_ROOT / "services/vanguard_verify_agent.py").read_text()
    assert "_VERIFY_RESCUE_MODEL" in src
    assert "VANGUARD_VERIFY_RESCUE_MODEL" in src
    assert "deepseek/deepseek-chat" in src


def test_vanguard_rescue_fallback_path_present():
    """When primary Claude call raises/empty, code path retries with
    the rescue model. Verify the source contains the try/except
    fallback pattern with primary_error tracking."""
    src = (BACKEND_ROOT / "services/vanguard_verify_agent.py").read_text()
    assert "primary_error" in src
    assert "_VERIFY_RESCUE_MODEL" in src
    # Rescue path calls model= with the rescue constant.
    assert "model=_VERIFY_RESCUE_MODEL" in src


# ────────────────────────────────────────────────────────────
# FIX 3 — smart_router imports Claude/DeepSeek IDs from services.llm
# ────────────────────────────────────────────────────────────

def test_smart_router_imports_from_llm_module():
    src = (BACKEND_ROOT / "services/smart_router.py").read_text()
    assert "from .llm import _CLAUDE_MODEL as _LLM_CLAUDE_MODEL" in src
    assert "from .llm import _deepseek_model as _llm_deepseek_model" in src


def test_smart_router_maxx_and_security_use_llm_claude():
    """maxx_code + security must default to services.llm._CLAUDE_MODEL
    so smart_router never drifts from Parliament V2 source of truth."""
    if "services.smart_router" in sys.modules:
        del sys.modules["services.smart_router"]
    import importlib
    sr = importlib.import_module("services.smart_router")
    from services.llm import _CLAUDE_MODEL, _deepseek_model
    # env override with the same value is fine — both should equal the
    # source-of-truth constant unless AUREM_MODEL_* was set.
    assert sr.MODELS["maxx_code"] == os.getenv("AUREM_MODEL_MAXX_CODE", _CLAUDE_MODEL)
    assert sr.MODELS["security"]  == os.getenv("AUREM_MODEL_SECURITY", _CLAUDE_MODEL)
    assert sr.MODELS["fallback"]  == os.getenv("AUREM_MODEL_FALLBACK", _deepseek_model())


def test_smart_router_get_model_delegates_correctly():
    if "services.smart_router" in sys.modules:
        del sys.modules["services.smart_router"]
    import importlib
    sr = importlib.import_module("services.smart_router")
    # maxx_code and security both point at the same Claude ID by default.
    assert sr.get_model("code", "maxx").startswith("anthropic/") or \
           sr.get_model("code", "maxx") == sr.MODELS["maxx_code"]
    assert sr.get_model("security") == sr.MODELS["security"]


# ────────────────────────────────────────────────────────────
# FIX 4 — FeatureWindow uses council_a_primary_model()
# ────────────────────────────────────────────────────────────

def test_feature_window_uses_council_a_primary_model():
    src = (BACKEND_ROOT / "routers/feature_window.py").read_text()
    assert "council_a_primary_model" in src
    assert "swift_model_id" in src
    assert "swift_model_label" in src


def test_feature_window_hardcoded_glm_label_removed():
    """The old hardcoded 'z-ai/glm-5.2 via OpenRouter' string must be
    replaced with the dynamic label."""
    src = (BACKEND_ROOT / "routers/feature_window.py").read_text()
    # The dynamic label is now `swift_model_label` — verify it's used
    # inside the Swift row.
    swift_row_idx = src.find('"Swift"')
    assert swift_row_idx != -1
    # Look for the dynamic label reference near the Swift row.
    row_slice = src[swift_row_idx: swift_row_idx + 200]
    assert "swift_model_label" in row_slice, (
        f"Swift row still has a hardcoded model label:\n{row_slice}"
    )


def test_health_endpoint_exposes_longcat_flags():
    src = (BACKEND_ROOT / "main.py").read_text()
    assert "council_a_primary_model" in src
    assert "longcat_live" in src
    assert "longcat_enabled" in src


# ────────────────────────────────────────────────────────────
# FIX 5 — SidebarBound mobile bottom-sheet UserDropdown
# ────────────────────────────────────────────────────────────

def test_sidebar_bound_mobile_bottom_sheet_variant():
    src = (Path("/app/frontend/src/components/dashboard/v2/SidebarBound.jsx")).read_text()
    # Mobile prop threaded through UserDropdown + used in bottom-sheet branch.
    assert "isMobile" in src
    assert "ds2-user-sheet" in src
    assert "ds2-user-sheet-backdrop" in src
    assert "ds2-user-settings-mobile" in src
    assert "ds2-user-recharge-mobile" in src
    assert "ds2-user-logout-mobile" in src


def test_dashboard_passes_isMobile_to_sidebar():
    src = (Path("/app/frontend/src/pages/Dashboard.jsx")).read_text()
    # Dashboard.jsx now passes isMobile down through SidebarReal → SidebarV2Bound.
    assert "isMobile={isMobile}" in src


# ────────────────────────────────────────────────────────────
# FIX 6 — Loop awaiting_confirmation timeout sweeper
# ────────────────────────────────────────────────────────────

def test_loop_engine_has_expired_state():
    from services.loop_engine import LoopState
    assert LoopState.EXPIRED.value == "expired"


def test_loop_engine_has_awaiting_confirm_max_constant():
    from services import loop_engine
    assert hasattr(loop_engine, "AWAITING_CONFIRM_MAX_S")
    # Default 10 min unless env-overridden.
    assert loop_engine.AWAITING_CONFIRM_MAX_S >= 60


def test_loop_engine_has_sweep_function():
    from services import loop_engine
    assert hasattr(loop_engine, "sweep_expired_awaiting_confirmations")


class _StubDB:
    """Minimal Motor-shaped stub for the sweeper.

    `loop_sessions.find(...)` returns an async iterator over the docs
    matching the provided filter.  Updates go into an update log we can
    assert against.
    """
    def __init__(self, docs):
        self._docs = docs
        self._updates: list[dict] = []
        self.loop_sessions = self

    def find(self, filt):
        # match every doc whose state is in the filter set + updated_at < cutoff
        state_in = filt["state"]["$in"]
        cutoff = filt["updated_at"]["$lt"]
        matching = [
            d for d in self._docs
            if d["state"] in state_in and d["updated_at"] < cutoff
        ]
        async def _iter():
            for d in matching:
                yield d
        return _iter()

    async def update_one(self, filt, update):
        self._updates.append({"filt": filt, "update": update})
        for d in self._docs:
            if d.get("loop_id") == filt.get("loop_id"):
                d.update(update["$set"])


@pytest.mark.asyncio
async def test_sweeper_expires_stale_awaiting_confirmations():
    from services.loop_engine import (
        AWAITING_CONFIRM_MAX_S, LoopState,
        sweep_expired_awaiting_confirmations,
    )
    now = datetime.now(timezone.utc)
    old = now - timedelta(seconds=AWAITING_CONFIRM_MAX_S + 60)
    docs = [
        {"loop_id": "l_old_await",
         "state": LoopState.AWAITING_CONFIRMATION.value,
         "updated_at": old, "user_id": "u1", "project_id": "p1"},
        {"loop_id": "l_old_paused",
         "state": LoopState.PAUSED_FOR_USER.value,
         "updated_at": old, "user_id": "u2", "project_id": "p2"},
        {"loop_id": "l_fresh",
         "state": LoopState.AWAITING_CONFIRMATION.value,
         "updated_at": now, "user_id": "u3", "project_id": "p3"},
        # Non-paused state must be ignored.
        {"loop_id": "l_active",
         "state": LoopState.EXECUTING.value,
         "updated_at": old, "user_id": "u4", "project_id": "p4"},
    ]
    db = _StubDB(docs)

    # release_loop_lock imports lazily — patch it out.
    with patch("services.loop_safety.release_loop_lock",
               new=AsyncMock(return_value=True)):
        n = await sweep_expired_awaiting_confirmations(db)

    assert n == 2, f"expected 2 expired sessions, got {n}"
    # State transitions land in DB.
    expired_ids = {u["filt"]["loop_id"] for u in db._updates}
    assert expired_ids == {"l_old_await", "l_old_paused"}
    for u in db._updates:
        assert u["update"]["$set"]["state"] == "expired"
        assert u["update"]["$set"]["resume_reason"] == "awaiting_confirmation_timeout"


@pytest.mark.asyncio
async def test_sweeper_no_ops_when_nothing_expired():
    from services.loop_engine import (
        LoopState, sweep_expired_awaiting_confirmations,
    )
    now = datetime.now(timezone.utc)
    docs = [
        {"loop_id": "l1", "state": LoopState.AWAITING_CONFIRMATION.value,
         "updated_at": now, "user_id": "u1", "project_id": "p1"},
    ]
    db = _StubDB(docs)
    n = await sweep_expired_awaiting_confirmations(db)
    assert n == 0
    assert db._updates == []


# ────────────────────────────────────────────────────────────
# FIX 7 — main.py linter auto-install hook
# ────────────────────────────────────────────────────────────

def test_main_py_installs_linters_at_boot():
    """Linter probe must actually attempt to install ruff + eslint on
    the pod, not just warn about them (previous P0 blocker on PROD)."""
    src = (BACKEND_ROOT / "main.py").read_text()
    assert 'subprocess.run' in src or "subprocess" in src
    # Both binaries covered.
    assert "pip" in src and "install" in src
    assert "ruff" in src
    # eslint installed via npm.
    assert "eslint" in src
    # No longer JUST a warning — actual install code present.
    assert 'subprocess.run,\n                        ["pip"' in src or \
           'subprocess.run,\n                        ["npm"' in src or \
           'install", "--quiet"' in src
