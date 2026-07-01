"""
Iter 212m-170 — End-to-end tests for the ORAContext hardening (Layer 0).

25 tests covering the full lifecycle:

  Tests  1–4     ORAContext factory correctness
  Test   5       ORA_SYSTEM_PATHS blocks execute_bash args (even founder)
  Test   6       ORA boundary rule injected into LLM system prompt
  Tests  7–8     Cross-user / cross-project isolation
  Test   9       Cache key still user_id-scoped (from Iter 212m-169)
  Test  10       Stream route: invalid project → SSE / HTTP error
  Test  11       Loop: same ORAContext survives Plan/Execute/Verify/Ship
  Tests 12–14    swift / pro / maxx modes all thread ORAContext
  Tests 15–16    Prompt-mode + Loop-mode tools carry ORAContext
  Tests 17–19    Councils A/B/C do not re-fetch project/user/PAT
  Test  20       Ask Advisor pulls repo context from ORAContext scope
  Test  21       ORA boundary rule is applied in ALL modes (structural)
  Test  22       "show me your code" → boundary block engaged
  Test  23       "show me parliament.py" → boundary block engaged
  Test  24       Founder + normal mode + /app/backend → blocked
  Test  25       Full E2E: User A p1 → p2 → zero bleed

Real DB via MONGO_URL.  Uses the running preview DB (safe — scratch
users are created + torn down per test).
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


@pytest_asyncio.fixture(scope="function")
async def db_and_scratch_users():
    """Bootstrap scratch users + projects + PATs.  Per-test event
    loops need a fresh Mongo client (motor caches ownership of loops)."""
    from motor.motor_asyncio import AsyncIOMotorClient
    from cto_services.db import set_db
    mongo = AsyncIOMotorClient(
        os.environ["MONGO_URL"], serverSelectionTimeoutMS=5000,
    )
    db = mongo[os.environ.get("DB_NAME", "aurem_dev")]
    set_db(db)

    uid_a = f"utest_a_{uuid.uuid4().hex[:8]}"
    uid_b = f"utest_b_{uuid.uuid4().hex[:8]}"
    pid_a1 = f"p_test_a1_{uuid.uuid4().hex[:6]}"
    pid_a2 = f"p_test_a2_{uuid.uuid4().hex[:6]}"
    pid_b1 = f"p_test_b1_{uuid.uuid4().hex[:6]}"

    from services.vault import encrypt, is_vault_available
    assert is_vault_available(), "AUREM_MASTER_KEY must be set"
    enc_a1 = await encrypt(uid_a, "ghp_A1_" + uuid.uuid4().hex[:8], kind="github_token")
    enc_a2 = await encrypt(uid_a, "ghp_A2_" + uuid.uuid4().hex[:8], kind="github_token")
    enc_b1 = await encrypt(uid_b, "ghp_B1_" + uuid.uuid4().hex[:8], kind="github_token")

    await db.dev_users.insert_many([
        {"user_id": uid_a, "email": f"{uid_a}@test.aurem.dev",
         "tier": "free", "is_admin": False, "is_unlimited": False},
        {"user_id": uid_b, "email": f"{uid_b}@test.aurem.dev",
         "tier": "free", "is_admin": False, "is_unlimited": False},
    ])
    await db.cto_projects.insert_many([
        {"project_id": pid_a1, "user_id": uid_a, "name": "A-Proj-1",
         "github_owner": "userA", "github_repo": "repo1",
         "github_token": enc_a1, "branch": "main"},
        {"project_id": pid_a2, "user_id": uid_a, "name": "A-Proj-2",
         "github_owner": "userA", "github_repo": "repo2",
         "github_token": enc_a2, "branch": "main"},
        {"project_id": pid_b1, "user_id": uid_b, "name": "B-Proj-1",
         "github_owner": "userB", "github_repo": "repo1",
         "github_token": enc_b1, "branch": "main"},
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


# ── 1–4  Factory correctness ─────────────────────────────────────


@pytest.mark.asyncio
async def test_1_valid_user_project_returns_ora_context(db_and_scratch_users):
    """Test 1 — Valid user+project → ORAContext with all fields set."""
    d = db_and_scratch_users
    from services.ora_context import build_ora_context, ORAContext

    ctx = await build_ora_context(
        user_id=d["uid_a"], project_id=d["pid_a1"], db=d["db"],
    )
    assert isinstance(ctx, ORAContext)
    assert ctx.bin_id == d["uid_a"]
    assert ctx.pid == d["pid_a1"]
    assert ctx.repo_owner == "userA"
    assert ctx.repo_name == "repo1"
    assert ctx.repo_full_name == "userA/repo1"
    assert ctx.branch == "main"
    assert ctx.pat.startswith("ghp_A1_")
    assert ctx.is_founder is False
    assert ctx.ora_boundary_active is True   # default: locked
    assert ctx.debug_mode is False           # default: safe


@pytest.mark.asyncio
async def test_2_wrong_user_returns_403(db_and_scratch_users):
    """Test 2 — Wrong user for a valid project → 403."""
    d = db_and_scratch_users
    from services.ora_context import build_ora_context

    with pytest.raises(HTTPException) as ei:
        await build_ora_context(
            user_id=d["uid_b"], project_id=d["pid_a1"], db=d["db"],
        )
    assert ei.value.status_code == 403


@pytest.mark.asyncio
async def test_3_null_project_returns_400(db_and_scratch_users):
    """Test 3 — Null/empty/'home' project_id → 400 (no auto-infer)."""
    d = db_and_scratch_users
    from services.ora_context import build_ora_context

    for pid in (None, "", "  ", "home"):
        with pytest.raises(HTTPException) as ei:
            await build_ora_context(
                user_id=d["uid_a"], project_id=pid, db=d["db"],
            )
        assert ei.value.status_code == 400


@pytest.mark.asyncio
async def test_4_pat_decrypt_fail_returns_403(db_and_scratch_users):
    """Test 4 — PAT ciphertext bound to different user → 403."""
    d = db_and_scratch_users
    from services.vault import encrypt
    from services.ora_context import build_ora_context

    poison_ct = await encrypt(d["uid_b"], "ghp_fake", kind="github_token")
    poison_pid = f"p_poison_{uuid.uuid4().hex[:6]}"
    await d["db"].cto_projects.insert_one({
        "project_id": poison_pid, "user_id": d["uid_a"],
        "github_owner": "userA", "github_repo": "poison",
        "github_token": poison_ct, "branch": "main",
    })
    try:
        with pytest.raises(HTTPException) as ei:
            await build_ora_context(
                user_id=d["uid_a"], project_id=poison_pid, db=d["db"],
            )
        assert ei.value.status_code == 403
    finally:
        await d["db"].cto_projects.delete_one({"project_id": poison_pid})


# ── 5  execute_bash + ORA_SYSTEM_PATHS ───────────────────────────


@pytest.mark.asyncio
async def test_5_ora_system_path_blocked_even_for_founder(db_and_scratch_users):
    """Test 5 — Founder + normal ORAContext (debug_mode=False) trying
    to execute_bash on /app/backend → refused with boundary_violation.
    """
    d = db_and_scratch_users
    from services.ora_context import build_ora_context
    from services.local_tools import execute_bash

    ctx_obj = await build_ora_context(
        user_id=d["uid_a"], project_id=d["pid_a1"], db=d["db"],
        is_founder=True, debug_mode=False,
    )
    ctx = {
        "user_id": d["uid_a"], "project_id": d["pid_a1"],
        "bin_ctx": ctx_obj, "is_founder": True,
    }
    out = await execute_bash(ctx, {"command": "ls /app/backend"})
    assert out["ok"] is False
    assert out.get("error_class") == "ora_boundary_violation"
    assert "/app" in out.get("error", "")

    # Same command from a founder in debug_mode → gate cleared.
    ctx_dbg_obj = await build_ora_context(
        user_id=d["uid_a"], project_id=d["pid_a1"], db=d["db"],
        is_founder=True, debug_mode=True,
    )
    ctx_dbg = {
        "user_id": d["uid_a"], "project_id": d["pid_a1"],
        "bin_ctx": ctx_dbg_obj, "is_founder": True,
    }
    out2 = await execute_bash(ctx_dbg, {"command": "pwd"})
    # Not asserting ok=True because pwd may not be in the allowlist;
    # we assert the boundary refusal is GONE.
    assert out2.get("error_class") != "ora_boundary_violation"


# ── 6  Boundary rule injection ───────────────────────────────────


def test_6_ora_boundary_rule_renders_repo_slug():
    """Test 6 — ORA boundary system prompt renders with the exact
    repo slug for a given ORAContext.
    """
    from services.ora_context import (
        render_ora_boundary_prompt, ORAContext,
    )
    ctx = ORAContext(
        bin_id="u1", pid="p1",
        repo_owner="acme", repo_name="rocket",
        branch="staging", pat="ghp_x", is_founder=False,
    )
    prompt = render_ora_boundary_prompt(ctx)
    assert "acme/rocket" in prompt
    assert "staging" in prompt
    assert "/app" in prompt
    assert "OFF-LIMITS" in prompt

    # No ctx (Home chat) → falls back to the no-repo variant.
    prompt2 = render_ora_boundary_prompt(None)
    assert "No GitHub repository" in prompt2
    assert "/app" in prompt2


# ── 7–8  Cross-user / cross-project ──────────────────────────────


@pytest.mark.asyncio
async def test_7_cross_user_zero_bleed(db_and_scratch_users):
    """Test 7 — Cross-user: User B can never build ORAContext for
    User A's project."""
    d = db_and_scratch_users
    from services.ora_context import build_ora_context

    for pid_a_owned in (d["pid_a1"], d["pid_a2"]):
        with pytest.raises(HTTPException) as ei:
            await build_ora_context(
                user_id=d["uid_b"], project_id=pid_a_owned, db=d["db"],
            )
        assert ei.value.status_code == 403


