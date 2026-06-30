"""
Iter 212m-154 — Production-regression fixes (5 issues caught by
iter 212m-153 PROD QA pass).

Covers the BACKEND portion of the batch (Fix #1 + #2):
  • Fix #1 — /admin/insights/activation-funnel cold-start 499 removed
    by switching from in-process TTL cache to Mongo-backed SWR cache.
  • Fix #2 — /hosted-deploy/status/{project_id} returns 200 with a
    `project_found=false` flag instead of HTTP 404 when the project
    doesn't exist (silences the browser console error on /deploy).
  • Helper guard — `_compute_activation_funnel` sort key tolerates
    a mix of `int` and `datetime` `created_at` values that were
    crashing the cold-compute path.

Frontend fixes (#3 /tokens unlimited display, #4 mobile toast
overlap, #5 /bug-hunt authed redirect) are covered by
source-pattern guards below — no DOM testing here.
"""
from pathlib import Path

import pytest

_BACKEND  = Path(__file__).resolve().parent.parent
_FRONTEND = Path(__file__).resolve().parent.parent.parent / "frontend" / "src"


# ── Fix #1 — SWR cache for activation-funnel ────────────────────────

def test_admin_analytics_cache_exports_mongo_swr_cache():
    """The new helper must be importable from the analytics cache
    module — that's the contract `admin.py` relies on."""
    from services import admin_analytics_cache as cache
    assert hasattr(cache, "mongo_swr_cache")
    assert hasattr(cache, "warm_swr_keys")
    # Existing public API must keep working too.
    assert hasattr(cache, "cached_agg")
    assert hasattr(cache, "invalidate")
    assert hasattr(cache, "stats")


def test_activation_funnel_uses_swr_cache():
    """The route must use the new SWR cache helper, not the legacy
    60 s in-process cache that 499s on cold starts."""
    text = (_BACKEND / "routers" / "admin.py").read_text()
    # The new helper is wired into the route.
    assert "mongo_swr_cache(" in text
    # And the activation-funnel key is what it caches.
    # (`admin:activation_funnel:v1` matches the legacy key so the
    # warm doc carries over between deploys.)
    assert "admin:activation_funnel:v1" in text


def test_activation_funnel_sort_key_handles_mixed_types():
    """Pre-existing bug: `recent = sorted(...key=lambda x: x.get('created_at') or 0)`
    crashed when some users had datetime + others had int.  The fix
    normalises both to float epoch via `_ca_epoch`."""
    text = (_BACKEND / "routers" / "admin.py").read_text()
    assert "_ca_epoch" in text
    # And the bare-int fallback is gone.
    assert 'key=lambda x: x.get("created_at") or 0' not in text


# ── Fix #2 — hosted-deploy/status empty-state 200 ───────────────────

def test_hosted_deploy_status_returns_200_for_missing_project():
    text = (_BACKEND / "routers" / "hosted_deploy.py").read_text()
    # Pull just the `status(...)` route body — the disconnect endpoint
    # legitimately still raises 404, so we can't blanket-grep.
    start = text.index('async def status(')
    end   = text.index('async def ', start + 10)
    status_body = text[start:end]
    # The status route no longer 404s on missing projects.
    assert 'raise HTTPException(404' not in status_body, status_body
    # And it returns the graceful empty-state shape.
    assert '"project_found": False' in status_body
    assert '"ok":' in status_body and 'True' in status_body


# ── Fix #3 — Tokens.jsx unlimited display ───────────────────────────

def test_tokens_page_renders_unlimited_for_founder():
    src = (_FRONTEND / "pages" / "Tokens.jsx").read_text()
    assert "is_unlimited" in src
    assert "∞" in src or "Unlimited" in src
    # The unconditional `me?.tokens_remaining ?? "—"` render is gone —
    # we now branch on `is_unlimited` first.
    assert 'value={me?.tokens_remaining ?? "—"}' not in src


# ── Fix #4 — Toast.jsx mobile-safe positioning ──────────────────────

def test_toast_has_mobile_media_query():
    src = (_FRONTEND / "components" / "Toast.jsx").read_text()
    # The toaster element now carries a stable class for the
    # media-query override.
    assert "aurem-toaster" in src
    # And the media query shifts the toaster below the mobile top bar.
    assert "@media (max-width: 480px)" in src
    assert "top: 88px" in src


# ── Fix #5 — BugHunt redirect for authed users ──────────────────────

def test_bughunt_redirects_authed_users():
    src = (_FRONTEND / "pages" / "BugHunt.jsx").read_text()
    # Imports Navigate from react-router-dom + the auth helpers.
    assert "Navigate" in src
    assert "getToken" in src
    assert "getUser" in src
    # And redirects to the authed scan dashboard.
    assert '/codebase-health' in src


# ── Regression smoke — ensure no other admin route uses the old
#    `cached_agg` for the funnel key (mixed-cache would race) ───────

def test_no_mixed_cache_for_funnel_key():
    """The funnel cache key should be set ONLY through the SWR call.
    A docstring mention in the `invalidate` endpoint is allowed (it's
    just documentation, not a write path)."""
    text = (_BACKEND / "routers" / "admin.py").read_text()
    code_lines = [ln for ln in text.splitlines()
                  if "admin:activation_funnel" in ln
                  and not ln.lstrip().startswith(('#', '"""', '"'))]
    # Filter out docstring-style mentions inside a triple-quoted block.
    code_lines = [ln for ln in code_lines if "flushes" not in ln]
    assert len(code_lines) == 1, f"unexpected funnel write sites: {code_lines}"
    # And it lives within ~5 lines of the SWR call.
    idx = text.index(code_lines[0])
    nearby = text[max(0, idx - 400): idx + 50]
    assert "mongo_swr_cache" in nearby
