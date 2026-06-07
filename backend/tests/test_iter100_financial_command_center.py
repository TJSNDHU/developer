"""
test_iter100_financial_command_center.py — locks in the live financial
calculator + admin UI.

Offline:
  • services/financials exports required functions
  • Pricing constants match the founder-shared reference rates
  • Per-tier margin math is correct (Starter $9 should produce ~$8 profit)
  • Admin endpoints registered
  • Frontend page wired in App.jsx + sidebar
  • AdminFinancials.jsx renders the right metric cards & inputs

Live (opt-in via RUN_LIVE_NETWORK_TESTS=1):
  • FX rate fetched from frankfurter.app (USD→CAD reasonable range)
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
from dotenv import load_dotenv


ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
SRC = Path(__file__).resolve().parents[2] / "frontend" / "src"


@pytest.fixture(autouse=True)
def _load_env():
    load_dotenv(str(ENV_PATH), override=True)
    yield


def test_service_module_exports():
    from services import financials as f
    for name in ("PRICING_USD", "TIER_PROFILES", "cost_per_task",
                 "cost_per_user", "tier_margins", "stripe_fee",
                 "get_settings", "save_settings", "compute_financials",
                 "get_usd_cad_rate"):
        assert hasattr(f, name), f"financials.{name} missing"


def test_pricing_constants_match_founder_reference():
    """The numbers the founder verified on June 2026."""
    from services.financials import PRICING_USD
    assert PRICING_USD["deepseek_in"]   == 0.20
    assert PRICING_USD["deepseek_out"]  == 0.80
    assert PRICING_USD["claude_in"]     == 3.00
    assert PRICING_USD["claude_out"]    == 15.00
    assert PRICING_USD["tavily_per"]    == 0.008
    assert PRICING_USD["firecrawl_per"] == 0.0008
    assert PRICING_USD["e2b_per_hr"]    == 0.05
    assert PRICING_USD["stripe_pct"]    == 0.029
    assert PRICING_USD["stripe_flat"]   == 0.30


def test_cost_per_task_standard_and_maxx():
    from services.financials import cost_per_task
    standard = cost_per_task(0.0)
    maxx     = cost_per_task(1.0)
    # Reference (founder-shared screenshot): standard ~$0.009, Maxx $0.120
    assert 0.005 <= standard <= 0.012, f"standard task cost {standard}"
    # Claude maxx: 15k*3/M + 5k*15/M = 0.045 + 0.075 = 0.120
    assert round(maxx, 3) == 0.120
    # 30% Maxx mix (Pro tier) should land near $0.041
    pro = cost_per_task(0.30)
    assert 0.035 <= pro <= 0.050, f"Pro task cost {pro}"


def test_tier_margins_starter_profitable():
    """Starter must be profitable; Free must be a loss-leader; Team
    must beat Pro on per-user gross profit."""
    from services.financials import tier_margins
    rows = {r["tier"]: r for r in tier_margins()}
    assert rows["free"]["gross_profit"]    < 1, "Free shouldn't be profitable"
    assert rows["starter"]["gross_profit"] > 5, "Starter under-priced!"
    assert rows["pro"]["gross_profit"]     > 10, "Pro under-priced (post-Maxx-cap)"
    assert rows["team"]["gross_profit"]    > rows["pro"]["gross_profit"], (
        "Team should produce higher per-user gross profit than Pro"
    )


def test_admin_endpoints_registered():
    from routers.admin import router
    paths = {r.path for r in router.routes}
    assert "/admin/financials" in paths, (
        f"GET /admin/financials missing. Routes: {sorted(paths)[:30]}"
    )
    assert "/admin/financials/settings" in paths


def test_frontend_route_and_sidebar_link():
    app = (SRC / "App.jsx").read_text()
    assert "AdminFinancials" in app, "App.jsx must import AdminFinancials"
    assert 'path="/admin/financials"' in app, "/admin/financials route missing"
    overview = (SRC / "pages" / "AdminOverview.jsx").read_text()
    assert 'data-testid="goto-financials"' in overview, (
        "AdminOverview must surface the financials link as the primary CTA"
    )


def test_financials_page_contains_required_metric_cards():
    """Every metric the founder asked for must be in the React page.
    MetricCard/NumberInput render the data-testid via template-literal
    interpolation so we grep for the `testid="…"` prop value."""
    page = (SRC / "pages" / "AdminFinancials.jsx").read_text()
    must_have_testid_props = [
        'testid="mrr"',           # MRR card
        'testid="net-profit"',
        'testid="gross-margin"',
        'testid="ai-cost"',
        'testid="total-burn"',
        'testid="runway"',
        'testid="cac"',
        'testid="break-even"',
        'testid="cash-bank"',
        'testid="dev-salary"',
        'testid="free-users"',
        'testid="starter-users"',
        'testid="pro-users"',
        'testid="team-users"',
    ]
    for needle in must_have_testid_props:
        assert needle in page, f"AdminFinancials.jsx missing {needle}"
    # And the section-level static testids
    for needle in ('data-testid="cost-per-task"', 'data-testid="fixed-costs"',
                   'data-testid="tier-margins"', 'data-testid="pnl-roadmap"',
                   'data-testid="admin-financials-page"'):
        assert needle in page, f"AdminFinancials.jsx missing {needle}"


def test_no_mock_in_financials_service():
    """Audit guard — every number must come from real pricing constants
    or real DB queries, never a hardcoded sample."""
    src = (Path(__file__).resolve().parents[1] / "services" / "financials.py").read_text()
    assert "unittest.mock" not in src
    assert "MagicMock" not in src
    assert "Mock()" not in src
    # Must reference the real DB collections.
    for col in ("dev_users", "cto_payments", "cto_maxx_usage",
                "financial_settings"):
        assert col in src, f"financials.py must query {col}"


@pytest.mark.skipif(
    os.environ.get("RUN_LIVE_NETWORK_TESTS") != "1",
    reason="Live FX fetch — opt-in via RUN_LIVE_NETWORK_TESTS=1",
)
def test_live_fx_rate_in_reasonable_range():
    """USD→CAD historically 1.20-1.50. If we get something wild,
    something's wrong with the FX source."""
    from services.financials import get_usd_cad_rate
    res = asyncio.run(get_usd_cad_rate())
    assert 1.10 < res["rate"] < 1.60, (
        f"USD→CAD = {res['rate']} ({res['source']}) outside sane band"
    )
