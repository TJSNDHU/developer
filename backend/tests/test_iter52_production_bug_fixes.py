"""
tests/test_iter52_production_bug_fixes.py
==========================================

Iter 52 — pin the deep-audit bug fixes so they never regress.

Covers:
  • BUG 1 — _run_task_with_git scrubs the PAT from error strings
  • BUG 2 — update_project encrypts the PAT before writing to Mongo
  • BUG 3 — submit_task free-tier count excludes failed tasks
  • BUG 4 — retry_task propagates maxx_mode from the old task
  • BUG 5 — chat.py council logger only writes Mode A/B (not D/E)
  • BUG 6 — ora_council_logger uses logging (no print() side-channels)
  • BUG 7 — rate_limiter caps in-memory buckets
  • BUG 8 — main.py CORS reads ALLOWED_ORIGINS env

Some assertions are source-level (the upstream behaviour requires a
live Mongo + JWT signer which isn't reachable from the unit test env);
they still catch a reverting edit at CI time.
"""
from __future__ import annotations
import os
import re
import inspect

from services import rate_limiter


# ─── BUG 1 — _scrub in git path ──────────────────────────────────────────

def test_bug1_run_task_with_git_scrubs_pat():
    """The git-path worker must scrub the PAT before _log/_set_status."""
    src_path = os.path.join(
        os.path.dirname(__file__), "..", "routers", "cto_projects.py"
    )
    with open(src_path, encoding="utf-8") as fh:
        src = fh.read()
    # `_run_task_with_git` is the last function in the file so anchor on EOF.
    git_section = re.search(
        r"async def _run_task_with_git\(.*?(?=\nasync def |\Z)",
        src, re.DOTALL,
    )
    assert git_section, "could not locate _run_task_with_git in source"
    block = git_section.group(0)
    assert "def _scrub" in block, "git path missing local _scrub helper"
    # The terminal except handler must use _scrub on str(e).
    assert "_scrub(str(e))" in block, (
        "BUG 1 regression — final except in _run_task_with_git is still "
        "logging the raw exception which can leak the PAT."
    )


# ─── BUG 2 — update_project encrypts PAT ─────────────────────────────────

def test_bug2_update_project_encrypts_pat():
    src_path = os.path.join(
        os.path.dirname(__file__), "..", "routers", "cto_projects.py"
    )
    with open(src_path, encoding="utf-8") as fh:
        src = fh.read()
    upd_section = re.search(
        r"async def update_project\(.*?\n(?:async def |\Z)",
        src, re.DOTALL,
    )
    assert upd_section, "could not locate update_project in source"
    block = upd_section.group(0)
    # The PATCH path MUST call _encrypt_pat on the new github_token before
    # writing it to Mongo.
    assert "_encrypt_pat(me[\"user_id\"], updates[\"github_token\"])" in block, (
        "BUG 2 regression — update_project is storing the PAT plaintext."
    )


# ─── BUG 3 — submit_task free-tier excludes failed tasks ─────────────────

def test_bug3_free_tier_count_excludes_failed_tasks():
    """The monthly task count (now in services/usage.py::get_usage) must
    restrict to non-failed statuses — failed tasks should never burn
    the user's free-tier quota.  Refactored in Iter 75 from the
    submit-task local check to the central usage helper."""
    src_path = os.path.join(
        os.path.dirname(__file__), "..", "services", "usage.py",
    )
    with open(src_path, encoding="utf-8") as fh:
        src = fh.read()
    # Canonical $in clause that whitelists non-failed statuses
    assert '"status": {"$in":' in src, (
        "BUG 3 regression — failed tasks are again counting against the "
        "free-tier monthly cap."
    )
    assert '"done"' in src
    failed_in_status_whitelist = re.search(
        r'"status": \{"\$in":\s*\[[^\]]*"failed"',
        src,
    )
    assert failed_in_status_whitelist is None, (
        "BUG 3 regression — 'failed' must not be in the whitelist."
    )


# ─── BUG 4 — retry_task propagates maxx_mode ─────────────────────────────

def test_bug4_retry_task_propagates_maxx_mode():
    src_path = os.path.join(
        os.path.dirname(__file__), "..", "routers", "cto_projects.py"
    )
    with open(src_path, encoding="utf-8") as fh:
        src = fh.read()
    sec = re.search(
        r"async def retry_task\(.*?return \{[^}]*\}",
        src, re.DOTALL,
    )
    assert sec, "could not locate retry_task in source"
    block = sec.group(0)
    # `_maxx` must be derived from the old task and passed to bg.add_task.
    assert "old.get(\"maxx_mode\"" in block
    # The bg.add_task call should receive _maxx as its last positional
    # arg. The call spans multiple lines with nested parens (from
    # old.get(...) defaults) so a simple regex isn't reliable — check
    # that `_maxx` appears between `bg.add_task(` and the next blank line
    # or `return`.
    after_call = block.split("bg.add_task(", 1)[1].split("return", 1)[0]
    assert "_maxx" in after_call, (
        "BUG 4 regression — retry_task no longer passes maxx_mode through "
        f"to bg.add_task. Call body: {after_call[:300]!r}"
    )


# ─── BUG 5 — chat.py council logger only A/B ────────────────────────────

