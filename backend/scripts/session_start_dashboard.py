#!/usr/bin/env python3
"""
scripts/session_start_dashboard.py — Iter 293 (QA Meta-Layer, session-
start discipline enforcement)

Prints a 3-line dashboard summarising the current state of the two
proactive-QA signals from iter289-290. Run at the start of every new
session so the founder never has to remember to invoke them manually.

Output shape (exactly 3 lines, no ceremony):

  [static-vs-behavioural]   38/75 STATIC_GREP (50.7%)  ← baseline: 50.7%
  [mock-reality-check]      github=OK  openrouter=OK   ← last drift: 2026-02-...
  [environment-ledger]      docs/environments.md verified 2026-02

Non-fatal if network probes time out — always exit 0 so this hook
never blocks a session start. The point is visibility, not
enforcement.

Optional flags:
  --json   emit structured JSON on stdout instead of the 3-line view
  --no-net skip the network-probe (mock-reality-check) — useful in
           air-gapped environments or when only the local signal
           matters.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from datetime import datetime, timezone

sys.path.insert(0, "/app/backend")


_BASELINE_STATIC_GREP_PCT = 50.7      # iter290 measurement — the
                                       # "we are here today" number.
                                       # Session hook flags when we
                                       # move UP from this.


def _analyze() -> dict:
    from services.test_style_analyzer import analyze_suite
    r = analyze_suite(file_pattern=r"(invariants|iter28[2-9]|iter29[0-9]|mutation_iter|release_it)")
    return r


async def _probe(timeout: float = 5.0) -> dict:
    try:
        from services.mock_reality_check import run_all
        return await asyncio.wait_for(run_all(timeout=timeout), timeout=timeout + 2)
    except Exception as e:                                       # noqa: BLE001
        return {"ok": False, "reason": "probe_error",
                "error": repr(e)[:200], "results": [], "drift_summary": []}


def _read_env_verified_stamp() -> str | None:
    path = "/app/docs/environments.md"
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            head = "".join(f.readlines()[:20])
        m = re.search(r"[Vv]erified[:\s]+\**([0-9]{4}-[0-9]{2}[^\s\*]*)", head)
        if m:
            return m.group(1)
    except Exception:                                            # noqa: BLE001
        return None
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-net", action="store_true")
    args = ap.parse_args()

    style = _analyze()
    if args.no_net:
        probe = {"skipped": True}
    else:
        probe = asyncio.run(_probe())
    stamp = _read_env_verified_stamp() or "MISSING"

    grep_n = style["counts"]["STATIC_GREP"]
    total  = style["total_tests"]
    grep_p = style["ratio"]["static_grep_pct"]
    drift  = ""
    if grep_p > _BASELINE_STATIC_GREP_PCT + 1.0:
        drift = f"  ⚠ up from baseline {_BASELINE_STATIC_GREP_PCT}%"
    elif grep_p < _BASELINE_STATIC_GREP_PCT - 1.0:
        drift = f"  ✓ improved from baseline {_BASELINE_STATIC_GREP_PCT}%"

    if isinstance(probe, dict) and probe.get("skipped"):
        probe_line = "[mock-reality-check]     SKIPPED (--no-net)"
    else:
        parts = []
        for r in probe.get("results", []):
            ok = "OK" if r.get("ok") else "DRIFT"
            parts.append(f"{r.get('upstream')}={ok}")
        probe_line = ("[mock-reality-check]     " + "  ".join(parts)) if parts \
                     else "[mock-reality-check]     unreachable"

    if args.json:
        print(json.dumps({
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "style": {"static_grep": grep_n, "total": total,
                      "static_grep_pct": grep_p,
                      "baseline_pct": _BASELINE_STATIC_GREP_PCT},
            "probe": probe,
            "env_ledger_verified": stamp,
        }, indent=2))
        return 0

    print(f"[static-vs-behavioural]  {grep_n}/{total} STATIC_GREP ({grep_p}%){drift}")
    print(probe_line)
    print(f"[environment-ledger]     docs/environments.md verified {stamp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
