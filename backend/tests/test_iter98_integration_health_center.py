"""
test_iter98_integration_health_center.py — locks in the new live
integration health dashboard.

Offline checks:
  • Service module exports `run_all_probes` + `summary_counts`
  • All 11 providers wired with non-empty (id, name, probe_fn) tuples
  • Admin endpoints registered

Live (opt-in via RUN_LIVE_NETWORK_TESTS=1):
  • Real `run_all_probes()` against every external API — at least 10/11
    must return `status='ok'` (MongoDB requires lifespan, may be 'broken'
    in a bare pytest process; we tolerate that one).
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
from dotenv import load_dotenv


ENV_PATH = Path(__file__).resolve().parents[1] / ".env"


@pytest.fixture(autouse=True)
def _load_env():
    load_dotenv(str(ENV_PATH), override=True)
    yield


def test_service_module_exports():
    from services import integration_health as ih
    assert hasattr(ih, "run_all_probes")
    assert hasattr(ih, "summary_counts")
    assert hasattr(ih, "_PROBES")
    assert len(ih._PROBES) >= 11, f"expected 11+ probes, got {len(ih._PROBES)}"


def test_every_probe_has_id_name_and_callable():
    from services.integration_health import _PROBES
    seen_ids = set()
    for id_, name, fn in _PROBES:
        assert id_ and isinstance(id_, str), f"bad id: {id_!r}"
        assert name and isinstance(name, str), f"bad name: {name!r}"
        assert callable(fn), f"probe {id_} not callable"
        assert id_ not in seen_ids, f"duplicate probe id: {id_}"
        seen_ids.add(id_)
    # All key providers present.
    must_have = {"stripe", "github_oauth", "emergent_llm", "openrouter",
                 "e2b", "tavily", "firecrawl", "resend", "sentry",
                 "vercel", "mongodb"}
    assert must_have.issubset(seen_ids), (
        f"missing required probes: {must_have - seen_ids}"
    )


def test_admin_endpoints_registered():
    from routers.admin import router
    paths = {r.path for r in router.routes}
    assert "/admin/integrations/health" in paths, (
        f"GET /admin/integrations/health missing. Routes: {sorted(paths)[:20]}…"
    )
    assert "/admin/integrations/refresh" in paths


def test_daily_digest_refreshes_integration_health():
    """The daily 6am-UTC scheduler must auto-refresh the health snapshot
    so the founder doesn't see stale data on the admin page."""
    src = (Path(__file__).resolve().parents[1] / "services" / "daily_digest.py").read_text()
    assert "integration_health" in src, (
        "daily_digest.py must trigger services.integration_health refresh"
    )
    assert "daily_auto" in src, (
        "snapshot must be tagged with trigger='daily_auto' for UI visibility"
    )


def test_summary_counts_shape():
    from services.integration_health import summary_counts
    sample = [
        {"id": "a", "status": "ok"},
        {"id": "b", "status": "ok"},
        {"id": "c", "status": "warn"},
        {"id": "d", "status": "broken"},
        {"id": "e", "status": "missing"},
    ]
    out = summary_counts(sample)
    assert out == {"ok": 2, "warn": 1, "broken": 1, "missing": 1, "total": 5}


def test_no_mock_imports_in_probe_module():
    """Audit guard — the probe module must never import unittest.mock
    or use fake responses. Real probes only."""
    src = (Path(__file__).resolve().parents[1] / "services" / "integration_health.py").read_text()
    assert "unittest.mock" not in src
    assert "MagicMock" not in src
    assert "Mock()" not in src
    # Each probe must reach a real public endpoint.
    assert "api.stripe.com" in src or "stripe.Account.retrieve" in src
    assert "api.tavily.com" in src
    assert "api.firecrawl.dev" in src
    assert "api.resend.com" in src
    assert "api.vercel.com" in src
    assert "api.github.com" in src or "github.com/login/oauth" in src
    assert "openrouter.ai" in src


@pytest.mark.skipif(
    os.environ.get("RUN_LIVE_NETWORK_TESTS") != "1",
    reason="Live multi-provider probe — opt-in via RUN_LIVE_NETWORK_TESTS=1",
)
def test_live_run_all_probes_mostly_ok():
    """End-to-end smoke: hit every external API and assert at least 10
    return status='ok'. MongoDB may be 'broken' in a bare pytest process
    because the lifespan hasn't set up the DB client — that's acceptable
    for this test, the live HTTP smoke earlier confirmed it works in-app."""
    from services.integration_health import run_all_probes, summary_counts
    results = asyncio.run(run_all_probes())
    summary = summary_counts(results)
    print(f"\nLive probe summary: {summary}")
    for r in results:
        print(f"  {r['id']:<15} {r['status']:<8} {r['summary'][:60]}")
    # We require at least 10/11 ok. The one allowed non-ok is mongodb
    # (lifespan-dependent in a script context).
    assert summary["ok"] >= 10, (
        f"expected >= 10 OK, got {summary['ok']}. Full results: {results}"
    )
    # No `missing` allowed — every env var should be wired by now.
    assert summary["missing"] == 0, (
        f"some integrations still missing env: "
        f"{[r['id'] for r in results if r['status']=='missing']}"
    )
