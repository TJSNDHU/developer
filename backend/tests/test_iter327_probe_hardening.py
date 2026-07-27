"""
test_iter327_probe_hardening.py — Iter 327 (follow-up to 326)

Two founder-requested additions:

  A) Stripe probe: prove the `one_time → warn` branch actually names
     the offender. Iter 326's live probe only exercised the "missing"
     branch (preview shell had no env vars). Mock stripe.Price.retrieve
     to return a mixed set (some recurring, some one_time), assert the
     probe returns status='warn' with the exact offending env-var name
     in the detail string.

  B) Firecrawl probe: diagnostic instrumentation must attach a key-hash
     prefix (sha256[:8]) and elapsed_ms + status_code signature to the
     `detail` string — never the full key. Founder needs this to
     fingerprint the next prod-side 20s timeout.
"""
from __future__ import annotations

import asyncio
import os
from unittest.mock import patch, MagicMock

import pytest


# ═══════════════════════════════════════════════════════════════════
# A · Stripe one_time detection — proves the branch actually names it
# ═══════════════════════════════════════════════════════════════════

def _fake_recurring_price(pid: str):
    p = MagicMock()
    p.type = "recurring"
    p.recurring = {"interval": "month", "interval_count": 1}
    p.get = lambda k, d=None: {"type": "recurring", "recurring": p.recurring}.get(k, d)
    return p


def _fake_one_time_price(pid: str):
    p = MagicMock()
    p.type = "one_time"
    p.recurring = None
    p.get = lambda k, d=None: {"type": "one_time", "recurring": None}.get(k, d)
    return p


def test_stripe_probe_names_one_time_offenders():
    """A monthly price silently minted as `type=one_time` MUST surface
    at health-check time with the exact env-var name in the detail
    string — otherwise the founder only finds out at real user
    checkout when Stripe returns 400 in subscription mode. This test
    exercises the `one_time_offenders` branch that Iter 326 wrote."""
    from services import integration_health as ih

    # Populate env with all 6 price IDs so the probe reaches
    # the retrieve-and-validate loop.
    env_patch = {
        "STRIPE_SECRET_KEY":               "sk_test_dummy_iter327",
        "STRIPE_STARTER_PRICE_ID":         "price_starter_monthly",
        "STRIPE_PRO_PRICE_ID":             "price_pro_monthly",
        "STRIPE_TEAM_PRICE_ID":            "price_team_monthly",
        "STRIPE_STARTER_ANNUAL_PRICE_ID":  "price_starter_annual",
        "STRIPE_PRO_ANNUAL_PRICE_ID":      "price_pro_annual",
        "STRIPE_TEAM_ANNUAL_PRICE_ID":     "price_team_annual",
    }

    # Fake Account + Price.list + per-Price.retrieve
    fake_acct = MagicMock()
    fake_acct.charges_enabled = True
    fake_acct.id = "acct_iter327"
    fake_acct.business_profile = None

    def fake_retrieve(pid):
        # PRO monthly is the poisoned one — mints as one_time.
        if pid == "price_pro_monthly":
            return _fake_one_time_price(pid)
        return _fake_recurring_price(pid)

    fake_price_list = MagicMock()
    fake_price_list.data = [1, 2, 3]  # count only

    with patch.dict(os.environ, env_patch, clear=False), \
         patch("stripe.Account.retrieve", return_value=fake_acct), \
         patch("stripe.Price.list", return_value=fake_price_list), \
         patch("stripe.Price.retrieve", side_effect=fake_retrieve):
        result = asyncio.get_event_loop().run_until_complete(
            ih._probe_stripe()
        ) if not asyncio.iscoroutinefunction(ih._probe_stripe) \
            else asyncio.new_event_loop().run_until_complete(
                ih._probe_stripe()
            )

    assert result["status"] == "warn", (
        f"expected status='warn' when a price is one_time, got "
        f"{result.get('status')} — details: {result}"
    )
    detail = result.get("detail", "")
    assert "STRIPE_PRO_PRICE_ID" in detail, (
        f"detail must name the offending env var, got: {detail!r}"
    )
    assert "one_time" in detail or "type=one_time" in detail, (
        f"detail must mention `one_time` shape, got: {detail!r}"
    )
    # Must NOT flag the correct annuals/others.
    for good in ("STRIPE_STARTER_PRICE_ID", "STRIPE_TEAM_PRICE_ID",
                 "STRIPE_STARTER_ANNUAL_PRICE_ID",
                 "STRIPE_PRO_ANNUAL_PRICE_ID",
                 "STRIPE_TEAM_ANNUAL_PRICE_ID"):
        assert good not in detail, (
            f"non-offender {good} accidentally flagged in: {detail!r}"
        )


