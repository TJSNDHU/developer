"""
test_iter326_probe_fixes.py — Iter 326

Two defensive integration-health probe fixes, founder-approved:

  A) `_probe_tavily`: HTTP 432 is Tavily's documented "plan usage limit
     exceeded" code. Current classifier only checks 401/402/429 and
     falls through to `broken` on 432, painting `/admin/architecture`
     CRITICAL for what is really a soft "credits exhausted" warning.
     Fix: add 432 to the credits-exhausted branch → status `warn` with
     fix_hint pointing at tavily.com/pricing.

  B) `_probe_stripe`: Currently only checks that the 3 price IDs are
     SET in env. It does NOT verify `.recurring` is truthy — which
     means a monthly price silently minted as `type=one_time` slips
     past the health probe and only surfaces when a real user hits
     Stripe checkout (400/502 in production). Fix: iterate all 6
     price IDs (3 monthly + 3 annual), `stripe.Price.retrieve` each,
     warn per-price if `type != "recurring"` or `.recurring` missing.

  This test file is test-first: assertions written BEFORE the code
  changes land. Confirmed to fail-red pre-fix; expected green after.
"""
from __future__ import annotations

import inspect
import re
from pathlib import Path

_IH_SRC = Path(
    "/app/backend/services/integration_health.py"
).read_text()


# ═══════════════════════════════════════════════════════════════════
# A · Tavily 432 → warn
# ═══════════════════════════════════════════════════════════════════

def test_tavily_probe_classifies_432_as_warn():
    """Iter 326 A: `_probe_tavily` must treat HTTP 432 (Tavily's
    'plan usage limit exceeded') as a soft `warn`, not `broken`.

    Root of the current bug: classifier at ~line 212 checks
    `r.status_code in (402, 429)` — 432 falls through to the
    generic `broken` branch, painting /admin/architecture red for
    what is actually a top-up prompt."""
    m = re.search(
        r"async def _probe_tavily\(.*?(?=\nasync def |\Z)",
        _IH_SRC, re.DOTALL,
    )
    assert m, "_probe_tavily not found"
    body = m.group(0)

    # Contract: 432 must be listed in the credits/rate-limit branch.
    # We accept either a tuple `(402, 429, 432)` or a `== 432` clause
    # so the fix has one degree of freedom.
    assert (
        "432" in body
    ), (
        "Iter 326 A: `_probe_tavily` body must reference 432 "
        "somewhere in the classifier. Currently 432 is not "
        "handled — Tavily's actual quota-exhausted code."
    )
    # Structural check — the 432 handler must return `warn`, not
    # `broken`. Look at the surrounding lines.
    ok = False
    for m2 in re.finditer(r"432[^\n]*", body):
        chunk = body[m2.start(): m2.start() + 300]
        if '"warn"' in chunk or "'warn'" in chunk:
            ok = True
            break
    assert ok, (
        "Iter 326 A: 432 must land in the `warn` branch (credits "
        "exhausted), not `broken`. Live evidence: `tvly-dev-*` "
        "key returned {'detail':{'error':'This request exceeds "
        "your plan's set usage limit.'}} with HTTP 432 — that "
        "is a soft top-up prompt, not a critical outage."
    )


# ═══════════════════════════════════════════════════════════════════
# B · Stripe per-price `.recurring` verification
# ═══════════════════════════════════════════════════════════════════

def test_stripe_probe_validates_recurring_per_price_id():
    """Iter 326 B: `_probe_stripe` must iterate each configured
    price ID and verify `type == 'recurring'` (or `.recurring` is
    truthy). If any monthly price is silently `one_time`, a real
    user's subscription checkout crashes at 400/502 — the exact
    revenue block founder just hit."""
    m = re.search(
        r"async def _probe_stripe\(.*?(?=\nasync def |\Z)",
        _IH_SRC, re.DOTALL,
    )
    assert m, "_probe_stripe not found"
    body = m.group(0)

    # The fix must reference stripe.Price.retrieve on each configured
    # price ID and check `.recurring`.
    assert "Price.retrieve" in body, (
        "Iter 326 B: _probe_stripe must call stripe.Price.retrieve "
        "on each configured monthly + annual price ID. Currently "
        "it only verifies presence, not shape."
    )
    assert "recurring" in body, (
        "Iter 326 B: _probe_stripe must inspect `.recurring` (or "
        "`.type == 'recurring'`) on each retrieved Price. Without "
        "this check, a monthly `one_time` price passes health as "
        "OK and only crashes at real checkout time."
    )

    # Contract: annuals must be validated too. Founder's 6 price
    # env vars include STRIPE_*_ANNUAL_PRICE_ID — probe must cover
    # them because either monthly or annual can be misconfigured.
    assert "ANNUAL_PRICE_ID" in body, (
        "Iter 326 B: _probe_stripe must inspect all 6 price env "
        "vars including annual variants — either can be minted "
        "wrong. Currently annuals are ignored."
    )

    # Contract: a `one_time` price found → status `warn` (misconfigured
    # but the deployment itself is fine), with a fix_hint that names
    # WHICH price is wrong.
    assert (
        "one_time" in body or "type != " in body
        or "not p.get(\"recurring\"" in body
        or "recurring is None" in body
    ), (
        "Iter 326 B: _probe_stripe must explicitly detect the "
        "`type=one_time` failure mode (or a missing `.recurring`) "
        "and surface it as a per-price warning naming the offending "
        "env var. That's the exact class of bug just hit prod."
    )


# ═══════════════════════════════════════════════════════════════════
# C · Firecrawl probe reports latency (no fix, diagnosis only)
# ═══════════════════════════════════════════════════════════════════

def test_firecrawl_probe_diagnostic_state_documented():
    """This test is a placeholder marker: founder ruled out a
    timeout-bump fix without prod-side reachability data. The
    diagnosis report for prod is captured in the ITER_326_NOTES
    section of CHANGELOG.md — this test just asserts the
    diagnostic memo exists so we can't silently forget it."""
    changelog = Path("/app/memory/CHANGELOG.md").read_text()
    assert "Iter 326" in changelog and "Firecrawl" in changelog, (
        "Iter 326 diagnosis memo for Firecrawl must be recorded "
        "in CHANGELOG.md — prod-side reachability delta ruled "
        "out client timeout as a fix."
    )
