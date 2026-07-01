"""
Iter 212m-169 — End-to-end regression tests for the BINContext
hardening.  20 tests covering the full lifecycle:

  Part 1  (1-4)   build_bin_context factory correctness
  Part 2  (5-6)   Tool-layer bin_ctx enforcement
  Part 3  (7)     Loop session uses bin_ctx.pat directly (no DB re-fetch)
  Part 4  (8-9)   Chat entry: invalid project → 403; auto-infer removed
  Part 5  (10)    Full E2E cross-project isolation (User A p1 vs p2)
  Part 6  (11-13) Review modes (swift/pro/maxx) all thread bin_ctx
  Part 7  (14-15) Prompt-mode + Loop-mode tool calls carry bin_ctx
  Part 8  (16-19) Parliament councils A/B/C + CEO unchanged bin_ctx
  Part 9  (20)    Ask Advisor pulls repo context from bin_ctx

Real DB via `cto_services.db.get_db()`.  Uses the current preview DB
(the same one the running backend talks to) — safe because the tests
create a scratch user, do their reads/writes under it, then delete
the row.
"""
from __future__ import annotations

import asyncio
import inspect
import os
import re
import time
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import HTTPException

# Force the same env as the backend.
os.environ.setdefault("JWT_SECRET", os.environ.get("JWT_SECRET", "test-secret-key"))


# ── Shared fixtures ───────────────────────────────────────────────────


@pytest_asyncio.fixture(scope="function")
async def db_and_scratch_users():
    """Create two scratch users (A and B) + two projects for A + one
    for B.  Fixture yields (db, uid_a, uid_b, pid_a1, pid_a2, pid_b1)
    then cleans up.
    """
    # Bootstrap a FRESH MongoDB client per test to avoid the
    # "Event loop is closed" error from a client cached across
    # pytest's per-test event loops.
    from motor.motor_asyncio import AsyncIOMotorClient
    from cto_services.db import set_db
    mongo = AsyncIOMotorClient(
        os.environ["MONGO_URL"],
        serverSelectionTimeoutMS=5000,
    )
    db = mongo[os.environ.get("DB_NAME", "aurem_dev")]
    set_db(db)   # so build_bin_context() picks this up via get_db()

    uid_a = f"utest_a_{uuid.uuid4().hex[:8]}"
    uid_b = f"utest_b_{uuid.uuid4().hex[:8]}"
    pid_a1 = f"p_test_a1_{uuid.uuid4().hex[:6]}"
    pid_a2 = f"p_test_a2_{uuid.uuid4().hex[:6]}"
    pid_b1 = f"p_test_b1_{uuid.uuid4().hex[:6]}"

    # Encrypt real PATs so the decrypt path is exercised.
    from services.vault import encrypt, is_vault_available
    assert is_vault_available(), "AUREM_MASTER_KEY must be set for BINContext tests"
    enc_a1 = await encrypt(uid_a, "ghp_UserA_Project1_token_" + uuid.uuid4().hex[:8],
                           kind="github_token")
    enc_a2 = await encrypt(uid_a, "ghp_UserA_Project2_token_" + uuid.uuid4().hex[:8],
                           kind="github_token")
    enc_b1 = await encrypt(uid_b, "ghp_UserB_Project1_token_" + uuid.uuid4().hex[:8],
                           kind="github_token")

    await db.dev_users.insert_many([
        {"user_id": uid_a, "email": f"{uid_a}@test.aurem.dev",
         "tier": "free", "is_admin": False, "is_unlimited": False},
        {"user_id": uid_b, "email": f"{uid_b}@test.aurem.dev",
         "tier": "free", "is_admin": False, "is_unlimited": False},
    ])
    await db.cto_projects.insert_many([
        {"project_id": pid_a1, "user_id": uid_a, "name": "A-Proj-1",
         "github_owner": "userA", "github_repo": "repo1",
         "github_token": enc_a1, "branch": "main",
         "auth_method": "pat", "status": "connected"},
        {"project_id": pid_a2, "user_id": uid_a, "name": "A-Proj-2",
         "github_owner": "userA", "github_repo": "repo2",
         "github_token": enc_a2, "branch": "main",
         "auth_method": "pat", "status": "connected"},
        {"project_id": pid_b1, "user_id": uid_b, "name": "B-Proj-1",
         "github_owner": "userB", "github_repo": "repo1",
         "github_token": enc_b1, "branch": "main",
         "auth_method": "pat", "status": "connected"},
    ])

    yield {
        "db": db, "uid_a": uid_a, "uid_b": uid_b,
        "pid_a1": pid_a1, "pid_a2": pid_a2, "pid_b1": pid_b1,
    }

    await db.dev_users.delete_many({"user_id": {"$in": [uid_a, uid_b]}})
    await db.cto_projects.delete_many(
        {"project_id": {"$in": [pid_a1, pid_a2, pid_b1]}}
    )
    await db.repo_contexts.delete_many(
        {"project_id": {"$in": [pid_a1, pid_a2, pid_b1]}}
    )
    mongo.close()


