"""
Iter 212m-221 — Council reprobe + `_is_dockerfile` NameError fix.

Two contract tests:

1. `_is_dockerfile()` — restored helper that was silently removed
   while two call sites kept referencing it.  Its absence turned
   every scan on a Dockerfile-containing repo into a NameError
   → HTTPException(502) → Cloudflare "Bad gateway" HTML.  Test
   locks the return value contract so a future refactor cannot
   drop it again without a red pytest.

2. Council reprobe endpoint exists in `routers/admin.py` — this is
   the escape hatch a founder uses when the Advisor brief shows
   "Council A: degraded" and they want to force an immediate re-probe
   instead of waiting 60 s for the fast-backoff cadence.
"""

from __future__ import annotations

import pytest


def test_is_dockerfile_exists_and_matches_real_manifests():
    from routers.codebase_health import _is_dockerfile

    # Real Dockerfiles — MUST match.
    assert _is_dockerfile("dockerfile") is True
    assert _is_dockerfile("Dockerfile".lower()) is True
    assert _is_dockerfile("dockerfile.prod") is True
    assert _is_dockerfile("dockerfile.dev") is True
    assert _is_dockerfile("services/api/dockerfile") is True
    assert _is_dockerfile("app/backend/dockerfile.debug") is True

    # Docker Compose manifests — MUST match.
    assert _is_dockerfile("docker-compose.yml")  is True
    assert _is_dockerfile("docker-compose.yaml") is True
    assert _is_dockerfile("compose.yml")         is True
    assert _is_dockerfile("compose.yaml")        is True
    assert _is_dockerfile("infra/docker-compose.yml") is True

    # NOT Dockerfiles — MUST NOT false-positive.
    assert _is_dockerfile("docs/dockerfile-cheatsheet.md") is False
    assert _is_dockerfile("notes/my-dockerfile-tips.txt")  is False
    assert _is_dockerfile("README.md")   is False
    assert _is_dockerfile("app/main.py") is False
    assert _is_dockerfile("")            is False
    assert _is_dockerfile(None or "")    is False


def test_is_dockerfile_is_referenced_by_scan_pipeline():
    """Static assertion — every call site referenced in
    `_build_text_cache` must remain hooked to the restored helper."""
    src = open("/app/backend/routers/codebase_health.py").read()
    # There are two documented call sites (candidate filter + cache
    # re-filter). Keep them both — a regression that drops one would
    # let un-scanned Dockerfiles slip through the scanner. We count
    # only actual call syntax `or _is_dockerfile(lower)` (skips the
    # comment in the helper's docblock).
    assert src.count("or _is_dockerfile(lower)") == 2, (
        "One of the two `or _is_dockerfile(lower)` call sites was "
        "removed — the Dockerfile CIS scanner will silently stop "
        "receiving those files."
    )


def test_council_reprobe_endpoint_registered():
    """The `/admin/council/reprobe` endpoint must exist so a founder
    can force an immediate LongCat re-probe from the Admin UI. Without
    it, a transient OpenRouter blip keeps Council A in "degraded"
    for up to 15 minutes."""
    from routers.admin import router as admin_router
    route_paths = [r.path for r in admin_router.routes]
    assert "/admin/council/reprobe"     in route_paths, (
        "POST /admin/council/reprobe endpoint missing — founder can't "
        "force LongCat re-probe."
    )
    assert "/admin/council-health"      in route_paths, (
        "GET /admin/council-health alias missing — the 20-feature "
        "validation agent tried this path and 404-ed. Alias should "
        "stay to keep tooling green."
    )
    assert "/admin/council/health"      in route_paths, (
        "GET /admin/council/health (canonical) missing."
    )


def test_council_reprobe_is_throttled():
    """Two rapid reprobe calls in <3 s should short-circuit to
    `throttled: true`. Protects OpenRouter budget from a founder
    button-mash."""
    import routers.admin as admin_mod
    src = open(admin_mod.__file__).read()
    # Static: the 3 s guard is in place.
    assert "_COUNCIL_REPROBE_LAST_AT" in src
    assert "< 3.0" in src, (
        "Council reprobe rate-limit guard changed — verify the new "
        "value doesn't allow reprobe spam."
    )


@pytest.mark.asyncio
async def test_probe_treats_429_as_live_not_degraded():
    """Iter 212m-221 — a rate-limit response from OpenRouter (429)
    means the model IS live, we're just throttled.  Council A must
    stay `LIVE`, not flip to `DEGRADED` for the next 15 min."""
    import httpx
    import services.llm as llm_mod

    class _Resp:
        def __init__(self, sc):
            self.status_code = sc
            self.text = ""
        def json(self):
            return {"error": {"message": "rate limited"}}

    class _Client:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            pass
        async def post(self, *a, **kw):
            return _Resp(429)

    # Force the LongCat path.
    orig_enabled = llm_mod.LONGCAT_ENABLED
    orig_live    = llm_mod.LONGCAT_LIVE
    orig_key_fn  = llm_mod._openrouter_key
    orig_client  = httpx.AsyncClient
    try:
        llm_mod.LONGCAT_ENABLED = True
        llm_mod.LONGCAT_LIVE    = True
        llm_mod._openrouter_key = lambda: "sk-test-fake"
        httpx.AsyncClient       = lambda **kw: _Client()

        result = await llm_mod.probe_longcat_availability()
        assert result is True, (
            "429 must be treated as `live=True` — the model is "
            "reachable, we're just throttled."
        )
        assert llm_mod.LONGCAT_LIVE is True
        assert llm_mod._LONGCAT_LAST_PROBE["http_code"] == 429
        assert "rate_limited" in (llm_mod._LONGCAT_LAST_PROBE.get("error") or "")
    finally:
        llm_mod.LONGCAT_ENABLED = orig_enabled
        llm_mod.LONGCAT_LIVE    = orig_live
        llm_mod._openrouter_key = orig_key_fn
        httpx.AsyncClient       = orig_client
