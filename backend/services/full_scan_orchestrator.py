"""
services/full_scan_orchestrator.py  —  Directive Session 2 · Part B
====================================================================

Runs the FULL 4-category Full Scan on a set of files. Categories:

    1. Vanguard (25-pattern security regex catalog) — existing.
    2. Bug Hunt (50+ Nuclei-inspired static rules)  — existing.
    3. HTTP security headers (repo-level FastAPI/Flask/Express).
    4. Docker CIS (Dockerfile + compose file checks).

Category 5 in the Codebase Health suite (dependencies, performance,
code-quality, database) is intentionally excluded from the Loop-Mode
Full Scan — those categories require full-repo context (manifest
files, connection pool config, etc.) that a per-file diff can't
provide reliably. They remain accessible via the dedicated
`/codebase-health/scan` endpoint.

Design contract:

  • Input is a `text_cache: dict[str, str]` (path → content) covering
    ONLY the files just written/changed. Full-repo scanning stays in
    the dedicated Health-Scan endpoint.

  • Output shape stabilised — the callers (Loop engine, future
    /parliament/analyze) rely on it:

        {
          "findings":       list[dict],       # union across scanners
          "summary": {
              "total":         int,
              "by_severity":   {critical, high, medium, low, info: int},
              "by_scanner":    {vanguard, bug_hunt, http, docker: int},
          },
          "scanner_status": {
              # per-scanner "ok" / "degraded" / "error"
              "vanguard":  str,
              "bug_hunt":  str,
              "http":      str,
              "docker":    str,
          },
          "degraded":       bool,   # True if ANY scanner is not "ok"
          "critical_count": int,
          "high_count":     int,
          "elapsed_seconds": float,
        }

  • Every scanner call is wrapped in a try/except so one broken
    module can never abort the whole scan — it becomes "degraded"
    and the honest status is surfaced to the dashboard per Directive
    Part B ("dashboard status honesty").
"""
from __future__ import annotations

import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────
# Finding-shape normaliser
# The three scanner modules emit slightly different key names — this
# reduces them to a single stable shape callers can rely on.
# ──────────────────────────────────────────────────────────────────────
def _normalise(finding: dict, scanner: str) -> dict:
    """Coerce a scanner finding into the unified Loop-Mode shape."""
    sev = (finding.get("severity") or "medium").lower()
    if sev not in ("critical", "high", "medium", "low", "info"):
        sev = "medium"
    return {
        "scanner":  scanner,
        "rule_id":  finding.get("rule_id") or finding.get("id")
                    or finding.get("name") or finding.get("rule") or "unknown",
        "severity": sev,
        "file":     finding.get("file") or finding.get("filepath") or "",
        "line":     int(finding.get("line") or 0),
        "title":    finding.get("title") or finding.get("name") or "",
        "message":  finding.get("message") or finding.get("desc")
                    or finding.get("snippet") or "",
        "fix_hint": finding.get("fix_hint") or "",
        "raw":      {
            k: v for k, v in finding.items()
            if k not in ("severity",)  # keep everything for provenance
        },
    }


def _empty_summary() -> dict:
    return {
        "total": 0,
        "by_severity": {
            "critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0,
        },
        "by_scanner": {
            "vanguard": 0, "bug_hunt": 0, "http": 0, "docker": 0,
        },
    }


def _summarise(findings: list[dict]) -> dict:
    s = _empty_summary()
    s["total"] = len(findings)
    for f in findings:
        sev = f.get("severity") or "medium"
        s["by_severity"][sev] = s["by_severity"].get(sev, 0) + 1
        scn = f.get("scanner") or "unknown"
        if scn in s["by_scanner"]:
            s["by_scanner"][scn] += 1
    return s


def run_full_scan(text_cache: dict[str, str]) -> dict:
    """Run all 4 scanners against the given `{path: content}` cache.

    Returns the stable Full-Scan result dict described in the module
    docstring. Safe to call with an empty cache — returns a
    zero-findings result with all scanners marked "ok".
    """
    started = time.monotonic()
    findings: list[dict] = []
    scanner_status: dict[str, str] = {
        "vanguard": "ok", "bug_hunt": "ok",
        "http":     "ok", "docker":   "ok",
    }

    # ── 1. Vanguard ────────────────────────────────────────────────
    try:
        from services.vanguard_scanner import scan_file_blocks
        vg = scan_file_blocks(text_cache or {}) or []
        for f in vg:
            findings.append(_normalise(f, "vanguard"))
    except Exception as e:                              # noqa: BLE001
        scanner_status["vanguard"] = "error"
        logger.warning("[full-scan] vanguard failed: %r", e)

    # ── 2. Bug Hunt ────────────────────────────────────────────────
    try:
        from services.bug_hunt_rules import scan_bug_hunt
        bh = scan_bug_hunt(text_cache or {}) or []
        for f in bh:
            findings.append(_normalise(f, "bug_hunt"))
    except Exception as e:                              # noqa: BLE001
        scanner_status["bug_hunt"] = "error"
        logger.warning("[full-scan] bug_hunt failed: %r", e)

    # ── 3. HTTP security headers ───────────────────────────────────
    # Only meaningful when the diff actually touches a web-app
    # entrypoint file. The scanner itself is fast enough that we
    # always call it and let it short-circuit internally when the
    # repo already sets headers somewhere.
    try:
        from services.full_scan_scanners import scan_http_headers
        hh = scan_http_headers(text_cache or {}) or []
        for f in hh:
            findings.append(_normalise(f, "http"))
    except Exception as e:                              # noqa: BLE001
        scanner_status["http"] = "error"
        logger.warning("[full-scan] http_headers failed: %r", e)

    # ── 4. Docker CIS ──────────────────────────────────────────────
    try:
        from services.full_scan_scanners import scan_docker_cis
        dk = scan_docker_cis(text_cache or {}) or []
        for f in dk:
            findings.append(_normalise(f, "docker"))
    except Exception as e:                              # noqa: BLE001
        scanner_status["docker"] = "error"
        logger.warning("[full-scan] docker_cis failed: %r", e)

    summary = _summarise(findings)
    critical = summary["by_severity"].get("critical", 0)
    high     = summary["by_severity"].get("high", 0)
    degraded = any(s != "ok" for s in scanner_status.values())

    return {
        "findings":        findings,
        "summary":         summary,
        "scanner_status":  scanner_status,
        "degraded":        degraded,
        "critical_count":  critical,
        "high_count":      high,
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }


