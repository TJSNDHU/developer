"""
Iter 212m-147 — Source-pattern contract tests for the Bulk Fix Drawer
diff streaming. Verifies:

1. Backend `_compute_diff_lines()` returns the correct structure for
   add/remove/hunk/context.
2. Backend `fix_pipeline.py` emits `fix-diff` BEFORE `fix-committing`,
   carrying a non-empty diff payload (regression guard).
3. Frontend `FixProgressDrawer.jsx` handles `fix-diff`, `fix-committing`,
   `hydrated`, and `done` phases.
4. Frontend diff lines render with a 40 ms stagger animation (key UX
   requirement from the founder spec).
5. Backend `/health/ora` endpoint is wired with founder gating.
6. Backend `codebase-health/last` returns null when (score=0, total=0)
   defensive guard.
7. Frontend Dashboard health-score effect uses a per-repo cache map
   and a loading flag.
"""
import re
from pathlib import Path


def test_compute_diff_lines_basic_add():
    """The diff helper produces a clean add-only payload."""
    from routers.fix_pipeline import _compute_diff_lines
    before = "line1\nline2\n"
    after  = "line1\nline2\nline3\n"
    diff = _compute_diff_lines(before, after)
    assert len(diff) > 0
    types = {d["type"] for d in diff}
    assert "add" in types
    add_lines = [d for d in diff if d["type"] == "add"]
    assert any("line3" in d["line"] for d in add_lines)


def test_compute_diff_lines_remove_and_add():
    """Mixed remove+add produces both markers."""
    from routers.fix_pipeline import _compute_diff_lines
    before = "alpha\nbeta\ngamma\n"
    after  = "alpha\ndelta\ngamma\n"
    diff = _compute_diff_lines(before, after)
    types = {d["type"] for d in diff}
    assert "add" in types
    assert "remove" in types


def test_compute_diff_lines_no_change_empty():
    """No diff for identical input."""
    from routers.fix_pipeline import _compute_diff_lines
    assert _compute_diff_lines("same\n", "same\n") == []


def test_compute_diff_lines_truncates_at_max():
    """Diff exceeding _MAX_DIFF_LINES is truncated with a sentinel."""
    from routers.fix_pipeline import _compute_diff_lines, _MAX_DIFF_LINES
    before = ""
    after  = "\n".join(f"line {i}" for i in range(_MAX_DIFF_LINES + 50)) + "\n"
    diff = _compute_diff_lines(before, after)
    # Should be capped; last entry is the truncation note.
    assert len(diff) <= _MAX_DIFF_LINES + 1
    assert any("truncated" in d.get("line", "") for d in diff)


def test_fix_pipeline_emits_fix_diff_before_committing():
    """Regression guard: source emits `fix-diff` event before
    `fix-committing` for the staggered animation contract."""
    src = Path(__file__).resolve().parent.parent / "routers" / "fix_pipeline.py"
    text = src.read_text()
    diff_idx = text.find('emit(job_id, "fix-diff"')
    commit_idx = text.find('emit(job_id, "fix-committing"')
    assert diff_idx > 0, "fix-diff emit missing"
    assert commit_idx > 0, "fix-committing emit missing"
    assert diff_idx < commit_idx, (
        "fix-diff must be emitted before fix-committing so the "
        "drawer can animate the diff before the commit badge."
    )


def test_fix_progress_drawer_handles_new_phases():
    """Frontend source contains explicit handlers for the new SSE
    phases introduced by Iter 212m-147."""
    src = Path(__file__).resolve().parent.parent.parent / "frontend" / "src" / "components" / "FixProgressDrawer.jsx"
    text = src.read_text()
    # New phase handlers must exist somewhere in the file.
    assert '"fix-diff"' in text or "'fix-diff'" in text, \
        "Drawer must handle the fix-diff SSE phase"
    assert '"fix-committing"' in text or "'fix-committing'" in text, \
        "Drawer must handle the fix-committing SSE phase"
    assert '"verifying"' in text or "'verifying'" in text, \
        "Drawer must handle the verifying SSE phase"
    # Hydrated + done + restart paths preserved.
    assert "hydrated" in text
    assert '"done"' in text or "'done'" in text