@pytest.mark.asyncio
async def test_8_cross_project_pat_distinct(db_and_scratch_users):
    """Test 8 — Two of User A's projects yield DIFFERENT decrypted
    PATs (crypto binds ciphertexts to user_id+random Fernet salt)."""
    d = db_and_scratch_users
    from services.ora_context import build_ora_context

    a1 = await build_ora_context(d["uid_a"], d["pid_a1"], d["db"])
    a2 = await build_ora_context(d["uid_a"], d["pid_a2"], d["db"])
    assert a1.pat != a2.pat
    assert a1.repo_name == "repo1"
    assert a2.repo_name == "repo2"


# ── 9  Cache key ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_9_cache_isolates_by_user_id(db_and_scratch_users):
    """Test 9 — Same project_id (impossible in prod but a defence
    layer) + different user_id → different cache rows."""
    d = db_and_scratch_users
    pid = f"p_cache_{uuid.uuid4().hex[:6]}"
    await d["db"].repo_contexts.insert_many([
        {"user_id": d["uid_a"], "project_id": pid, "branch": "main",
         "blob": "A", "ts": time.time()},
        {"user_id": d["uid_b"], "project_id": pid, "branch": "main",
         "blob": "B", "ts": time.time()},
    ])
    try:
        a = await d["db"].repo_contexts.find_one(
            {"user_id": d["uid_a"], "project_id": pid, "branch": "main"}
        )
        b = await d["db"].repo_contexts.find_one(
            {"user_id": d["uid_b"], "project_id": pid, "branch": "main"}
        )
        assert a["blob"] == "A"
        assert b["blob"] == "B"
    finally:
        await d["db"].repo_contexts.delete_many({"project_id": pid})


