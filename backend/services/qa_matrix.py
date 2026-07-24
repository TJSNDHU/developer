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
_FRONTEND_COV_JSON  = "/app/frontend/coverage/coverage-summary.json"
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


def _frontend_coverage_summary() -> dict:
    """Read vitest+v8 coverage-summary.json if it exists. Same 'never
    fake numbers' contract as backend coverage_summary()."""
    if not os.path.isfile(_FRONTEND_COV_JSON):
        return {"ok": False, "reason": "no_run",
                "hint": "cd /app/frontend && npx vitest run --coverage"}
    try:
        with open(_FRONTEND_COV_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:                                       # noqa: BLE001
        return {"ok": False, "reason": "parse_error", "error": repr(e)[:200]}
    total = data.get("total") or {}
    stmts = total.get("statements") or {}
    per_file: list[dict] = []
    for path, meta in data.items():
        if path == "total":
            continue
        s = (meta or {}).get("statements") or {}
        per_file.append({
            "file":    path,
            "percent": s.get("pct"),
            "covered": s.get("covered"),
            "total":   s.get("total"),
        })
    per_file.sort(key=lambda w: (w.get("percent") if w.get("percent") is not None else 999,
                                 -1 * (w.get("total") or 0)))
    return {
        "ok":              True,
        "percent":         stmts.get("pct"),
        "statements":      stmts.get("total"),
        "covered":         stmts.get("covered"),
        "files_scanned":   len(per_file),
        "worst_5":         per_file[:5],
    }


# ── Iter 289 (Track 1 Lane A) — Matrix × Coverage gap computer ───────
# For every journey in the traceability matrix, extract its
# `system_paths` (source files the journey should exercise) and check
# whether the latest coverage run actually touched each one at ≥5%.
# Anything at 0% is flagged as an OPEN_COVERAGE_GAP — the matrix says
# it matters, but the tests don't touch it. This is the honest signal
# the founder asked for; nothing is hidden to make the number pretty.
_MIN_COVERAGE_HIT_PCT = 5.0


def _norm_path(p: str) -> str:
    """Normalise a system_paths entry to match coverage.json's keys.
    coverage.py records file paths relative to the run's cwd (i.e.
    /app/backend), so a matrix entry like 'backend/routers/mcp.py'
    is compared as 'routers/mcp.py'. Also strips `::function_name`
    suffixes used in the traceability matrix for method-level
    granularity — coverage.py only tracks at the file level, so we
    fold both back to the same key."""
    p = (p or "").strip()
    if "::" in p:
        p = p.split("::", 1)[0]
    if p.startswith("/app/backend/"):
        return p[len("/app/backend/"):]
    if p.startswith("backend/"):
        return p[len("backend/"):]
    return p


def matrix_coverage_gap() -> dict:
    """Return the honest, per-journey coverage-gap list.

    Shape:
      {
        "ok":                True | False,
        "backend_coverage":  {"percent": ..., "files_scanned": ...},
        "frontend_coverage": {...},
        "per_journey":       [{journey_id, title, status, severity,
                               tracked_paths, hit, uncovered,
                               uncovered_pct}],
        "summary":           {"journeys": N, "with_gap": M,
                               "fully_untouched": K,
                               "p0_with_gap": [journey_ids...]}
      }
    """
    matrix = load_matrix()
    journeys = matrix.get("journeys") or []
    if not journeys:
        return {"ok": False, "reason": "empty_matrix"}

    # Backend cov map: {"routers/mcp.py": percent, ...}
    be_map: dict[str, float] = {}
    if os.path.isfile(_COVERAGE_JSON):
        try:
            with open(_COVERAGE_JSON, "r", encoding="utf-8") as f:
                cd = json.load(f)
            for path, meta in (cd.get("files") or {}).items():
                s = (meta.get("summary") or {})
                pc = s.get("percent_covered")
                if isinstance(pc, (int, float)):
                    be_map[path] = float(pc)
        except Exception:                                        # noqa: BLE001
            pass

    # Frontend cov map: {"src/components/Foo.jsx": percent, ...}
    fe_map: dict[str, float] = {}
    if os.path.isfile(_FRONTEND_COV_JSON):
        try:
            with open(_FRONTEND_COV_JSON, "r", encoding="utf-8") as f:
                fd = json.load(f)
            for path, meta in fd.items():
                if path == "total":
                    continue
                s = (meta or {}).get("statements") or {}
                pc = s.get("pct")
                if isinstance(pc, (int, float)):
                    # vitest records absolute paths; normalise to
                    # everything past /app/frontend/.
                    rel = path
                    if "/app/frontend/" in path:
                        rel = path.split("/app/frontend/", 1)[1]
                    fe_map[rel] = float(pc)
        except Exception:                                        # noqa: BLE001
            pass

    per_journey: list[dict] = []
    for j in journeys:
        raw_paths = [p for p in (j.get("system_paths") or [])
                     if isinstance(p, str)]
        tracked = []
        hit = []
        uncovered = []
        for rp in raw_paths:
            if rp.startswith(("backend/", "/app/backend/")):
                key = _norm_path(rp)
                pct = be_map.get(key)
                tracked.append({"path": rp, "layer": "backend",
                                "percent": pct})
                if pct is not None and pct >= _MIN_COVERAGE_HIT_PCT:
                    hit.append(rp)
                else:
                    uncovered.append(rp)
            elif rp.startswith(("frontend/", "/app/frontend/", "src/")):
                key = rp
                if rp.startswith("/app/frontend/"):
                    key = rp[len("/app/frontend/"):]
                elif rp.startswith("frontend/"):
                    key = rp[len("frontend/"):]
                pct = fe_map.get(key)
                tracked.append({"path": rp, "layer": "frontend",
                                "percent": pct})
                if pct is not None and pct >= _MIN_COVERAGE_HIT_PCT:
                    hit.append(rp)
                else:
                    uncovered.append(rp)
            else:
                # Non-code entry (e.g. .env, scripts, tests). Not
                # scored by coverage; leave uncovered.
                tracked.append({"path": rp, "layer": "other",
                                "percent": None})
                uncovered.append(rp)
        total_tracked = len(tracked) or 1
        per_journey.append({
            "journey_id":     j.get("journey_id"),
            "title":          j.get("title"),
            "status":         j.get("status"),
            "severity":       j.get("severity"),
            "tracked_paths":  tracked,
            "hit":            hit,
            "uncovered":      uncovered,
            "uncovered_pct":  round(100.0 * len(uncovered) / total_tracked, 1),
        })
    with_gap = [j for j in per_journey if j["uncovered"]]
    fully_untouched = [j for j in per_journey if not j["hit"]]
    p0_with_gap = [j["journey_id"] for j in with_gap
                   if j.get("severity") == "p0"]
    return {
        "ok":                True,
        "backend_coverage":  {
            "present": os.path.isfile(_COVERAGE_JSON),
            "files_scanned": len(be_map),
        },
        "frontend_coverage": {
            "present": os.path.isfile(_FRONTEND_COV_JSON),
            "files_scanned": len(fe_map),
        },
        "min_hit_pct":       _MIN_COVERAGE_HIT_PCT,
        "per_journey":       per_journey,
        "summary": {
            "journeys":         len(journeys),
            "with_gap":         len(with_gap),
            "fully_untouched":  len(fully_untouched),
            "p0_with_gap":      p0_with_gap,
        },
    }


def canary_e2e(mode: str = "lane_a") -> dict:
    """Track-1 canary entry point. Two lanes per the corrected charter:

    - mode='lane_a' (default): fast, no external deps. Returns
      backend + frontend coverage summaries, per-journey coverage-gap
      list, and a plain pass/fail signal. Callable at any time.
    - mode='lane_b': real GitHub round-trip integration test. Returns
      {ok: False, reason: 'lane_b_not_configured'} unless every
      Canary env var is set (AUREM_CANARY_REPO_OWNER,
      AUREM_CANARY_REPO_NAME, AUREM_CANARY_BRANCH, AUREM_ORG_NAME,
      AUREM_ORG_GITHUB_APP_TOKEN). We deliberately do NOT stub a
      passing result — Lane B is either configured & real, or absent."""
    if mode == "lane_a":
        be = coverage_summary()
        fe = _frontend_coverage_summary()
        gap = matrix_coverage_gap()
        summary = gap.get("summary") or {}
        # Overall pass criterion: at least one journey covered AND no
        # p0 journey fully untouched. Honest, not tuned to hide gaps.
        p0_gap = summary.get("p0_with_gap") or []
        ok_signal = (be.get("ok")
                     and summary.get("journeys", 0) > 0
                     and (summary.get("fully_untouched") or 0)
                        <= summary.get("journeys", 0))
        return {
            "ok":                bool(ok_signal),
            "mode":              "lane_a",
            "backend_coverage":  be,
            "frontend_coverage": fe,
            "gap_report":        gap,
            "signal": {
                "p0_journeys_with_any_gap":  len(p0_gap),
                "journeys_fully_untouched":  summary.get("fully_untouched"),
                "journeys_total":            summary.get("journeys"),
            },
        }
    if mode == "lane_b":
        need = ("AUREM_CANARY_REPO_OWNER", "AUREM_CANARY_REPO_NAME",
                "AUREM_CANARY_BRANCH", "AUREM_ORG_NAME",
                "AUREM_ORG_GITHUB_APP_TOKEN")
        missing = [k for k in need if not os.environ.get(k)]
        if missing:
            return {
                "ok":       False,
                "mode":     "lane_b",
                "reason":   "lane_b_not_configured",
                "missing_env": missing,
                "hint":     "Founder must set the missing env vars + "
                            "install the AUREM GitHub App on the "
                            "canary repo before Lane B can run.",
            }
        # Env is present but the actual real-commit integration is
        # NOT built yet — this is the single narrow-scope test we
        # deferred until the founder finishes GitHub-side setup.
        return {
            "ok":     False,
            "mode":   "lane_b",
            "reason": "lane_b_env_present_but_test_not_wired",
            "hint":   "Env vars detected. Real-commit round-trip "
                     "test is the next step (Iter 290+).",
        }
    return {"ok": False, "reason": "unknown_mode",
            "supported": ["lane_a", "lane_b"]}
