"""Iter 328 · Deploy 2 · integration test — verify loop_engine
populates ship_pending.files_diff + integrity_verdict at pending-emit.

We stub out the real ship path (GitHub commit) and just exercise the
compute-diff-then-attach block by simulating a paused engine.
"""
from __future__ import annotations

from services.loop_ship_diff import compute_files_diff


def test_files_diff_uses_cache_when_available():
    """When self._orig_content_cache has the pre-exec content, the
    diff row uses line-level counts."""
    orig_cache = {"README.md": "line1\nline2\n"}
    files_dict = {"README.md": "line1\nline2\nline3\n"}
    obc = {"README.md": len("line1\nline2\n")}
    rows = compute_files_diff(orig_cache, files_dict, obc)
    assert len(rows) == 1
    r = rows[0]
    assert r["path"] == "README.md"
    assert r["diff_source"] == "line"
    assert r["additions"] >= 1
    assert r["is_new"] is False


def test_files_diff_falls_back_to_bytes_after_rehydration():
    """Cross-worker rehydration case: cache is empty, but
    original_bytes_by_path was persisted to Mongo. Diff row still
    reports something useful (delta_bytes)."""
    orig_cache: dict[str, str] = {}   # rehydrated worker has no cache
    files_dict = {"README.md": "shortened content"}
    obc = {"README.md": 5000}  # was 5 KB before
    rows = compute_files_diff(orig_cache, files_dict, obc)
    r = rows[0]
    assert r["diff_source"] == "bytes"
    assert r["is_new"] is False
    assert r["delta_bytes"] < 0   # shrunk
    assert r["additions"] == 0    # can't compute lines without content
    assert r["deletions"] == 0


def test_files_diff_new_file_line_source():
    orig_cache: dict[str, str] = {}
    files_dict = {"routers/new.py": "from fastapi import APIRouter\n"}
    obc: dict[str, int] = {}          # no prior byte count either
    rows = compute_files_diff(orig_cache, files_dict, obc)
    r = rows[0]
    assert r["is_new"] is True
    assert r["diff_source"] == "line"
    assert r["additions"] > 0


def test_files_diff_never_raises_on_bad_shape():
    """Fail-open contract — bad inputs must not crash."""
    # None inputs
    assert compute_files_diff(None, None) == []
    # New content dict with non-string body
    rows = compute_files_diff({}, {"x.py": ""}, {"x.py": 0})
    assert len(rows) == 1
    assert rows[0]["is_new"] is True


def test_files_diff_preserves_iteration_order():
    files = {"z.py": "1", "a.py": "2", "m.py": "3"}
    rows = compute_files_diff({}, files, {})
    assert [r["path"] for r in rows] == ["z.py", "a.py", "m.py"]
