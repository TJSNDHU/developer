"""
tests/test_e2e_iter42.py
=========================
Iter 42 / 43 end-to-end test suite. Pure unit + integration tests — no
external mocks, no `httpx.AsyncClient` calls to a running server. Every
assertion is on real code paths.

Run:
    cd /app/backend
    set -a && source .env && set +a
    python -m pytest tests/test_e2e_iter42.py -v
"""
from __future__ import annotations

import asyncio
import os

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# 1. Mode classifier — A/B/C/D/E routing
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("message,f12,expected", [
    ("hi there",                                   None, "A"),
    ("hello, you working today?",                  None, "A"),
    ("should I use postgres or mongo for this?",   None, "B"),
    ("which is better, JWT or sessions?",          None, "B"),
    ("add a /health endpoint to my repo",          None, "C"),
    ("ship it",                                    None, "C"),
    ("why is my API returning 422?",               None, "D"),
    ("CORS error blocking my frontend",            None, "D"),
    ("audit my codebase and find bugs",            None, "E"),
    ("security review on my repo",                 None, "E"),
    # F12 payload always forces Mode D
    ("check this", {"console_errors": [{"message": "TypeError: cannot read"}]}, "D"),
    ("check this", {"network_errors": [{"status": 500, "url": "/api/x"}]}, "D"),
])
def test_classify_intent(message, f12, expected):
    from routers.chat import classify_intent
    assert classify_intent(message, f12) == expected, \
        f"classify_intent({message!r}) should be {expected}"


# ─────────────────────────────────────────────────────────────────────────────
# 2. Design linter — blocks hardcoded secrets, auto-fixes safe issues
# ─────────────────────────────────────────────────────────────────────────────

def test_lint_blocks_hardcoded_secret():
    from services.design_linter import lint_file_blocks
    file_blocks = {"routers/auth.py":
                   'API_KEY = "sk-abc123secretkey9876"\ndef get_user(): pass'}
    r = lint_file_blocks(file_blocks)
    assert r["blocked"] is True
    assert any("secret" in br.lower() for br in r.get("block_reasons", []))


def test_lint_auto_fix_console_log_and_transition_all():
    from services.design_linter import auto_fix_blocks
    bad = {
        "src/App.jsx":   'function f() {\n  console.log("debug");\n  return 1;\n}',
        "src/styles.css": '.btn { transition: all 0.3s; }',
    }
    fixed, log = auto_fix_blocks(bad)
    assert "console.log" not in fixed["src/App.jsx"]
    assert "transition: all" not in fixed["src/styles.css"]
    assert log["src/App.jsx"]
    assert log["src/styles.css"]


# ─────────────────────────────────────────────────────────────────────────────
# 3. Parallel agents — decides correctly without LLM
# ─────────────────────────────────────────────────────────────────────────────

def test_parallel_agents_decides_to_split_for_multi_domain():
    from services.parallel_agents import should_parallelize
    tree = [
        "routers/auth.py", "routers/chat.py",
        "components/LoginPage.jsx", "components/ChatPanel.jsx",
        "tests/test_auth.py",
    ]
    assert should_parallelize(
        "add authentication to both frontend and backend with tests",
        tree,
    ) is True


def test_parallel_agents_single_path_for_tiny_task():
    from services.parallel_agents import should_parallelize
    assert should_parallelize("fix typo in README", ["README.md"]) is False


# ─────────────────────────────────────────────────────────────────────────────
# 4. PAT encryption — per-user round-trip + cross-user rejection
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_pat_encryption_round_trip():
    if not os.environ.get("AUREM_MASTER_KEY"):
        pytest.skip("AUREM_MASTER_KEY not set in this test env")
    from services.vault import encrypt, decrypt
    pat = "ghp_e2e_test_token_for_iter43_xyz"
    ct = await encrypt("user_alpha", pat, kind="github_token")
    assert ct.startswith("v1:")
    pt = await decrypt("user_alpha", ct, kind="github_token")
    assert pt == pat