def test_stripe_probe_all_recurring_returns_ok():
    """Regression guard: when ALL 6 price IDs are proper recurring,
    the probe returns `status='ok'` (Iter 326's happy path)."""
    from services import integration_health as ih

    env_patch = {
        "STRIPE_SECRET_KEY":               "sk_test_dummy_iter327_ok",
        "STRIPE_STARTER_PRICE_ID":         "price_starter_monthly",
        "STRIPE_PRO_PRICE_ID":             "price_pro_monthly",
        "STRIPE_TEAM_PRICE_ID":            "price_team_monthly",
        "STRIPE_STARTER_ANNUAL_PRICE_ID":  "price_starter_annual",
        "STRIPE_PRO_ANNUAL_PRICE_ID":      "price_pro_annual",
        "STRIPE_TEAM_ANNUAL_PRICE_ID":     "price_team_annual",
    }
    fake_acct = MagicMock()
    fake_acct.charges_enabled = True
    fake_acct.id = "acct_iter327_ok"
    fake_acct.business_profile = None
    fake_price_list = MagicMock()
    fake_price_list.data = [1, 2, 3]

    with patch.dict(os.environ, env_patch, clear=False), \
         patch("stripe.Account.retrieve", return_value=fake_acct), \
         patch("stripe.Price.list", return_value=fake_price_list), \
         patch("stripe.Price.retrieve",
               side_effect=lambda pid: _fake_recurring_price(pid)):
        result = asyncio.new_event_loop().run_until_complete(
            ih._probe_stripe()
        )
    assert result["status"] == "ok", (
        f"expected ok when all 6 recurring, got {result.get('status')}: {result}"
    )
    assert "all 6 configured price IDs verified recurring" in \
        result.get("detail", "")


# ═══════════════════════════════════════════════════════════════════
# B · Firecrawl signature — key hash + elapsed_ms in detail
# ═══════════════════════════════════════════════════════════════════

def test_firecrawl_probe_attaches_key_hash_and_signature():
    """The Firecrawl probe result MUST embed a diagnostic fingerprint
    (`key_hash=<8hex>` + latency) into the `detail` string so the
    founder can distinguish the next prod-side failure — timeout vs
    401 (bad key) vs 402 (credit) vs 5xx (upstream). Full key value
    MUST NOT appear anywhere in the returned dict."""
    from services import integration_health as ih

    env_patch = {"FIRECRAWL_API_KEY": "fc-iter327ThisIsASecretKeyDoNotLog"}
    # Mock httpx.AsyncClient.post to return a 200-success shape.
    class _FakeResp:
        status_code = 200
        text = '{"success":true,"data":{"markdown":"# Example"}}'
        def json(self):
            return {"success": True, "data": {"markdown": "# Example"}}
    class _FakeClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a, **kw): return False
        async def post(self, *a, **kw): return _FakeResp()

    with patch.dict(os.environ, env_patch, clear=False), \
         patch("httpx.AsyncClient", _FakeClient):
        result = asyncio.new_event_loop().run_until_complete(
            ih._probe_firecrawl()
        )

    # Must not leak the full key anywhere in the returned dict.
    dumped = repr(result)
    assert "iter327ThisIsASecretKeyDoNotLog" not in dumped, (
        f"FULL FIRECRAWL KEY LEAKED into probe result: {dumped[:300]}"
    )
    # Must include the diagnostic fingerprint tokens.
    assert "key_hash=" in dumped, (
        f"detail must include key_hash=<8hex> fingerprint, got: {dumped[:300]}"
    )
    # Elapsed latency must be reported (either summary or detail).
    assert "ms" in dumped, (
        f"detail/summary must include elapsed_ms marker for diagnosis: {dumped[:300]}"
    )


def test_firecrawl_timeout_signature_captured():
    """When the probe times out — the actual prod failure mode —
    the returned dict must carry a `Timeout after Xms` summary and
    a `key_hash=<8hex>` in detail so the founder can differentiate
    a real timeout from a bad-key silent hang next time."""
    from services import integration_health as ih
    import httpx as _hx

    env_patch = {"FIRECRAWL_API_KEY": "fc-iter327TimeoutProbeSecret"}
    class _TimeoutClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a, **kw): return False
        async def post(self, *a, **kw):
            raise _hx.TimeoutException("simulated 10s timeout")

    with patch.dict(os.environ, env_patch, clear=False), \
         patch("httpx.AsyncClient", _TimeoutClient):
        result = asyncio.new_event_loop().run_until_complete(
            ih._probe_firecrawl()
        )
    assert result["status"] == "broken"
    dumped = repr(result)
    assert "iter327TimeoutProbeSecret" not in dumped, (
        "full key leaked on timeout path"
    )
    assert "Timeout after" in result.get("summary", ""), (
        f"summary must name Timeout on timeout path: {result}"
    )
    assert "key_hash=" in result.get("detail", ""), (
        f"detail must carry key_hash on timeout path: {result}"
    )
