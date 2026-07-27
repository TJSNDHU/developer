"""
test_iter318_verify_skip_runtime_regression.py — Iter 318 hardening

Runtime regression added after bug_testing_agent RCA:

  > The implemented verify guard mutates the initial verify report,
  > but the self-heal loop later replaces failing rows with
  > subset_report results from verify_files and recomputes ok from
  > linter results only. For .md files, verify_files returns
  > skip/ok:true without inspecting content, so a no-op/escalated
  > healer can convert an integrity failure back to ok:true while
  > placeholder content remains. Pre-ship defense-in-depth still
  > blocks the commit, but the verify-skip contract remains broken.

This test verifies the fix (helper `_apply_integrity_guard_to_report`
re-invoked after subset reverify merge) by exercising the helper
directly on a synthetic report the same way _do_verify does.
"""
from __future__ import annotations

from services.loop_engine import LoopEngine


class _Ctx(dict):
    """LoopEngine expects .context to be a dict; simplest mock."""


def _make_engine_with_readme_elision():
    """Build a bare-bones LoopEngine instance with just enough
    surface (context, loop_id, user_message) to call the guard
    helper. No DB, no state machine, no async."""
    eng = LoopEngine.__new__(LoopEngine)
    eng.loop_id = "test_iter318_runtime"
    eng.user_id = "test-user"
    eng.project_id = None
    eng.user_message = "add a comment line to README.md"
    eng.context = {
        "original_bytes_by_path": {"README.md": 10_000},
    }
    return eng


def test_helper_downgrades_skip_row_with_elision_marker():
    """Initial condition (bug_testing_agent Step 1): verify_files
    returns .md → linter=skip → ok=true for a body carrying the
    live-incident placeholder. Guard must downgrade ok to False."""
    eng = _make_engine_with_readme_elision()
    report = {
        "ok":      True,
        "results": [{
            "path":   "README.md",
            "ok":     True,
            "linter": "skip",
            "stdout": "", "stderr": "",
        }],
        "errors":  [],
    }
    file_objs = [{
        "path":    "README.md",
        "content": (
            "# README\n\n## Table of Contents\n- a\n- b\n\n"
            "[Rest of existing README content remains unchanged...]\n"
        ),
    }]
    eng._apply_integrity_guard_to_report(report, file_objs)
    assert report["ok"] is False, (
        "Iter 318 Bug 2: guard failed to downgrade a .md skip row "
        "carrying an elision marker."
    )
    row = report["results"][0]
    assert row["ok"] is False
    assert row.get("integrity_guard", {}).get("rule_fired") \
        == "elision_marker"
    assert any(
        "integrity_guard:elision_marker" in e for e in report["errors"]
    )


def test_helper_reapplied_after_reverify_merge_still_holds():
    """bug_testing_agent Step 2: the healer escalates (no output);
    the failing row is 'replaced' by a fresh verify_files() result
    for the same file, which still returns skip/ok:true. Without
    the re-sweep the row silently flips back to ok:true. With the
    re-sweep (the fix), it stays False."""
    eng = _make_engine_with_readme_elision()

    # Simulate the state right after the self-heal loop rebuilds
    # `report` from the subset reverify merge — verify_files
    # returned a clean skip/ok:true row again for the .md file
    # even though the content still carries the elision marker.
    report_after_reverify = {
        "ok":      True,
        "results": [{
            "path":   "README.md",
            "ok":     True,      # ← the exact silent-flip bug
            "linter": "skip",
            "stdout": "", "stderr": "",
        }],
        "errors":  [],
    }
    file_objs_still_dirty = [{
        "path":    "README.md",
        "content": (
            "# README\n\n"
            "[Rest of README content remains unchanged...]\n"
        ),
    }]

    # The fix: _do_verify calls this helper AGAIN after the merge.
    eng._apply_integrity_guard_to_report(
        report_after_reverify, file_objs_still_dirty,
    )

    assert report_after_reverify["ok"] is False, (
        "Iter 318 hardening: the post-heal re-sweep did not "
        "downgrade an .md skip row that still carries an elision "
        "marker. This reopens the exact loop_678eea28436c4e-class "
        "incident bug_testing_agent identified."
    )
    assert report_after_reverify["results"][0]["ok"] is False
    assert (
        report_after_reverify["results"][0]
        .get("integrity_guard", {}).get("rule_fired") == "elision_marker"
    )


def test_helper_leaves_clean_body_untouched():
    """A file body that is NOT an elision marker and NOT a
    catastrophic shrink must pass through unchanged so the guard
    doesn't produce false positives on legitimate small edits."""
    eng = _make_engine_with_readme_elision()
    report = {
        "ok":      True,
        "results": [{
            "path":   "README.md",
            "ok":     True,
            "linter": "skip",
            "stdout": "", "stderr": "",
        }],
        "errors":  [],
    }
    file_objs = [{
        "path":    "README.md",
        "content": (
            "# README\n\n" + "Regular prose. " * 800
            + "\n"  # ~11k bytes, no markers
        ),
    }]
    eng._apply_integrity_guard_to_report(report, file_objs)
    assert report["ok"] is True, (
        "Iter 318 hardening: guard produced a false positive on a "
        "clean, correctly-sized body."
    )
    assert "integrity_guard" not in report["results"][0]


def test_error_lines_deduped_across_reapplies():
    """The guard must be idempotent: calling it twice on the same
    already-downgraded report must not double the error lines
    (matters because it's now invoked in a loop)."""
    eng = _make_engine_with_readme_elision()
    report = {
        "ok":      True,
        "results": [{
            "path":   "README.md",
            "ok":     True,
            "linter": "skip",
            "stdout": "", "stderr": "",
        }],
        "errors":  [],
    }
    file_objs = [{
        "path":    "README.md",
        "content": "[Rest of README unchanged]",
    }]
    eng._apply_integrity_guard_to_report(report, file_objs)
    eng._apply_integrity_guard_to_report(report, file_objs)
    hits = [
        e for e in report["errors"] if "integrity_guard:" in e
    ]
    assert len(hits) == 1, (
        f"Iter 318 hardening: guard error lines must be deduped "
        f"across re-applies (found {len(hits)} of "
        f"'{hits[0] if hits else ''}')."
    )
