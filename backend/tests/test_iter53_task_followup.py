"""
tests/test_iter53_task_followup.py
===================================

Iter 53 — post-commit wrap-up message.

When a Mode C task reaches a terminal status (done | failed), the
frontend calls `POST /chat/task-followup` and ORA appends a closing
assistant message to the chat session that explains:

  - what files were touched
  - whether the original ask is likely resolved
  - one concrete verification step

This test suite verifies the endpoint exists, has the right contract,
is idempotent, falls back deterministically when the LLM is unavailable,
and persists the wrap-up into both `db.cto_tasks` and the chat session.
"""
from __future__ import annotations
import os
import inspect

# ─── Endpoint wiring ────────────────────────────────────────────────────

def test_endpoint_registered():
    """The router must expose /chat/task-followup."""
    from routers.chat import router as chat_router
    paths = [r.path for r in chat_router.routes]
    assert "/chat/task-followup" in paths, paths


def test_endpoint_request_body_shape():
    """Body model carries session_id + task_id."""
    from routers.chat import TaskFollowupBody
    fields = TaskFollowupBody.model_fields
    assert "session_id" in fields
    assert "task_id" in fields


def test_endpoint_signature():
    """Endpoint must be async and accept authorization header for auth."""
    from routers.chat import chat_task_followup
    assert inspect.iscoroutinefunction(chat_task_followup)
    sig = inspect.signature(chat_task_followup)
    assert "authorization" in sig.parameters
    assert "body" in sig.parameters


# ─── Helpers ────────────────────────────────────────────────────────────

def test_failed_followup_template_is_deterministic():
    """Failed tasks never call the LLM — fail-fast, fail-honest."""
    from routers.chat import _build_failed_followup
    out = _build_failed_followup(
        original="add a webhook",
        err="git push failed: 403 Forbidden",
        files=["routers/webhook.py", "services/billing.py"],
    )
    assert "Task failed" in out
    # Error visible (truncated):
    assert "403 Forbidden" in out
    # Files listed:
    assert "routers/webhook.py" in out
    # Concrete next action:
    assert "Mode D" in out or "retry" in out.lower()


def test_failed_followup_does_not_explode_on_missing_fields():
    """Empty error / no files is a real production case (clone-failed
    tasks)."""
    from routers.chat import _build_failed_followup
    out = _build_failed_followup(original="", err="", files=[])
    assert "Task failed" in out
    # No literal "None" or "undefined" leaks:
    assert "None" not in out and "undefined" not in out


def test_done_fallback_template_lists_files_and_sha():
    from routers.chat import _build_done_fallback
    out = _build_done_fallback(
        original="wire apollo api",
        summary="Added Apollo client with retry",
        files=["scout/apollo.py", "scout/leads.py"],
        sha="59bf64d",
    )
    assert "59bf64d" in out
    assert "scout/apollo.py" in out
    assert "scout/leads.py" in out
    # Always tells the user how to verify:
    assert "Verify" in out or "verify" in out


def test_done_fallback_handles_no_files():
    from routers.chat import _build_done_fallback
    out = _build_done_fallback(
        original="add tests", summary="", files=[], sha=None,
    )
    # Must still produce SOMETHING readable, not "files: " with nothing.
    assert "no files reported" in out or "_no files" in out
    assert "commit" in out.lower()


def test_followup_sys_prompt_enforces_structure():
    """The system prompt must constrain the model to a specific format
    so the wrap-up doesn't degrade into 'Great question! Here's what I
    did…' fluff."""
    from routers.chat import _FOLLOWUP_SYS
    # Required structure markers:
    assert "Files:" in _FOLLOWUP_SYS
    assert "Likely resolves" in _FOLLOWUP_SYS
    assert "Verify it:" in _FOLLOWUP_SYS
    # Honesty clause:
    assert "Partially" in _FOLLOWUP_SYS
    # Word budget:
    assert "under 90 words" in _FOLLOWUP_SYS or "90 words" in _FOLLOWUP_SYS


# ─── Worker persists files_changed on done ──────────────────────────────

def test_done_status_persists_files_changed():
    """Both worker paths (API + git) must include `files_changed=` in
    the final _set_status call so the wrap-up can list real filenames."""
    src_path = os.path.join(
        os.path.dirname(__file__), "..", "routers", "cto_projects.py"
    )
    with open(src_path, encoding="utf-8") as fh:
        src = fh.read()
    # Both successful-push blocks should record the file list.
    assert src.count("files_changed=list(edits.keys())") >= 2, (
        f"files_changed= present {src.count('files_changed=list(edits.keys())')} "
        f"times — expected at least 2 (API path + git path)."
    )


# ─── Idempotency contract ───────────────────────────────────────────────

def test_endpoint_checks_followup_message_cache():
    """The endpoint reads `followup_message` from the task doc before
    generating — second call must return the cached text, not re-bill
    the LLM."""
    src_path = os.path.join(
        os.path.dirname(__file__), "..", "routers", "chat.py"
    )
    with open(src_path, encoding="utf-8") as fh:
        src = fh.read()
    assert "task.get(\"followup_message\")" in src
    assert "\"cached\": True" in src
    assert "\"followup_message\":" in src  # written back on first run


# ─── Frontend wiring ────────────────────────────────────────────────────

def test_frontend_triggers_followup_on_terminal():
    """ChatPanel must call /chat/task-followup when polling sees the
    task hit done|failed."""
    src_path = os.path.join(
        os.path.dirname(__file__),
        "..", "..", "frontend", "src", "components", "ChatPanel.jsx",
    )
    with open(src_path, encoding="utf-8") as fh:
        src = fh.read()
    assert "/chat/task-followup" in src, (
        "Frontend never calls the wrap-up endpoint."
    )
    # The dedup ref is critical — without it React StrictMode double-fires.
    assert "followupFiredRef" in src
    # The polling effect must dispatch on terminal status.
    assert "onTaskCompleted" in src
    # The new message kind so we can find / de-dupe it.
    assert "task_followup" in src
