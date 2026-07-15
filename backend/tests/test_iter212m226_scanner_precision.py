"""
Iter 212m-226 — Scanner precision hardening.

Fixes six specific false-positive classes uncovered by the dogfood
self-scan run (/app/test_reports/self_scan.md, 2026-07-15):

1. `self_scan.py` was reading the WRONG field names from
   `architecture_health.run_health_report()` output — so every
   `high_complexity` finding printed `?:1402 — complexity=0`.
2. `self_scan.py` aggregation only checked `f["file"]` / `f["path"]`,
   missing vanguard's `f["filepath"]` key — 37 vanguard findings
   printed `?:10`.
3. `bug_hunt.admin_route_no_auth` fired on every file defining an
   `/admin` route, even when the router already declared
   `dependencies=[Depends(require_admin)]` at APIRouter level.
4. `bug_hunt.env_var_in_code` was too lax and matched harmless
   constants like `MESSAGE_TEMPLATE = "Hey there, welcome ..."`.
5. Vanguard's dangerous-code sweep flagged JSDoc `* dangerouslySetInnerHTML`
   explainer comments as HIGH findings.
6. Vanguard's `eval_usage` / `exec_usage` fired on shell scripts
   and prose text where `eval` appears in an echo string.

Regression: any of these coming back as CRITICAL/HIGH means the
scanner is drifting back toward noise.
"""

from __future__ import annotations


# ── Fix 1 & 2 (self_scan.py field mismatches) ────────────────────
def test_self_scan_reads_correct_arch_fields():
    """Confirm self_scan uses `hit['file']`, `hit['func']`, `hit['cc']`
    — the actual keys emitted by `ComplexityHit.as_dict()`."""
    src = open("/app/backend/scripts/self_scan.py").read()
    # Should reference the correct architecture_health field names.
    assert 'hit.get("file"' in src or 'hit.get("file",' in src, (
        "self_scan.py must read `file` (not `path`) from complexity_hits"
    )
    assert "hit.get('cc'" in src or 'hit.get("cc"' in src, (
        "self_scan.py must read `cc` (not `complexity`) from complexity_hits"
    )
    assert 'report.get("circular_imports"' in src, (
        "self_scan.py must read `circular_imports` (not `cycles`)"
    )
    # And the aggregation footer should check filepath too.
    assert 'f.get("filepath")' in src, (
        "self_scan aggregation must recognise vanguard's `filepath` key"
    )


# ── Fix 3: admin_route_no_auth respects auth guards ──────────────
def test_admin_route_no_auth_skips_files_with_require_admin():
    from services.bug_hunt_rules import scan_bug_hunt

    admin_file = (
        "from fastapi import APIRouter, Depends\n"
        "from cto_services.auth import require_admin\n"
        "router = APIRouter(dependencies=[Depends(require_admin)])\n"
        "\n"
        "@router.get('/admin/stats')\n"
        "async def admin_stats():\n"
        "    return {'ok': True}\n"
    )
    findings = scan_bug_hunt({"backend/routers/admin.py": admin_file})
    admin_hits = [f for f in findings if f["title"] == "admin_route_no_auth"]
    assert admin_hits == [], (
        f"Should NOT flag admin_route_no_auth when file uses "
        f"require_admin, got: {admin_hits}"
    )


def test_admin_route_no_auth_still_fires_when_unguarded():
    """Guard against over-correction — an actual unguarded /admin
    route MUST still surface as a finding."""
    from services.bug_hunt_rules import scan_bug_hunt

    unguarded = (
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n"
        "\n"
        "@router.get('/admin/secrets')\n"
        "async def leak_it():\n"
        "    return {'stripe_key': 'sk_live_deadbeef'}\n"
    )
    findings = scan_bug_hunt({"backend/routers/danger.py": unguarded})
    admin_hits = [f for f in findings if f["title"] == "admin_route_no_auth"]
    assert admin_hits, (
        "Unguarded /admin route MUST still surface — false-negative"
    )


# ── Fix 4: env_var_in_code entropy requirement ───────────────────
def test_env_var_in_code_ignores_low_entropy_constants():
    from services.bug_hunt_rules import scan_bug_hunt
    benign = (
        'MESSAGE_TEMPLATE = "Hey there, welcome to our platform"\n'
        'ONBOARDING_TEXT = "Please verify your email address"\n'
    )
    findings = scan_bug_hunt({"backend/services/onboarding_email.py": benign})
    env_hits = [f for f in findings if f["title"] == "env_var_in_code"]
    assert env_hits == [], (
        f"Long prose constants must NOT be flagged as env vars: {env_hits}"
    )


def test_env_var_in_code_still_flags_actual_secret():
    from services.bug_hunt_rules import scan_bug_hunt
    bad = 'OPENAI_KEY = "sk-proj-abc123def456ghijklmnop789"\n'
    findings = scan_bug_hunt({"backend/services/creds.py": bad})
    env_hits = [f for f in findings if f["title"] == "env_var_in_code"]
    assert env_hits, "Genuine env-like assignment must still surface"


# ── Fix 5: skip JSDoc comment lines in vanguard ──────────────────
def test_vanguard_skips_comment_line_dangerously_set_html():
    from services.vanguard_scanner import scan_text
    jsdoc = (
        "/**\n"
        " *   - message: HTML string (rendered via dangerouslySetInnerHTML).\n"
        " */\n"
        "export const RobotGuide = ({ message }) => <div>{message}</div>;\n"
    )
    findings = scan_text(jsdoc, filepath="frontend/src/components/RobotGuide.jsx")
    dangerous = [f for f in findings if f["name"] == "dangerously_set_html"]
    assert dangerous == [], (
        f"JSDoc explainer comment must not trigger XSS finding: {dangerous}"
    )


def test_vanguard_still_flags_real_dangerously_set_html():
    from services.vanguard_scanner import scan_text
    real = "return <div dangerouslySetInnerHTML={{ __html: html }} />;\n"
    findings = scan_text(real, filepath="frontend/src/pages/Policy.jsx")
    dangerous = [f for f in findings if f["name"] == "dangerously_set_html"]
    assert dangerous, "Actual XSS sink must still be caught"


# ── Fix 6: eval_usage skips non-code files ───────────────────────
def test_vanguard_skips_eval_usage_in_shell_scripts():
    from services.vanguard_scanner import scan_text
    shell = (
        "#!/bin/bash\n"
        'echo "→ 3. Running promptfoo eval (self-hosted, no cloud calls)…"\n'
        "eval $(cat /etc/env)\n"   # a REAL eval on a shell line — still ignored
        # because .sh is not in _is_code_ext
    )
    findings = scan_text(shell, filepath="qa/simulated-user/run.sh")
    eval_hits = [f for f in findings if f["name"] == "eval_usage"]
    assert eval_hits == [], (
        f"Shell scripts must not be scanned for Python eval(): {eval_hits}"
    )


def test_vanguard_still_flags_eval_in_python():
    from services.vanguard_scanner import scan_text
    py = "result = eval(user_input)\n"
    findings = scan_text(py, filepath="backend/services/badcode.py")
    eval_hits = [f for f in findings if f["name"] == "eval_usage"]
    assert eval_hits, "Python eval() must still be caught"
