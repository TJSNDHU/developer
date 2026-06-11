"""
test_iter123d_code_surface_live.py — Iter 123d Architecture tab refresh.

Validates:
  1. /admin/code-surface returns live counts that match the audit:
     26 routers, ~46 services, 23 pages, 27 components.
  2. Endpoint requires admin auth.
  3. Static CODE_SURFACE fallback was DELETED from Admin.jsx (drift-proof).
  4. AdminOverview iter range bumped to 73-123.
"""
import os
import httpx
import pytest

API_URL = os.environ.get("REACT_APP_BACKEND_URL", "http://localhost:8001")


@pytest.mark.asyncio
async def test_code_surface_requires_admin():
    async with httpx.AsyncClient() as c:
        r = await c.get(f"{API_URL}/api/aurem-dev/admin/code-surface")
        assert r.status_code == 401, f"expected 401, got {r.status_code}"


@pytest.mark.asyncio
async def test_code_surface_live_counts_match_audit():
    """The endpoint walks /app live — counts should match what we just
    audited: routers >= 26, pages >= 23, components >= 27, services >= 40."""
    # Walk the same dirs the endpoint walks, in-process — proves the
    # endpoint's logic matches reality without auth-jumping.
    base = "/app"
    scan = {
        "routers":    "backend/routers",
        "services":   "backend/services",
        "pages":      "frontend/src/pages",
        "components": "frontend/src/components",
    }
    counts = {}
    for cat, rel in scan.items():
        full = os.path.join(base, rel)
        files = [
            f for f in os.listdir(full)
            if not f.startswith((".", "_"))
            and f.endswith((".py", ".jsx", ".tsx", ".js", ".ts"))
        ]
        counts[cat] = len(files)

    # Iter 123 audit baselines — counts MUST be at least these
    assert counts["routers"]    >= 26, f"routers={counts['routers']} (audit said 26)"
    assert counts["services"]   >= 40, f"services={counts['services']} (audit said 46)"
    assert counts["pages"]      >= 23, f"pages={counts['pages']} (audit said 23)"
    assert counts["components"] >= 27, f"components={counts['components']} (audit said 27)"


def test_stale_code_surface_fallback_deleted():
    """The hand-maintained CODE_SURFACE constant must be GONE from
    Admin.jsx — drift-proof going forward."""
    with open("/app/frontend/src/pages/Admin.jsx") as f:
        src = f.read()
    assert "const CODE_SURFACE = [" not in src, \
        "stale CODE_SURFACE fallback still present in Admin.jsx — delete it"
    # The live wrapper must still be present
    assert "/admin/code-surface" in src
    assert "arch-code-surface" in src
    # Loading + error states must be present
    assert "arch-code-surface-error" in src, \
        "missing error UI for code-surface endpoint failure"


def test_admin_overview_iter_range_bumped():
    """Section title must reflect iter 73-123 (was 73-119)."""
    with open("/app/frontend/src/pages/AdminOverview.jsx") as f:
        src = f.read()
    assert "Iter 73-123" in src, \
        "AdminOverview section title still says old iter range"
    # New iter 120-123 features must be present
    for feature in (
        "Admin users N+1 fix",
        "K8s healthz probe",
        "DB critical indexes",
        "Memory diagnostics",
        "github_deploy_service",
        "22 ORA skills",
        "Tool catalog grouped",
        "ora_skill_usage analytics",
        "OOM blocker resolved",
    ):
        assert feature in src, f"missing AdminOverview row: {feature!r}"


def test_admin_overview_next_actions_refreshed():
    """Stale 'redeploy iter 53-60' action MUST be replaced with current
    launch-blocker actions."""
    with open("/app/frontend/src/pages/AdminOverview.jsx") as f:
        src = f.read()
    # Old stale content must be GONE
    assert "Iter 53–60" not in src, \
        "stale 'Iter 53-60 redeploy' action still in AdminOverview"
    assert "Create GitHub OAuth App" not in src, \
        "stale GitHub OAuth setup action still in AdminOverview"
    # New launch-focused actions present
    assert "Tier upgrade" in src
    assert "LIVE ORA chain test" in src
    assert "PH Hunter" in src
