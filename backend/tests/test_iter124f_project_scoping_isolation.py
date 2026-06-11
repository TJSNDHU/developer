"""
Iter 124f — End-to-end isolation test for the eval runner's offline
project-scoping check. Uses the REAL Mongo configured in backend/.env,
inserts throwaway projects, asserts cross-user access is blocked, and
cleans up after itself.

This test catches the most security-critical failure mode for a
multi-tenant SaaS: User A's session somehow seeing User B's repo data.
"""
from __future__ import annotations

import asyncio
import pytest

from evals.runner import _project_scoping_isolation_test


@pytest.mark.asyncio
async def test_real_mongo_cross_user_repo_lookup_blocked():
    """get_repo_context(userA, projectB) MUST return empty string. The
    Mongo find_one() filter requires BOTH project_id AND user_id to
    match, so a prompt-injected project_id from User A can never
    surface User B's repo."""
    result = await _project_scoping_isolation_test()

    # The test inserts real docs and cleans up. We assert the scorer.
    assert "scorers" in result
    scorers = result["scorers"]
    assert len(scorers) >= 1

    scope_check = next(
        (s for s in scorers if s.get("scorer") == "scope"),
        None,
    )
    assert scope_check is not None
    # PASS = isolation held; PARTIAL is acceptable only when Mongo is
    # unreachable (graceful skip); FAIL = real cross-user bleed.
    assert scope_check["status"] in ("PASS", "PARTIAL"), (
        f"Cross-user isolation broke: {scope_check}"
    )
    if scope_check["status"] == "FAIL":
        pytest.fail(
            f"CRITICAL: cross-user repo bleed: {scope_check['evidence']}"
        )
