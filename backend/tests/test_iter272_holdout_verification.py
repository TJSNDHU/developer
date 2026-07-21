"""
Iter 272 — Milestone A end-to-end tests against REAL MongoDB.

Covers:
  Feature 1.1  Frozen task spec — `freeze()` + `get()` are WORM.
  Feature 1.2  Diff classifier — a test-file edit is detected.
  Feature 1.3  Independent verifier — `verify()` writes a real row
               to `loop_verification_log` and returns "no" when the
               reviewer LLM says the diff doesn't satisfy the spec.
               (The LLM call is intercepted via a monkeypatch on
               `services.ora_chat.providers.one_shot` — everything
               else — Mongo IO, spec fetch, verdict parsing, log row
               shape — is exercised for real.)
  Feature 1.5  loop_run_log — audit-log writer is non-raising and
               readable back.
  Feature 2.1  loop_outcomes — record + repeat_touch detection over
               the 14-day window.

Every test round-trips through the actual MONGO_URL configured in
backend/.env. No mocks around Motor.
"""
from __future__ import annotations

import asyncio
import os
import pytest
import pytest_asyncio
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv("/app/backend/.env")

from services import (                                        # noqa: E402
    loop_task_specs as lts,
    loop_diff_classifier as ldc,
    loop_independent_verifier as liv,
    loop_outcomes as lo,
    loop_audit_log as lal,
)


@pytest_asyncio.fixture
async def db():
    """Fresh Motor client per test — Motor binds to the current event
    loop at connect time, and pytest-asyncio spins a new loop per
    test function by default."""
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    d = client[os.environ["DB_NAME"]]
    for mod in (lts, liv, lo, lal):
        try:
            await mod.ensure_indexes(d)
        except Exception:
            pass
    yield d
    client.close()


@pytest.fixture
def unique_id():
    """Per-test unique suffix so tests don't collide with each other
    or with real production data."""
    return "iter272_test_" + os.urandom(6).hex()


# ═══════════════════════════════════════════════════════════════════
# 1.1  Frozen task spec
# ═══════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_freeze_writes_worm_row(db, unique_id):
    loop_id = f"{unique_id}_freeze"
    row = await lts.freeze(
        db, loop_id=loop_id, task_id=None, user_id="u1",
        project_id="p1",
        user_message="Add a rate limit to the login endpoint",
        plan=("Plan:\n"
              "- Add middleware in auth.py that limits by IP\n"
              "- Return 429 on exceed\n"
              "- Cover with a test\n"
              "- Update the docs briefly"),
    )
    assert row["loop_id"] == loop_id
    # Extracted at least 3 criteria (we ignore trivial-length ones).
    assert len(row["acceptance_criteria"]) >= 3
    assert row["worm"] is True

    # Re-freeze is idempotent — returns the SAME row.
    again = await lts.freeze(
        db, loop_id=loop_id, task_id=None, user_id="u1",
        project_id="p1",
        user_message="TAMPERED — should not overwrite",
        plan="different plan",
    )
    assert again["original_task"] == row["original_task"]
    assert again["frozen_at"] == row["frozen_at"]

    fetched = await lts.get(db, loop_id)
    assert fetched is not None
    assert fetched["original_task"] == row["original_task"]

    # No public update() exists — WORM at the code layer.
    assert not hasattr(lts, "update")
    assert not hasattr(lts, "delete")


@pytest.mark.asyncio
async def test_freeze_with_no_structured_plan_falls_back_to_task(
        db, unique_id):
    """A plan with no bullets should still yield >=1 criterion (the
    original user message) — never an empty list."""
    loop_id = f"{unique_id}_bare"
    row = await lts.freeze(
        db, loop_id=loop_id, task_id=None, user_id="u1",
        project_id="p1",
        user_message="Fix the broken avatar upload",
        plan="I will look at the code and then fix it.",
    )
    assert len(row["acceptance_criteria"]) >= 1
    assert "avatar upload" in row["acceptance_criteria"][0].lower()


# ═══════════════════════════════════════════════════════════════════
# 1.2  Diff classifier
# ═══════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("path,expected", [
    ("backend/tests/test_auth.py",                       True),
    ("frontend/src/__tests__/Login.test.jsx",            True),
    ("frontend/src/pages/Login.jsx",                     False),
    ("backend/services/auth.py",                         False),
    ("conftest.py",                                      True),
    ("jest.config.js",                                   True),
    ("cypress/e2e/login.cy.js",                          True),
    ("backend/fixtures/users.json",                      True),
    ("backend/__mocks__/db.py",                          True),
    ("app/services/testing_helpers.py",                  False),
    ("",                                                 False),
    ("some_test_helper.py",                              False),   # not test_*
    ("test_iter272_holdout.py",                          True),
])
def test_classifier_recognises_test_paths(path, expected):
    assert ldc.is_test_or_fixture(path) is expected, path


