"""
services/qa_matrix.py — Iter 287 (Master QA Track 1 Step 5)

Read-only helpers backing the four QA MCP tools exposed by
`routers/mcp.py`. Deterministic, no LLM calls, no external I/O beyond
reading files already inside /app.

Public surface (all sync — cheap):
    load_matrix()          → dict           # /app/docs/traceability_matrix.json
    matrix_summary()       → dict           # counts by status / severity
    open_gaps(severity)    → list[dict]     # rows where status == OPEN_GAP
    regression_index()     → list[dict]     # regression test files + iters
    coverage_summary()     → dict           # aggregate from /app/backend/coverage.json if present
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Optional

_MATRIX_PATH        = "/app/docs/traceability_matrix.json"
_TESTS_DIR          = "/app/backend/tests"
_POSTMORTEMS_DIR    = "/app/postmortems"
_COVERAGE_JSON      = "/app/backend/coverage.json"
_REGRESSION_PREFIX  = "test_regression_iter"
_ITER_RE            = re.compile(r"test_regression_iter(\d+)[_.]")


def load_matrix() -> dict:
    """Return the raw matrix JSON. Never mutates the on-disk file."""
    try:
        with open(_MATRIX_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"journeys": [], "summary": {}, "_error": "traceability_matrix.json missing"}
    except json.JSONDecodeError as e:
        return {"journeys": [], "summary": {}, "_error": f"matrix parse error: {e}"}


def matrix_summary() -> dict:
    """Live counts computed from the actual `journeys` array — never
    trust the persisted `summary` block (it can drift)."""
    m = load_matrix()
    journeys = m.get("journeys") or []
    status_counts:   dict[str, int] = {}
    severity_counts: dict[str, int] = {}
    open_p0: list[str] = []
    for j in journeys:
        s = j.get("status") or "UNKNOWN"
        v = j.get("severity") or "unknown"
        status_counts[s]   = status_counts.get(s, 0) + 1
        severity_counts[v] = severity_counts.get(v, 0) + 1
        if s == "OPEN_GAP" and v == "p0":
            open_p0.append(j.get("journey_id", ""))
    return {
        "total_journeys":      len(journeys),
        "by_status":           status_counts,
        "by_severity":         severity_counts,
        "open_p0_gaps":        open_p0,
        "matrix_error":        m.get("_error"),
    }


def open_gaps(severity: Optional[str] = None) -> list[dict]:
    """Return rows where status == 'OPEN_GAP'. Optionally filter by
    severity ('p0' | 'p1' | 'p2')."""
    journeys = (load_matrix().get("journeys") or [])
    out = [j for j in journeys if j.get("status") == "OPEN_GAP"]
    if severity:
        out = [j for j in out if (j.get("severity") or "") == severity]
    # Sort p0 → p1 → p2 → other, then by id for stability.
    order = {"p0": 0, "p1": 1, "p2": 2}
    out.sort(key=lambda j: (order.get(j.get("severity", ""), 99), j.get("journey_id", "")))
    # Trim the payload to what a caller needs — the gap description is
    # the interesting part, not every metadata field.
    return [
        {
            "journey_id":       j.get("journey_id"),
            "title":            j.get("title"),
            "severity":         j.get("severity"),
            "gap_description":  j.get("gap_description"),
            "proposed_fix_family": j.get("proposed_fix_family"),
            "must_ship_regression_when_fixed": j.get("must_ship_regression_when_fixed", True),
        }
        for j in out
    ]


def regression_index() -> list[dict]:
    """List every `test_regression_iter*` file, grouped by iter, with
    the paired postmortem doc path when present."""
    if not os.path.isdir(_TESTS_DIR):
        return []
    rows: list[dict] = []
    for name in sorted(os.listdir(_TESTS_DIR)):
        if not name.startswith(_REGRESSION_PREFIX) or not name.endswith(".py"):
            continue
        m = _ITER_RE.search(name)
        iter_n = int(m.group(1)) if m else None
        pm = None
        if iter_n is not None and os.path.isdir(_POSTMORTEMS_DIR):
            for pm_name in os.listdir(_POSTMORTEMS_DIR):
                if pm_name.startswith(f"iter{iter_n}_") and pm_name.endswith(".md"):
                    pm = f"/app/postmortems/{pm_name}"
                    break
        rows.append({
            "test_file":   f"/app/backend/tests/{name}",
            "iter":        iter_n,
            "postmortem":  pm,
        })
    rows.sort(key=lambda r: (r["iter"] or 0, r["test_file"]))
    return rows


def coverage_summary() -> dict:
    """Return the aggregate coverage.py summary if a coverage run has
    been captured to /app/backend/coverage.json. Otherwise return a
    friendly `no_run` payload — never fake numbers."""
    if not os.path.isfile(_COVERAGE_JSON):
        return {
            "ok":     False,
            "reason": "no_run",
            "hint":   "Run `cd /app/backend && python -m pytest --cov=. --cov-report=json:coverage.json` to populate.",
        }
    try:
        with open(_COVERAGE_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:                                       # noqa: BLE001
        return {"ok": False, "reason": "parse_error", "error": repr(e)[:200]}
    totals = data.get("totals") or {}
    files  = data.get("files") or {}
    worst: list[dict] = []
    for path, meta in files.items():
        s = (meta.get("summary") or {})
        worst.append({
            "file":         path,
            "percent":      s.get("percent_covered"),
            "missing":      s.get("missing_lines"),
            "num_stmts":    s.get("num_statements"),
        })
    worst = [w for w in worst if isinstance(w["percent"], (int, float))]
    worst.sort(key=lambda w: (w["percent"], -1 * (w["num_stmts"] or 0)))
    return {
        "ok":            True,
        "percent":       totals.get("percent_covered"),
        "num_statements": totals.get("num_statements"),
        "missing_lines": totals.get("missing_lines"),
        "files_scanned": len(files),
        "worst_10":      worst[:10],
        "generated_at":  data.get("meta", {}).get("timestamp"),
    }