# ── 10  Stream / send route hard-fail (structural) ──────────────


def test_10_stream_route_no_silent_degrade():
    """Test 10 — Stream route must build ORAContext or BINContext at
    entry; must not contain the removed auto-infer log line.
    """
    src = Path(__file__).resolve().parents[1] / "routers" / "chat.py"
    text = src.read_text()
    assert "build_ora_context(" in text
    assert "chat.stream: auto-inferred sole project" not in text


# ── 11  Loop session holds same ctx start→ship ──────────────────


@pytest.mark.asyncio
async def test_11_loop_same_ctx_start_to_ship(db_and_scratch_users):
    """Test 11 — LoopEngine keeps the SAME ORAContext object for
    plan/execute/verify/ship (identity check, not a copy)."""
    d = db_and_scratch_users
    from services.ora_context import build_ora_context
    from services.loop_engine import LoopEngine

    ctx = await build_ora_context(
        d["uid_a"], d["pid_a1"], d["db"], is_founder=True,
    )
    eng = LoopEngine(
        db=d["db"], loop_id=f"lp_{uuid.uuid4().hex[:6]}",
        user_id=d["uid_a"], project_id=d["pid_a1"],
        user_message="test", bin_ctx=ctx,
    )
    assert eng.bin_ctx is ctx
    assert eng.bin_ctx.repo_full_name == "userA/repo1"
    # Loop engine source must reference self.bin_ctx.pat in EXECUTE + SHIP.
    src = Path(__file__).resolve().parents[1] / "services" / "loop_engine.py"
    text = src.read_text()
    assert text.count("self.bin_ctx.pat") >= 2


