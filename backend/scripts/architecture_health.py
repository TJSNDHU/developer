#!/usr/bin/env python3
"""
scripts/architecture_health.py — CLI wrapper around
services.architecture_health.

Usage:
    cd backend && python scripts/architecture_health.py
    cd backend && python scripts/architecture_health.py --json > report.json
    cd backend && python scripts/architecture_health.py --fail-on-new

Exit codes:
    0  → no new regressions
    1  → a NEW bloated file appeared since the last baseline
         (use --fail-on-new in CI to block PRs)

The baseline lives at /app/memory/arch_health_baseline.json and is
updated by running with --update-baseline.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

# Make `services.*` importable when invoked from any CWD.
HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(HERE)
sys.path.insert(0, BACKEND)

from services.architecture_health import (  # noqa: E402
    run_health_report, summarise,
)

BASELINE_PATH = os.path.join(
    os.path.dirname(BACKEND), "memory", "arch_health_baseline.json",
)


def _load_baseline() -> dict:
    if not os.path.exists(BASELINE_PATH):
        return {"bloated_files": []}
    try:
        with open(BASELINE_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {"bloated_files": []}


def _save_baseline(report: dict) -> None:
    os.makedirs(os.path.dirname(BASELINE_PATH), exist_ok=True)
    snapshot = {
        "bloated_files": [
            {"rel": r["rel"], "lines": r["lines"]}
            for r in report.get("bloated_files", [])
        ],
        "line_limit": report.get("line_limit"),
    }
    with open(BASELINE_PATH, "w", encoding="utf-8") as fh:
        json.dump(snapshot, fh, indent=2, sort_keys=True)


def _diff_against_baseline(report: dict, baseline: dict) -> list[str]:
    """Return paths that are NEWLY bloated (not in baseline) — these
    are the regressions the next 1952-line file is hiding behind."""
    base_paths = {r["rel"] for r in baseline.get("bloated_files", [])}
    current   = {r["rel"] for r in report.get("bloated_files", [])}
    return sorted(current - base_paths)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--json", action="store_true",
                   help="Emit raw JSON instead of the summary table.")
    p.add_argument("--fail-on-new", action="store_true",
                   help="Exit code 1 if a NEW bloated file appeared "
                        "since the baseline (for CI gating).")
    p.add_argument("--update-baseline", action="store_true",
                   help="Persist the current bloated-file list as "
                        "the new baseline.")
    args = p.parse_args()

    report = run_health_report()

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(summarise(report))

    if args.update_baseline:
        _save_baseline(report)
        print(f"\nbaseline updated → {BASELINE_PATH}")

    if args.fail_on_new:
        new = _diff_against_baseline(report, _load_baseline())
        if new:
            print("\n❌ NEW bloated files since baseline:", file=sys.stderr)
            for n in new:
                print(f"   {n}", file=sys.stderr)
            print(
                "   Fix or refactor BEFORE merging. To accept this "
                "regression as the new normal, re-run with "
                "--update-baseline.", file=sys.stderr,
            )
            return 1
        print("\n✅ no new bloated files since baseline")
    return 0


if __name__ == "__main__":
    sys.exit(main())
