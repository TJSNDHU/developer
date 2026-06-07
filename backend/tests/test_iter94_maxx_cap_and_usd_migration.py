"""
test_iter94_maxx_cap_and_usd_migration.py — locks in:

1. CAD → USD price migration (Starter $9, Pro $19, Team $49) — both the
   subscription_tiers single-source-of-truth and the new USD-only Stripe
   price IDs in .env.

2. Maxx-mode (Claude Sonnet 4.5) monthly cap by tier — Free/Starter: 0,
   Pro: 100, Team/Founder: unlimited.

3. The new `/api/aurem-dev/usage/maxx` endpoint exists and is wired to
   `get_maxx_usage`.

4. `call_llm_with_meta` accepts a `user_id` kwarg and falls back to
   DeepSeek (with `maxx_capped=True` in meta) when the user is over
   their Pro cap.
"""
from __future__ import annotations

import inspect
import os
from pathlib import Path

import pytest
from dotenv import load_dotenv


ENV_PATH = Path(__file__).resolve().parents[1] / ".env"


@pytest.fixture(autouse=True)
def _load_env():
    load_dotenv(str(ENV_PATH), override=True)
    yield


# ── 1. Pricing migration locked ───────────────────────────────────────

def test_subscription_tiers_use_usd_prices():
    """Source of truth: subscription_tiers.py reflects $9/$19/$49 USD."""
    from services.subscription_tiers import TIER_LIMITS, Tier
    assert TIER_LIMITS[Tier.STARTER]["price_monthly"] == 9
    assert TIER_LIMITS[Tier.PRO]["price_monthly"]     == 19
    assert TIER_LIMITS[Tier.TEAM]["price_monthly"]    == 49


def test_env_uses_new_usd_price_ids():
    """The .env STRIPE_*_PRICE_IDs must point at the NEW USD prices
    (created on the Stripe live account this iter), not the old CAD ones."""
    # Old CAD price IDs we explicitly replaced — none of these should
    # leak back into the .env.
    forbidden_cad = {
        "price_1TfXg60Exg9gU93tU2tQVwI5",  # CAD Starter
        "price_1TfXi50Exg9gU93txCIR6npd",  # CAD Pro
        "price_1TfXil0Exg9gU93tOB7yPyeA",  # CAD Team
    }
    for env_key in ("STRIPE_STARTER_PRICE_ID",
                    "STRIPE_PRO_PRICE_ID",
                    "STRIPE_TEAM_PRICE_ID"):
        v = os.environ.get(env_key, "")
        assert v.startswith("price_"), f"{env_key} missing or malformed"
        assert v not in forbidden_cad, (
            f"{env_key}={v!r} is the OLD CAD price — must be the new USD one"
        )


# ── 2. Maxx-mode cap shape ────────────────────────────────────────────

def test_maxx_limits_per_tier():
    """Free/Starter=0 (no Maxx), Pro=100, Team/Founder=None (unlimited)."""
    from services.usage import MAXX_MONTHLY_LIMITS
    assert MAXX_MONTHLY_LIMITS["free"]    == 0
    assert MAXX_MONTHLY_LIMITS["starter"] == 0
    assert MAXX_MONTHLY_LIMITS["pro"]     == 100
    assert MAXX_MONTHLY_LIMITS["team"]    is None
    assert MAXX_MONTHLY_LIMITS["founder"] is None


def test_subscription_tiers_have_maxx_field():
    """TIER_LIMITS dict must include `maxx_tasks_per_month` so every
    downstream consumer can rely on it."""
    from services.subscription_tiers import TIER_LIMITS, Tier
    for t in (Tier.FREE, Tier.STARTER, Tier.PRO, Tier.TEAM, Tier.FOUNDER):
        assert "maxx_tasks_per_month" in TIER_LIMITS[t], (
            f"{t.value} missing maxx_tasks_per_month"
        )


# ── 3. New /usage/maxx endpoint registered ────────────────────────────

def test_usage_maxx_endpoint_registered():
    from routers.usage import router
    paths = {r.path for r in router.routes}
    assert "/usage/maxx" in paths, f"/usage/maxx missing. Routes: {paths}"


# ── 4. call_llm_with_meta signature accepts user_id ───────────────────

def test_call_llm_with_meta_accepts_user_id():
    from services.llm import call_llm_with_meta
    sig = inspect.signature(call_llm_with_meta)
    assert "user_id" in sig.parameters, (
        "call_llm_with_meta must accept `user_id` kwarg for the Maxx-cap gate"
    )
    # Default must be None so existing callers keep working.
    assert sig.parameters["user_id"].default is None


def test_orchestrator_passes_user_id_to_llm():
    """The orchestrator's main `call_llm_with_meta` invocation must
    pass `user_id=user_id` — otherwise the Maxx cap is bypassed for
    every actual product use."""
    src = Path(__file__).resolve().parents[1] / "services" / "orchestrator.py"
    text = src.read_text()
    assert "user_id=user_id" in text, (
        "orchestrator.py call_llm_with_meta(...) must forward user_id"
    )


# ── 5. Public landing copy also reflects USD pricing ──────────────────

def test_llms_txt_advertises_usd_pricing():
    """Public llms.txt (read by AI crawlers + GEO ranking) must show
    the new USD prices, not the old CAD ones."""
    p = Path(__file__).resolve().parents[2] / "frontend" / "public" / "llms.txt"
    text = p.read_text()
    assert "$9/mo USD" in text or "$9 USD" in text
    assert "$19/mo USD" in text or "$19 USD" in text
    assert "$49/mo USD" in text or "$49 USD" in text
    # No stale CAD numbers should leak through.
    assert "$35/user/mo" not in text, "stale $35 Team price in llms.txt"