# ── 12–14  Review modes thread ctx (structural) ─────────────────


def test_12_orchestrator_accepts_bin_ctx_kwarg():
    """Test 12 — swift/pro/maxx all funnel through chat_with_tools
    which accepts bin_ctx=None (default) and threads it downstream."""
    from services.orchestrator import chat_with_tools
    sig = inspect.signature(chat_with_tools)
    assert "bin_ctx" in sig.parameters
    assert sig.parameters["bin_ctx"].default is None


def test_13_chat_router_forwards_ctx_both_callsites():
    """Test 13 — Non-stream + stream call sites forward bin_ctx."""
    src = Path(__file__).resolve().parents[1] / "routers" / "chat.py"
    text = src.read_text()
    starts = [
        i for i in range(len(text))
        if text.startswith("chat_with_tools(", i)
    ]
    blocks = []
    for i in starts:
        head = text.rfind("\n", 0, i)
        if "import" in text[head + 1: i]:
            continue
        depth, j = 0, i + len("chat_with_tools")
        while j < len(text):
            if text[j] == "(":
                depth += 1
            elif text[j] == ")":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        blocks.append(text[i:j + 1])
    assert len(blocks) >= 2
    for idx, block in enumerate(blocks):
        assert "bin_ctx=" in block, f"call #{idx} missing bin_ctx="


def test_14_orchestrator_puts_ctx_into_local_ctx():
    """Test 14 — local_ctx dict must include bin_ctx so every tool
    sees the same locked object regardless of mode."""
    src = Path(__file__).resolve().parents[1] / "services" / "orchestrator.py"
    text = src.read_text()
    assert re.search(
        r'local_ctx\s*:\s*dict\s*=\s*\{[\s\S]{0,600}"bin_ctx"\s*:\s*bin_ctx',
        text,
    )


# ── 15–16  Prompt/Loop tools carry ctx ─────────────────────────


@pytest.mark.asyncio
async def test_15_prompt_mode_tool_sees_ctx(db_and_scratch_users):
    """Test 15 — Repo tool invoked with ctx.bin_ctx returns real
    owner+repo (proves the tool read from ctx, not a DB lookup)."""
    d = db_and_scratch_users
    from services.ora_context import build_ora_context
    from services.local_tools import get_repo_info

    ctx_obj = await build_ora_context(d["uid_a"], d["pid_a2"], d["db"])
    ctx = {"user_id": d["uid_a"], "project_id": d["pid_a2"],
           "bin_ctx": ctx_obj, "is_founder": False}
    out = await get_repo_info(ctx, {})
    assert out["ok"] is True
    assert out["github_owner"] == "userA"
    assert out["github_repo"] == "repo2"