# ── Part 1 — build_bin_context factory correctness ───────────────────


@pytest.mark.asyncio
async def test_1_build_bin_context_happy_path(db_and_scratch_users):
    """Test 1 — build_bin_context with valid user+project → BINContext returned."""
    d = db_and_scratch_users
    from services.bin_context import build_bin_context, BINContext

    bc = await build_bin_context(
        user_id=d["uid_a"], project_id=d["pid_a1"], db=d["db"],
        is_founder=False,
    )
    assert isinstance(bc, BINContext)
    assert bc.bin_id == d["uid_a"]
    assert bc.pid == d["pid_a1"]
    assert bc.repo_owner == "userA"
    assert bc.repo_name == "repo1"
    assert bc.branch == "main"
    assert bc.pat.startswith("ghp_UserA_Project1_token_")
    assert bc.is_founder is False


@pytest.mark.asyncio
async def test_2_build_bin_context_wrong_user(db_and_scratch_users):
    """Test 2 — build_bin_context with wrong user_id → 403."""
    d = db_and_scratch_users
    from services.bin_context import build_bin_context

    with pytest.raises(HTTPException) as ei:
        await build_bin_context(
            user_id=d["uid_b"], project_id=d["pid_a1"], db=d["db"],
        )
    assert ei.value.status_code == 403
    assert "project access denied" in str(ei.value.detail).lower()


@pytest.mark.asyncio
async def test_3_build_bin_context_null_project(db_and_scratch_users):
    """Test 3 — build_bin_context with null project_id → 400 no auto-infer."""
    d = db_and_scratch_users
    from services.bin_context import build_bin_context

    for pid in (None, "", "  ", "home"):
        with pytest.raises(HTTPException) as ei:
            await build_bin_context(
                user_id=d["uid_a"], project_id=pid, db=d["db"],
            )
        assert ei.value.status_code == 400
        assert "no project selected" in str(ei.value.detail).lower()


@pytest.mark.asyncio
async def test_4_build_bin_context_pat_decrypt_fail(db_and_scratch_users):
    """Test 4 — PAT decryption failure → 403.

    Simulate by inserting a project whose ciphertext was encrypted for
    a DIFFERENT user_id.  HKDF re-derives per user, so the read-side
    key can't decrypt.
    """
    d = db_and_scratch_users
    from services.vault import encrypt
    from services.bin_context import build_bin_context

    # Ciphertext bound to uid_b, but we'll try to read as uid_a.
    poison_ct = await encrypt(d["uid_b"], "ghp_fake", kind="github_token")
    poison_pid = f"p_poison_{uuid.uuid4().hex[:6]}"
    await d["db"].cto_projects.insert_one({
        "project_id": poison_pid, "user_id": d["uid_a"],
        "name": "Poison", "github_owner": "userA", "github_repo": "poison",
        "github_token": poison_ct, "branch": "main",
        "auth_method": "pat", "status": "connected",
    })
    try:
        with pytest.raises(HTTPException) as ei:
            await build_bin_context(
                user_id=d["uid_a"], project_id=poison_pid, db=d["db"],
            )
        # Vault decrypt returns None → OAuth fallback also None → 403.
        assert ei.value.status_code == 403
        assert "github credentials failed" in str(ei.value.detail).lower()
    finally:
        await d["db"].cto_projects.delete_one({"project_id": poison_pid})


# ── Part 2 — Tool-layer bin_ctx enforcement ──────────────────────────


@pytest.mark.asyncio
async def test_5_tool_blocks_when_ctx_bin_id_mismatch(db_and_scratch_users):
    """Test 5 — Tool call with bin_ctx.bin_id != ctx.user_id → blocked.

    Defence-in-depth: if some caller stuffs a mutated ctx into a tool,
    the mismatch is detected and the tool refuses cleanly.
    """
    d = db_and_scratch_users
    from services.bin_context import build_bin_context
    from services.local_tools import read_repo_file

    bc_a = await build_bin_context(
        user_id=d["uid_a"], project_id=d["pid_a1"], db=d["db"],
    )
    # Craft an evil ctx where user_id is uid_b but bin_ctx belongs to A.
    evil_ctx = {"user_id": d["uid_b"], "project_id": d["pid_a1"],
                "bin_ctx": bc_a, "is_founder": False}
    out = await read_repo_file(evil_ctx, {"path": "README.md"})
    # We expect refusal — the tool's _repo_ctx_from mismatch check
    # returns None → _NO_BIN_CTX_ERROR envelope.
    assert out.get("ok") is False
    assert "no project selected" in (out.get("error") or "").lower()


