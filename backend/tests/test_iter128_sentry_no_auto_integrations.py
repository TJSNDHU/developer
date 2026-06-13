"""Iter 128 regression: Sentry SDK must NOT auto-enable integrations
at import time, because doing so pulls heavy LLM/cloud SDKs onto the
critical import path and uvicorn can't bind port 8001 in time for the
K8s readiness probe.

Background: with `auto_enabling_integrations=True` (Sentry's default),
sentry_sdk.init() probes every known integration target and imports
the dependency if installed — google.genai (285 ms), openai (185 ms),
huggingface_hub, celery, botocore, django, flask, falcon, gql, …
That's ~3 s of cold imports BEFORE `Started server process`, on top
of the ~4 s of legit imports. nginx upstream saw ECONNREFUSED for
the whole window and the production deploy CrashLoopBackOff'd.

This test pins `auto_enabling_integrations=False` in the sentry_sdk
init kwargs so the regression cannot silently come back.
"""
from __future__ import annotations

import pathlib
import re


MAIN_PY = pathlib.Path(__file__).resolve().parents[1] / "main.py"


def _sentry_init_block() -> str:
    """Return the sentry_sdk.init(...) call argument block."""
    src = MAIN_PY.read_text(encoding="utf-8")
    m = re.search(
        r"sentry_sdk\.init\(\s*(.*?)\n\s*\)\s*\n",
        src,
        re.DOTALL,
    )
    assert m, "could not locate sentry_sdk.init(...) call in main.py"
    return m.group(1)


def test_sentry_init_disables_auto_enabling_integrations() -> None:
    """Without this flag, sentry_sdk imports google.genai, openai,
    huggingface_hub and a dozen other framework integrations during
    init — directly causing the production CrashLoopBackOff."""
    block = _sentry_init_block()
    assert "auto_enabling_integrations=False" in block, (
        "sentry_sdk.init must pass `auto_enabling_integrations=False` "
        "to avoid loading google.genai / openai / huggingface_hub / "
        "celery / django / botocore at boot. See Iter 128."
    )


def test_sentry_explicit_integrations_present() -> None:
    """The four integrations we actually need (FastAPI, Starlette,
    Asyncio, PyMongo) must still be wired explicitly — they don't
    get loaded by `auto_enabling_integrations` either way, but we
    pin the list so a careless edit doesn't drop them."""
    block = _sentry_init_block()
    for required in (
        "FastApiIntegration(",
        "StarletteIntegration(",
        "AsyncioIntegration(",
        "PyMongoIntegration(",
    ):
        assert required in block, (
            f"sentry_sdk.init lost the `{required}` integration — "
            f"FastAPI request spans / Mongo span / asyncio task "
            f"errors will stop being captured."
        )
