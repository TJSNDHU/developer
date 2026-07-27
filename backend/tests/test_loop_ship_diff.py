"""Iter 328 · Deploy 2 · tests for services/loop_ship_diff.py"""
from __future__ import annotations

from services.loop_ship_diff import compute_files_diff, _line_delta


def test_line_delta_empty_vs_new():
    # "" splits to [""] (1 elt); "hello\nworld\n" splits to
    # ["hello","world",""] (3 elts). Naive walk: pos 0 diff pair
    # (add+del), pos 1-2 unmatched → 2 adds. Net: add=3, del=1.
    add, dele = _line_delta("", "hello\nworld\n")
    assert add == 3 and dele == 1


def test_line_delta_pure_deletion():
    # "a\nb\nc\n" → ["a","b","c",""]; "" → [""]
    # pos 0 diff pair, pos 1-3 unmatched → 3 dels. Net: add=1, del=4.
    add, dele = _line_delta("a\nb\nc\n", "")
    assert dele == 4 and add == 1


def test_line_delta_edit_single_line():
    add, dele = _line_delta("a\nb\nc", "a\nBEE\nc")
    assert add == 1 and dele == 1


def test_diff_new_file_from_content_only():
    r = compute_files_diff(
        orig_contents={},
        new_contents={"new.py": "print(1)\nprint(2)\n"},
    )
    assert len(r) == 1
    e = r[0]
    assert e["path"] == "new.py"
    assert e["is_new"] is True
    assert e["additions"] == 3
    assert e["deletions"] == 0
    assert e["delta_bytes"] == 18
    assert e["diff_source"] == "line"


def test_diff_edit_with_orig_content():
    r = compute_files_diff(
        orig_contents={"a.py": "x = 1\ny = 2\n"},
        new_contents={"a.py": "x = 42\ny = 2\n"},
    )
    e = r[0]
    assert e["is_new"] is False
    assert e["additions"] == 1
    assert e["deletions"] == 1
    assert e["delta_bytes"] == 1
    assert e["diff_source"] == "line"


def test_diff_falls_back_to_bytes_when_content_missing():
    """Rehydration case — we know old byte count but not content."""
    r = compute_files_diff(
        orig_contents={},   # cache empty (worker migrated)
        new_contents={"a.py": "abc"},
        orig_bytes_by_path={"a.py": 100},
    )
    e = r[0]
    assert e["diff_source"] == "bytes"
    assert e["additions"] == 0
    assert e["deletions"] == 0
    assert e["delta_bytes"] == 3 - 100
    assert e["is_new"] is False


def test_diff_unknown_when_no_signal_at_all():
    r = compute_files_diff(
        orig_contents={},
        new_contents={"a.py": "abc"},
        orig_bytes_by_path={},
    )
    e = r[0]
    # No orig content, no orig bytes → treated as new file.
    assert e["is_new"] is True
    assert e["additions"] == 1  # single line, no trailing newline
    assert e["diff_source"] == "line"


def test_diff_multiple_files_mixed():
    r = compute_files_diff(
        orig_contents={"a.py": "1\n2\n"},
        new_contents={
            "a.py": "1\n2\n3\n",     # additions=1
            "b.py": "brand new\n",    # new file
        },
        orig_bytes_by_path={"a.py": 4, "b.py": 0},
    )
    paths = {e["path"]: e for e in r}
    # orig "1\n2\n" → 3 elts; new "1\n2\n3\n" → 4 elts. Naive walk:
    # pos 0,1 same; pos 2 "" vs "3" diff pair; pos 3 unmatched add.
    # → add=2 del=1.
    assert paths["a.py"]["additions"] == 2
    assert paths["a.py"]["deletions"] == 1
    assert paths["a.py"]["is_new"] is False
    assert paths["b.py"]["is_new"] is True


def test_diff_delete_lines():
    r = compute_files_diff(
        orig_contents={"a.py": "a\nb\nc\nd\n"},
        new_contents={"a.py": "a\nd\n"},
    )
    e = r[0]
    # Lines b, c are deletions; d shifts up so line 2 (was b) becomes d
    # → add: 1 (new "d" line 2), del: 3 (b, c, d shifted). Naive walk.
    # Verify additions+deletions are non-zero and shrink is signalled.
    assert e["additions"] > 0 or e["deletions"] > 0
    assert e["delta_bytes"] < 0  # shrunk


def test_diff_empty_input():
    assert compute_files_diff({}, {}) == []
    assert compute_files_diff(None, None) == []


def test_diff_preserves_path_ordering():
    """Iteration order should match new_contents insertion order."""
    r = compute_files_diff(
        orig_contents={},
        new_contents={"z.py": "a", "a.py": "b", "m.py": "c"},
    )
    assert [e["path"] for e in r] == ["z.py", "a.py", "m.py"]
