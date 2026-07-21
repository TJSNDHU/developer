"""
services/loop_diff_classifier.py — Iter 272 Feature 1.2

Splits a set of files-being-committed into `source` vs `test/fixture`.
The rule: if the loop's diff touches ANY test or fixture file, we
FORCE human review regardless of trust level (yes, L3 too). A fixing
agent must never be able to silently weaken the very test it's being
graded by.

`classify(files)` — pure, no DB, no I/O. Deterministic. Used at ship
gate time to decide whether to hoist the run into
`requires_human_review: true`.

The classifier is intentionally strict — false-positives (flagging
a source file that just happens to have "test" in its path) are
acceptable; false-negatives (missing a real test edit) are not.
"""
from __future__ import annotations

import re
from typing import Iterable

# Directory patterns → test/fixture. Match anywhere in the path.
_TEST_DIR_PATTERNS = (
    r"(?:^|/)tests?/",
    r"(?:^|/)__tests__/",
    r"(?:^|/)test/",
    r"(?:^|/)__mocks__/",
    r"(?:^|/)fixtures?/",
    r"(?:^|/)cypress/",
    r"(?:^|/)e2e/",
    r"(?:^|/)spec/",
)

# Filename patterns → test/fixture. Applied to basename OR path.
_TEST_FILE_PATTERNS = (
    r"(?:^|/)test_[^/]+\.py$",
    r"(?:^|/)[^/]+_test\.py$",
    r"(?:^|/)[^/]+\.test\.(?:js|jsx|ts|tsx|mjs|cjs)$",
    r"(?:^|/)[^/]+\.spec\.(?:js|jsx|ts|tsx|py|rb|go)$",
    r"(?:^|/)test-[^/]+\.js$",
    r"(?:^|/)conftest\.py$",
    r"(?:^|/)pytest\.ini$",
    r"(?:^|/)jest\.config\.(?:js|ts|mjs|cjs)$",
    r"(?:^|/)cypress\.config\.(?:js|ts)$",
    r"(?:^|/)vitest\.config\.(?:js|ts|mjs)$",
)

_COMPILED = [re.compile(p, re.IGNORECASE)
             for p in (_TEST_DIR_PATTERNS + _TEST_FILE_PATTERNS)]


def is_test_or_fixture(path: str) -> bool:
    """Single-file check. `path` is repo-relative (`backend/tests/foo.py`,
    `frontend/src/__tests__/App.test.jsx`, …). Empty / falsy → False."""
    p = (path or "").strip().lstrip("./")
    if not p:
        return False
    return any(rx.search(p) for rx in _COMPILED)


def classify(files: Iterable[dict]) -> dict:
    """Split `[{path, content}, ...]` into two disjoint lists.
    Returns:
        {
          "source":       [paths that are NOT tests/fixtures],
          "tests":        [paths that ARE tests/fixtures],
          "test_touched": bool,   # convenience
          "test_lines":   [{path, added_lines}, ...],   # for reviewer
        }
    `added_lines` is a naive count of non-empty content lines — the
    reviewer can look at the doc itself for context; this is only a
    quick "how much did they change?" signal.
    """
    source: list[str] = []
    tests: list[str] = []
    test_lines: list[dict] = []

    for f in files or []:
        path = ((f or {}).get("path") or "").strip()
        content = (f or {}).get("content") or ""
        if not path:
            continue
        if is_test_or_fixture(path):
            tests.append(path)
            # Count non-blank / non-comment lines so reviewers see the
            # rough shape of the change.
            lines = [ln for ln in (content or "").splitlines()
                     if ln.strip() and not ln.lstrip().startswith(("#", "//"))]
            test_lines.append({"path": path, "added_lines": len(lines)})
        else:
            source.append(path)

    return {
        "source":       source,
        "tests":        tests,
        "test_touched": bool(tests),
        "test_lines":   test_lines,
    }