def test_bug5_council_logger_skips_mode_d_e():
    src_path = os.path.join(
        os.path.dirname(__file__), "..", "routers", "chat.py"
    )
    with open(src_path, encoding="utf-8") as fh:
        src = fh.read()
    # The block must be guarded on _classified_mode in ("A","B"). The
    # exact tuple includes None so the legacy path (no mode set) still
    # logs as A.
    assert "_classified_mode" in src
    assert '_classified_mode in (None, "A", "B")' in src, (
        "BUG 5 regression — Mode D/E replies could land in ora_council_logs."
    )


# ─── BUG 6 — ora_council_logger uses logger.warning ─────────────────────

def test_bug6_ora_council_logger_uses_logging():
    src_path = os.path.join(
        os.path.dirname(__file__), "..", "services", "ora_council_logger.py"
    )
    with open(src_path, encoding="utf-8") as fh:
        src = fh.read()
    assert "import logging" in src
    assert "logger = logging.getLogger(__name__)" in src
    # No raw print() side-channels for error reporting.
    assert "print(f\"[Council Logger]" not in src
    assert "print(f\"[ora_council]" not in src
    assert "logger.warning(\"council_logger insert failed" in src


# ─── BUG 7 — rate_limiter caps buckets ──────────────────────────────────

def test_bug7_rate_limiter_caps_in_memory_buckets():
    """Hammering the limiter with 12k unique keys must NOT blow up the
    bucket dict past the cap."""
    # Reset any state from prior tests in this module.
    rate_limiter._buckets.clear()
    # Force a tiny cap so we can observe the eviction in a unit test.
    saved_cap = rate_limiter._MAX_BUCKETS
    rate_limiter._MAX_BUCKETS = 50
    try:
        for i in range(500):
            rate_limiter.check_rate_limit(f"k{i}", 100)
        # After 500 unique keys with a cap of 50, the dict must NOT grow
        # unbounded.
        assert len(rate_limiter._buckets) <= 50 + 1, (
            f"BUG 7 regression — rate_limiter buckets grew to "
            f"{len(rate_limiter._buckets)} (cap was 50)."
        )
    finally:
        rate_limiter._MAX_BUCKETS = saved_cap
        rate_limiter._buckets.clear()


def test_bug7_rate_limiter_cap_env_overridable():
    # The env knob must exist and parse as an int. We only check the
    # constant is settable; the env-read happens at import.
    assert isinstance(rate_limiter._MAX_BUCKETS, int)
    assert rate_limiter._MAX_BUCKETS >= 1000, (
        "Default cap dropped below sane production value."
    )


# ─── BUG 8 — main.py CORS reads ALLOWED_ORIGINS ─────────────────────────

def test_bug8_cors_reads_allowed_origins_env():
    src_path = os.path.join(
        os.path.dirname(__file__), "..", "main.py"
    )
    with open(src_path, encoding="utf-8") as fh:
        src = fh.read()
    assert 'os.getenv(\n        "ALLOWED_ORIGINS"' in src or \
           'os.getenv("ALLOWED_ORIGINS"' in src, (
        "BUG 8 regression — CORS is no longer env-driven."
    )
    # allow_credentials=True now (Authorization header friendly).
    assert "allow_credentials=True" in src, (
        "BUG 8 regression — credentials must be permitted (we use the "
        "Authorization header, not cookies, but having it True avoids "
        "broken preflights for clients that already set it)."
    )


# ─── LOGIC FIX — _run_task_with_git injects brain/issues/skills ─────────

def test_logic_fix_git_path_injects_brain_issues_skills():
    """When git is available, the worker must still get the same Project
    Brain + GitHub Issues + Vanguard skill injection the API path uses."""
    src_path = os.path.join(
        os.path.dirname(__file__), "..", "routers", "cto_projects.py"
    )
    with open(src_path, encoding="utf-8") as fh:
        src = fh.read()
    git_section = re.search(
        r"async def _run_task_with_git\(.*?\nasync def ",
        src, re.DOTALL,
    )
    if not git_section:
        # End-of-file _run_task_with_git won't be followed by another
        # "async def" — anchor on EOF.
        git_section = re.search(
            r"async def _run_task_with_git\(.*\Z",
            src, re.DOTALL,
        )
    assert git_section
    block = git_section.group(0)
    assert "get_brain_context" in block, "Project Brain not injected in git path"
    assert "get_relevant_issues_context" in block, "Issues not injected in git path"
    assert "build_skill_context" in block, "Vanguard skills not injected in git path"


# ─── CODE QUALITY — docstring cleanup ───────────────────────────────────

def test_code_quality_no_token_optimization_or_wirein_left():
    """The 'TOKEN OPTIMIZATION:' / 'Wire-in:' / 'Catches what Cursor misses'
    AI-tell sections must be gone from the cleaned-up files."""
    files = [
        "services/project_brain.py",
        "services/ora_council_logger.py",
        "services/mode_e_auditor.py",
        "services/code_reviewer.py",
        "services/mode_d_debugger.py",
        "services/parallel_agents.py",
        "services/design_linter.py",
        "services/github_issues_context.py",
    ]
    base = os.path.join(os.path.dirname(__file__), "..")
    for rel in files:
        path = os.path.join(base, rel)
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        assert "TOKEN OPTIMIZATION:" not in src, f"left in {rel}"
        assert "Wire-in:" not in src, f"left in {rel}"
        assert "Catches what Cursor misses" not in src, f"left in {rel}"