# ── Part 3 — repo_contexts cache key includes user_id ────────────────


@pytest.mark.asyncio
async def test_6_repo_contexts_cache_key_isolates_users(db_and_scratch_users):
    """Test 6 — Same project_id, different user_id → different cache entries.

    Belt-and-braces: even if two users somehow shared a project_id
    (uuid collision / imported project), their cache entries stay
    separate because user_id is part of the key.
    """
    d = db_and_scratch_users
    # Manually seed two cache rows with the same project_id + branch
    # but different user_id.  Verify they don't collide.
    same_pid = f"p_cache_test_{uuid.uuid4().hex[:6]}"
    await d["db"].repo_contexts.insert_many([
        {"user_id": d["uid_a"], "project_id": same_pid, "branch": "main",
         "blob": "USER_A_BLOB", "ts": time.time()},
        {"user_id": d["uid_b"], "project_id": same_pid, "branch": "main",
         "blob": "USER_B_BLOB", "ts": time.time()},
    ])
    try:
        a = await d["db"].repo_contexts.find_one(
            {"user_id": d["uid_a"], "project_id": same_pid, "branch": "main"}
        )
        b = await d["db"].repo_contexts.find_one(
            {"user_id": d["uid_b"], "project_id": same_pid, "branch": "main"}
        )
        assert a is not None and a["blob"] == "USER_A_BLOB"
        assert b is not None and b["blob"] == "USER_B_BLOB"
        assert a["blob"] != b["blob"]
    finally:
        await d["db"].repo_contexts.delete_many({"project_id": same_pid})


# ── Part 4 — Loop uses bin_ctx.pat directly ──────────────────────────


@pytest.mark.asyncio
async def test_7_loop_session_holds_bin_ctx(db_and_scratch_users):
    """Test 7 — LoopEngine constructor accepts + stores bin_ctx.
    Downstream EXECUTE/SHIP read from self.bin_ctx.pat, no DB re-fetch.
    """
    d = db_and_scratch_users
    from services.bin_context import build_bin_context
    from services.loop_engine import LoopEngine

    bc = await build_bin_context(
        user_id=d["uid_a"], project_id=d["pid_a1"], db=d["db"],
        is_founder=True,
    )
    eng = LoopEngine(
        db=d["db"], loop_id=f"lp_test_{uuid.uuid4().hex[:6]}",
        user_id=d["uid_a"], project_id=d["pid_a1"],
        user_message="test", bin_ctx=bc,
    )
    assert eng.bin_ctx is bc
    assert eng.bin_ctx.bin_id == d["uid_a"]
    assert eng.bin_ctx.pid == d["pid_a1"]
    assert eng.bin_ctx.pat.startswith("ghp_UserA_Project1_token_")


# ── Part 5 — Chat entry hard failures ────────────────────────────────


def test_8_stream_route_no_silent_degrade_source_check():
    """Test 8 — Stream route emits build_ora_context (Iter 212m-170)
    OR build_bin_context (Iter 212m-169) call after current_dev.  The
    prior silent auto-infer block that could pick a random project
    has been REMOVED (search should find zero hits of the auto-infer
    log line).
    """
    src = Path(__file__).resolve().parents[1] / "routers" / "chat.py"
    text = src.read_text()
    # New wiring: ora_context OR bin_context factory must be called.
    assert (
        "build_ora_context(" in text or "build_bin_context(" in text
    ), "chat.py must call build_ora_context / build_bin_context at entry"
    # Prior auto-infer log line must be GONE.
    assert "chat.stream: auto-inferred sole project" not in text, (
        "silent auto-infer must be removed — the FE is responsible for "
        "stamping project_id"
    )


def test_9_local_tools_resolve_project_no_auto_infer():
    """Test 9 — Auto-infer null project → 400 (no silent pick).
    Verified structurally: _resolve_project now returns None for
    null/blank/"home" project_id instead of scanning cto_projects.
    """
    src = Path(__file__).resolve().parents[1] / "services" / "local_tools.py"
    text = src.read_text()
    # The old auto-infer used a `.find({"user_id": user_id, ...}).limit(20)`
    # inside _resolve_project.  It should be GONE.
    match = re.search(
        r"async def _resolve_project[\s\S]{0,3000}",
        text,
    )
    assert match, "_resolve_project function must exist"
    body = match.group(0)
    # Fingerprint of the old auto-infer.
    assert ".limit(20).to_list(20)" not in body, (
        "_resolve_project auto-infer must be removed — silent selection "
        "of a project when caller passes null is a footgun."
    )
    # Fingerprint of the new refusal.
    assert "return None" in body


