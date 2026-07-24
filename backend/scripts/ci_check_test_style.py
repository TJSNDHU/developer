#!/usr/bin/env python3
"""
scripts/ci_check_test_style.py — Iter 291 (CI-guard)

Blocks a PR when a NEWLY ADDED or MODIFIED test file has >60% of its
test functions classified as STATIC_GREP by
`services.test_style_analyzer.classify_test_function`.

Rationale — from iter290's real measurement:
  Half of the existing 75-test suite (50.7%) is grep-only. That's the
  historical debt we're paying down via behavioural upgrades. The
  discipline this guard adds: no NEW test file may cross the 60%
  static-grep threshold, so grep-debt does not compound while we
  drain the backlog.

Exempt patterns (opt-in per file, via a magic comment on line 1 or
inside the docstring):
  # static-grep-ok: <reason>
The reason is required and echoed in the CI log for reviewer sign-off.

Fires on:
  - `test_mutation_iter*` files — mutation tests are STATIC_GREP by
    design (they mutate source strings). Must self-declare with the
    magic comment.

Usage:
    python scripts/ci_check_test_style.py <base_sha> <head_sha>

Exit codes:
    0 — all changed test files pass the threshold
    1 — one or more files exceed the threshold without exemption
    2 — invocation / IO error
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# The threshold (fraction, not percent — matches ratio.static_grep_pct
# divided by 100). Founder-agreed: 60%.
_THRESHOLD_FRACTION = 0.60
# Minimum test count in a file for the guard to fire — smaller files
# are statistical noise. A file with 2 tests, both grep-style, is
# 100% grep — but the sample size is too small to be meaningful.
_MIN_TESTS_FOR_GUARD = 3
# Magic-comment marker for a deliberate opt-out. Matches Python
# `# static-grep-ok: <reason>` AND JS/TS `// static-grep-ok: <reason>`.
_EXEMPT_RE = re.compile(
    r"(?:#|//)\s*static-grep-ok\s*:\s*(?P<reason>.+)$", re.M
)


def _git_diff_test_files(base_sha: str, head_sha: str) -> list[str]:
    """Return the list of test_*.py files that were ADDED or MODIFIED
    (not deleted, not renamed to a non-test path) between the two
    commits. We use --diff-filter=AM to skip deletions."""
    try:
        raw = subprocess.check_output(
            ["git", "diff", "--name-only", "--diff-filter=AM",
             f"{base_sha}..{head_sha}"],
            text=True, timeout=30,
        )
    except subprocess.CalledProcessError as e:
        print(f"::error::git diff failed: {e}", file=sys.stderr)
        sys.exit(2)
    files: list[str] = []
    for line in raw.splitlines():
        path = line.strip()
        if not path:
            continue
        # Iter 294 — Python + JS/TS test file globs. Python tests must
        # live under a `tests/` dir + basename `test_*.py`. JS/TS test
        # files live anywhere under frontend/ with a `*.test.{js,jsx,
        # ts,tsx}` basename (project convention).
        base = os.path.basename(path)
        parts = path.split("/")
        py_test = ("tests" in parts and base.startswith("test_")
                    and base.endswith(".py"))
        js_test = base.endswith((".test.jsx", ".test.js",
                                  ".test.tsx", ".test.ts"))
        if py_test or js_test:
            files.append(path)
    return files


def _file_exempts_itself(path: str) -> str | None:
    """If the file's first 40 lines carry a
    `# static-grep-ok: <reason>` (Python) or `// static-grep-ok: <reason>`
    (JS/TS) marker, return the reason. Else None."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            head_lines = "".join(f.readlines()[:40])
    except OSError:
        return None
    m = _EXEMPT_RE.search(head_lines)
    return m.group("reason").strip() if m else None


def _classify_file(path: str) -> dict:
    """Delegate to the analyzer for a single file."""
    from services.test_style_analyzer import analyze_file
    return analyze_file(path)


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: ci_check_test_style.py <base_sha> <head_sha>",
              file=sys.stderr)
        return 2
    base_sha, head_sha = argv[1], argv[2]
    changed = _git_diff_test_files(base_sha, head_sha)
    if not changed:
        print("No changed test files. Guard passes trivially.")
        return 0

    print(f"Changed test files ({len(changed)}):")
    for p in changed:
        print(f"  - {p}")
    print()

    violations: list[dict] = []
    exemptions: list[dict] = []
    passes:     list[dict] = []

    for path in changed:
        exempt_reason = _file_exempts_itself(path)
        rep = _classify_file(path)
        if not rep.get("ok"):
            # Broken parse — don't silently ignore. Fail loud.
            print(f"::error::could not analyze {path}: "
                  f"{rep.get('reason')}", file=sys.stderr)
            return 2
        tests = rep.get("tests") or []
        n_total = len(tests)
        n_grep  = sum(1 for t in tests if t["kind"] == "STATIC_GREP")

        entry = {"path": path, "total": n_total, "grep": n_grep,
                 "grep_frac": (n_grep / n_total) if n_total else 0.0,
                 "exempt_reason": exempt_reason}

        if n_total < _MIN_TESTS_FOR_GUARD:
            entry["outcome"] = "skipped_too_small"
            passes.append(entry)
            continue
        if entry["grep_frac"] > _THRESHOLD_FRACTION:
            if exempt_reason:
                entry["outcome"] = "exempt"
                exemptions.append(entry)
            else:
                entry["outcome"] = "violation"
                violations.append(entry)
        else:
            entry["outcome"] = "pass"
            passes.append(entry)

    # Report — every category prints even when empty so the log is
    # self-documenting.
    print("=== ci_check_test_style report ===")
    for e in passes:
        print(f"  [PASS   ] {e['path']} ({e['grep']}/{e['total']} grep)")
    for e in exemptions:
        print(f"  [EXEMPT ] {e['path']} ({e['grep']}/{e['total']} grep) "
              f"— reason: {e['exempt_reason']}")
    for e in violations:
        pct = round(100.0 * e["grep_frac"], 1)
        print(f"::error file={e['path']}::static-grep {pct}% > "
              f"60% threshold ({e['grep']}/{e['total']} tests are "
              f"STATIC_GREP). Rewrite as behavioural, or add "
              f"`# static-grep-ok: <reason>` to the file's docstring "
              f"if this is intentional (e.g. a mutation suite).")

    if violations:
        print()
        print(f"FAIL — {len(violations)} test file(s) exceed the 60% "
              f"STATIC_GREP threshold. See errors above.")
        return 1
    print()
    print(f"OK — {len(passes)} pass, {len(exemptions)} exempt, "
          f"0 violations.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
