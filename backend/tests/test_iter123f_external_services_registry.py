"""
test_iter123f_external_services_registry.py — Iter 123f drift-proof
external services + integrations.

Locks in:
  • A single REGISTRY drives BOTH services-probe AND integrations grid.
  • Probing is skipped when no env keys are configured (no Sentry probe
    on dev without a DSN — saves rate-limit budget + UI noise).
  • Adding a new integration is ONE entry, not two edits.
  • Existing UI keys (integration_id slugs) are preserved so the
    frontend chip text doesn't break.
"""
import os
import pytest
from unittest.mock import patch

from services.external_services_registry import (
    REGISTRY, Service, is_configured, should_probe,
)


# ── Registry structural invariants ────────────────────────────────────

def test_registry_is_tuple_of_service_records():
    assert isinstance(REGISTRY, tuple)
    for svc in REGISTRY:
        assert isinstance(svc, Service)
        assert svc.display_name
        assert svc.integration_id
        # env_keys can be empty (e.g. public APIs)
        assert isinstance(svc.env_keys, tuple)


def test_registry_has_no_duplicate_display_names():
    names = [s.display_name for s in REGISTRY]
    assert len(names) == len(set(names)), f"duplicate names: {names}"


def test_registry_has_no_duplicate_integration_ids():
    ids = [s.integration_id for s in REGISTRY]
    assert len(ids) == len(set(ids)), f"duplicate ids: {ids}"


# ── Auto-discovery: is_configured / should_probe ──────────────────────

def test_is_configured_returns_false_when_any_env_key_missing():
    svc = Service(
        display_name="Test",
        integration_id="test",
        env_keys=("CASE_A_KEY", "CASE_A_OTHER_KEY"),
    )
    with patch.dict(os.environ, {"CASE_A_KEY": "x"}, clear=False):
        # The OTHER key is unset → must be False
        os.environ.pop("CASE_A_OTHER_KEY", None)
        assert is_configured(svc) is False


def test_is_configured_returns_true_when_all_env_keys_set():
    svc = Service(
        display_name="Test",
        integration_id="test",
        env_keys=("CASE_B_KEY_1", "CASE_B_KEY_2"),
    )
    with patch.dict(os.environ, {
        "CASE_B_KEY_1": "x",
        "CASE_B_KEY_2": "y",
    }):
        assert is_configured(svc) is True


def test_is_configured_true_when_no_env_keys_required():
    """Services with no env_keys (public APIs) are always configured."""
    svc = Service(display_name="Public", integration_id="public")
    assert is_configured(svc) is True


def test_should_probe_skips_when_unconfigured():
    """The whole POINT of the registry: don't probe services we KNOW
    aren't configured. Saves 4s × N timeouts per page load."""
    svc = Service(
        display_name="Lonely",
        integration_id="lonely",
        env_keys=("CASE_C_KEY",),
        probe_url="https://example.com",
    )
    os.environ.pop("CASE_C_KEY", None)
    assert should_probe(svc) is False


def test_should_probe_runs_when_always_probe_set():
    """GitHub-style public APIs probe even without keys."""
    svc = Service(
        display_name="Public",
        integration_id="public",
        env_keys=("OPTIONAL_KEY",),
        probe_url="https://api.public.com",
        always_probe=True,
    )
    os.environ.pop("OPTIONAL_KEY", None)
    assert should_probe(svc) is True


def test_should_probe_false_when_no_probe_url():
    """Internal services (no public probe URL) are never probed."""
    svc = Service(
        display_name="Internal",
        integration_id="internal",
        env_keys=("INTERNAL_KEY",),
        probe_url=None,
    )
    with patch.dict(os.environ, {"INTERNAL_KEY": "x"}):
        assert should_probe(svc) is False


# ── UI-key preservation (no breaking the frontend chip text) ──────────

def test_existing_ui_keys_preserved():
    """The Architecture tab's chip text uses these slugs. Changing
    them breaks user bookmarks of admin screenshots + screenshot tests."""
    expected_slugs = {
        "openrouter (deepseek)",
        "emergent_llm (maxx)",
        "anthropic (claude maxx)",
        "cloudflare_purge",
        "vercel_deploy_hook",
        "sentry_dsn",
        "stripe",
        "resend (email)",
        "github_oauth",
    }
    actual = {s.integration_id for s in REGISTRY}
    missing = expected_slugs - actual
    assert not missing, f"breaking change — missing slugs: {missing}"


# ── Router wiring — hardcoded probe_targets list MUST be gone ─────────

def test_admin_router_no_longer_has_hardcoded_probe_list():
    with open("/app/backend/routers/admin.py") as f:
        src = f.read()
    # The bad pattern (hand-maintained list of tuples)
    assert "probe_targets = [" not in src, \
        "stale hardcoded probe_targets list still in admin.py"
    # The bad pattern (integrations dict assigned to literal)
    # We allow the dict literal that BUILDS integrations from REGISTRY,
    # but not the old hardcoded one with all the env-var lookups inline.
    assert 'integrations = {\n' not in src or 'REGISTRY' in src, \
        "stale integrations dict literal — must come from REGISTRY"
    # The good pattern
    assert "from services.external_services_registry import" in src
    assert "for svc in REGISTRY" in src


# ── At least the obvious integrations are present ─────────────────────

def test_critical_integrations_in_registry():
    """If any of these slugs disappear, customer support will get
    confused tickets about 'why is Stripe missing'."""
    required = {"stripe", "github_oauth", "openrouter (deepseek)"}
    actual = {s.integration_id for s in REGISTRY}
    for r in required:
        assert r in actual, f"critical integration absent: {r}"