@pytest.mark.asyncio
async def test_pat_encryption_rejects_cross_user():
    if not os.environ.get("AUREM_MASTER_KEY"):
        pytest.skip("AUREM_MASTER_KEY not set in this test env")
    from cryptography.fernet import InvalidToken
    from services.vault import encrypt, decrypt
    ct = await encrypt("user_alpha", "secret_token", kind="github_token")
    with pytest.raises(InvalidToken):
        await decrypt("user_beta", ct, kind="github_token")


# ─────────────────────────────────────────────────────────────────────────────
# 5. Mode D — fast-path diagnosis for known errors (zero LLM cost)
# ─────────────────────────────────────────────────────────────────────────────

def test_mode_d_fast_path_cors():
    from services.mode_d_debugger import fast_path_diagnosis
    d = fast_path_diagnosis("CORS policy blocking my fetch from frontend")
    assert d is not None
    assert d["fast_path"] is True
    assert "CORS" in d["cause"]
    assert d["severity"] == "high"


def test_mode_d_fast_path_500():
    from services.mode_d_debugger import fast_path_diagnosis
    d = fast_path_diagnosis("server returned 500 Internal Server Error")
    assert d is not None
    assert d["severity"] == "critical"


def test_mode_d_no_fast_path_for_obscure():
    from services.mode_d_debugger import fast_path_diagnosis
    assert fast_path_diagnosis("weird unique app-specific issue") is None


# ─────────────────────────────────────────────────────────────────────────────
# 6. Mode E — static scan finds quick wins
# ─────────────────────────────────────────────────────────────────────────────

def test_mode_e_quick_wins_finds_missing_readme():
    from services.mode_e_auditor import check_quick_wins
    tree = ["main.py", "routers/auth.py"]   # no README, no .gitignore
    wins = check_quick_wins(tree)
    descriptions = [w["description"] for w in wins]
    assert any("README" in d for d in descriptions)
    assert any(".gitignore" in d for d in descriptions)


def test_mode_e_static_scan_catches_eval():
    from services.mode_e_auditor import static_scan_all
    findings = static_scan_all({
        "backend/bad.py": "result = eval(user_input)\n",
    })
    msgs = [f["message"] for f in findings]
    assert any("eval" in m.lower() for m in msgs)


# ─────────────────────────────────────────────────────────────────────────────
# 7. Council log — fields + indexes + stats counters
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_council_log_creation():
    if not os.environ.get("MONGO_URL"):
        pytest.skip("MONGO_URL not set in this test env")
    from motor.motor_asyncio import AsyncIOMotorClient
    from services.ora_council_logger import log_conversational, get_council_stats
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    before = await db.ora_council_logs.count_documents({})
    await log_conversational(
        db=db, mode="A",
        user_message="pytest hello",
        ora_reply="pytest reply",
        user_id="pytest_user",
        project_id="pytest_proj",
    )
    # log_conversational fires non-blocking via asyncio.create_task; give it a tick
    await asyncio.sleep(0.2)
    after = await db.ora_council_logs.count_documents({})
    assert after > before
    stats = await get_council_stats(db)
    # New v2 fields must exist
    for k in ("D_debug", "E_audit", "A_chat", "B_advice", "C_code"):
        assert k in stats["by_mode"], f"by_mode missing {k}"
    assert "lint_blocks_caught" in stats
    assert "parallel_tasks_run" in stats
    client.close()


# ─────────────────────────────────────────────────────────────────────────────
# 8. Project brain — context shape
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_project_brain_empty_returns_empty_string():
    if not os.environ.get("MONGO_URL"):
        pytest.skip("MONGO_URL not set in this test env")
    from motor.motor_asyncio import AsyncIOMotorClient
    from services.project_brain import get_brain_context
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    ctx = await get_brain_context(db, "nonexistent_pid", "nobody/nothing")
    assert isinstance(ctx, str)
    # Empty brain should produce empty (or near-empty) context — never crash
    assert len(ctx) < 200
    client.close()
