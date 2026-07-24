"""
test_faithfulness_llm_judge.py — Iter 301 (Track 3 v1 — LLM judge)

The ONLY LLM-using test file in the reasoning suite. Marked
`@pytest.mark.llm_judge` so it's EXCLUDED from every PR CI run
(cost + judge-model flakiness). Runs on-demand or via the weekly
cron:

    pytest -m "llm_judge or not llm_judge" tests/reasoning/

Coverage: the invariant that when the loop's answer-formatter is
given a source doc + a claim to make, an unfaithful claim (one
that invents a fact) MUST be flagged. If the judge ever grades an
unfaithful claim as "faithful", we have a calibration bug in the
judge OR the underlying model has drifted — both signals we care
about.

Deliberately minimal: 3 fixtures (1 faithful, 2 unfaithful) so
each cron run costs ~$0.06 (3 Claude Sonnet 4.5 messages ≈ 2K
tokens each). Extend the fixture list only when a new class of
hallucination class becomes worth guarding against.
"""
from __future__ import annotations

import asyncio
import os

import pytest

from services.reasoning_evals import llm_faithfulness_check


# `pytest -m "not llm_judge"` (default) skips every test in this file.
pytestmark = pytest.mark.llm_judge


# ── Fixture 1: faithful — every claim in the output IS in the source ──
_SOURCE_FAITHFUL = """The loop_events collection is backed by MongoDB and
carries a TTL index on `created_at` set to 7 days. Rows outside
this window are auto-deleted by Mongo's expireAfterSeconds sweeper.
The composite index (loop_id, created_at) is used by the SSE
stream to paginate."""

_OUTPUT_FAITHFUL = """loop_events has a TTL index of 7 days on
`created_at` and is stored in MongoDB. It supports the SSE stream
via a composite index."""


# ── Fixture 2: unfaithful — invents a fact not in the source ──
_SOURCE_UNFAITHFUL = """FastAPI serves the app on port 8001 in
production. All API routes are prefixed with /api and forwarded by
Kubernetes ingress."""

_OUTPUT_UNFAITHFUL = """FastAPI serves the app on port 8001, uses
GraphQL for its API layer, and stores sessions in Redis."""
# ↑ GraphQL + Redis are inventions — source says neither.


# ── Fixture 3: unfaithful — invents a version number ──
_SOURCE_VERSION = """The frontend uses React with Vite as the
bundler. Tests run via Vitest + React Testing Library."""

_OUTPUT_VERSION_HALLUCINATION = """The frontend uses React 19.2
with Vite 5.4 as the bundler. Tests run via Vitest 3.0 with the
official React 19 plugin."""
# ↑ Versions and "official React 19 plugin" invented.


def _require_key():
    if not os.environ.get("EMERGENT_LLM_KEY"):
        pytest.skip("EMERGENT_LLM_KEY not set — skipping live judge tests")


def test_judge_grades_faithful_output_as_faithful():
    _require_key()
    r = asyncio.run(llm_faithfulness_check(
        output=_OUTPUT_FAITHFUL, source=_SOURCE_FAITHFUL,
    ))
    assert r["verdict"] == "faithful", (
        f"faithful output was graded {r['verdict']!r}; "
        f"reasoning: {r['reasoning']}; raw: {r['raw_response'][:400]}"
    )
    assert r["ok"] is True


def test_judge_flags_invented_facts_as_unfaithful():
    _require_key()
    r = asyncio.run(llm_faithfulness_check(
        output=_OUTPUT_UNFAITHFUL, source=_SOURCE_UNFAITHFUL,
    ))
    assert r["verdict"] == "unfaithful", (
        f"unfaithful output graded {r['verdict']!r}; "
        f"reasoning: {r['reasoning']}; raw: {r['raw_response'][:400]}"
    )
    # The judge must name at least one unsupported claim.
    assert len(r["unsupported_claims"]) >= 1, (
        f"judge said unfaithful but named no claims; response: {r}"
    )
    # And they must reference the invented terms.
    joined = " ".join(r["unsupported_claims"]).lower()
    assert ("graphql" in joined) or ("redis" in joined), (
        f"unsupported_claims didn't identify the inventions; got: "
        f"{r['unsupported_claims']}"
    )


def test_judge_flags_hallucinated_versions_as_unfaithful():
    _require_key()
    r = asyncio.run(llm_faithfulness_check(
        output=_OUTPUT_VERSION_HALLUCINATION, source=_SOURCE_VERSION,
    ))
    assert r["verdict"] == "unfaithful", (
        f"version-hallucination graded {r['verdict']!r}; "
        f"reasoning: {r['reasoning']}"
    )
    assert len(r["unsupported_claims"]) >= 1
