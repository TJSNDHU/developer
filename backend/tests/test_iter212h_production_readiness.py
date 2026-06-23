"""
test_iter212h_production_readiness.py

Iter 212h — Production Readiness Pass. Five fixes in one commit:

  1. Gate 7 (frontend) — allow new-file-creation paths through the
     fabricated-citation guard when surrounding brief text suggests
     ALL paths are net-new files (not references to existing code).
  2. `verified_paths` logging in orchestrator so prod can diagnose
     Gate 7 misfires by cross-checking what the LLM claims vs what
     it actually opened.
  3. Admin error endpoints — public POST /errors/report (dedupes by
     message+url, increments count); admin GET/POST endpoints to
     list, autofix, and resolve.
  4. `_wants_execute` triggers EXECUTE mode whenever a connected
     project is present AND the prompt contains a file-path token —
     even without a verb. Killed the "ORA replies conversationally
     to 'admin.py'" bug.
  5. CitationGuard.enforce() wired into orchestrator's `if flags:`
     block so hallucinated citations trigger an auto-fetch + LLM
     re-prompt with verified contents (instead of just appending a
     soft warning footer).
"""
from __future__ import annotations

from pathlib import Path

import pytest


# ── 1. Gate 7 frontend allowance ─────────────────────────────────

MSGBUBBLE = Path("/app/frontend/src/components/MessageBubble.jsx").read_text(encoding="utf-8")


def test_gate7_allows_new_file_creation():
    """When the brief mentions only paths that aren't in verifiedPaths
    BUT the surrounding language signals new-file creation (new / create
    / add / write / generate), Gate 7 must pass instead of returning
    null."""
    # The allowance check is named `looksLikeAllNewFiles` and uses a
    # NEW_FILE_HINTS regex.
    assert "looksLikeAllNewFiles" in MSGBUBBLE
    assert "NEW_FILE_HINTS" in MSGBUBBLE
    # And the early-return only fires when looksLikeAllNewFiles is false.
    assert "if (!looksLikeAllNewFiles)" in MSGBUBBLE


# ── 2. verified_paths logging in orchestrator ────────────────────

ORCH = Path("/app/backend/services/orchestrator.py").read_text(encoding="utf-8")


def test_orchestrator_logs_verified_paths():
    assert 'logger.info("verified_paths this turn: %s"' in ORCH


def test_orchestrator_verified_paths_includes_both_tools():
    """The set must merge both read_repo_file (single path) AND
    read_repo_files (plural paths)."""
    # The set-builder grabs both tools.
    assert '("read_repo_file",)'  in ORCH
    assert '("read_repo_files",)' in ORCH


# ── 3. Admin error endpoints ─────────────────────────────────────

ADMIN = Path("/app/backend/routers/admin.py").read_text(encoding="utf-8")


def test_admin_error_endpoints_present():
    assert "class ErrorReport(BaseModel)" in ADMIN
    assert '@router.post("/errors/report")' in ADMIN
    assert '@router.get("/errors")'         in ADMIN
    assert '@router.post("/errors/{error_id}/autofix")' in ADMIN
    assert '@router.post("/errors/{error_id}/resolve")' in ADMIN


def test_report_endpoint_is_unauthenticated():
    """The /errors/report endpoint must NOT call _require_admin or
    current_dev — frontend posts must work for any user (including
    anonymous visitors hitting a console error on a public page)."""
    # Carve out the function body for `report_error`.
    start = ADMIN.index('async def report_error(')
    next_def = ADMIN.index('@router.', start)
    body = ADMIN[start:next_def]
    assert "_require_admin" not in body, (
        "/errors/report MUST NOT require admin — public endpoint."
    )
    assert "current_dev" not in body, (
        "/errors/report MUST NOT require auth — public endpoint."
    )


