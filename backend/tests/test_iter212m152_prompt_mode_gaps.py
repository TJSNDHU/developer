"""
Iter 212m-152 — Prompt-mode production gap fixes.

Three fixes shipped as one surgical change:
  Fix 1 — Tool namespace reduction via core/tool_router.py
  Fix 2 — Mandatory syntax gate before write_repo_file commits
  Fix 3 — Context trim after iter 2 in chat_with_tools

Constraints under test:
  • Intent gateway, JWT, rate limit, persona, Vanguard, Loop mode,
    Parliament, ORA, codebase-health all UNTOUCHED.
  • Only orchestrator.py, local_tools.py, tool_router.py (new)
    appear in git diff.
"""
from pathlib import Path

import pytest

from core import tool_router as tr
from services import orchestrator as orch
from services import local_tools as lt


_BACKEND = Path(__file__).resolve().parent.parent


# ─── Fix 1 — tool_router ──────────────────────────────────────────────

def test_tool_router_module_shape():
    for k in ("code", "query", "web", "deploy", "debug", "casual"):
        assert k in tr.TOOL_GROUPS
    assert tr.TOOL_GROUPS["casual"] == []


@pytest.mark.parametrize("msg,expected_group", [
    ("fix the auth bug in routes/api.py",          "code"),
    ("refactor the login function",                "code"),
    ("show me my pipeline status",                 "query"),
    ("list all the open issues",                   "query"),
    ("search latest Python docs for asyncio",      "web"),
    ("fetch the url and summarize",                "web"),
    ("deploy to production",                       "deploy"),
    ("vercel build the staging preview",           "deploy"),
    ("debug the failing test in /tests/x.py",      "debug"),
    ("run the script and check console errors",    "debug"),
])
def test_tool_router_picks_correct_group(msg, expected_group):
    assert tr.pick_group(msg, "agentic") == expected_group


def test_tool_router_casual_returns_empty():
    assert tr.get_tools_for_task("good morning", "casual") == []
    assert tr.pick_group("anything",            "casual") == "casual"


def test_tool_router_agentic_default_on_zero_signal():
    """Random message with no signals should fall back to `code`
    (agentic) — safer default than empty."""
    tools = tr.get_tools_for_task("xyzzy 123", "agentic")
    assert tools
    # All returned tools belong to the code group.
    assert set(tools).issubset(set(tr.TOOL_GROUPS["code"]))


def test_tool_router_query_default_on_zero_signal():
    tools = tr.get_tools_for_task("xyzzy 123", "query")
    assert tools
    assert set(tools).issubset(set(tr.TOOL_GROUPS["query"]))


def test_tool_router_code_plus_deploy_combines_tools():
    """When code wins primary AND deploy signals are also present,
    deploy tools must be appended (spec: combine code+deploy when
    code is primary)."""
    tools = tr.get_tools_for_task(
        "fix the bug in auth.py refactor the login function then deploy",
        "agentic",
    )
    # Code core present (code won primary — more code signals than deploy).
    assert "write_repo_file" in tools
    # Deploy tools added because deploy signal was also present.
    assert "vercel_trigger_deploy_hook" in tools


def test_tool_router_dramatically_reduces_namespace():
    """Web-only task must return a small (≤ 6) catalog."""
    tools = tr.get_tools_for_task("search the latest react docs", "agentic")
    assert 1 <= len(tools) <= 6


def test_orchestrator_wires_tool_router():
    src = (_BACKEND / "services" / "orchestrator.py").read_text()
    assert "from core.tool_router import" in src
    assert "_tool_router_pick" in src
    assert "tool_router:" in src    # log line


# ─── Fix 2 — Syntax gate ──────────────────────────────────────────────

def test_syntax_check_blocks_broken_python():
    """Broken Python must be flagged by py_compile."""
    res = lt._run_syntax_check(
        content="def foo(\n    pass\n",
        file_path="bad.py", ext=".py",
    )
    assert res["has_errors"] is True
    assert "SyntaxError" in res["errors"] or "syntax" in res["errors"].lower() \
        or "expected" in res["errors"].lower()


