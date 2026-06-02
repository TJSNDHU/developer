"""
tests/test_iter55_tool_call_leak_and_timeout.py
================================================

Iter 55 — root-cause fix for the two recurring bugs the user called out:

  1. Raw ``` tool_call ``` JSON leaking verbatim into the chat bubble
     (was happening on `max_iters` when the LLM's final emission was
     a tool fence with no surrounding prose).
  2. 90s wall-clock timeout that left the user with only a red "AUREM
     timed out" banner — no insight into what the model did manage to
     inspect.

These tests pin the fix at the SOURCE so a future refactor can't
silently bring the leaks back.
"""
from __future__ import annotations
import os
import re

from services.orchestrator import (
    _synthesise_max_iters_summary,
    _is_same_tool_call,
)


# ─── Summary helper ─────────────────────────────────────────────────────

def test_synthesise_summary_never_returns_raw_tool_call():
    """The summary builder must NEVER produce a string containing a
    ```tool_call``` fence — that was the literal symptom the user saw."""
    invs = [
        {"tool": "read_repo_files",
         "args": {"paths": ["backend/middleware/health_probe.py",
                            "backend/routers/pillars_health_router.py"]}},
        {"tool": "read_repo_file",
         "args": {"path": "backend/pillars/sales/worker.py"}},
    ]
    out = _synthesise_max_iters_summary("status of 4 pillars", invs)
    assert "```tool_call" not in out
    assert "```json" not in out
    # Must mention the files so the user knows what got inspected.
    assert "health_probe" in out
    assert "worker.py" in out


def test_synthesise_summary_empty_invocations_still_useful():
    """If we hit the cap before any tool ran, the summary still gives
    the user a concrete next move — not a silent empty string."""
    out = _synthesise_max_iters_summary("audit all four pillars", [])
    assert out.strip()
    assert "narrow scope" in out or "one file" in out or "one pillar" in out


def test_synthesise_summary_truncates_huge_path_lists():
    """A very long inspection list must be clamped (we cap at 6
    visible paths + an overflow indicator)."""
    invs = [
        {"tool": "read_repo_files",
         "args": {"paths": [f"file_{i}.py" for i in range(20)]}},
    ]
    out = _synthesise_max_iters_summary("everything", invs)
    # The visible cap is 6, with a `+N more` indicator.
    assert "+14 more" in out


# ─── Tool-loop guard ────────────────────────────────────────────────────

def test_same_tool_call_detector_matches_identical():
    a = {"tool": "read_repo_file", "args": {"path": "x.py"}}
    b = {"tool": "read_repo_file", "args": {"path": "x.py"}}
    assert _is_same_tool_call(a, b)


def test_same_tool_call_detector_arg_order_independent():
    """JSON arg ordering must not break dedup (paths list order is OK
    as a difference; dict key order is not)."""
    a = {"tool": "search",
         "args": {"query": "auth", "limit": 5, "kind": "code"}}
    b = {"tool": "search",
         "args": {"kind": "code", "limit": 5, "query": "auth"}}
    assert _is_same_tool_call(a, b)


def test_same_tool_call_detector_rejects_different_args():
    a = {"tool": "read_repo_file", "args": {"path": "a.py"}}
    b = {"tool": "read_repo_file", "args": {"path": "b.py"}}
    assert not _is_same_tool_call(a, b)


def test_same_tool_call_detector_rejects_different_tools():
    a = {"tool": "read_repo_file", "args": {"path": "a.py"}}
    b = {"tool": "list_repo_files", "args": {"path": "a.py"}}
    assert not _is_same_tool_call(a, b)


def test_same_tool_call_detector_handles_missing():
    assert not _is_same_tool_call({}, {"tool": "x"})
    assert not _is_same_tool_call(None, None)


# ─── Source-level pins on the broken fallback ──────────────────────────

def test_orchestrator_max_iters_no_longer_falls_back_to_raw_content():
    """The original bug was `if not clean.strip(): clean = content`.
    That line is what leaked the tool fence. After Iter 55 the empty
    fallback must go through `_synthesise_max_iters_summary` instead."""
    src_path = os.path.join(
        os.path.dirname(__file__), "..", "services", "orchestrator.py"
    )
    with open(src_path, encoding="utf-8") as fh:
        src = fh.read()
    # The smoking-gun line MUST be gone.
    bad = re.search(r"if not clean\.strip\(\):\s*\n\s*clean = content", src)
    assert bad is None, (
        "regression — empty strip_tool_calls() fallback is leaking raw "
        "content again. Use _synthesise_max_iters_summary(...)."
    )
    # And the replacement must be in place.
    assert "_synthesise_max_iters_summary(prompt, invocations)" in src


def test_chat_router_timeout_emits_tokens_not_just_error():
    """The 90s timeout used to emit `{"error": ...}` which the
    frontend renders red. Iter 55 emits proper `meta` + `token` SSE
    frames so the user gets a real assistant message."""
    src_path = os.path.join(
        os.path.dirname(__file__), "..", "routers", "chat.py"
    )
    with open(src_path, encoding="utf-8") as fh:
        src = fh.read()
    # The old "AUREM timed out after Xs" error literal must be GONE.
    assert "AUREM timed out after" not in src, (
        "regression — the old red-error timeout banner is back. The "
        "user must get a graceful tokens-stream summary instead."
    )
    # The new path's marker provider name must be present.
    assert "aurem-timeout-guard" in src
    # And it must build the summary from the live tool history.
    assert "_synthesise_max_iters_summary" in src


def test_chat_with_tools_accepts_live_invocations_ref():
    """The timeout guard reads tool history mid-flight via a ref the
    chat router passes in. If that kwarg gets dropped, the timeout
    summary regresses to "nothing inspected"."""
    import inspect
    from services.orchestrator import chat_with_tools
    sig = inspect.signature(chat_with_tools)
    assert "live_invocations_ref" in sig.parameters, (
        "live_invocations_ref kwarg missing — timeout guard cannot see "
        "what tools the worker ran before it ran out of time."
    )


def test_chat_router_passes_live_invocations_ref():
    src_path = os.path.join(
        os.path.dirname(__file__), "..", "routers", "chat.py"
    )
    with open(src_path, encoding="utf-8") as fh:
        src = fh.read()
    assert "live_invocations_ref=_published" in src
