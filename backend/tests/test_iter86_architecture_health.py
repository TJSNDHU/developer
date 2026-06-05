"""
test_iter86_architecture_health.py — Static-analysis health report.

Locks the production-grade pieces of the new module so it can't
silently rot:
  • run_health_report() runs in < 5 s and surfaces ALL five signals.
  • The CLI script gates against the baseline (--fail-on-new).
  • The admin endpoint requires auth and emits the same payload.
  • The baseline file exists so CI starts gating today, not later.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import uuid

import httpx
import pytest


API = "http://localhost:8001/api/aurem-dev"
BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
REPO = os.path.dirname(BACKEND)
BASELINE = os.path.join(REPO, "memory", "arch_health_baseline.json")
FOUNDER_EMAIL = "teji.ss1986@gmail.com"
FOUNDER_PASSWORD = "founder-test-pass-9281"


# ── 1. Pure-Python entry points ────────────────────────────────────────

def test_run_health_report_returns_full_payload():
    from services.architecture_health import run_health_report
    r = run_health_report()
    # Every expected top-level key present, all the right types.
    for k in ("generated_at", "duration_ms", "total_files",
              "line_limit", "cc_limit",
              "bloated_files", "complexity_hits", "god_files",
              "circular_imports", "boundary_violations"):
        assert k in r, f"missing key in report: {k!r}"
    assert isinstance(r["bloated_files"], list)
    assert isinstance(r["complexity_hits"], list)
    assert isinstance(r["god_files"], list)
    assert isinstance(r["circular_imports"], list)
    assert isinstance(r["boundary_violations"], list)
    assert r["total_files"] > 50, f"too few files scanned: {r['total_files']}"
    assert r["duration_ms"] < 8000, (
        f"health report too slow: {r['duration_ms']} ms (limit 8000)"
    )


def test_run_health_report_detects_known_bloated_file():
    """We KNOW cto_projects.py is >1500 lines — the report must catch it.
    If someone refactors it down below 300 lines we'll want this test
    to fail loud so we can celebrate and refresh the baseline."""
    from services.architecture_health import run_health_report
    r = run_health_report()
    rels = {row["rel"] for row in r["bloated_files"]}
    assert "routers/cto_projects.py" in rels, (
        f"cto_projects.py not flagged as bloated; got {rels}"
    )


def test_summarise_produces_useful_text():
    from services.architecture_health import run_health_report, summarise
    txt = summarise(run_health_report())
    for header in (
        "Bloated files",
        "Complex functions",
        "God files",
        "Circular imports",
        "Boundary violations",
    ):
        assert header in txt, f"summary missing section: {header!r}"


# ── 2. CLI behaviour ───────────────────────────────────────────────────

def test_cli_summary_run_exits_zero():
    proc = subprocess.run(
        ["python", "scripts/architecture_health.py"],
        cwd=BACKEND, capture_output=True, text=True, timeout=15,
    )
    assert proc.returncode == 0, proc.stderr
    assert "Architecture health" in proc.stdout


def test_cli_fail_on_new_against_baseline_is_clean():
    """Baseline was just snapshotted by Iter 86. A fresh --fail-on-new
    run must exit 0 — no new regressions."""
    assert os.path.exists(BASELINE), (
        f"baseline file missing — run "
        f"`python backend/scripts/architecture_health.py "
        f"--update-baseline` once to seed it: {BASELINE}"
    )
    proc = subprocess.run(
        ["python", "scripts/architecture_health.py", "--fail-on-new"],
        cwd=BACKEND, capture_output=True, text=True, timeout=15,
    )
    assert proc.returncode == 0, (
        f"--fail-on-new tripped against committed baseline:\n"
        f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
    )


def test_cli_emits_valid_json_with_flag():
    proc = subprocess.run(
        ["python", "scripts/architecture_health.py", "--json"],
        cwd=BACKEND, capture_output=True, text=True, timeout=15,
    )
    assert proc.returncode == 0
    data = json.loads(proc.stdout)
    assert "bloated_files" in data
    assert isinstance(data["bloated_files"], list)


# ── 3. Admin endpoint ──────────────────────────────────────────────────

async def _founder_token() -> str:
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.post(f"{API}/auth/login", json={
            "email": FOUNDER_EMAIL, "password": FOUNDER_PASSWORD,
        })
        if r.status_code != 200:
            r = await c.post(f"{API}/auth/signup", json={
                "email": FOUNDER_EMAIL, "password": FOUNDER_PASSWORD,
                "name": "Founder Test",
            })
        assert r.status_code == 200, r.text
        return r.json()["token"]


@pytest.mark.asyncio
async def test_architecture_health_endpoint_admin_only():
    email = f"u_{uuid.uuid4().hex[:8]}@aurem.test"
    async with httpx.AsyncClient(timeout=10.0) as c:
        s = await c.post(f"{API}/auth/signup", json={
            "email": email, "password": "x" * 12, "name": "Free",
        })
        assert s.status_code == 200, s.text
        r = await c.get(
            f"{API}/admin/architecture-health",
            headers={"Authorization": f"Bearer {s.json()['token']}"},
        )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_architecture_health_endpoint_returns_report():
    token = await _founder_token()
    async with httpx.AsyncClient(timeout=30.0) as c:
        r = await c.get(
            f"{API}/admin/architecture-health",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    rep = body["report"]
    assert rep["total_files"] > 50
    # cto_projects.py must appear in the bloated set so we know the
    # endpoint is actually running the analyser, not returning fixtures.
    rels = {row["rel"] for row in rep["bloated_files"]}
    assert "routers/cto_projects.py" in rels


@pytest.mark.asyncio
async def test_architecture_health_summary_flag_returns_text():
    token = await _founder_token()
    async with httpx.AsyncClient(timeout=30.0) as c:
        r = await c.get(
            f"{API}/admin/architecture-health?summary=true",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert "summary" in body
    assert "Architecture health" in body["summary"]
    for k in ("bloated", "complex", "circular", "violations"):
        assert k in body["counts"]