# ──────────────────────────────────────────────────────────────────────
# Depth gate
# ──────────────────────────────────────────────────────────────────────
# The directive specifies EXACT thresholds:
#   • Small = ≤ 1 file changed AND ≤ 50 lines diff  →  skip Full Scan
#   • Anything else, OR a diff that touches a FastAPI/Flask/Express
#     entrypoint / Dockerfile, triggers the full pipeline.
#
# We keep the numbers as module-level constants so they can be tuned
# from a single place and referenced in tests without magic numbers.
DEPTH_GATE_MAX_FILES = 1
DEPTH_GATE_MAX_LINES = 50

_WEB_ENTRYPOINT_HINTS = (
    "FastAPI(",           # fastapi
    "Flask(__name__",     # flask
    "express()",          # express
)


def _diff_metrics(files: list[dict]) -> tuple[int, int]:
    """Return (file_count, line_count) for the submitted-files list.

    `files` is the Loop engine's `submitted_files` shape, i.e.
    `[{path, content, prev_content?}, ...]`. When `prev_content` is
    available we count only added/changed lines; when it is not we
    fall back to counting all lines in the new content (worst-case
    upper bound so the gate is safe against under-scanning).
    """
    n_files = 0
    n_lines = 0
    for f in files or []:
        content = f.get("content") or ""
        if not content:
            continue
        n_files += 1
        prev = f.get("prev_content")
        if prev is None:
            n_lines += content.count("\n") + (1 if content else 0)
            continue
        # Cheap line-count difference: measure added lines only.
        prev_lines = set(prev.splitlines())
        new_lines  = content.splitlines()
        added = sum(1 for ln in new_lines if ln not in prev_lines)
        n_lines += added
    return n_files, n_lines


def _touches_web_or_dockerfile(files: list[dict]) -> bool:
    """True iff any changed file is a FastAPI/Flask/Express entrypoint
    or a Dockerfile / docker-compose file. These require the deeper
    scan even when the diff is small — a one-line Dockerfile change
    can introduce a CIS 4.10 secret leak that Vanguard alone won't
    catch."""
    for f in files or []:
        path = (f.get("path") or "").lower()
        base = path.rsplit("/", 1)[-1]
        if base.startswith("dockerfile") or base.endswith(".dockerfile"):
            return True
        if base.startswith("docker-compose") and base.endswith((".yml", ".yaml")):
            return True
        content = f.get("content") or ""
        if not content:
            continue
        if any(hint in content for hint in _WEB_ENTRYPOINT_HINTS):
            return True
    return False


def should_run_full_scan(files: list[dict]) -> tuple[bool, str]:
    """Depth-gate decision. Returns (should_run, reason_str)."""
    n_files, n_lines = _diff_metrics(files)

    if _touches_web_or_dockerfile(files):
        return True, (
            f"diff touches an HTTP entrypoint or Dockerfile "
            f"({n_files} files, {n_lines} lines) — Full Scan required"
        )
    if n_files > DEPTH_GATE_MAX_FILES:
        return True, (
            f"{n_files} files changed > {DEPTH_GATE_MAX_FILES} — Full Scan required"
        )
    if n_lines > DEPTH_GATE_MAX_LINES:
        return True, (
            f"{n_lines} lines changed > {DEPTH_GATE_MAX_LINES} — Full Scan required"
        )
    return False, (
        f"small diff ({n_files} files, {n_lines} lines) — "
        f"Verify + Vanguard only"
    )


def files_to_text_cache(files: list[dict]) -> dict[str, str]:
    """Convert Loop `submitted_files` into the `text_cache` shape
    every scanner expects. Filters out empty entries."""
    cache: dict[str, str] = {}
    for f in files or []:
        path = f.get("path") or ""
        content = f.get("content") or ""
        if path and content:
            cache[path] = content
    return cache


def group_findings_for_self_heal(
    findings: list[dict],
    *,
    scoped_paths: Optional[set[str]] = None,
) -> dict[str, list[dict]]:
    """Group critical/high findings by file so the self-heal loop can
    process one file's issues per attempt.

    If `scoped_paths` is given (typically Loop's `submitted_files`
    paths), findings on unrelated files are dropped. This satisfies
    the directive rule that Loop-Mode Full Scan only *blocks Ship*
    on findings in files ORA itself just generated — legacy vulns in
    untouched files never block a commit.
    """
    grouped: dict[str, list[dict]] = {}
    for f in findings or []:
        sev = (f.get("severity") or "").lower()
        if sev not in ("critical", "high"):
            continue
        path = f.get("file") or ""
        if scoped_paths is not None and path not in scoped_paths:
            continue
        grouped.setdefault(path, []).append(f)
    return grouped
