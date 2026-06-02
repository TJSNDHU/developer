"""
tests/test_iter57_repo_context_and_brain_memory.py
====================================================

Iter 57 — root fix for the recurring user complaint:
"AUREM repo me kuch nahin dekhta, README ke baahar ka kuch poochho toh
bolta hai 'mere README me iska zikr nahin'. Aur commit ke baad bhi
agle chat me kuch yaad nahin rehta."

Three source-level pins:

  1. `repo_context._wrap` must explicitly mandate tool use for files
     not in the inlined slice — the old "Answer using ONLY this real
     data" wording trained the model to refuse repo questions.
  2. `project_brain._build_context_string` must surface recent commits
     from `event_log` so a fresh chat turn knows what AUREM shipped.
  3. `chat.chat_stream` must inject `get_brain_context(...)` into the
     system prompt (was only used by the CTO worker before).
  4. Git-path worker must fire `update_brain_after_commit` on success
     (was only the API path).
"""
from __future__ import annotations
import os


def _read(rel: str) -> str:
    path = os.path.join(os.path.dirname(__file__), "..", rel)
    with open(path, encoding="utf-8") as fh:
        return fh.read()


# ─── Fix 1 — repo_context wording ───────────────────────────────────────

def test_repo_context_wrap_mandates_tool_use_for_non_inlined_files():
    """The system prompt block returned by `_wrap` must explicitly tell
    the model to call `read_repo_file` when the user asks about a path
    that isn't in the inlined slice."""
    from services.repo_context import _wrap
    out = _wrap("acme", "widgets", "main",
                "src/\n  app.py\n  routes.py\n",
                "--- README.md ---\nWidgets project\n", "")
    # The new mandatory-tool-use line must be present.
    assert "read_repo_file" in out
    # And the smoking-gun "Answer using ONLY this real data" string
    # must be GONE (it was the line training the model to refuse).
    assert "ONLY this real" not in out
    # The fix line must teach the model not to say "not in README".
    assert "not in the README" in out or "Never say" in out


def test_repo_context_wrap_still_includes_tree_and_inlined():
    from services.repo_context import _wrap
    out = _wrap("o", "r", "main", "src/app.py", "--- README ---\nhi", "")
    assert "src/app.py" in out
    assert "README" in out


# ─── Fix 2 — brain surfaces commit history ──────────────────────────────

def test_brain_context_includes_recent_commits():
    """Without this, ORA had no idea what was just shipped — the user's
    "memory update kyun nahin hota" bug."""
    from services.project_brain import _build_context_string
    brain = {
        "tech_stack": ["python", "fastapi", "react"],
        "event_log": [
            {"type": "commit",
             "description": "Add apollo API client to scout module",
             "files": ["backend/scout/apollo.py", "backend/scout/leads.py"],
             "correction_applied": False},
            {"type": "commit",
             "description": "Fix scout scheduler picking up zero jobs",
             "files": ["backend/services/cron_schedulers.py"],
             "correction_applied": True},
        ],
    }
    out = _build_context_string(brain)
    assert "Recent commits" in out, "no commit history surfaced"
    assert "apollo" in out
    assert "scout/apollo.py" in out
    assert "cron_schedulers.py" in out
    # Claude correction marker must surface so ORA learns from it.
    assert "Claude reviewer corrected" in out


def test_brain_context_clamps_long_commit_lists():
    """Only the last 6 commits should show — older noise gets dropped."""
    from services.project_brain import _build_context_string
    events = [
        {"type": "commit",
         "description": f"commit number {i}",
         "files": [f"file_{i}.py"]}
        for i in range(20)
    ]
    out = _build_context_string({"event_log": events})
    # 20 commits but only the last 6 show.
    assert "commit number 19" in out
    assert "commit number 14" in out  # 14..19 = last 6
    assert "commit number 5" not in out
    assert "commit number 0" not in out


def test_brain_context_handles_no_events():
    """An empty brain still produces a clean string (no NoneType errors)."""
    from services.project_brain import _build_context_string
    out = _build_context_string({"tech_stack": ["python"]})
    # Should still work — no commits section, no crashes.
    assert "Recent commits" not in out
    assert "python" in out


# ─── Fix 3 — chat router injects brain ──────────────────────────────────

def test_chat_router_injects_brain_context():
    """The chat stream handler must read `get_brain_context` and stitch
    it into `extra_sys`. Without this, brain memory only flows into the
    CTO worker, never into the user-facing chat."""
    src = _read("routers/chat.py")
    assert "get_brain_context" in src
    # The injection must happen inside chat_stream — anchor on the
    # extra_sys assembly to make sure the brain is part of the system
    # prompt the orchestrator sees.
    assert 'brain_ctx' in src
    assert "(repo_ctx, brain_ctx, url_ctx)" in src or \
           "(repo_ctx, url_ctx, brain_ctx)" in src or \
           "brain_ctx" in src.split("extra_sys = ")[1].split("\n", 1)[0]


# ─── Fix 4 — git-path worker parity ─────────────────────────────────────

def test_git_path_worker_updates_brain():
    """`_run_task_with_git` MUST fire `update_brain_after_commit` on
    success — API + git workers must keep parity."""
    src = _read("routers/cto_projects.py")
    # Two call sites total: one in API path (Iter 41), one in git path
    # (Iter 57). If only one exists, the git worker silently loses brain
    # updates whenever it's the active code path.
    assert src.count("update_brain_after_commit") >= 2, (
        "git-path worker still missing the brain update — "
        f"only {src.count('update_brain_after_commit')} reference(s)."
    )
