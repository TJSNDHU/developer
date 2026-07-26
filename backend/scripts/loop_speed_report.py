#!/usr/bin/env python3
"""
scripts/loop_speed_report.py — Permanent artifact for the founder's
speed-diagnostic prompt (Iter 309).

Usage:
  # Preview (uses backend/.env)
  cd /app/backend && python3 scripts/loop_speed_report.py

  # Prod (override MONGO_URL / DB_NAME):
  MONGO_URL="<prod_url>" DB_NAME=aurem_dev \\
      python3 scripts/loop_speed_report.py --window-days 30 --sample 20

Read-only. Zero writes. Zero side effects.
"""
import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

# Make `services` importable when run from repo root or from backend/.
_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from dotenv import load_dotenv                          # noqa: E402
load_dotenv(_BACKEND / ".env")

from motor.motor_asyncio import AsyncIOMotorClient       # noqa: E402
from services.loop_speed_diagnostic import (             # noqa: E402
    compute_speed_report,
)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--window-days", type=int, default=30)
    ap.add_argument("--sample", type=int, default=20)
    ap.add_argument("--json", action="store_true",
                    help="Emit raw JSON instead of pretty report.")
    args = ap.parse_args()

    mongo_url = os.environ.get("MONGO_URL")
    db_name   = os.environ.get("DB_NAME")
    if not mongo_url or not db_name:
        print("ERROR: MONGO_URL / DB_NAME env vars required.",
              file=sys.stderr)
        return 2
    print(f"# Connecting to {mongo_url.split('@')[-1][:50]}… db={db_name}",
          file=sys.stderr)
    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]
    report = await compute_speed_report(
        db, window_days=args.window_days, sample_target=args.sample,
    )

    if args.json:
        print(json.dumps(report, indent=2, default=str))
        return 0

    # Human-readable summary
    print("=" * 70)
    print("LOOP SPEED DIAGNOSTIC — Iter 309 · Part 1 Report")
    print("=" * 70)
    print(f"generated_at  : {report.get('generated_at')}")
    print(f"window_days   : {report.get('window_days')}")
    print(f"sample_size   : {report.get('sample_size')} / "
          f"{report.get('sample_target')} target")
    if report.get("sample_too_small"):
        print("⚠ SAMPLE TOO SMALL for statistical significance (<15).")
    print()
    print("Total loop duration (real completed loops):")
    print(f"  {report.get('total_loop_duration')}")
    print()
    print("Per-phase wall-clock (seconds):")
    for ph, stats in (report.get("phase_wall_clock") or {}).items():
        print(f"  {ph:<8s} → {stats}")
    print()
    print(f"Execute share of total: "
          f"{(report.get('execute_share_of_total') or {}).get('avg_pct')}% avg")
    print()
    print(f"Per-file generation duration (execute phase):")
    print(f"  {report.get('per_file_generation')}")
    print(f"  outcomes: {report.get('per_file_outcomes')}")
    print()
    print(f"Queue-wait signal (MAX_PARALLEL_GENS=3):")
    qw = report.get("queue_wait_signal") or {}
    print(f"  loops with queue-wait: {qw.get('loops_with_queue_wait')}")
    for sample in qw.get("samples", []):
        print(f"    - {sample}")
    print()
    print("LLM calls by phase (n = calls per loop):")
    for ph, stats in (report.get("llm_calls_by_phase") or {}).items():
        print(f"  loop.{ph:<8s} → {stats}")
    print()
    print("Self-heal:")
    print(f"  {report.get('self_heal')}")
    print()
    print("Notes:")
    for note in report.get("notes", []):
        print(f"  • {note}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
