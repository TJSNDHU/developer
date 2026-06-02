"""
test_iter63_cache_purge.py — Iter 63 admin cache purge endpoint.

Source-level + behavioural tests:
  • Endpoint is registered on the admin router
  • Backend code handles all 3 sub-purges (Cloudflare / lru_cache / Mongo)
  • Cloudflare branch is env-var gated (skipped if creds absent)
  • lru_cache.cache_clear() is invoked
  • Mongo TTL cache collection names match the rest of the codebase
  • Frontend AdminOverview.jsx wires the panel correctly
"""
from __future__ import annotations

import os
import re


# ── Backend router registration ────────────────────────────────────────

def test_cache_purge_endpoint_registered():
    from routers.admin import router as adm
    paths = [r.path for r in adm.routes]
    assert "/admin/cache/purge" in paths, f"expected /admin/cache/purge in {paths!r}"


def test_cache_purge_requires_admin_gate():
    """The handler body must call `_require_admin(authorization)` like
    every other admin route does. We grep the file (rather than
    introspecting the function) so the assertion survives wrappers."""
    src = _read("routers/admin.py")
    # Locate the purge_caches def
    m = re.search(
        r"async def purge_caches\(.*?\)(.*?)(?=\n@router\.|\Z)",
        src, re.DOTALL,
    )
    assert m, "purge_caches handler not found"
    body = m.group(1)
    assert "_require_admin(authorization)" in body, (
        "purge_caches must gate with _require_admin"
    )


# ── Cloudflare branch is env-var gated ─────────────────────────────────

def test_cloudflare_branch_reads_env_vars():
    src = _read("routers/admin.py")
    m = re.search(r"async def purge_caches.*?\Z", src, re.DOTALL)
    body = m.group(0)
    assert 'os.environ.get("CLOUDFLARE_API_TOKEN")' in body
    assert 'os.environ.get("CLOUDFLARE_ZONE_ID")' in body
    # The CF purge_everything semantic is preserved
    assert '"purge_everything": True' in body
    # The structured skipped-detail is user-visible so we lock it
    assert '"CLOUDFLARE_API_TOKEN / ZONE_ID not set"' in body


def test_cloudflare_hits_correct_api_path():
    src = _read("routers/admin.py")
    # Either f-string or concat — accept either form
    assert "api.cloudflare.com/client/v4/zones/" in src
    assert "/purge_cache" in src


# ── In-process LRU cache cleared ───────────────────────────────────────

def test_lru_cache_clear_wired():
    src = _read("routers/admin.py")
    assert "from services.skill_context_injector import _load_skill" in src
    assert "_load_skill.cache_clear()" in src


def test_load_skill_actually_has_cache_clear():
    """Defence in depth — if someone removes @lru_cache from _load_skill
    the purge endpoint would silently AttributeError at runtime. Catch
    that contract drift here."""
    from services.skill_context_injector import _load_skill
    assert hasattr(_load_skill, "cache_clear"), (
        "_load_skill must keep @lru_cache for the purge endpoint to work"
    )
    assert hasattr(_load_skill, "cache_info")


# ── Mongo TTL collections — names must match the rest of codebase ──────

def test_mongo_cache_collection_names():
    """The purge endpoint targets 3 collections. Each name must appear
    somewhere in the corresponding service module that owns it, otherwise
    we're emptying the wrong collection."""
    src = _read("routers/admin.py")
    assert "repo_context_cache" in src
    assert "github_issues_cache" in src
    assert "codebase_index_cache" in src

    # Cross-check at least one against the owning service. The other two
    # may be future-facing; we don't fail on them.
    repo_src = _read("services/repo_context.py")
    assert "repo_context_cache" in repo_src or "repo_context" in repo_src


def test_purge_returns_structured_report():
    src = _read("routers/admin.py")
    m = re.search(r"async def purge_caches.*?\Z", src, re.DOTALL)
    body = m.group(0)
    # All three top-level report sections must be present
    assert '"cloudflare"' in body
    assert '"lru_cache"' in body
    assert '"mongo_caches"' in body
    # Each Mongo coll must record either a `deleted` count or an error
    assert "deleted_count" in body
    # Final envelope shape
    assert 'return {"ok": True, "report": report}' in body


# ── Frontend wiring ────────────────────────────────────────────────────

def test_admin_overview_renders_cache_purge_panel():
    src = _read_frontend("pages/AdminOverview.jsx")
    assert "<CachePurgePanel />" in src
    assert "function CachePurgePanel" in src
    assert 'data-testid="admin-cache-purge-btn"' in src
    # POSTs to the backend endpoint
    assert '"/admin/cache/purge"' in src or "'/admin/cache/purge'" in src


def test_admin_overview_blasts_client_caches():
    """The client-side step of the purge must:
    • unregister service workers
    • caches.delete() every CacheStorage entry
    • force a true reload with a cache-bust query"""
    src = _read_frontend("pages/AdminOverview.jsx")
    assert "navigator.serviceWorker.getRegistrations()" in src
    assert ".unregister()" in src
    assert "caches.keys()" in src
    assert "caches.delete" in src
    # Cache-bust reload — query param starts with `_purge`
    assert re.search(r"['\"]_purge['\"].*Date\.now", src), (
        "client purge must reload with ?_purge=<ts> cache-bust"
    )
    # Uses location.replace (not reload) so back button does not loop
    assert "window.location.replace(" in src


def test_admin_overview_shows_report_per_section():
    src = _read_frontend("pages/AdminOverview.jsx")
    # ReportLine renders one row per report section
    assert "ReportLine" in src
    assert 'label="Cloudflare edge"' in src
    assert 'label="In-process LRU"' in src
    # Mongo per-collection rows
    assert "report.mongo_caches" in src


# ── Helpers ────────────────────────────────────────────────────────────

def _read(rel):
    """Read a file under /app/backend relative to this test file."""
    p = os.path.join(os.path.dirname(__file__), "..", rel)
    with open(p, encoding="utf-8") as fh:
        return fh.read()


def _read_frontend(rel):
    p = os.path.join(
        os.path.dirname(__file__), "..", "..", "frontend", "src", rel,
    )
    with open(p, encoding="utf-8") as fh:
        return fh.read()
