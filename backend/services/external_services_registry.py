"""
services/external_services_registry.py — Iter 123f drift-proof external
services catalog.

Single source of truth for the Architecture tab's "External services"
and "Integrations" cards. Adding a new external dep is ONE entry here,
not two edits across the router file.

A `Service` ties together:
  • display_name   — what the founder sees in the Architecture tab
  • env_keys       — the env vars that MUST be set for this service to be
                     "configured". Missing ANY → integration shows missing.
  • probe_url      — best-effort unauth GET. None → skip probing
                     (e.g. MongoDB, which is checked via the db handle).
  • integration_id — short slug used as the integration card key.
                     Keeps the existing UI keys ("openrouter (deepseek)" etc.)
                     so we don't break the frontend chip text.

Probing rule: if no env_key is set, we DON'T probe — that's the
auto-discovery the founder asked for (no point hitting Sentry if no DSN).
Services WITHOUT env_keys (like GitHub's public API) always probe.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Service:
    display_name:   str
    integration_id: str
    env_keys:       tuple[str, ...] = field(default_factory=tuple)
    probe_url:      str | None      = None
    always_probe:   bool            = False  # True for public APIs (GitHub etc.)


# Order = order shown in the External services card (left → right).
REGISTRY: tuple[Service, ...] = (
    Service(
        display_name="GitHub API",
        integration_id="github_oauth",
        env_keys=("GITHUB_OAUTH_CLIENT_ID", "GITHUB_OAUTH_CLIENT_SECRET"),
        probe_url="https://api.github.com",
        always_probe=True,    # public endpoint — probe even without keys
    ),
    Service(
        display_name="OpenRouter",
        integration_id="openrouter (deepseek)",
        env_keys=("OPENROUTER_API_KEY",),
        probe_url="https://openrouter.ai/api/v1/models",
    ),
    Service(
        display_name="Emergent LLM",
        integration_id="emergent_llm (maxx)",
        env_keys=("EMERGENT_LLM_KEY",),
        probe_url=None,    # internal — no public probe URL
    ),
    Service(
        display_name="Anthropic API",
        integration_id="anthropic (claude maxx)",
        env_keys=("ANTHROPIC_API_KEY",),
        probe_url="https://api.anthropic.com/v1/messages",
    ),
    Service(
        display_name="Cloudflare API",
        integration_id="cloudflare_purge",
        env_keys=("CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ZONE_ID"),
        probe_url="https://api.cloudflare.com/client/v4/user/tokens/verify",
    ),
    Service(
        display_name="Vercel API",
        integration_id="vercel_deploy_hook",
        env_keys=("VERCEL_API_TOKEN",),
        probe_url="https://api.vercel.com/v2/user",
    ),
    Service(
        display_name="Sentry ingest",
        integration_id="sentry_dsn",
        env_keys=("SENTRY_DSN",),
        probe_url="https://sentry.io/api/0/",
    ),
    Service(
        display_name="Stripe API",
        integration_id="stripe",
        # Codebase reads either STRIPE_API_KEY or STRIPE_SECRET_KEY (see
        # _stripe_key() in routers/payments.py — preserves backwards-compat
        # with older deploys). Treat the integration as "configured" iff
        # EITHER is set to a real (non-placeholder) value.
        env_keys=("STRIPE_API_KEY",),
        probe_url="https://api.stripe.com/v1/",
    ),
    Service(
        display_name="Resend email",
        integration_id="resend (email)",
        env_keys=("RESEND_API_KEY",),
        probe_url=None,     # Resend has no public unauth ping
    ),
    Service(
        display_name="Tavily search",
        integration_id="tavily (web search)",
        env_keys=("TAVILY_API_KEY",),
        probe_url=None,     # Tavily auth-only
    ),
    Service(
        display_name="Firecrawl",
        integration_id="firecrawl (web scrape)",
        env_keys=("FIRECRAWL_API_KEY",),
        probe_url=None,     # auth-only
    ),
    Service(
        display_name="e2b sandbox",
        integration_id="e2b (code exec)",
        env_keys=("E2B_API_KEY",),
        probe_url=None,     # SDK manages connection
    ),
)


def is_configured(svc: Service) -> bool:
    """A service is configured iff EVERY env_key it declares is set."""
    if not svc.env_keys:
        return True   # services with no required keys are always "configured"
    return all(bool(os.getenv(k)) for k in svc.env_keys)


def should_probe(svc: Service) -> bool:
    """We probe only when there's a URL AND either it's a public endpoint
    OR the keys are configured. Saves rate-limit budget on dev environments
    where most keys are deliberately empty."""
    if not svc.probe_url:
        return False
    return svc.always_probe or is_configured(svc)
