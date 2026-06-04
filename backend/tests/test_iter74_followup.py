"""
Iter 74 follow-up — Brain "Show diff →" + task_state SSE + node --check.

Three orthogonal hardenings layered on top of Iter 74 gaps:
  T1  /admin/brain/{pid}/recent-commits exposes SHAs to BrainDump
  T2  worker emits per-file `task_state` SSE frames for the live tape
  T3  JS/TS syntax check uses `node --check` (with graceful fallback)
"""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import time

import pytest


def _read(rel: str) -> str:
    base = os.path.join(os.path.dirname(__file__), "..", "..")
    with open(os.path.join(base, rel), encoding="utf-8") as fh:
        return fh.read()


# ── T1 — brain SHA persistence + recent-commits endpoint ──────────────

def test_record_commit_event_now_stores_sha():
    """update_brain_after_commit accepts sha and stores it on the event."""
    import inspect
    from services.project_brain import update_brain_after_commit
    sig = inspect.signature(update_brain_after_commit)
    assert "sha" in sig.parameters
    src = _read("backend/services/project_brain.py")
    # The event dict carries the sha (capped to 40 chars to be safe)
    assert '"sha":' in src and 'sha or' in src


def test_recent_commits_endpoint_registered():
    """New admin route powers BrainDump's commit rows."""
    from routers.admin import router
    paths = {r.path for r in router.routes}
    assert "/admin/brain/{project_id}/recent-commits" in paths


def test_brain_dump_renders_show_diff_buttons():
    js = _read("frontend/src/pages/BrainDump.jsx")
    assert "/admin/brain/" in js and "/recent-commits" in js
    assert "Show diff →" in js
    assert "ora:prefill" in js
    assert "get_commit_diff" in js
    # Testid the testing agent can target
    assert "brain-commit-show-diff-" in js


def test_chat_panel_listens_for_prefill_event():
    js = _read("frontend/src/components/ChatPanel.jsx")
    assert 'addEventListener("ora:prefill"' in js
    assert "setInput(msg)" in js


# ── T2 — task_state SSE frames + tape rendering ───────────────────────

def test_task_state_emit_wired_into_runner():
    src = _read("backend/routers/cto_projects.py")
    assert "kind=\"task_state\"" in src
    assert "files_done" in src and "files_total" in src
    # Loop walks every file in `edits` so a 5-file ship emits 5 frames
    assert "for _i, _fp in enumerate(_file_list, 1):" in src


def test_task_live_tape_renders_task_state_frames():
    js = _read("frontend/src/components/TaskLiveTape.jsx")
    assert 's.type === "task_state"' in js
    assert "Writing " in js and "files_total" in js
    # Mini progress bar for files_done / files_total
    assert "task-live-tape-state-" in js


@pytest.mark.asyncio
async def test_emit_task_state_frame_shape():
    """Smoke-test that _emit can carry the new structured fields and the
    SSE queue captures them in order."""
    from routers import cto_projects as m
    tid = f"ts-{time.time_ns()}"
    m._task_queues.pop(tid, None)
    await m._emit(tid, "Writing file 1 of 3: a.py",
                  kind="task_state", files_done=1, files_total=3, pct=87)
    await m._emit(tid, "Writing file 3 of 3: c.py",
                  kind="task_state", files_done=3, files_total=3, pct=90)
    q = m._task_queues[tid]
    a = q.get_nowait(); b = q.get_nowait()
    assert a["type"] == b["type"] == "task_state"
    assert a["files_done"] == 1 and a["files_total"] == 3
    assert b["files_done"] == 3 and b["pct"] == 90
    m._task_queues.pop(tid, None)


# ── T3 — node --check JS/TS syntax validation ─────────────────────────

def test_syntax_check_uses_node_check():
    src = _read("backend/routers/cto_projects.py")
    assert "_check_js_syntax" in src
    # The real parser, not the old heuristic
    assert "node" in src and "--check" in src
    assert "FileNotFoundError" in src  # graceful skip when node absent
    # Old heuristic must be GONE — bracket count would re-introduce false positives
    assert "bracket imbalance" not in src


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_node_check_detects_invalid_js(tmp_path):
    """End-to-end node --check call as used by the worker pipeline."""
    bad = tmp_path / "bad.js"
    bad.write_text("function foo( { return 1;\n", encoding="utf-8")
    r = subprocess.run(
        ["node", "--check", str(bad)],
        capture_output=True, text=True, timeout=5,
    )
    assert r.returncode != 0
    assert r.stderr.strip() != ""

    good = tmp_path / "good.js"
    good.write_text("function foo() { return 1; }\n", encoding="utf-8")
    r2 = subprocess.run(
        ["node", "--check", str(good)],
        capture_output=True, text=True, timeout=5,
    )
    assert r2.returncode == 0
