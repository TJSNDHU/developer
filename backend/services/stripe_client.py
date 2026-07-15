"""
services/stripe_client.py — Iter 212m-230

Canonical Stripe key resolver + client bootstrap.  Extracted from
`routers/payments.py` to break the circular dependency
`services/billing_cron → routers/payments → services/billing_cron`
flagged by `architecture_health`.

Both the payments router AND the billing cron service now import
their Stripe key from HERE, restoring the router → service
dependency direction.

Public API
==========
    stripe_key() -> str
        Returns the effective Stripe secret key.  Runtime admin
        override (set via set_runtime_stripe_key) wins over env,
        which in turn wins over the dotenv fallback.  Filters out
        the platform's `sk_test_emergent...` placeholder that
        supervisor injects into every process.

    set_runtime_stripe_key(key: str) -> None
        Hot-swap the key at runtime — used by
        POST /admin/stripe-config so a founder can rotate keys
        without a redeploy.

    stripe_client()
        Returns the `stripe` module with `.api_key` set to
        `stripe_key()`.  Convenience wrapper used by
        billing_cron._stripe_client() and payments'
        webhook_handler().
"""
from __future__ import annotations

import os
from typing import Optional


# ── Runtime override (populated at boot or via /admin/stripe-config) ─
_RUNTIME_STRIPE_KEY: str = ""


def stripe_key() -> str:
    """Return the effective Stripe secret key.

    Resolution order:
      1. Runtime admin override (highest priority; set via
         `set_runtime_stripe_key()` — e.g. from
         POST /admin/stripe-config).
      2. `STRIPE_SECRET_KEY` env var.
      3. `STRIPE_API_KEY` env var (legacy name).
      4. Values read directly from `.env` via `dotenv_values`
         (defends against supervisor injecting a stale placeholder
         `sk_test_emergent...` that shadows the real key).

    Returns an empty string if none of the sources yield a usable key.
    """
    if (_RUNTIME_STRIPE_KEY
            and not _RUNTIME_STRIPE_KEY.startswith("sk_test_emergent")):
        return _RUNTIME_STRIPE_KEY

    for candidate in (os.environ.get("STRIPE_SECRET_KEY"),
                      os.environ.get("STRIPE_API_KEY")):
        if candidate and not candidate.startswith("sk_test_emergent"):
            return candidate

    # dotenv fallback — bypasses the stale supervisor-exported placeholder.
    try:
        from dotenv import dotenv_values
        env_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            ".env",
        )
        vals = dotenv_values(env_path)
        for k in ("STRIPE_SECRET_KEY", "STRIPE_API_KEY"):
            v = (vals.get(k) or "").strip().strip('"').strip("'")
            if v and not v.startswith("sk_test_emergent"):
                return v
    except Exception:
        pass
    return ""


def set_runtime_stripe_key(key: str) -> None:
    """Hot-swap the Stripe secret key for this process.

    Used by POST /admin/stripe-config so a founder can rotate the key
    from the admin panel without redeploying.  Also writes to the
    underlying `stripe` SDK so already-imported call-sites pick up
    the new key on their next call.
    """
    global _RUNTIME_STRIPE_KEY
    _RUNTIME_STRIPE_KEY = (key or "").strip()
    if _RUNTIME_STRIPE_KEY:
        try:
            import stripe as _stripe
            _stripe.api_key = _RUNTIME_STRIPE_KEY
        except Exception:
            pass


def stripe_client():
    """Return the `stripe` module with `.api_key` set to `stripe_key()`.
    Callers use this to get a ready-to-go client without repeating the
    2-line key-then-import dance."""
    import stripe as _stripe
    _stripe.api_key = stripe_key()
    return _stripe


__all__ = ["stripe_key", "set_runtime_stripe_key", "stripe_client"]