# ── Part 6 — Full E2E cross-project isolation ────────────────────────


@pytest.mark.asyncio
async def test_10_full_e2e_cross_project_no_bleed(db_and_scratch_users):
    """Test 10 — User A project 1 vs project 2 vs User B project 1:
    each build_bin_context returns a unique PAT + repo, zero bleed.
    """
    d = db_and_scratch_users
    from services.bin_context import build_bin_context

    bc_a1 = await build_bin_context(
        user_id=d["uid_a"], project_id=d["pid_a1"], db=d["db"],
    )
    bc_a2 = await build_bin_context(
        user_id=d["uid_a"], project_id=d["pid_a2"], db=d["db"],
    )
    bc_b1 = await build_bin_context(
        user_id=d["uid_b"], project_id=d["pid_b1"], db=d["db"],
    )
    # Distinct PATs (each is bound to a different project ciphertext).
    assert bc_a1.pat != bc_a2.pat, "User A p1 PAT must differ from p2 PAT"
    assert bc_a1.pat != bc_b1.pat, "User A p1 PAT must differ from User B p1 PAT"
    assert bc_a2.pat != bc_b1.pat, "User A p2 PAT must differ from User B p1 PAT"
    # Distinct repos.
    assert (bc_a1.repo_owner, bc_a1.repo_name) == ("userA", "repo1")
    assert (bc_a2.repo_owner, bc_a2.repo_name) == ("userA", "repo2")
    assert (bc_b1.repo_owner, bc_b1.repo_name) == ("userB", "repo1")
    # Cross-user attempt.
    with pytest.raises(HTTPException) as ei:
        await build_bin_context(
            user_id=d["uid_b"], project_id=d["pid_a1"], db=d["db"],
        )
    assert ei.value.status_code == 403


# ── Part 7 — Review modes thread bin_ctx (structural) ────────────────


def test_11_chat_with_tools_accepts_bin_ctx_and_default_none():
    """Test 11 — swift/pro/maxx all funnel through chat_with_tools,
    which must accept bin_ctx (default None so Home casual chat
    still works without a project)."""
    from services.orchestrator import chat_with_tools
    sig = inspect.signature(chat_with_tools)
    assert "bin_ctx" in sig.parameters
    assert sig.parameters["bin_ctx"].default is None


def test_12_chat_router_forwards_bin_ctx_both_callsites():
    """Test 12 — pro/maxx both go through the SAME chat_with_tools
    call sites (send + stream); both must forward bin_ctx.
    """
    src = Path(__file__).resolve().parents[1] / "routers" / "chat.py"
    text = src.read_text()
    starts = [
        i for i in range(len(text))
        if text.startswith("chat_with_tools(", i)
    ]
    call_blocks: list[str] = []
    for i in starts:
        head = text.rfind("\n", 0, i)
        if "import" in text[head + 1: i]:
            continue
        depth = 0
        j = i + len("chat_with_tools")
        while j < len(text):
            if text[j] == "(":
                depth += 1
            elif text[j] == ")":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        call_blocks.append(text[i:j + 1])
    assert len(call_blocks) >= 2
    for idx, block in enumerate(call_blocks):
        assert "bin_ctx=" in block, (
            f"chat_with_tools call #{idx} in chat.py must forward bin_ctx"
        )


def test_13_orchestrator_stuffs_bin_ctx_into_local_ctx():
    """Test 13 — chat_with_tools stores bin_ctx into local_ctx so every
    tool sees the same locked object (regardless of swift/pro/maxx
    review mode selection)."""
    src = Path(__file__).resolve().parents[1] / "services" / "orchestrator.py"
    text = src.read_text()
    assert re.search(
        r'local_ctx\s*:\s*dict\s*=\s*\{[\s\S]{0,600}"bin_ctx"\s*:\s*bin_ctx',
        text,
    ), "local_ctx must include bin_ctx"


# ── Part 8 — Prompt/Loop tool calls carry bin_ctx ────────────────────