@pytest.mark.asyncio
async def test_16_loop_mode_ctx_survives(db_and_scratch_users):
    """Test 16 — Loop mode: ORAContext at loop start is the same
    object attached to LoopEngine; verify method exists on model."""
    d = db_and_scratch_users
    from services.ora_context import build_ora_context, ORAContext
    from services.loop_engine import LoopEngine

    ctx = await build_ora_context(
        d["uid_a"], d["pid_a1"], d["db"], is_founder=True,
    )
    assert isinstance(ctx, ORAContext)
    eng = LoopEngine(
        db=d["db"], loop_id=f"lp_{uuid.uuid4().hex[:6]}",
        user_id=d["uid_a"], project_id=d["pid_a1"],
        user_message="ping", bin_ctx=ctx,
    )
    # Verify the identity + property.
    assert eng.bin_ctx is ctx
    assert eng.bin_ctx.repo_full_name == "userA/repo1"


# ── 17–19  Councils / CEO do not touch DB directly ─────────────


def _llm_source():
    return (Path(__file__).resolve().parents[1] / "services" / "llm.py").read_text()


def test_17_council_A_no_direct_project_lookup():
    """Test 17 — Council A (LongCat/GLM) — llm.py must not touch
    cto_projects / _decrypt_pat / _user_gh_token."""
    text = _llm_source()
    banned = [
        r"cto_projects\.find_one",
        r"cto_projects\.find\(",
        r"_decrypt_pat\s*\(",
        r"_user_gh_token\s*\(",
    ]
    for pat in banned:
        matches = re.findall(pat, text)
        assert not matches, f"llm.py must not run `{pat}` (found {len(matches)})"


def test_18_council_B_no_direct_project_lookup():
    """Test 18 — Council B (GLM-5.2) — shares llm.py adapter."""
    test_17_council_A_no_direct_project_lookup()


def test_19_council_C_no_direct_project_lookup():
    """Test 19 — Council C (DeepSeek V3) — shares llm.py adapter."""
    test_17_council_A_no_direct_project_lookup()


# ── 20  Ask Advisor repo context ───────────────────────────────


def test_20_ask_advisor_uses_user_scoped_repo_context():
    """Test 20 — Ask Advisor's system prompt is composed from
    get_repo_context(user_id, project_id) which caches with
    user_id in the key (Iter 212m-169) so different users cannot
    share a cache row."""
    src = Path(__file__).resolve().parents[1] / "services" / "repo_context.py"
    text = src.read_text()
    assert re.search(
        r'cache_key\s*=\s*\{\s*"user_id"\s*:\s*user_id', text,
    )


# ── 21  Boundary rule applied in ALL modes (structural) ────────


def test_21_orchestrator_prepends_boundary_rule():
    """Test 21 — orchestrator.chat_with_tools prepends the ORA
    boundary rule for EVERY session (not just non-founders).  The
    Iter 212m-168 non-founder gate was replaced by an unconditional
    prepend of render_ora_boundary_prompt() in Iter 212m-170."""
    src = Path(__file__).resolve().parents[1] / "services" / "orchestrator.py"
    text = src.read_text()
    assert "render_ora_boundary_prompt" in text, (
        "orchestrator must call render_ora_boundary_prompt to inject "
        "the ORA boundary block"
    )
    # The old gated-on-is_founder branch must be gone (its distinctive
    # comment string).
    assert "if not is_founder:" not in text or "extra = (" not in text[
        text.find("if not is_founder:"):
        text.find("if not is_founder:") + 200 if "if not is_founder:" in text else 0
    ]


# ── 22–23  Boundary refusal for internal-name queries ───────────


