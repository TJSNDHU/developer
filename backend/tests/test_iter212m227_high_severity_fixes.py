"""
Iter 212m-227 — Phase 5: fix real HIGH-severity findings.

Locks the following hardening changes:

1. Docker CIS 4.1 — `USER` directive present in backend/frontend/outbox
   Dockerfiles so containers never run as root.
2. Docker CIS 4.6 — `HEALTHCHECK` present in every Dockerfile.
3. Motor pool config — all AsyncIOMotorClient() call sites in
   scripts/migrations/evals/infra/memory_tiers pass maxPoolSize+
   maxIdleTimeMS+connectTimeoutMS so they never starve connections.
4. Bug-hunt endpoint rules skip regex hits landing inside comment
   lines (kills the `main.py:735 cors_allow_all` false-positive
   which flagged an explainer comment about a FIXED bug).
5. Perf scanner skips scanner-rule-definition files (kills the
   `codebase_health.py:162 unbounded_tolist` self-referential FP).
6. DOMPurify is a runtime dependency and is used by RobotGuide,
   MermaidBlock, PolicyPage, and Projects' XSS sinks.
"""

from __future__ import annotations

import re


# ── Dockerfile CIS hardening ─────────────────────────────────────
def test_backend_dockerfile_has_user_and_healthcheck():
    src = open("/app/backend/Dockerfile").read()
    assert re.search(r"^\s*USER\s+\w", src, re.MULTILINE), (
        "backend/Dockerfile must set USER (CIS 4.1)"
    )
    assert "HEALTHCHECK" in src, "backend/Dockerfile must have HEALTHCHECK (CIS 4.6)"


def test_frontend_dockerfile_has_user_and_healthcheck():
    src = open("/app/frontend/Dockerfile").read()
    assert re.search(r"^\s*USER\s+\w", src, re.MULTILINE), (
        "frontend/Dockerfile must set USER (CIS 4.1)"
    )
    assert "HEALTHCHECK" in src, "frontend/Dockerfile must have HEALTHCHECK (CIS 4.6)"


def test_outbox_dockerfile_has_user_and_healthcheck():
    src = open("/app/infra/outbox/Dockerfile").read()
    assert re.search(r"^\s*USER\s+\w", src, re.MULTILINE), (
        "infra/outbox/Dockerfile must set USER (CIS 4.1)"
    )
    assert "HEALTHCHECK" in src, "infra/outbox/Dockerfile must have HEALTHCHECK (CIS 4.6)"


# ── Motor pool config on all scripted call sites ──────────────────
def test_motor_pool_config_on_scripts():
    """AsyncIOMotorClient() must always pass maxPoolSize + timeouts
    so a burst never starves connections."""
    paths = [
        "/app/backend/scripts/cleanup_orphans.py",
        "/app/backend/scripts/migrate_iter34.py",
        "/app/backend/migrations/001_aurem_upgrade_indexes.py",
        "/app/backend/migrations/002_encrypt_pats.py",
        "/app/backend/shared/memory_tiers.py",
        "/app/infra/outbox/worker.py",
        "/app/qa/simulated-user/seed_qa_user.py",
    ]
    for path in paths:
        src = open(path).read()
        assert "maxPoolSize" in src, (
            f"{path} — AsyncIOMotorClient() missing maxPoolSize (P1 db finding)"
        )
        assert "maxIdleTimeMS" in src, (
            f"{path} — AsyncIOMotorClient() missing maxIdleTimeMS"
        )


# ── Bug-hunt endpoint rules skip comment-line hits ────────────────
def test_bug_hunt_cors_wildcard_skips_comment_line():
    """main.py:735 has `# CORS lockdown. allow_origins=['*']` in a
    comment explaining a fixed bug. That must NOT trigger."""
    from services.bug_hunt_rules import scan_bug_hunt

    src = (
        "# CORS lockdown. allow_origins=['*'] meant ANY website could hit\n"
        "# the API. Now we read ALLOWED_ORIGINS from env.\n"
        "_ALLOWED_ORIGINS = ['https://auremcto.com']\n"
        "app.add_middleware(CORSMiddleware, allow_origins=_ALLOWED_ORIGINS)\n"
    )
    findings = scan_bug_hunt({"backend/main.py": src})
    cors_hits = [f for f in findings if f["title"] == "cors_allow_all"]
    assert cors_hits == [], (
        f"cors_allow_all must skip explainer comments: {cors_hits}"
    )


def test_bug_hunt_cors_wildcard_still_fires_on_real_code():
    """Guard against over-correction."""
    from services.bug_hunt_rules import scan_bug_hunt
    src = (
        "app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_credentials=True)\n"
    )
    findings = scan_bug_hunt({"backend/main.py": src})
    cors_hits = [f for f in findings if f["title"] == "cors_allow_all"]
    assert cors_hits, "Real allow_origins=['*'] MUST still be flagged"


# ── Perf scanner skips scanner rule files ─────────────────────────
def test_perf_scanner_skips_own_rule_files():
    """codebase_health.py contains fix-hint strings like `.to_list(None)`
    that used to trigger unbounded_tolist on itself."""
    from routers.codebase_health import _scan_performance

    src = (
        'def _perf_fix_hint(rid):\n'
        '    return {\n'
        '        "unbounded_tolist":\n'
        '            "Replace `.to_list(None)` with `.skip(skip).limit(limit)`.",\n'
        '    }.get(rid, "")\n'
    )
    findings = _scan_performance({"backend/routers/codebase_health.py": src})
    unbounded = [f for f in findings if f["title"] == "unbounded_tolist"]
    assert unbounded == [], (
        f"Perf scanner must skip scanner rule files: {unbounded}"
    )


# ── DOMPurify dependency + import wiring ──────────────────────────
def test_dompurify_in_package_json():
    import json
    pkg = json.loads(open("/app/frontend/package.json").read())
    deps = {**(pkg.get("dependencies") or {}), **(pkg.get("devDependencies") or {})}
    assert "dompurify" in deps, "dompurify must be a runtime dependency"


def test_robot_guide_imports_dompurify():
    src = open("/app/frontend/src/components/RobotGuide.jsx").read()
    assert 'import DOMPurify' in src, "RobotGuide must import DOMPurify"
    assert 'DOMPurify.sanitize' in src, "RobotGuide must call DOMPurify.sanitize"


def test_policy_page_imports_dompurify():
    src = open("/app/frontend/src/pages/PolicyPage.jsx").read()
    assert 'import DOMPurify' in src, "PolicyPage must import DOMPurify"
    assert 'DOMPurify.sanitize' in src, "PolicyPage must sanitize marked output"


def test_mermaid_block_imports_dompurify():
    src = open("/app/frontend/src/components/MermaidBlock.jsx").read()
    assert 'import DOMPurify' in src, "MermaidBlock must import DOMPurify"


def test_projects_page_imports_dompurify():
    src = open("/app/frontend/src/pages/Projects.jsx").read()
    assert 'import DOMPurify' in src, "Projects must import DOMPurify"
