"""Iter 212m-4 — Tool-reliability tests.

Covers:
  • orchestrator._wants_execute → catch-all for repo-scoped file/code questions
  • orchestrator._should_inject_tool_reminder → guard before the
    "you MUST call a tool" reminder is appended to first_iter_system
  • local_tools._apply_chunking → 200-line + structure map for large
    files, slice for explicit `lines=[s,e]`, passthrough for small.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.orchestrator import _wants_execute, _should_inject_tool_reminder
from services.local_tools import _apply_chunking


# ──────────────────────────────────────────────────────────────────
# _wants_execute — catch-all repo-scoped triggers.
# ──────────────────────────────────────────────────────────────────


def test_wants_execute_file_path():
    assert _wants_execute("read backend/routers/admin.py", True, []) is True


def test_wants_execute_no_repo():
    assert _wants_execute("read backend/routers/admin.py", False, []) is False


def test_wants_execute_greeting():
    assert _wants_execute("hello how are you", True, []) is False


def test_wants_execute_router_keyword():
    """Iter 212m-4 catch-all: bare topic keyword on connected repo
    should still flip EXECUTE on."""
    assert _wants_execute("show me the routers", True, []) is True


def test_wants_execute_router_keyword_no_repo():
    """Same prompt with no repo connected stays conversational."""
    assert _wants_execute("show me the routers", False, []) is False


def test_wants_execute_thanks_short():
    """Brief acknowledgements must not trip the catch-all even on a
    connected repo."""
    assert _wants_execute("thanks", True, []) is False
    assert _wants_execute("ok cool", True, []) is False


# ──────────────────────────────────────────────────────────────────
# _should_inject_tool_reminder — reminder gate.
# ──────────────────────────────────────────────────────────────────


def test_should_inject_reminder_with_path():
    assert _should_inject_tool_reminder("read auth.py", True) is True


def test_should_inject_reminder_no_repo():
    assert _should_inject_tool_reminder("read auth.py", False) is False


def test_should_inject_reminder_greeting():
    assert _should_inject_tool_reminder("hi there", True) is False


def test_should_inject_reminder_topic_word():
    """Even without a path token, repo-connected `backend/router/service`
    questions must get the reminder."""
    assert _should_inject_tool_reminder("how many routes do we have", True) is True
    assert _should_inject_tool_reminder("list backend services", True) is True


# ──────────────────────────────────────────────────────────────────
# _apply_chunking — file-read chunking contract.
# ──────────────────────────────────────────────────────────────────


def test_chunked_small_file():
    """≤ 12,000-char file passes through untouched."""
    result = _apply_chunking("x" * 100, {})
    assert result["ok"] is True
    assert result["truncated"] is False
    assert result["content"] == "x" * 100


def test_chunked_large_no_hint():
    """> 12k chars, no lines arg → first 200 lines + structure map."""
    body = "\n".join(["def foo():", "    pass"] * 1000)   # ≈ 24k chars
    result = _apply_chunking(body, {})
    assert result["truncated"] is True
    assert "structure" in result
    assert "total_lines" in result
    assert result["total_lines"] == 2000
    # Preview must be exactly the first 200 lines.
    assert len(result["content"].splitlines()) == 200
    # Structure capped at 40 entries.
    assert len(result["structure"]) <= 40
    # And the first entry must reference the very first def.
    assert result["structure"][0].startswith("L1:")


def test_chunked_large_with_lines():
    """> 12k chars + lines=[10,20] → 0-indexed Python slice
    (lines 10..19 inclusive)."""
    lines = [f"line {i}" for i in range(500)]
    big = "\n".join(lines) + "\n" + "x" * 12_000  # > limit
    result = _apply_chunking(big, {"lines": [10, 20]})
    assert result["truncated"] is True
    assert "line 10" in result["content"]
    assert "line 19" in result["content"]
    # And the slice should NOT include line 20 (exclusive end).
    assert "line 20" not in result["content"].split("\n")
    assert "note" in result


def test_chunked_handles_none_content():
    """Defensive: None passed in must not crash; returns empty body."""
    result = _apply_chunking(None, {})
    assert result["ok"] is True
    assert result["truncated"] is False
    assert result["content"] == ""


def test_chunked_structure_detects_router_decorator():
    """Structure regex must pick up @router. lines too."""
    body = (
        "import x\n"
        "from y import z\n"
        "@router.get('/a')\n"
        "async def handler():\n"
        "    pass\n"
    ) * 800   # blow past 12k
    result = _apply_chunking(body, {})
    assert result["truncated"] is True
    decorator_hits = [s for s in result["structure"] if "@router." in s]
    assert decorator_hits, "expected @router.* lines in structure"
    # And async def lines too.
    async_hits = [s for s in result["structure"] if "async def" in s]
    assert async_hits, "expected async def lines in structure"