def test_syntax_check_passes_valid_python():
    res = lt._run_syntax_check(
        content="def foo():\n    return 1\n",
        file_path="good.py", ext=".py",
    )
    assert res["has_errors"] is False
    assert not res.get("skipped")


def test_syntax_check_blocks_broken_javascript():
    res = lt._run_syntax_check(
        content="function foo( { return 1 }",
        file_path="bad.js", ext=".js",
    )
    # If node is available we get has_errors=True; if missing
    # we get skipped=True.  Either is acceptable behaviour (fail-open).
    assert res["has_errors"] is True or res.get("skipped") is True


def test_syntax_check_passes_valid_javascript():
    res = lt._run_syntax_check(
        content="function foo() { return 1; }\n",
        file_path="good.js", ext=".js",
    )
    assert res["has_errors"] is False


def test_syntax_check_empty_content_skipped():
    res = lt._run_syntax_check(content="", file_path="x.py", ext=".py")
    assert res["has_errors"] is False
    assert res["skipped"] is True


@pytest.mark.asyncio
async def test_write_repo_file_blocks_broken_python(monkeypatch):
    """End-to-end: write_repo_file must reject broken Python BEFORE
    the GitHub commit call.  Asserts no commit happened (monkeypatched
    commit_files would raise if called)."""
    # Stub _resolve_project so the function reaches the syntax gate.
    async def _stub_resolve(uid, pid):
        return {
            "github_owner": "test-owner", "github_repo": "test-repo",
            "branch": "main", "github_token": "FAKE_TOKEN",
        }
    monkeypatch.setattr(lt, "_resolve_project", _stub_resolve)

    # Sentinel: if anyone calls commit_files we know the gate failed.
    commit_called = {"hit": False}
    async def _stub_commit(**kwargs):
        commit_called["hit"] = True
        return {"sha": "abc", "html_url": "x", "path": "x"}
    monkeypatch.setattr(
        "services.github_api_writer.commit_files", _stub_commit, raising=False,
    )

    # Stub Vanguard so it passes through (we're testing the syntax
    # gate, not the Vanguard pre-scan).
    monkeypatch.setattr(
        "services.vanguard_scanner.scan_file_blocks",
        lambda blocks: [], raising=False,
    )
    monkeypatch.setattr(
        "services.vanguard_scanner.has_critical",
        lambda findings: False, raising=False,
    )

    res = await lt.write_repo_file(
        ctx={"user_id": "u1", "project_id": "p1"},
        args={"path": "bad.py", "content": "def foo(\n    pass\n"},
    )
    assert res["ok"] is False
    assert res["error"] == "syntax_gate_blocked"
    assert "Syntax errors" in res["message"]
    assert commit_called["hit"] is False, \
        "Syntax gate must run BEFORE commit_files"


@pytest.mark.asyncio
async def test_write_repo_file_passes_valid_python(monkeypatch):
    """Sanity: valid Python passes the gate and reaches commit."""
    async def _stub_resolve(uid, pid):
        return {
            "github_owner": "test-owner", "github_repo": "test-repo",
            "branch": "main", "github_token": "FAKE_TOKEN",
        }
    monkeypatch.setattr(lt, "_resolve_project", _stub_resolve)

    commit_called = {"hit": False}
    async def _stub_commit(**kwargs):
        commit_called["hit"] = True
        return {"sha": "abc123", "html_url": "https://x", "path": "good.py"}
    monkeypatch.setattr(
        "services.github_api_writer.commit_files", _stub_commit, raising=False,
    )
    monkeypatch.setattr(
        "services.vanguard_scanner.scan_file_blocks",
        lambda blocks: [], raising=False,
    )
    monkeypatch.setattr(
        "services.vanguard_scanner.has_critical",
        lambda findings: False, raising=False,
    )

    res = await lt.write_repo_file(
        ctx={"user_id": "u1", "project_id": "p1"},
        args={"path": "good.py", "content": "def foo():\n    return 1\n"},
    )
    assert commit_called["hit"] is True
    # `ok` may be True or come from the stubbed commit; the important
    # thing is the gate didn't block.
    assert res.get("error") != "syntax_gate_blocked"