def test_classifier_split_flags_test_touch():
    files = [
        {"path": "backend/services/auth.py",       "content": "src"},
        {"path": "backend/tests/test_auth.py",     "content": "def test_x(): pass\n"},
    ]
    out = ldc.classify(files)
    assert out["test_touched"] is True
    assert out["tests"] == ["backend/tests/test_auth.py"]
    assert out["source"] == ["backend/services/auth.py"]
    assert out["test_lines"][0]["added_lines"] >= 1


# ═══════════════════════════════════════════════════════════════════
# 1.3  Independent verifier — real Mongo, mocked LLM
# ═══════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_verifier_returns_no_and_logs_when_llm_says_no(
        db, unique_id, monkeypatch):
    loop_id = f"{unique_id}_verifier_no"
    # Snapshot a spec first — verifier depends on this row.
    await lts.freeze(
        db, loop_id=loop_id, task_id=None, user_id="u1",
        project_id="p1",
        user_message="Add rate limiting to /api/login",
        plan="- Add rate limit middleware\n- Return 429 on exceed\n"
             "- Add a test proving it fires",
    )

    async def fake_llm(*, model, messages, temperature, top_p,
                       presence_penalty, max_tokens):
        # Reviewer says NO — the diff only edits a test, not the
        # production code that would satisfy the spec.
        return ('{"verdict":"no","reason":"only test edits — production'
                ' rate-limit code not modified"}',
                {"input_tokens": 10, "output_tokens": 8,
                 "total_tokens": 18},
                None)
    monkeypatch.setattr(liv, "_one_shot", fake_llm)

    result = await liv.verify(
        db, loop_id=loop_id,
        files=[{"path": "backend/tests/test_login_rate.py",
                "content": "def test_rate(): assert True\n"}],
    )
    assert result["verdict"] == "no"
    assert "test edits" in result["reason"].lower()

    log_row = await db["loop_verification_log"].find_one(
        {"loop_id": loop_id})
    assert log_row is not None
    assert log_row["verdict"] == "no"
    assert log_row["latency_s"] >= 0


@pytest.mark.asyncio
async def test_verifier_returns_no_on_parse_error(
        db, unique_id, monkeypatch):
    """Fail-closed contract: reviewer returned garbage → verdict=no."""
    loop_id = f"{unique_id}_parse_err"
    await lts.freeze(
        db, loop_id=loop_id, task_id=None, user_id="u1",
        project_id="p1",
        user_message="Do the thing", plan="- Do the thing",
    )

    async def junk_llm(**_):
        return ("I have some thoughts but no JSON.", {}, None)
    monkeypatch.setattr(liv, "_one_shot", junk_llm)

    result = await liv.verify(db, loop_id=loop_id,
                              files=[{"path": "x.py", "content": ""}])
    assert result["verdict"] == "no"
    assert result["reason"] == "verifier_parse_error"


@pytest.mark.asyncio
async def test_verifier_skips_gracefully_when_no_spec(db, unique_id):
    """No frozen spec → skipped_no_spec, NOT auto-pass."""
    loop_id = f"{unique_id}_no_spec"
    result = await liv.verify(db, loop_id=loop_id, files=[])
    assert result["verdict"] == "skipped_no_spec"
    assert (await db["loop_verification_log"].find_one(
        {"loop_id": loop_id})) is not None


# ═══════════════════════════════════════════════════════════════════
# 1.5  Audit log
# ═══════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_audit_log_writes_and_is_readable(db, unique_id):
    await lal.log(db, loop_id=unique_id, phase="ship",
                  kind=lal.KIND_TEST_TOUCH, verdict=lal.VERDICT_FAIL,
                  detail={"tests_touched": ["tests/test_a.py"]})
    row = await db["loop_run_log"].find_one({"loop_id": unique_id})
    assert row is not None
    assert row["verdict"] == "fail"
    assert row["kind"] == "test_file_touch"


@pytest.mark.asyncio
async def test_audit_log_never_raises_on_bad_db(unique_id):
    """Contract: logging failure must not break the caller. Feed a
    broken db-like object and assert log() returns normally."""
    class ExplodingColl:
        async def insert_one(self, _row):
            raise RuntimeError("mongo unavailable")
    class ExplodingDB:
        def __getitem__(self, _k):
            return ExplodingColl()
    # Must NOT raise.
    await lal.log(ExplodingDB(), loop_id=unique_id, phase="test",
                  kind="unit", verdict="pass")


# ═══════════════════════════════════════════════════════════════════
# 2.1  Outcomes + repeat_touch
# ═══════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_outcomes_first_commit_has_no_repeat_touch(
        db, unique_id):
    project = f"proj_{unique_id}"
    row = await lo.record_shipped_commit(
        db, loop_id=f"{unique_id}_a", task_id=None,
        user_id="u1", project_id=project,
        commit_sha=f"{unique_id}_sha_a",
        file_paths=["backend/services/auth.py"],
        owner="o", repo="r", branch="main",
    )
    assert row["repeat_touch"] is False


