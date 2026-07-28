"""
routers/admin_qa.py — Iter 303 (Gap 2: /admin/qa dashboard)

Founder-facing QA status endpoint. One route:

    GET /api/admin/qa/status

Returns a single JSON payload the frontend renders as 4 cards:

    1. Test counts (backend Pytest, Frontend Vitest, Playwright, a11y)
    2. Style-classifier ratio (STATIC_GREP % — the iter289 CI-guard)
    3. a11y baseline counts (component + journey)
    4. CI status per job (frontend-vitest, visual-regression, test-style-guard,
       invariants) — pulled from GitHub Actions API if
       `GITHUB_ACTIONS_TOKEN` is set + `GITHUB_REPO` is set;
       otherwise reports `{"available": False, "reason": ...}`
       (honest — no fake green statuses).

All counts are computed at request time from the ACTUAL test files
on disk — no cached numbers, no manual bookkeeping to drift.

Auth: reuses `_require_admin` from routers/admin.py — same pattern
as every other admin endpoint. No new auth surface introduced.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Optional

import httpx
from fastapi import APIRouter, Header, HTTPException

from routers.admin import _require_admin


router = APIRouter(prefix="/admin/qa", tags=["admin-qa"])


# ═══════════════════════════════════════════════════════════════════
# Test-count harvesters (deterministic on-disk grep)
# ═══════════════════════════════════════════════════════════════════

_APP_ROOT = Path("/app")

_TEST_FN_RE_PY  = re.compile(r"^\s*(?:async\s+)?def\s+test_\w+\s*\(", re.M)
_TEST_FN_RE_JS  = re.compile(r"^\s*(?:test|it)\s*\(\s*[\"']", re.M)


def _count_matches(paths: list[Path], regex: re.Pattern) -> int:
    total = 0
    for p in paths:
        if not p.exists() or not p.is_file():
            continue
        try:
            src = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        total += len(regex.findall(src))
    return total


def _glob_files(root: Path, patterns: list[str]) -> list[Path]:
    out: list[Path] = []
    for pat in patterns:
        out.extend(root.glob(pat))
    return out


def _harvest_counts() -> dict:
    """Test counts. For backend Pytest we use the AST-based analyzer
    (same source as the style-ratio guard) so the number aligns with
    what CI grades against — a plain regex-grep would count test
    helpers inside class bodies too."""
    # ── Backend pytest — trust the AST analyzer ─────────────────
    try:
        from services.test_style_analyzer import analyze_suite
        r = analyze_suite()
        backend_pytest_total = r.get("total_tests") or 0
    except Exception:                                       # noqa: BLE001
        backend_pytest_total = 0
    backend_pytest_files = len(list(
        (_APP_ROOT / "backend" / "tests").glob("test_*.py")
    )) + len(list(
        (_APP_ROOT / "backend" / "tests" / "reasoning").glob("test_*.py")
    ))

    # ── Frontend Vitest (component RTL + a11y) ───────────────────
    frontend_vitest_files = list(
        (_APP_ROOT / "frontend" / "src").rglob("*.test.jsx")
    ) + list(
        (_APP_ROOT / "frontend" / "src").rglob("*.test.js")
    )
    frontend_vitest_total = _count_matches(frontend_vitest_files, _TEST_FN_RE_JS)

    # ── Playwright ───────────────────────────────────────────────
    playwright = list(
        (_APP_ROOT / "frontend" / "tests" / "visual").glob("*.spec.js")
    )
    playwright_total = _count_matches(playwright, _TEST_FN_RE_JS)

    # ── Reasoning-eval suite (Track 3) — subset of backend ──────
    reasoning = list(
        (_APP_ROOT / "backend" / "tests" / "reasoning").glob("test_*.py")
    )
    reasoning_total = _count_matches(reasoning, _TEST_FN_RE_PY)

    return {
        "backend_pytest": {
            "files": backend_pytest_files,
            "tests": backend_pytest_total,
        },
        "frontend_vitest": {
            "files": len(frontend_vitest_files),
            "tests": frontend_vitest_total,
        },
        "playwright": {
            "files": len(playwright),
            "tests": playwright_total,
        },
        "reasoning_evals": {
            "files": len(reasoning),
            "tests": reasoning_total,
            "note": "subset of backend_pytest (Track 3 v1)",
        },
        "grand_total_tests": (
            backend_pytest_total + frontend_vitest_total + playwright_total
        ),
    }


# ═══════════════════════════════════════════════════════════════════
# Style-classifier ratio (iter289 CI-guard number)
# ═══════════════════════════════════════════════════════════════════

def _harvest_test_style_ratio() -> dict:
    """Delegates to services.test_style_analyzer.analyze_suite — the
    SAME code path the CI guard uses, so this dashboard number is
    always aligned with whatever CI is grading against."""
    try:
        from services.test_style_analyzer import analyze_suite
        r = analyze_suite()
    except Exception as e:                                # noqa: BLE001
        return {"available": False, "reason": f"analyzer error: {e!r}"}
    counts = r.get("counts") or {}
    total  = r.get("total_tests") or 0
    static_grep = counts.get("STATIC_GREP", 0)
    ratio = (static_grep / total) if total else 0.0
    return {
        "available":         True,
        "static_grep":       static_grep,
        "total_tests":       total,
        "ratio_pct":         round(ratio * 100, 1),
        "threshold_pct":     60.0,
        "passes_threshold":  ratio <= 0.60,
        "weak_p0_count":     len(r.get("weak_p0") or []),
    }


# ═══════════════════════════════════════════════════════════════════
# a11y baselines (component-level + journey-level)
# ═══════════════════════════════════════════════════════════════════

_A11Y_BASELINES = {
    "components": _APP_ROOT / "docs" / "a11y_baseline.json",
    "journeys":   _APP_ROOT / "docs" / "a11y_journey_baseline.json",
}


def _harvest_a11y() -> dict:
    out: dict = {}
    for kind, path in _A11Y_BASELINES.items():
        try:
            j = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            out[kind] = {"available": False, "reason": str(e)}
            continue
        # Exclude keys starting with "_" (metadata like "_note").
        per_key = {k: v for k, v in j.items() if not k.startswith("_")}
        total_violations = sum(
            len(v) if isinstance(v, list) else 0
            for v in per_key.values()
        )
        out[kind] = {
            "available":         True,
            "surfaces_tracked":  len(per_key),
            "surfaces_clean":    sum(
                1 for v in per_key.values()
                if isinstance(v, list) and len(v) == 0
            ),
            "total_known_violations": total_violations,
            "per_surface": {
                k: (v if isinstance(v, list) else [])
                for k, v in per_key.items()
            },
        }
    return out


# ═══════════════════════════════════════════════════════════════════
# GitHub Actions status per job (honest: unavailable if not wired)
# ═══════════════════════════════════════════════════════════════════

# Env contract:
#   GITHUB_ACTIONS_TOKEN  — a PAT or fine-grained token with `actions:read`
#                          scope on the repo. Read-only; never mutating.
#   GITHUB_REPO           — "owner/name" of the repo whose runs to fetch.
# When either is missing, we return an honest "unavailable" payload
# instead of faking green statuses.

_JOB_NAMES_WE_CARE_ABOUT = (
    "bug-fix-discipline",
    "invariants",
    "test-style-guard",
    "frontend-vitest",
    "visual-regression",
)


async def _harvest_ci_status() -> dict:
    token = os.environ.get("GITHUB_ACTIONS_TOKEN")
    repo  = os.environ.get("GITHUB_REPO")
    if not token or not repo:
        return {
            "available": False,
            "reason": (
                "GITHUB_ACTIONS_TOKEN and/or GITHUB_REPO not set. "
                "Set both in backend/.env to enable live CI status. "
                "Token needs `actions:read` scope on the repo."
            ),
            "jobs": {},
        }

    url = (f"https://api.github.com/repos/{repo}/actions/runs?"
           f"per_page=15&event=push")
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            r = await client.get(url, headers=headers)
        if r.status_code != 200:
            return {
                "available": False,
                "reason": f"GitHub API returned {r.status_code}: {r.text[:200]}",
                "jobs": {},
            }
        runs = (r.json() or {}).get("workflow_runs") or []
    except (httpx.HTTPError, ValueError) as e:                 # noqa: BLE001
        return {
            "available": False,
            "reason": f"GitHub API call failed: {e!r}",
            "jobs": {},
        }

    # For each named job we care about, find the MOST RECENT run
    # whose workflow lists that job. Simplification: use the newest
    # `quality-gate` run and enumerate its jobs.
    quality_runs = [r for r in runs
                    if (r.get("name") or "").lower().startswith("quality")]
    if not quality_runs:
        return {
            "available": False,
            "reason": "no quality-gate workflow runs found",
            "jobs": {},
        }
    latest = quality_runs[0]
    jobs_url = latest.get("jobs_url")
    if not jobs_url:
        return {"available": False, "reason": "run has no jobs_url", "jobs": {}}
    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            rj = await client.get(jobs_url, headers=headers)
        job_data = (rj.json() or {}).get("jobs") or []
    except (httpx.HTTPError, ValueError) as e:                 # noqa: BLE001
        return {"available": False, "reason": f"jobs fetch failed: {e!r}",
                 "jobs": {}}

    jobs_map = {}
    for j in job_data:
        name = j.get("name")
        if name in _JOB_NAMES_WE_CARE_ABOUT:
            jobs_map[name] = {
                "status":     j.get("status"),           # queued/in_progress/completed
                "conclusion": j.get("conclusion"),       # success/failure/cancelled/None
                "started_at": j.get("started_at"),
                "completed_at": j.get("completed_at"),
                "html_url":   j.get("html_url"),
            }
    # Fill in missing jobs so the UI can render "unknown" chips.
    for name in _JOB_NAMES_WE_CARE_ABOUT:
        jobs_map.setdefault(name, {
            "status": "unknown", "conclusion": None,
            "started_at": None, "completed_at": None,
            "html_url": latest.get("html_url"),
        })
    return {
        "available":       True,
        "workflow_run_id": latest.get("id"),
        "workflow_url":    latest.get("html_url"),
        "commit_sha":      latest.get("head_sha"),
        "commit_message":  (latest.get("head_commit") or {}).get("message"),
        "run_started_at":  latest.get("run_started_at"),
        "jobs":            jobs_map,
    }


# ═══════════════════════════════════════════════════════════════════
# Route
# ═══════════════════════════════════════════════════════════════════

@router.get("/latest-report")
async def get_latest_qa_report(authorization: Optional[str] = Header(None)):
    """Iter 334 — Auto-QA agent report viewer. Serves the markdown
    written by services/qa_matrix.write_report() (locally or by the
    auto-qa-agent CI job committing .emergent/latest-qa-report.md).
    Honest empty-state when the job has never run."""
    await _require_admin(authorization)
    path = "/app/.emergent/latest-qa-report.md"
    if not os.path.exists(path):
        return {"error": "No report yet — auto-qa-agent job has not run"}
    with open(path, "r", encoding="utf-8") as f:
        return {"content": f.read(), "modified_at": os.path.getmtime(path)}


@router.get("/status")
async def qa_status(authorization: Optional[str] = Header(None)):
    """Aggregate QA snapshot for /admin/qa. Locked to admin/founder."""
    await _require_admin(authorization)
    t0 = time.time()
    counts    = _harvest_counts()
    style     = _harvest_test_style_ratio()
    a11y      = _harvest_a11y()
    ci_status = await _harvest_ci_status()
    return {
        "generated_at": time.time(),
        "took_ms":      int((time.time() - t0) * 1000),
        "test_counts":  counts,
        "test_style":   style,
        "a11y":         a11y,
        "ci_status":    ci_status,
    }
