"""Iter 75/76 — Subscription tiers + Stripe billing routes."""
import pytest


# ── subscription_tiers single source of truth ────────────────────────

def test_free_tier_limits():
    from services.subscription_tiers import get_limit, can_use_feature
    assert get_limit("free", "tasks_per_month") == 10
    assert can_use_feature("free", "maxx_mode") is False
    assert can_use_feature("free", "parallel_agents") is False
    assert can_use_feature("free", "brain_memory") is False


def test_starter_tier():
    from services.subscription_tiers import get_limit, can_use_feature
    assert get_limit("starter", "tasks_per_month") == 50
    assert can_use_feature("starter", "maxx_mode") is False
    assert can_use_feature("starter", "brain_memory") is True
    assert can_use_feature("starter", "parallel_agents") is False


def test_pro_tier_unlimited():
    from services.subscription_tiers import get_limit, can_use_feature
    assert get_limit("pro", "tasks_per_month") is None
    assert can_use_feature("pro", "maxx_mode") is True
    assert can_use_feature("pro", "parallel_agents") is True
    assert can_use_feature("pro", "tasks_per_month") is True  # unlimited == allowed


def test_team_tier_priority():
    from services.subscription_tiers import can_use_feature, get_limit
    assert can_use_feature("team", "priority_queue") is True
    assert get_limit("team", "price_monthly") == 35


def test_founder_mirrors_pro():
    """Founders must not be feature-gated during dogfooding."""
    from services.subscription_tiers import can_use_feature
    assert can_use_feature("founder", "maxx_mode") is True
    assert can_use_feature("founder", "parallel_agents") is True


def test_unknown_tier_defaults_to_free():
    from services.subscription_tiers import get_limit, can_use_feature
    assert get_limit("nonsense_tier", "tasks_per_month") == 10
    assert can_use_feature(None, "maxx_mode") is False


def test_usage_module_uses_subscription_tiers():
    """MONTHLY_TASK_LIMITS must mirror subscription_tiers — no drift."""
    from services.usage import MONTHLY_TASK_LIMITS
    from services.subscription_tiers import TIER_LIMITS, Tier
    for t in (Tier.FREE, Tier.STARTER, Tier.PRO, Tier.TEAM, Tier.FOUNDER):
        assert MONTHLY_TASK_LIMITS[t.value] == TIER_LIMITS[t]["tasks_per_month"]


# ── Stripe routes registration ────────────────────────────────────────

def test_payment_endpoints_registered():
    from routers.payments import router
    paths = {r.path for r in router.routes}
    assert "/payments/checkout" in paths
    assert "/payments/status/{session_id}" in paths
    assert "/payments/webhook" in paths
    assert "/payments/my-plan" in paths
    assert "/payments/portal" in paths
    # Legacy alias preserved so already-configured Stripe dashboards don't 404
    assert "/webhook/stripe" in paths


def test_payments_loads_subscription_tiers():
    import routers.payments as m
    from services.subscription_tiers import Tier, TIER_LIMITS
    # The module imports both — guarantees there's no parallel tier list
    # secretly drifting inside payments.py
    assert m.TIER_LIMITS is TIER_LIMITS
    assert m.Tier is Tier


# ── cto_projects.py feature gates ─────────────────────────────────────

def test_maxx_mode_gated_in_submit_task():
    import os
    src_path = os.path.join(
        os.path.dirname(__file__), "..", "routers", "cto_projects.py",
    )
    with open(src_path, encoding="utf-8") as fh:
        src = fh.read()
    # Maxx-mode gate present
    assert "can_use_feature(me.get(\"tier\"), \"maxx_mode\")" in src
    assert "feature_locked" in src
    # Parallel agents gated in _run_task_via_api
    assert "can_use_feature(user_tier, \"parallel_agents\")" in src