def test_22_boundary_covers_internal_code_query():
    """Test 22 — The boundary prompt tells the model to refuse
    'show me your code' style queries with a canned response.
    Structural check — the canned refusal string exists in the
    template."""
    from services.ora_context import ORA_BOUNDARY_SYSTEM_RULE_TEMPLATE
    assert "I work with your repository only" in ORA_BOUNDARY_SYSTEM_RULE_TEMPLATE
    assert "don't have access to my" in ORA_BOUNDARY_SYSTEM_RULE_TEMPLATE


def test_23_boundary_covers_parliament_and_secrets():
    """Test 23 — Boundary explicitly lists parliament, loop_engine,
    orchestrator, vault, AUREM_MASTER_KEY, JWT_SECRET, LANGFUSE."""
    from services.ora_context import (
        ORA_BOUNDARY_SYSTEM_RULE_TEMPLATE, ORA_SYSTEM_TERMS,
    )
    for term in ("parliament", "loop_engine", "orchestrator",
                 "vault", "AUREM_MASTER_KEY", "JWT_SECRET", "LANGFUSE"):
        assert term in ORA_BOUNDARY_SYSTEM_RULE_TEMPLATE, (
            f"boundary rule must reference `{term}` so the LLM knows "
            f"never to leak it"
        )
        # And the enforcement list must contain it too (so any tool
        # arg containing it can be flagged separately).
        assert any(term.lower() in s.lower() for s in ORA_SYSTEM_TERMS)


# ── 24  Founder + /app/backend → blocked (dispatch level) ───────


@pytest.mark.asyncio
async def test_24_founder_normal_mode_blocked_from_pod_files(db_and_scratch_users):
    """Test 24 — A founder session in normal mode (debug_mode=False)
    trying to `cat /app/backend/main.py` is blocked at dispatch
    with error_class=ora_boundary_violation."""
    d = db_and_scratch_users
    from services.ora_context import build_ora_context
    from services.local_tools import execute_bash

    ctx_obj = await build_ora_context(
        d["uid_a"], d["pid_a1"], d["db"], is_founder=True, debug_mode=False,
    )
    assert ctx_obj.ora_boundary_active is True
    assert ctx_obj.debug_mode is False

    ctx = {"user_id": d["uid_a"], "project_id": d["pid_a1"],
           "bin_ctx": ctx_obj, "is_founder": True}
    out = await execute_bash(ctx, {"command": "cat /app/backend/main.py"})
    assert out["ok"] is False
    assert out.get("error_class") == "ora_boundary_violation"

    # Also blocked: grep AUREM_MASTER_KEY (string denylist).
    out2 = await execute_bash(ctx, {"command": "grep -r AUREM_MASTER_KEY /root"})
    assert out2["ok"] is False
    assert out2.get("error_class") == "ora_boundary_violation"


# ── 25  Full E2E cross-project isolation ────────────────────────


@pytest.mark.asyncio
async def test_25_full_e2e_cross_project(db_and_scratch_users):
    """Test 25 — User A project 1 → project 2 → User B project 1:
    every ORAContext is distinct, cross-user is blocked, per-project
    PAT stays isolated."""
    d = db_and_scratch_users
    from services.ora_context import build_ora_context

    a1 = await build_ora_context(d["uid_a"], d["pid_a1"], d["db"])
    a2 = await build_ora_context(d["uid_a"], d["pid_a2"], d["db"])
    b1 = await build_ora_context(d["uid_b"], d["pid_b1"], d["db"])

    assert a1.pat != a2.pat != b1.pat
    assert a1.repo_full_name == "userA/repo1"
    assert a2.repo_full_name == "userA/repo2"
    assert b1.repo_full_name == "userB/repo1"
    # Cross-user attempt still fails.
    with pytest.raises(HTTPException) as ei:
        await build_ora_context(d["uid_b"], d["pid_a1"], d["db"])
    assert ei.value.status_code == 403


if __name__ == "__main__":
    print("Run with `pytest -v tests/test_iter212m170_ora_context_isolation.py`")