def test_local_tools_has_syntax_check_helper():
    src = (_BACKEND / "services" / "local_tools.py").read_text()
    assert "def _run_syntax_check" in src
    assert "syntax_gate_blocked" in src
    assert "syntax_gate BLOCKED" in src or "syntax_gate" in src


# ─── Fix 3 — Context trim ─────────────────────────────────────────────

def test_trim_noop_when_few_blocks():
    """Transcript with 0/1/2 tool-result blocks must NOT be touched."""
    base = "USER: do thing\n"
    block = ("=== TOOL RESULTS (iter {n}) ===\n"
             "{{some result}}\n=== END TOOL RESULTS ===\n")
    for n_blocks in (0, 1, 2):
        t = base + "".join(block.format(n=i) for i in range(1, n_blocks + 1))
        out, count = orch._trim_tool_results(t)
        assert out == t
        assert count == 0


def test_trim_compresses_older_blocks():
    """With 4 blocks, the 2 oldest get truncated; the 2 newest stay."""
    big_payload = "X" * 8000     # > TRIM_OLD_BLOCK_CHARS
    base = "USER: do thing\n"
    blocks = [
        f"=== TOOL RESULTS (iter {i}) ===\n{big_payload}\n=== END TOOL RESULTS ===\n"
        for i in range(1, 5)
    ]
    t = base + "".join(blocks)
    out, count = orch._trim_tool_results(t)
    assert count == 2
    # The last two blocks must still contain the full payload.
    assert blocks[2] in out, "iter 3 block must be untouched"
    assert blocks[3] in out, "iter 4 block must be untouched"
    # The first two block payloads must have been compressed.
    assert blocks[0] not in out, "iter 1 block must have been trimmed"
    assert blocks[1] not in out, "iter 2 block must have been trimmed"
    assert "trimmed for context efficiency" in out


def test_trim_handles_short_blocks_gracefully():
    """Blocks already under the cap aren't replaced."""
    base = "USER: do thing\n"
    short_blocks = [
        f"=== TOOL RESULTS (iter {i}) ===\nsmall\n=== END TOOL RESULTS ===\n"
        for i in range(1, 5)
    ]
    t = base + "".join(short_blocks)
    out, count = orch._trim_tool_results(t)
    # Short blocks below cap → not trimmed, count stays 0.
    assert count == 0
    # All blocks still present verbatim.
    for b in short_blocks:
        assert b in out


def test_orchestrator_wires_trim_call_after_iter_two():
    src = (_BACKEND / "services" / "orchestrator.py").read_text()
    assert "_trim_tool_results(transcript)" in src
    assert "if iters >= 2:" in src
    # Log line surfaces the trim count.
    assert "context_trim:" in src


# ─── No-regression — only the three expected files were touched ──────

def test_only_expected_files_mention_tool_router():
    """grep -rln tool_router /app/backend should return EXACTLY
    core/tool_router.py + services/orchestrator.py."""
    hits = []
    for p in _BACKEND.rglob("*.py"):
        try:
            text = p.read_text()
        except Exception:
            continue
        if "tool_router" in text:
            hits.append(p.relative_to(_BACKEND).as_posix())
    # Tests directory references tool_router too — strip those.
    hits = [h for h in hits if not h.startswith("tests/")]
    assert sorted(hits) == [
        "core/tool_router.py",
        "services/orchestrator.py",
    ], f"tool_router leaked into other modules: {hits}"


def test_intent_gateway_untouched_by_iter_152():
    """Sanity: intent_gateway.py must NOT be modified by this iter."""
    text = (_BACKEND / "core" / "intent_gateway.py").read_text()
    # Iter 149 module still owns these — Iter 152 didn't add anything.
    assert "Iter 212m-149" in text


def test_loop_engine_untouched_by_iter_152():
    """Sanity: loop_engine.py must NOT mention tool_router."""
    text = (_BACKEND / "services" / "loop_engine.py").read_text()
    assert "tool_router" not in text


def test_parliament_untouched_by_iter_152():
    text = (_BACKEND / "core" / "parliament.py").read_text()
    assert "tool_router" not in text


def test_chat_router_untouched_by_iter_152():
    text = (_BACKEND / "routers" / "chat.py").read_text()
    assert "tool_router" not in text
