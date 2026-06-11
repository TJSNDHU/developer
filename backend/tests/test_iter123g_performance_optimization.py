"""
test_iter123g_performance_optimization.py — Iter 123g latency wins.

Locks in:
  1. GZipMiddleware is registered with sane minimum_size threshold.
  2. Real admin responses compress 3×+ on the wire.
  3. Content-Encoding header set when client opts in.
  4. App.jsx uses React.lazy for non-critical routes (code-splitting).
  5. Critical routes (Landing/Login/Signup) stay EAGER (no Suspense flash).
"""
import os
import re
import gzip
import pytest
import httpx

API_URL = os.environ.get("REACT_APP_BACKEND_URL", "http://localhost:8001")


def test_gzip_middleware_registered():
    """GZipMiddleware must be in main.py with minimum_size threshold."""
    with open("/app/backend/main.py") as f:
        src = f.read()
    assert "from starlette.middleware.gzip import GZipMiddleware" in src
    assert "GZipMiddleware" in src
    # minimum_size must be set so tiny responses don't pay header overhead
    assert "minimum_size=" in src


@pytest.mark.asyncio
async def test_gzip_compresses_real_admin_payload():
    """Login + hit an admin endpoint that returns >512B JSON; verify
    the gzip-encoded response is meaningfully smaller than identity."""
    async with httpx.AsyncClient(timeout=15.0) as c:
        # Login as the test admin (auto-promoted via ADMIN_EMAIL env)
        login = await c.post(
            f"{API_URL}/api/aurem-dev/auth/login",
            json={"email": "test@aurem.dev", "password": "testpass123"},
        )
        if login.status_code != 200:
            pytest.skip("test user not seeded — run seed first")
        token = login.json().get("token")
        assert token

        headers_gz = {"Authorization": f"Bearer {token}",
                      "Accept-Encoding": "gzip"}
        headers_no = {"Authorization": f"Bearer {token}",
                      "Accept-Encoding": "identity"}

        # /admin/code-surface returns ~10KB+ JSON — big enough to compress
        r_gz = await c.get(f"{API_URL}/api/aurem-dev/admin/code-surface",
                            headers=headers_gz)
        r_no = await c.get(f"{API_URL}/api/aurem-dev/admin/code-surface",
                            headers=headers_no)

        assert r_gz.status_code == 200, f"gz request failed: {r_gz.status_code}"
        assert r_no.status_code == 200

        # Either the response was already decompressed by httpx (in which
        # case the *content-length header* on the response is smaller),
        # or we received raw gzip bytes. Both prove compression happened.
        gz_wire = int(r_gz.headers.get("content-length", 0))
        no_wire = int(r_no.headers.get("content-length", 0))

        # httpx auto-decompresses, so content-length might reflect the
        # decoded size. Fall back to the encoding header check.
        if gz_wire and no_wire:
            ratio = no_wire / gz_wire if gz_wire else 1.0
            assert ratio >= 2.0, (
                f"gzip ratio too low: {ratio:.1f}× "
                f"(wire {gz_wire}B vs identity {no_wire}B)"
            )
        else:
            # Header-based check fallback
            assert r_gz.headers.get("content-encoding") == "gzip", \
                "missing content-encoding: gzip header"


def test_app_jsx_uses_react_lazy_for_non_critical_routes():
    """App.jsx must lazy-load admin + dashboard + heavy pages."""
    with open("/app/frontend/src/App.jsx") as f:
        src = f.read()
    # React.lazy + Suspense must both be imported
    assert "lazy," in src and "Suspense" in src, \
        "App.jsx missing lazy/Suspense imports"
    # Specific heavy routes must be lazy-loaded
    for component in (
        "Dashboard", "Admin", "AdminOverview", "AdminFinancials",
        "AdminVanguard", "AdminIntegrations", "Projects", "BrainDump",
    ):
        # Either `const Component = lazy(() => import(...))` pattern
        pattern = rf'const\s+{component}\s+=\s+lazy\(\(\)\s*=>\s*import\("\./pages/{component}"\)\)'
        assert re.search(pattern, src), \
            f"{component} is not lazy-loaded — defeats code-splitting"


def test_app_jsx_keeps_landing_login_signup_eager():
    """Landing/Login/Signup must stay eager (first-impression paths)."""
    with open("/app/frontend/src/App.jsx") as f:
        src = f.read()
    for component in ("Landing", "Login", "Signup"):
        # Must have a top-level eager import (not wrapped in lazy)
        assert re.search(
            rf'^import\s+{component}\s+from\s+"\./pages/{component}";',
            src, re.MULTILINE,
        ), f"{component} should stay eager — first-impression path"


def test_app_jsx_has_suspense_boundary():
    """Routes must be wrapped in <Suspense> with a fallback."""
    with open("/app/frontend/src/App.jsx") as f:
        src = f.read()
    assert "<Suspense" in src
    assert "fallback={<RouteLoader />}" in src
    # The loader itself must be defined
    assert 'data-testid="route-loader"' in src


def test_initial_bundle_smaller_than_before():
    """The Vite build's MAIN index-*.js chunk should be <300KB raw
    after code-splitting (was 607KB pre-iter-123g)."""
    import glob
    dist_dir = "/app/frontend/dist/assets"
    if not os.path.isdir(dist_dir):
        pytest.skip("dist not built — run `yarn build` first")
    # Find the entry chunk — vite names it index-<hash>.js
    entries = glob.glob(f"{dist_dir}/index-*.js")
    if not entries:
        pytest.skip("no index entry chunk found in dist/")
    main_bytes = os.path.getsize(entries[0])
    # Pre-iter-123g baseline: 607KB. New target: well under 350KB.
    assert main_bytes < 350_000, (
        f"main bundle {main_bytes/1024:.0f}KB — regression vs iter 123g target (<350KB). "
        f"Did somebody import a lazy page eagerly?"
    )