def test_fix_progress_drawer_stagger_animation():
    """Founder spec: each diff line animates in with a 40 ms stagger."""
    src = Path(__file__).resolve().parent.parent.parent / "frontend" / "src" / "components" / "FixProgressDrawer.jsx"
    text = src.read_text()
    # The 40 ms-per-line stagger must be present.
    assert re.search(r"\* 40\s*\}\s*ms`?", text) or "* 40}ms" in text, \
        "Stagger animation must use a 40 ms-per-line delay"
    # And an entrance keyframe.
    assert "diffLineIn" in text
    assert "@keyframes diffLineIn" in text


def test_fix_progress_drawer_active_card_and_completed_list():
    """The new UI separates an Active Fix Card from a Completed list."""
    src = Path(__file__).resolve().parent.parent.parent / "frontend" / "src" / "components" / "FixProgressDrawer.jsx"
    text = src.read_text()
    assert "fix-active-card" in text, "Active fix card must exist"
    assert "fix-completed-list" in text, "Completed fixes list must exist"
    assert "fix-final-summary" in text, "Final summary card must exist"


def test_fix_progress_drawer_preserves_restart_and_localstorage():
    """Preserved from Iter 212m-128, moved to Iter 212m-148 context:
    restart + localStorage persistence now live in FixJobContext."""
    drawer_src = Path(__file__).resolve().parent.parent.parent / "frontend" / "src" / "components" / "FixProgressDrawer.jsx"
    ctx_src    = Path(__file__).resolve().parent.parent.parent / "frontend" / "src" / "components" / "FixJobContext.jsx"
    drawer_text = drawer_src.read_text()
    ctx_text    = ctx_src.read_text()
    # localStorage persistence + restart endpoint moved to the context.
    assert "aurem_fix_active_job" in ctx_text
    assert "/fix-pipeline/restart/" in ctx_text
    # Drawer still surfaces the restart action via the restart() handler.
    assert "fix-progress-restart" in drawer_text


def test_health_ora_endpoint_wired():
    """`/api/aurem-dev/health/ora` endpoint exists with founder gating."""
    src = Path(__file__).resolve().parent.parent / "main.py"
    text = src.read_text()
    assert '@app.get("/api/aurem-dev/health/ora")' in text
    assert "ORA health probe is admin-only" in text \
        or "founder" in text.lower()
    # Must use a tight timeout so a hung LLM can't hang the health
    # probe itself.
    assert "timeout=8.0" in text or "timeout=8" in text


def test_codebase_health_last_zero_zero_normalised_to_null():
    """Iter 212m-147 — Defensive guard against legacy (score=0,
    total=0) rows that misrepresent the ring as "0/100"."""
    src = Path(__file__).resolve().parent.parent / "routers" / "codebase_health.py"
    text = src.read_text()
    assert "_score == 0 and (not _total or _total == 0)" in text, \
        "Defensive guard for (score=0, total=0) must be present"


def test_dashboard_uses_per_repo_health_score_cache():
    """Frontend Dashboard caches per-repo health score for instant
    repo switching."""
    src = Path(__file__).resolve().parent.parent.parent / "frontend" / "src" / "pages" / "Dashboard.jsx"
    text = src.read_text()
    assert "_healthScoreCacheRef" in text
    assert "new Map()" in text
    assert "healthScoreLoading" in text


def test_topbar_renders_skeleton_when_loading():
    """TopBar shows a `--` skeleton ring while loading instead of
    hiding the ring entirely or flashing a stale value."""
    src = Path(__file__).resolve().parent.parent.parent / "frontend" / "src" / "components" / "dashboard" / "v2" / "TopBar.jsx"
    text = src.read_text()
    assert "HealthRingSkeleton" in text
    assert "topbar-health-ring-skeleton" in text
    # Colour bands by score (green/orange/red).
    assert "score >= 80" in text
    assert "score >= 50" in text