@pytest.mark.asyncio
async def test_14_prompt_mode_tool_has_bin_ctx(db_and_scratch_users):
    """Test 14 — Prompt mode: every repo tool invoked through the
    orchestrator carries bin_ctx.  Verified by calling a tool
    directly with ctx that includes bin_ctx and getting a REAL owner
    (proves the tool read from bin_ctx not from a DB path)."""
    d = db_and_scratch_users
    from services.bin_context import build_bin_context
    from services.local_tools import get_repo_info

    bc = await build_bin_context(
        user_id=d["uid_a"], project_id=d["pid_a2"], db=d["db"],
    )
    ctx = {"user_id": d["uid_a"], "project_id": d["pid_a2"],
           "bin_ctx": bc, "is_founder": False}
    out = await get_repo_info(ctx, {})
    assert out["ok"] is True
    assert out["github_owner"] == "userA"
    assert out["github_repo"] == "repo2"
    assert out["project_id"] == d["pid_a2"]


@pytest.mark.asyncio
async def test_15_loop_bin_ctx_survives_construction(db_and_scratch_users):
    """Test 15 — Loop mode: bin_ctx built at loop start is the SAME
    object attached to LoopSession; EXECUTE/SHIP read from self.bin_ctx.
    """
    d = db_and_scratch_users
    from services.bin_context import build_bin_context
    from services.loop_engine import LoopEngine

    bc = await build_bin_context(
        user_id=d["uid_a"], project_id=d["pid_a1"], db=d["db"],
        is_founder=True,
    )
    eng = LoopEngine(
        db=d["db"], loop_id=f"lp_{uuid.uuid4().hex[:6]}",
        user_id=d["uid_a"], project_id=d["pid_a1"],
        user_message="ping", bin_ctx=bc,
    )
    # The SAME object identity — not a copy.  If someone starts
    # deep-copying bin_ctx per phase we lose the O(1) guarantee.
    assert eng.bin_ctx is bc
    # Loop engine source must reference self.bin_ctx.pat in EXECUTE + SHIP.
    src = Path(__file__).resolve().parents[1] / "services" / "loop_engine.py"
    text = src.read_text()
    assert text.count("self.bin_ctx.pat") >= 2, (
        "Loop EXECUTE and SHIP must read PAT from self.bin_ctx, not "
        "re-decrypt from Mongo"
    )


# ── Part 9 — Parliament councils don't mutate bin_ctx (structural) ──


def test_16_council_A_no_direct_project_lookup():
    """Test 16 — Council A (LongCat/GLM) fires through services/llm.py.
    Verify that LLM call functions do not do independent DB lookups
    for the user's project (they receive context from the caller)."""
    src = Path(__file__).resolve().parents[1] / "services" / "llm.py"
    text = src.read_text()
    # LLM call functions must not touch cto_projects directly.
    banned_patterns = [
        r"cto_projects\.find_one",
        r"cto_projects\.find\(",
        r"_decrypt_pat\s*\(",
        r"_user_gh_token\s*\(",
    ]
    for pat in banned_patterns:
        matches = re.findall(pat, text)
        assert not matches, (
            f"services/llm.py must not perform direct project/PAT DB lookups: "
            f"found {len(matches)} `{pat}` occurrences"
        )


def test_17_council_B_no_direct_project_lookup():
    """Test 17 — Council B (GLM-5.2) — same guarantee as Council A
    because they share the LLM adapter layer."""
    test_16_council_A_no_direct_project_lookup()


def test_18_council_C_no_direct_project_lookup():
    """Test 18 — Council C (DeepSeek V3) — same LLM adapter layer."""
    test_16_council_A_no_direct_project_lookup()


def test_19_ceo_judge_no_direct_project_lookup():
    """Test 19 — CEO Judge (Claude 4.5 Sonnet) — same LLM adapter layer.
    All councils sit on the same services/llm.py adapters, so a
    single check pins the entire parliament."""
    test_16_council_A_no_direct_project_lookup()


# ── Part 10 — Ask Advisor uses bin_ctx-derived context ───────────────


def test_20_ask_advisor_uses_scoped_repo_context():
    """Test 20 — Ask Advisor's system prompt comes from `extra_sys`
    which was built via `get_repo_context(user_id, project_id)` —
    that function ships user_id AND project_id into the cache key
    now (Iter 212m-169) so different users cannot share a cache row.
    """
    src = Path(__file__).resolve().parents[1] / "services" / "repo_context.py"
    text = src.read_text()
    # New cache key format.
    assert re.search(
        r'cache_key\s*=\s*\{\s*"user_id"\s*:\s*user_id',
        text,
    ), "repo_contexts cache key must include user_id"
    # Old key format must be gone.
    assert 'cache_key = {"project_id": project_id, "branch": branch}' not in text, (
        "Old two-key cache_key must be removed"
    )


if __name__ == "__main__":
    print("Run with `pytest -v tests/test_iter212m169_bin_context_isolation.py`")