@pytest.mark.asyncio
async def test_outcomes_second_commit_touching_same_file_is_repeat(
        db, unique_id):
    project = f"proj_{unique_id}"
    await lo.record_shipped_commit(
        db, loop_id=f"{unique_id}_first", task_id=None,
        user_id="u1", project_id=project,
        commit_sha=f"{unique_id}_sha_first",
        file_paths=["backend/services/auth.py",
                    "backend/routers/login.py"],
        owner="o", repo="r", branch="main",
    )
    later = await lo.record_shipped_commit(
        db, loop_id=f"{unique_id}_second", task_id=None,
        user_id="u1", project_id=project,
        commit_sha=f"{unique_id}_sha_second",
        file_paths=["backend/services/auth.py"],   # overlap
        owner="o", repo="r", branch="main",
    )
    assert later["repeat_touch"] is True
    assert later["repeat_of"]["commit_sha"] == f"{unique_id}_sha_first"
    assert "backend/services/auth.py" in later["repeat_of"]["overlap"]


@pytest.mark.asyncio
async def test_outcomes_mark_reverted_idempotent(db, unique_id):
    project = f"proj_{unique_id}"
    sha = f"{unique_id}_sha_revertable"
    await lo.record_shipped_commit(
        db, loop_id=f"{unique_id}_r", task_id=None,
        user_id="u1", project_id=project,
        commit_sha=sha, file_paths=["x.py"],
        owner="o", repo="r", branch="main",
    )
    r1 = await lo.mark_reverted(db, commit_sha=sha,
                                 reverted_by="u2")
    r2 = await lo.mark_reverted(db, commit_sha=sha,
                                 reverted_by="u2")
    assert r1["marked"] is True
    assert r2["marked"] is False   # already reverted → no-op

    doc = await db["loop_outcomes"].find_one({"commit_sha": sha})
    assert doc["reverted"] is True
    assert doc["reverted_by"] == "u2"


# ═══════════════════════════════════════════════════════════════════
# End-to-end story: a spec-gaming attempt gets caught at the gate
# ═══════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_e2e_spec_gaming_attempt_is_blocked(db, unique_id):
    """The full held-out gate story:
       1. Task frozen.
       2. Agent submits a diff that ONLY edits a test file.
       3. Classifier flags test_touched=True.
       4. Verifier is asked; reviewer says NO ("only test edits, no
          production change satisfies the criteria").
       5. Both events land in loop_run_log + loop_verification_log.
       6. If this were _do_ship, both gates would independently block
          the ship. This test asserts each gate's contract holds
          without needing to spin up the whole engine (which requires
          GitHub PATs, network I/O, an LLM for planning, etc.).
    """
    loop_id = f"{unique_id}_e2e"
    # 1. Freeze the spec.
    spec = await lts.freeze(
        db, loop_id=loop_id, task_id=None, user_id="u1",
        project_id="p1",
        user_message="Rate-limit /api/login to 5 attempts / minute",
        plan=("- Add IP-based rate limit middleware in auth.py\n"
              "- Return HTTP 429 on exceed\n"
              "- Cover with a test that fires the middleware"),
    )
    assert spec["worm"] is True

    # 2. Agent's malicious diff — only touches the test file.
    hostile_diff = [
        {"path": "backend/tests/test_login_rate.py",
         "content": "def test_rate_limit(): assert True  # weakened\n"},
    ]

    # 3. Classifier
    cls = ldc.classify(hostile_diff)
    assert cls["test_touched"] is True
    assert cls["tests"] == ["backend/tests/test_login_rate.py"]
    assert cls["source"] == []

    # 4. Verifier (fresh LLM context, mocked to be honest)
    import services.loop_independent_verifier as _liv

    async def honest_reviewer(**_):
        return ('{"verdict":"no","reason":"diff only weakens the test '
                'instead of implementing rate limiting"}',
                {"total_tokens": 20}, None)

    orig = _liv._one_shot
    _liv._one_shot = honest_reviewer
    try:
        result = await liv.verify(db, loop_id=loop_id,
                                   files=hostile_diff)
    finally:
        _liv._one_shot = orig
    assert result["verdict"] == "no"

    # 5. Audit rows exist
    await lal.log(
        db, loop_id=loop_id, phase="ship",
        kind=lal.KIND_TEST_TOUCH, verdict=lal.VERDICT_FAIL,
        detail={"tests_touched": cls["tests"],
                 "test_lines":    cls["test_lines"]})
    audit = await db["loop_run_log"].find_one(
        {"loop_id": loop_id, "kind": "test_file_touch"})
    assert audit is not None
    assert audit["verdict"] == "fail"

    verifier_row = await db["loop_verification_log"].find_one(
        {"loop_id": loop_id})
    assert verifier_row is not None
    assert verifier_row["verdict"] == "no"

    # 6. Assert BOTH gates would independently block the ship.
    #    (Belt + suspenders: even if the verifier had said "yes",
    #    test_touched would still force human review.)
    would_be_blocked = cls["test_touched"] or (
        result["verdict"] == "no")
    would_force_human_review = cls["test_touched"]
    assert would_be_blocked is True
    assert would_force_human_review is True
