"""Iter 331 · /health starvation fix — source-level locks.

PROD evidence: nginx `GET /health` upstream timeouts (110) + Starlette
"No response returned" fired exactly while integration_health probes
ran the synchronous Stripe SDK (8 sequential HTTP calls) and the
synchronous e2b SDK (15s sandbox boot) directly on the event loop.
Both probes must keep their blocking work inside asyncio.to_thread.
"""
from pathlib import Path

SRC = Path("/app/backend/services/integration_health.py").read_text(encoding="utf-8")


def _fn_body(name: str) -> str:
    seg = SRC.split(f"async def {name}")[1]
    return seg.split("async def ")[0]


def test_stripe_probe_offloads_to_thread():
    body = _fn_body("_probe_stripe")
    assert "asyncio.to_thread" in body
    # Blocking calls must live inside the sync helper, not the coroutine.
    sync_part = body.split("def _sync_probe")[1]
    assert "stripe.Account.retrieve()" in sync_part
    assert "stripe.Price.retrieve" in sync_part


def test_e2b_probe_offloads_to_thread():
    body = _fn_body("_probe_e2b")
    assert "asyncio.to_thread" in body
    sync_part = body.split("def _sync_probe")[1]
    assert "Sandbox.create" in sync_part
    assert "sbx.kill()" in sync_part


def test_no_bare_sync_stripe_calls_outside_helpers():
    # Every stripe SDK call in this module must be inside a _sync_probe
    # helper (executed via to_thread).
    for line in SRC.splitlines():
        s = line.strip()
        if s.startswith("acct = stripe.") or s.startswith("prices = stripe."):
            # ensure the line is indented deeper than the coroutine level
            assert line.startswith("        "), f"sync stripe call at coroutine level: {s}"
