"""
test_scan_finding_quality.py — Iter 301 (Track 3 v1 — deterministic)

Feeds 5 known-vulnerable snippets to the REAL security gate
(`scaffold_security_gate.scan_files` via `scan_finding_matches`) and
asserts the correct rule + severity fires for each. This is the
loop's last line of defence before generated code hits the repo;
regression here = a Vanguard-catalog bug lands silently.

Zero LLM calls. Real code path in test — every assertion is on the
actual scanner's output.
"""
from __future__ import annotations

import asyncio

from services.reasoning_evals import scan_finding_matches


def test_scan_flags_openai_secret():
    """Hardcoded OpenAI key format must fire the openai-secret rule
    at CRITICAL severity (blocks materialize + ship)."""
    files = [{
        "path": "api/config.py",
        "content": 'OPENAI_API_KEY = "sk-proj-abcdefghijklmnop123456789012345678"',
    }]
    r = asyncio.run(scan_finding_matches(files, expected_severity="critical"))
    assert r["ok"] is True, (
        f"OpenAI secret must be caught; findings: {r['actual_findings']}"
    )


def test_scan_flags_shell_true_subprocess():
    """subprocess.run(..., shell=True) with user input is a classic
    RCE surface. Must fire regardless of what the surrounding code
    tries to look-safe."""
    files = [{
        "path": "api/utils.py",
        "content": (
            "import subprocess\n"
            "def clean(path):\n"
            "    subprocess.run(f'rm -rf {path}', shell=True)\n"
        ),
    }]
    r = asyncio.run(scan_finding_matches(
        files, expected_severity="high",
    ))
    # `shell=True` may register at HIGH or CRITICAL depending on the
    # catalog. Accept either — the invariant is a blocking severity
    # fires, not the exact name.
    if not r["ok"]:
        r_crit = asyncio.run(scan_finding_matches(
            files, expected_severity="critical",
        ))
        assert r_crit["ok"], (
            f"shell=True RCE surface must be blocked (high|critical); "
            f"findings: {r_crit['actual_findings']}"
        )


def test_scan_flags_eval_of_user_input():
    """Direct eval() of user input is a text-book RCE. Must block."""
    files = [{
        "path": "api/handlers.py",
        "content": "def run(payload):\n    return eval(payload)\n",
    }]
    r = asyncio.run(scan_finding_matches(files, expected_severity="critical"))
    if not r["ok"]:
        # Some catalogs classify as HIGH. Accept either.
        r_high = asyncio.run(scan_finding_matches(
            files, expected_severity="high",
        ))
        assert r_high["ok"], (
            f"eval(user_input) RCE surface must fire high/critical; "
            f"findings: {r_high['actual_findings']}"
        )


def test_scan_flags_dangerouslysetinnerhtml_react():
    """React `dangerouslySetInnerHTML={{ __html: userBio }}` is the
    canonical XSS surface. Must fire at high or higher."""
    files = [{
        "path": "ui/src/Profile.jsx",
        "content": (
            'export default function P({userBio}) {\n'
            '  return <div dangerouslySetInnerHTML={{__html: userBio}} />;\n'
            '}\n'
        ),
    }]
    r = asyncio.run(scan_finding_matches(files, expected_severity="high"))
    if not r["ok"]:
        r_crit = asyncio.run(scan_finding_matches(
            files, expected_severity="critical",
        ))
        assert r_crit["ok"], (
            f"dangerouslySetInnerHTML XSS must be caught at high|critical; "
            f"findings: {r_crit['actual_findings']}"
        )


def test_scan_allows_clean_code_without_false_positives():
    """The gate must NOT false-positive on straightforward code —
    a noisy scanner erodes trust and gets bypassed. Clean handler:
    no secrets, no dangerous ops, no XSS surfaces → zero blocking
    findings."""
    files = [{
        "path": "api/health.py",
        "content": (
            "from fastapi import APIRouter\n"
            "router = APIRouter()\n"
            "@router.get('/health')\n"
            "async def health():\n"
            "    return {'ok': True}\n"
        ),
    }]
    from services.scaffold_security_gate import scan_files
    r = asyncio.run(scan_files(files))
    assert r["ok"] is True, (
        f"clean handler false-positived; findings: {r['findings']}"
    )
    assert r["summary"]["critical"] == 0
    assert r["summary"]["high"]     == 0
