"""
Iter 292 — QA Meta-Layer regression tests.

# static-grep-ok: this file locks the SHAPE of docs/environments.md
# + AGENTS.md meta-layer sections + pytest.ini flaky marker
# declaration. Assertions on docs are unavoidably STATIC_GREP by
# design — you can't behaviourally "call" a Markdown file.

Locks four permanent additions:
  A1. docs/environments.md exists with a verified-inspection stamp
      and covers the four surfaces (Mongo, env vars, GitHub, services).
  A2. AGENTS.md carries the per-env deploy-report rule.
  B1. pytest.ini declares the `flaky` marker.
  B2. pytest.ini defaults CI runs to `-m "not flaky"` so quarantine
      tests are non-blocking.
  B3. AGENTS.md documents the ownership + fix-by rule and the
      Loop/SSE flakiness exception.
  C1. AGENTS.md documents the Frontend behavioural-test mirror rule.
"""
from __future__ import annotations

import os
import re


ENV_LEDGER = "/app/docs/environments.md"
AGENTS_MD  = "/app/AGENTS.md"
PYTEST_INI = "/app/backend/pytest.ini"


def _read(p: str) -> str:
    with open(p, "r", encoding="utf-8") as f:
        return f.read()


# ── Part A ─────────────────────────────────────────────────────────

def test_environments_md_exists_and_has_verified_stamp():
    assert os.path.isfile(ENV_LEDGER), f"missing {ENV_LEDGER}"
    src = _read(ENV_LEDGER)
    assert "Environment Parity Ledger" in src or \
           "environments.md" in src.lower()
    # A `Verified: <date>` (or `_verified_at`) stamp MUST exist —
    # anchors the "no aspirational claims" rule.
    assert re.search(r"[Vv]erified[:\s]", src), (
        "environments.md must state a verification date so drift is "
        "visible"
    )


def test_environments_md_covers_four_required_surfaces():
    """Mongo, env vars, GitHub, services must each be addressed."""
    src = _read(ENV_LEDGER).lower()
    for token in ("mongo", "env var", "github", "services"):
        assert token in src, f"environments.md missing coverage: {token}"


def test_environments_md_flags_the_5_currently_missing_env_vars():
    """The AUREM_ORG_* + AUREM_CANARY_* keys are known-missing on
    preview — this ledger must surface that so it doesn't become
    invisible tribal knowledge."""
    src = _read(ENV_LEDGER)
    for k in ("AUREM_ORG_NAME", "AUREM_ORG_GITHUB_APP_TOKEN",
              "AUREM_CANARY_REPO_OWNER", "AUREM_CANARY_REPO_NAME",
              "AUREM_CANARY_BRANCH"):
        assert k in src, f"environments.md must list missing var {k}"


def test_agents_md_carries_per_env_deploy_report_rule():
    src = _read(AGENTS_MD)
    # The rule uses "live on preview" and "live on production" as its
    # canonical wording; lock that so a paraphrase drift is caught.
    assert "live on preview" in src.lower()
    assert "live on production" in src.lower()
    assert "docs/environments.md" in src, (
        "AGENTS.md must link to the ledger by exact path"
    )


# ── Part B ─────────────────────────────────────────────────────────

def test_pytest_ini_declares_flaky_marker():
    assert os.path.isfile(PYTEST_INI), f"missing {PYTEST_INI}"
    src = _read(PYTEST_INI)
    assert re.search(r"^\s*flaky\s*:", src, re.M), (
        "pytest.ini must declare the `flaky` marker so "
        "`@pytest.mark.flaky` never triggers a PytestUnknownMarkWarning"
    )


def test_pytest_ini_defaults_ci_to_not_flaky():
    src = _read(PYTEST_INI)
    # addopts line must contain `-m "not flaky"` (or equivalent).
    # A merge/deploy MUST NOT be blocked by a quarantined test.
    assert re.search(r'addopts\s*=.*not flaky', src), (
        "pytest.ini addopts must default to `-m \"not flaky\"` "
        "so quarantine is non-blocking"
    )


def test_agents_md_documents_owner_and_fixby_rule():
    src = _read(AGENTS_MD)
    # Both tokens must appear inside the flaky-quarantine section.
    assert "owner" in src and "fix_by" in src, (
        "AGENTS.md must require owner + fix_by on every @flaky test"
    )


def test_agents_md_documents_loop_sse_flakiness_exception():
    src = _read(AGENTS_MD)
    lower = src.lower()
    assert "loop" in lower and "sse" in lower
    # The specific insight — flake in async code often exposes a real
    # intermittent bug — must be captured verbatim enough that a
    # paraphrase drift is caught.
    assert "intermittent bug" in lower, (
        "AGENTS.md must warn that Loop/SSE flakes often expose real "
        "intermittent bugs (Google's own finding on async tests)"
    )


def test_agents_md_documents_quarantine_ceiling():
    src = _read(AGENTS_MD)
    # 5% ceiling per industry data (Google/Slack/Atlassian).
    assert re.search(r"[>≥]?\s*5\s*%", src), (
        "AGENTS.md must state the quarantine ceiling (~5%) "
        "beyond which the flake pattern is systemic"
    )


# ── Part C — frontend behavioural-test rule mirror ─────────────────

def test_agents_md_documents_frontend_behavioural_rule():
    src = _read(AGENTS_MD)
    lower = src.lower()
    # Must reference RTL/render+DOM + must call out that DOM/className
    # grep is NOT a valid test (mirror of iter290's STATIC_GREP finding).
    assert ("react testing library" in lower or "playwright" in lower)
    assert "grep" in lower or "static_grep" in lower.upper() or \
           "STATIC_GREP" in src, (
        "AGENTS.md must mirror the STATIC_GREP lesson to frontend"
    )


# ── Meta — this file MUST NOT drift into permitted STATIC_GREP ─────

def test_this_file_is_intentionally_exempt():
    """Sanity — the exempt marker is present on line 1-10 so
    iter291's CI guard skips this file (locking docs is inherently
    STATIC_GREP; that's fine when declared)."""
    src = _read(__file__)
    head = "\n".join(src.splitlines()[:10])
    assert "static-grep-ok" in head, (
        "this regression file locks doc shapes and is by-design "
        "STATIC_GREP — the exempt marker MUST be present so the "
        "CI guard doesn't false-positive on it"
    )
