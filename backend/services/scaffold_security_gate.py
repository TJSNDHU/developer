"""
services/scaffold_security_gate.py — Iter 212m-237 — Personal Track hardening

Single-source security gate for every code path that ships
LLM/scaffolder-generated files into AUREM-owned infrastructure.

Lovable's April 2026 breach happened because their scanner *existed*
but wasn't invoked at the ingress point where generated code entered
their infra — and it wasn't applied retroactively to older projects
on redeploy.  We fix both mistakes here:

    * ONE `scan_files()` function — reused by materialize AND every
      redeploy path.  No parallel/duplicate implementations.
    * Runs on EVERY call, not just first-time materialize — retroactive
      coverage is automatic by construction.

Threshold policy (approved by founder, Feb 13 2026):
    critical + high  → HARD BLOCK, materialize/redeploy rejected
    medium           → soft warn, allowed to ship
    info / low       → informational only

Coverage: reuses the full Vanguard 007 catalog (25+ patterns) via
`services.vanguard_scanner.scan_file_blocks`, which covers
    Secrets:         AWS, GCP, GitHub, Stripe live/test, OpenAI, SendGrid,
                     Slack, private keys, DB connection strings,
                     generic api_key / password / token / secret assigns.
    Dangerous code:  eval, exec, subprocess(shell=True), os.system,
                     pickle.loads, yaml.load (unsafe), requests
                     verify=False, SQL string interpolation,
                     .innerHTML=, dangerouslySetInnerHTML.
Plus one extra layer here:
    Path safety:     rejects any file with `..`, absolute paths, or
                     disallowed extensions.  Same rules as
                     scaffold_llm._path_is_safe — extracted here so
                     both LLM emission AND non-LLM heuristic files
                     go through the same check.
"""
from __future__ import annotations

import logging
from typing import Optional

from services.vanguard_scanner import scan_file_blocks
from services.scaffold_llm import _path_is_safe

logger = logging.getLogger(__name__)


# Hard-block severities. Anything at or above these levels rejects
# the materialize/redeploy call.  Founder-only override endpoint can
# bypass with audit-log.
_BLOCKING_SEVERITIES = frozenset({"CRITICAL", "HIGH"})


async def scan_files(files: list[dict]) -> dict:
    """Scan a `[{path, content}]` file list for security findings.

    Returns:
        {
            "ok":         bool,          # True iff blocking-count == 0
            "findings":   [...],         # full list, all severities
            "summary": {
                "critical":     int,
                "high":         int,
                "medium":       int,
                "info":         int,
                "path_unsafe":  int,
                "total":        int,
            },
            "blocking_severities": ["CRITICAL", "HIGH"],  # policy record
        }

    Behaviour guaranteed by the caller invariant: this function is
    invoked BEFORE any AUREM-owned resource is created (repo, project,
    deploy). If it returns `ok=False`, the caller must abort without
    side effects.
    """
    if not files:
        return _empty_summary(ok=True)

    # ── Layer 1: path safety (defence-in-depth) ─────────────────
    path_unsafe_findings: list[dict] = []
    for f in files:
        p = f.get("path") or ""
        if not _path_is_safe(p):
            path_unsafe_findings.append({
                "path":      p,
                "rule_id":   "unsafe_path",
                "severity":  "CRITICAL",
                "line":      0,
                "message":   f"Path is not permitted in a materialize payload: {p!r}",
            })

    # ── Layer 2: Vanguard 007 pattern catalog ───────────────────
    blocks = {f.get("path", ""): (f.get("content") or "") for f in files}
    try:
        pattern_findings = scan_file_blocks(blocks)
    except Exception as e:                                # noqa: BLE001
        # Fail-CLOSED — a scanner crash is treated as "unknown risk",
        # not "safe". This is the opposite of most fault-tolerance
        # heuristics but the correct posture for a security gate.
        logger.error("[scaffold-gate] scanner crashed: %r — failing closed", e)
        return {
            "ok":       False,
            "findings": [{
                "path":     "*",
                "rule_id":  "scanner_error",
                "severity": "CRITICAL",
                "message":  "Security scanner errored — refusing to ship.",
            }],
            "summary": {"critical": 1, "high": 0, "medium": 0, "info": 0,
                        "path_unsafe": 0, "total": 1,
                        "scanner_error": True},
            "blocking_severities": sorted(_BLOCKING_SEVERITIES),
        }

    all_findings = path_unsafe_findings + list(pattern_findings)

    # ── Summarise ──────────────────────────────────────────────
    counts = {"critical": 0, "high": 0, "medium": 0, "info": 0}
    for f in all_findings:
        sev = (f.get("severity") or "").upper()
        if sev == "CRITICAL": counts["critical"] += 1
        elif sev == "HIGH":   counts["high"] += 1
        elif sev == "MEDIUM": counts["medium"] += 1
        else:                 counts["info"] += 1
    summary = {
        **counts,
        "path_unsafe": len(path_unsafe_findings),
        "total":       len(all_findings),
    }
    ok = (counts["critical"] == 0 and counts["high"] == 0)

    if not ok:
        logger.warning("[scaffold-gate] BLOCKED: %s", summary)
    elif counts["medium"] > 0:
        logger.info("[scaffold-gate] soft-warn medium=%d", counts["medium"])

    return {
        "ok":                  ok,
        "findings":            all_findings,
        "summary":             summary,
        "blocking_severities": sorted(_BLOCKING_SEVERITIES),
    }


def _empty_summary(ok: bool = True) -> dict:
    return {
        "ok":       ok,
        "findings": [],
        "summary":  {"critical": 0, "high": 0, "medium": 0, "info": 0,
                     "path_unsafe": 0, "total": 0},
        "blocking_severities": sorted(_BLOCKING_SEVERITIES),
    }


def friendly_user_message(summary: dict) -> str:
    """Translate the scan summary into a jargon-free sentence for
    non-technical users. Never mention rule names, CVEs, or file
    paths — just a nudge to refine the brief."""
    crit = summary.get("critical", 0)
    high = summary.get("high", 0)
    total = crit + high
    if total == 0:
        return "Your app looks good."
    return (
        "We spotted something we don't want to ship as-is. Try being "
        "more specific in your brief about how users sign in and what "
        "data your app stores — we'll try again with a safer version."
    )


__all__ = [
    "scan_files",
    "friendly_user_message",
    "_BLOCKING_SEVERITIES",
]