def test_admin_listing_requires_admin():
    """The list/autofix/resolve endpoints must all gate on _require_admin."""
    for route_marker in (
        '@router.get("/errors")',
        '@router.post("/errors/{error_id}/autofix")',
        '@router.post("/errors/{error_id}/resolve")',
    ):
        start = ADMIN.index(route_marker)
        # Find the next route OR end-of-file (last route case).
        next_router = ADMIN.find('@router.', start + len(route_marker))
        end = next_router if next_router != -1 else len(ADMIN)
        body = ADMIN[start:end]
        assert "_require_admin" in body, f"{route_marker} missing admin gate"


def test_dedupe_uses_message_and_url_key():
    """Dedupe key is (message, url) — same message on different pages
    must NOT collapse into one document (helps localise the bug)."""
    start = ADMIN.index('async def report_error(')
    end = ADMIN.index('@router.', start)
    body = ADMIN[start:end]
    assert "update_one(" in body
    assert '"message": msg' in body and '"url": url' in body
    assert "$inc" in body and '"count": 1' in body


def test_autofix_marks_status_queued():
    """The autofix endpoint must flip status to 'queued' before
    dispatching the background task so admins see immediate feedback."""
    start = ADMIN.index('async def autofix_error(')
    end = ADMIN.index('@router.', start)
    body = ADMIN[start:end]
    assert '"autofix_status": "queued"' in body
    assert "asyncio.create_task" in body


# ── 4. _wants_execute fires on bare file paths ───────────────────

def test_wants_execute_fires_on_bare_file_path():
    """Iter 212h — `_wants_execute` returns True when there's a
    connected project AND the message contains a path-with-extension
    token, even without an action verb. Without this, ORA replied
    conversationally to bare `admin.py` and never read the file."""
    from services.orchestrator import _wants_execute
    assert _wants_execute("admin.py",                    True, []) is True
    assert _wants_execute("backend/routers/chat.py",     True, []) is True
    assert _wants_execute("read MessageBubble.jsx",      True, []) is True
    # No path → no force-execute (must use strong verb).
    assert _wants_execute("hello there",                 True, []) is False
    # Path but no connected project → falls through to existing rules.
    assert _wants_execute("admin.py",                   False, []) is False


# ── 5. CitationGuard.enforce() wired in orchestrator ─────────────

def test_citation_guard_enforce_wired_in_orchestrator():
    """The orchestrator must import CitationGuard and call enforce()
    inside the `if flags:` block. The legacy soft-warning footer is
    only emitted as a fallback when enforce() fails or doesn't retry."""
    assert "from services.citation_guard import CitationGuard" in ORCH
    assert "CitationGuard().enforce(" in ORCH
    # The fallback warning still exists for when enforce() degrades.
    assert "if not guard_retried:" in ORCH


def test_citation_guard_uses_local_ctx_and_invocations():
    """`enforce()` is called with `ctx=local_ctx` (so tools have the
    user/project context) and `tool_calls=invocations` (so the guard
    knows what was actually read this turn)."""
    # The call site uses these exact kwargs.
    assert "tool_calls=invocations" in ORCH
    assert "ctx=local_ctx" in ORCH


# ── 6. Frontend reporter ─────────────────────────────────────────

REPORTER = Path("/app/frontend/src/utils/errorReporter.js").read_text(encoding="utf-8")


def test_error_reporter_hooks_three_event_sources():
    """The reporter must intercept console.error, unhandledrejection,
    AND window.onerror so async + sync + console-only errors all
    surface."""
    assert "window.console.error" in REPORTER
    assert 'addEventListener("unhandledrejection"' in REPORTER
    assert 'addEventListener("error"' in REPORTER


def test_error_reporter_dedupes_locally():
    """Local dedupe spares the network even when the backend would
    coalesce — a runaway loop shouldn't burn 1000 req/s."""
    assert "COOLDOWN_MS" in REPORTER
    assert "MAX_PAYLOAD_PER_MIN" in REPORTER


def test_main_jsx_imports_reporter():
    main_jsx = Path("/app/frontend/src/main.jsx").read_text(encoding="utf-8")
    assert 'import "./utils/errorReporter"' in main_jsx


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
