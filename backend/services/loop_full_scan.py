"""
services/loop_full_scan.py  —  Directive Session 2 · Part B (glue layer)
=========================================================================

Loop-Mode-specific plumbing that wires `full_scan_orchestrator.run_full_scan`
into the existing execute → verify → scan → ship pipeline.

Responsibilities (kept intentionally thin so the orchestrator stays
reusable outside Loop Mode):

  1. Persist critical/high self-generated findings to
     `cto_open_findings` (Session 1 collection) so the notification
     strip's backlog surface has real data to draw from later.

  2. Ship-block + 3× auto-retry contract.
       • If Full Scan finds ANY critical/high finding on a file that
         Loop itself just wrote, block Ship and hand the file back to
         the self-heal loop (Parliament healer, existing path).
       • Retry up to `MAX_SCAN_HEALS = 3` times.
       • After the 3rd failed retry, surface to user for manual review
         (paused_for_user event with reason).

  3. Provide a single `get_full_scan_health()` inspection function so
     the dashboard "Verify / Full Scan: Active / Degraded" pattern
     can render honestly per Directive Part B ("dashboard status
     honesty — never claim full coverage falsely").

Everything here is pure-async, no FastAPI context, no globals. All
timing / retry limits live as module constants so they're grep-able
and testable.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# ── Retry contract ────────────────────────────────────────────────────
# Same 3-attempt cap the directive specifies. Kept as a module constant
# so tests + docs can reference it by name rather than by literal.
MAX_SCAN_HEALS = 3

# ── Latency guard on the Full Scan itself ─────────────────────────────
# The scanners are pure regex + AST work and are fast — but if a
# pathological repo takes > 30 s we abandon the retry loop and surface
# to the user honestly rather than looping forever.
FULL_SCAN_MAX_SECONDS = 30.0


# ══════════════════════════════════════════════════════════════════════
# Persistence — critical/high findings backlog
# ══════════════════════════════════════════════════════════════════════

async def persist_findings_to_backlog(
    db,
    *,
    user_id: str,
    project_id: str,
    findings: list[dict],
    scan_source: str = "loop_full_scan",
) -> int:
    """Write critical/high findings into `cto_open_findings` so the
    notification-strip backlog surface (Session 3) has real data.

    Uses upsert keyed on `(user_id, project_id, finding_id)` so the
    same rule triggered on the same line across multiple scans
    increments `last_seen_at` + `exposure_count` bounded at 4
    (per Directive Part D auto-archive rule) instead of duplicating.

    Medium/low findings are intentionally excluded from the backlog
    per the directive — they exist in scan reports but never surface
    on the strip.

    Returns the number of docs written / updated.
    """
    if not findings:
        return 0
    now = datetime.now(timezone.utc)
    count = 0
    for f in findings:
        sev = (f.get("severity") or "").lower()
        if sev not in ("critical", "high"):
            continue
        # Stable, deterministic finding id — same rule on same file+line
        # collapses to one row even across scanner boundaries.
        file_ = f.get("file") or ""
        line_ = int(f.get("line") or 0)
        rule  = f.get("rule_id") or f.get("id") or "unknown"
        finding_id = f"{f.get('scanner', 'unknown')}::{file_}:{line_}:{rule}"

        try:
            # $setOnInsert covers first-seen fields; $set covers the
            # values that legitimately refresh on re-detection.
            # $inc caps at 4 via a $min-style upsert would need a
            # pipeline update; simpler: pre-read exposure_count and
            # bump only when < 4. Do it in two steps for clarity.
            existing = await db.cto_open_findings.find_one(
                {"user_id": user_id, "project_id": project_id,
                 "finding_id": finding_id},
                projection={"exposure_count": 1, "status": 1},
            )
            new_exposure = min(
                4, int((existing or {}).get("exposure_count") or 0) + 1
            )
            # If already aged-out, leave it alone — user has to
            # manually resurrect from Settings.
            if (existing or {}).get("status") == "aged-out":
                continue

            await db.cto_open_findings.update_one(
                {"user_id": user_id, "project_id": project_id,
                 "finding_id": finding_id},
                {
                    "$setOnInsert": {
                        "user_id":       user_id,
                        "project_id":    project_id,
                        "finding_id":    finding_id,
                        "first_seen_at": now,
                        "status":        "open",
                    },
                    "$set": {
                        "scanner":         f.get("scanner"),
                        "rule_id":         rule,
                        "severity":        sev,
                        "file":            file_,
                        "line":            line_,
                        "title":           f.get("title") or "",
                        "message":         f.get("message") or "",
                        "fix_hint":        f.get("fix_hint") or "",
                        "last_seen_at":    now,
                        "last_exposed_at": now,
                        "exposure_count":  new_exposure,
                        "source":          scan_source,
                    },
                },
                upsert=True,
            )
            count += 1
        except Exception as e:                          # noqa: BLE001
            logger.warning(
                "[full-scan] persist failed for %s: %r", finding_id, e,
            )
    return count


# ══════════════════════════════════════════════════════════════════════
# Ship-block decision + retry-attempt formatter
# ══════════════════════════════════════════════════════════════════════

def format_retry_message(attempt: int, offending: dict[str, list[dict]]) -> str:
    """One-line status message emitted before each self-heal retry."""
    total_files = len(offending)
    total_findings = sum(len(v) for v in offending.values())
    return (
        f"Full-scan self-heal {attempt}/{MAX_SCAN_HEALS} — "
        f"{total_findings} critical/high issue(s) across "
        f"{total_files} generated file(s)…"
    )


def format_ship_block_reason(offending: dict[str, list[dict]]) -> str:
    """Human-readable reason returned to the user when the retry
    budget is exhausted and Ship remains blocked."""
    lines = [
        f"Ship blocked: {MAX_SCAN_HEALS} self-heal attempt(s) exhausted. "
        f"The following critical/high findings remain in code ORA "
        f"generated for this task:",
    ]
    for path, hits in sorted(offending.items()):
        lines.append(f"  {path}:")
        for h in hits[:5]:   # cap at 5 per file to keep the message tidy
            rule = h.get("rule_id") or "unknown"
            line = h.get("line") or 0
            sev  = (h.get("severity") or "").upper()
            msg  = (h.get("message") or "").strip()
            if len(msg) > 140:
                msg = msg[:137] + "…"
            lines.append(f"    L{line} [{sev}] {rule}: {msg}")
        if len(hits) > 5:
            lines.append(f"    …and {len(hits) - 5} more.")
    lines.append(
        "Please review manually — do not force-Ship. The Full Scan is a "
        "safety net; if these are truly false positives, mark them "
        "explicitly in code with `# vanguard: ignore` and re-run Loop."
    )
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════
# Health / degraded surface
# ══════════════════════════════════════════════════════════════════════

# Module-scoped health cache — updated on every Full Scan run so a
# dashboard poll can read the last-known status without re-triggering
# a scan. Not thread-safe by design (asyncio single-loop) — every
# writer is the running Loop coroutine.
_LAST_HEALTH: dict = {
    "status":           "unknown",           # "ok" | "degraded" | "error"
    "scanner_status":   {},
    "last_run_at":      None,
    "last_elapsed_s":   0.0,
    "last_finding_count": 0,
}


def record_scan_health(result: dict) -> None:
    """Update the cached health record after a Full Scan run."""
    status = "degraded" if result.get("degraded") else "ok"
    _LAST_HEALTH.update({
        "status":             status,
        "scanner_status":     dict(result.get("scanner_status") or {}),
        "last_run_at":        datetime.now(timezone.utc).isoformat(),
        "last_elapsed_s":     float(result.get("elapsed_seconds") or 0.0),
        "last_finding_count": int(result.get("summary", {}).get("total") or 0),
    })


def get_full_scan_health() -> dict:
    """Read-only view of the current Full-Scan health. Consumed by the
    dashboard "Verify: Active/Degraded" strip so it can honestly
    reflect scanner availability instead of assuming all-green."""
    return dict(_LAST_HEALTH)


def reset_health_for_tests() -> None:
    """Test-only reset. Never called by production code paths."""
    _LAST_HEALTH.update({
        "status": "unknown", "scanner_status": {},
        "last_run_at": None, "last_elapsed_s": 0.0,
        "last_finding_count": 0,
    })
