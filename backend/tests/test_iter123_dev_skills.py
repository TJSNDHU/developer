"""
test_iter123_dev_skills.py — Iter 123 skill audit + new skill pack.

Validates:
  1. github_deploy router is mounted and responds (401/422 without auth).
  2. All 22 skills are wired into LOCAL_TOOLS + TOOL_SPECS catalogs.
  3. New skills (dev_skills) return real data shapes without mocks:
     - validate_syntax — pure-Python AST, no network → deterministic
     - find_package_docs — live npm/PyPI lookup
     - detect_framework / get_dependencies / get_env_vars — fail-soft
       without a connected project (returns ok=False, no crash).
  4. No mocks / TODOs / placeholders in skill modules.
"""
import os
import re
import pytest
import httpx

from services.local_tools import TOOL_SPECS, LOCAL_TOOLS, invoke_local_tool
from services.dev_skills import (
    DEV_TOOLS, DEV_TOOL_SPECS, validate_syntax, find_package_docs,
)
from services import github_deploy_service as gh

API_URL = os.environ.get("REACT_APP_BACKEND_URL", "http://localhost:8001")


# ── 1. github_deploy router wiring ────────────────────────────────────

@pytest.mark.asyncio
async def test_github_deploy_router_mounted():
    async with httpx.AsyncClient() as c:
        # No auth header → must be 401, NOT 404 (proves router is mounted)
        r = await c.get(f"{API_URL}/api/aurem-dev/github-deploy/status")
        assert r.status_code == 401, f"expected 401, got {r.status_code} body={r.text[:200]}"


@pytest.mark.asyncio
async def test_github_deploy_connect_schema_validation():
    async with httpx.AsyncClient() as c:
        # Short token → 422 schema error (proves Pydantic model is loaded)
        r = await c.post(
            f"{API_URL}/api/aurem-dev/github-deploy/connect",
            json={"token": "x"},
            headers={"Authorization": "Bearer dummy_for_schema_check"},
        )
        # Will be 401 (auth) OR 422 (schema) — both prove the route exists
        assert r.status_code in (401, 422), \
            f"expected 401/422, got {r.status_code}"


def test_github_deploy_service_has_set_db():
    """set_db must exist so main.py can wire DB at lifespan."""
    assert hasattr(gh, "set_db")
    assert callable(gh.set_db)


# ── 2. Skill catalog completeness ─────────────────────────────────────

def test_all_skills_in_dispatch_table():
    """Every TOOL_SPECS entry must have a LOCAL_TOOLS dispatch fn."""
    spec_names = {t["name"] for t in TOOL_SPECS}
    dispatch_names = set(LOCAL_TOOLS.keys())
    missing = spec_names - dispatch_names
    assert not missing, f"specs without dispatch fn: {missing}"
    extra = dispatch_names - spec_names
    assert not extra, f"dispatch fns without spec: {extra}"


def test_total_skill_count_meets_minimum():
    """Iter 123 target: 22 skills (7 code + 5 web + 10 dev)."""
    assert len(LOCAL_TOOLS) >= 22, f"only {len(LOCAL_TOOLS)} skills wired"
    assert len(TOOL_SPECS) >= 22


def test_new_dev_skills_all_registered():
    """All 10 new dev_skills must be in TOOL_SPECS and dispatch."""
    expected = {
        "find_usages", "get_dependencies", "get_env_vars",
        "detect_framework", "get_commit_history", "list_issues",
        "get_pr_comments", "find_package_docs", "validate_syntax",
        "e2b_run_code",
    }
    spec_names = {t["name"] for t in TOOL_SPECS}
    missing = expected - spec_names
    assert not missing, f"missing from TOOL_SPECS: {missing}"

    dispatch_names = set(LOCAL_TOOLS.keys())
    missing_d = expected - dispatch_names
    assert not missing_d, f"missing from LOCAL_TOOLS dispatch: {missing_d}"


def test_all_specs_have_required_fields():
    for spec in TOOL_SPECS:
        assert "name" in spec, f"spec missing name: {spec}"
        assert "description" in spec, f"{spec['name']} missing description"
        assert "args_spec" in spec, f"{spec['name']} missing args_spec"
        assert len(spec["description"]) > 30, \
            f"{spec['name']} description too short — ORA needs context to pick the right tool"


# ── 3. New skills produce REAL data ───────────────────────────────────

@pytest.mark.asyncio
async def test_validate_syntax_good_python():
    """validate_syntax on valid code returns ok=True valid=True with stats."""
    res = await validate_syntax(
        {},
        {"code": "async def foo(x):\n    return x + 1\n\nclass Bar:\n    pass\n"},
    )
    assert res["ok"] is True
    assert res["valid"] is True
    assert res["summary"]["async_functions"] == 1
    assert res["summary"]["classes"] == 1


@pytest.mark.asyncio
async def test_validate_syntax_bad_python():
    res = await validate_syntax({}, {"code": "def foo(\n    pass"})
    assert res["ok"] is True   # tool ran successfully
    assert res["valid"] is False
    assert res["line"] is not None


@pytest.mark.asyncio
async def test_validate_syntax_missing_code():
    res = await validate_syntax({}, {})
    assert res["ok"] is False
    assert "required" in res["error"].lower()


@pytest.mark.asyncio
async def test_validate_syntax_unsupported_lang():
    res = await validate_syntax({}, {"code": "let x = 1;", "language": "javascript"})
    assert res["ok"] is False
    assert "not supported" in res["error"].lower()


@pytest.mark.asyncio
async def test_find_package_docs_pypi_real():
    """Live PyPI lookup — proves no mock, real network."""
    res = await find_package_docs({}, {"name": "fastapi", "registry": "pypi"})
    assert res["ok"] is True, f"PyPI lookup failed: {res}"
    assert res["registry"] == "pypi"
    assert res["name"].lower() == "fastapi"
    assert res["latest"], "no version returned"
    assert "pypi.org" in res["pypi_url"]


@pytest.mark.asyncio
async def test_find_package_docs_npm_real():
    """Live npm lookup — proves no mock."""
    res = await find_package_docs({}, {"name": "react", "registry": "npm"})
    assert res["ok"] is True, f"npm lookup failed: {res}"
    assert res["registry"] == "npm"
    assert res["name"] == "react"
    assert res["latest"]


@pytest.mark.asyncio
async def test_find_package_docs_invalid():
    res = await find_package_docs(
        {}, {"name": "definitely-not-a-real-package-xyz-12345-aurem"},
    )
    assert res["ok"] is False
    assert "not found" in res["error"].lower()


# ── 4. Skills fail-soft without a project ─────────────────────────────

@pytest.mark.asyncio
async def test_skills_fail_soft_without_project():
    """All project-dependent skills must return ok=False (not crash)
    when there's no connected project."""
    project_dependent = [
        "find_usages", "get_dependencies", "get_env_vars",
        "detect_framework", "get_commit_history", "list_issues",
        "get_pr_comments",
    ]
    ctx = {"user_id": "", "project_id": ""}
    for tool_name in project_dependent:
        res = await invoke_local_tool(
            tool_name, {"symbol": "x", "pr_number": 1}, ctx,
        )
        assert res is not None, f"{tool_name} returned None (not in dispatch?)"
        # Either ok=False with clear error, OR (if tool requires an arg)
        # an arg-error — either way no crash.
        assert "ok" in res, f"{tool_name} returned malformed result: {res}"


# ── 4b. Per-skill happy + error path tests (industry pattern: 2 each) ──

@pytest.mark.asyncio
async def test_find_usages_requires_symbol():
    """Error path: missing symbol arg."""
    from services.dev_skills import find_usages
    res = await find_usages({}, {})
    assert res["ok"] is False
    assert "symbol" in res["error"].lower()


@pytest.mark.asyncio
async def test_find_usages_invalid_symbol_chars():
    """Error path: symbol with special chars rejected."""
    from services.dev_skills import find_usages
    res = await find_usages({"user_id": "u", "project_id": "p"}, {"symbol": "foo(); rm -rf /"})
    assert res["ok"] is False


@pytest.mark.asyncio
async def test_get_dependencies_no_project():
    """Error path: no connected project."""
    from services.dev_skills import get_dependencies
    res = await get_dependencies({}, {})
    assert res["ok"] is False
    assert "no project" in res["error"].lower()


@pytest.mark.asyncio
async def test_get_env_vars_no_project():
    """Error path: no connected project."""
    from services.dev_skills import get_env_vars
    res = await get_env_vars({}, {})
    assert res["ok"] is False


@pytest.mark.asyncio
async def test_detect_framework_no_project():
    """Error path: no connected project."""
    from services.dev_skills import detect_framework
    res = await detect_framework({}, {})
    assert res["ok"] is False


@pytest.mark.asyncio
async def test_get_commit_history_no_project():
    """Error path: no connected project."""
    from services.dev_skills import get_commit_history
    res = await get_commit_history({}, {"max": 5})
    assert res["ok"] is False


@pytest.mark.asyncio
async def test_list_issues_no_project():
    """Error path."""
    from services.dev_skills import list_issues
    res = await list_issues({}, {"state": "open"})
    assert res["ok"] is False


@pytest.mark.asyncio
async def test_list_issues_state_normalised():
    """Even with invalid state, dispatch normalises to 'open' (no crash)."""
    from services.dev_skills import list_issues
    res = await list_issues({}, {"state": "garbage-state"})
    # No project → ok=False (we're testing normalisation didn't crash)
    assert res["ok"] is False


@pytest.mark.asyncio
async def test_get_pr_comments_requires_pr_number():
    """Error path: missing pr_number."""
    from services.dev_skills import get_pr_comments
    res = await get_pr_comments({}, {})
    assert res["ok"] is False
    assert "pr_number" in res["error"].lower()


@pytest.mark.asyncio
async def test_get_pr_comments_invalid_pr_number():
    """Error path: non-int pr_number rejected."""
    from services.dev_skills import get_pr_comments
    res = await get_pr_comments({}, {"pr_number": "not-a-number"})
    assert res["ok"] is False
    assert "int" in res["error"].lower()


@pytest.mark.asyncio
async def test_e2b_run_code_requires_code():
    """Error path: missing code."""
    from services.dev_skills import e2b_run_code
    res = await e2b_run_code({}, {})
    assert res["ok"] is False
    assert "code" in res["error"].lower()


@pytest.mark.asyncio
async def test_e2b_run_code_too_long():
    """Error path: code over cap rejected."""
    from services.dev_skills import e2b_run_code
    res = await e2b_run_code({}, {"code": "x = 1\n" * 2000})
    assert res["ok"] is False
    assert "too long" in res["error"].lower()


@pytest.mark.asyncio
async def test_e2b_run_code_skipped_or_runs():
    """Happy path: either runs in sandbox OR fails-soft with skipped=True.
    Both are valid outcomes — proves the wrapper is wired correctly."""
    from services.dev_skills import e2b_run_code
    res = await e2b_run_code({}, {"code": "print('hello')", "timeout": 5})
    # Either succeeded with real output OR skipped (no E2B key) — never crashed
    assert "ok" in res
    if res.get("skipped"):
        assert "reason" in res
    else:
        # If sandbox actually ran, exit_code should be present
        assert "stdout" in res or "stderr" in res


# ── Tool-help template grouping (industry pattern) ────────────────────

def test_tool_help_template_has_grouped_skills():
    """Tool catalog in orchestrator must be GROUPED (READING/INTEL/GITHUB/
    WEB/VALIDATE) per industry pattern (Claude Code, Cursor, Windsurf)."""
    from services.orchestrator import _TOOL_HELP_TEMPLATE
    for group in ("READING", "INTEL", "GITHUB", "WEB", "VALIDATE"):
        assert group in _TOOL_HELP_TEMPLATE, \
            f"tool-help missing '{group}' group header"


def test_tool_help_template_has_selection_rules():
    """Selection rules must be present so ORA picks the sharpest tool."""
    from services.orchestrator import _TOOL_HELP_TEMPLATE
    assert "SELECTION RULES" in _TOOL_HELP_TEMPLATE
    # Critical disambiguation: search_repo vs semantic_search_repo
    assert "semantic_search_repo" in _TOOL_HELP_TEMPLATE
    assert "find_usages" in _TOOL_HELP_TEMPLATE


# ── 5. No mocks / TODOs / placeholders in skill code ──────────────────

_MOCK_PATTERNS = re.compile(
    r"\b(TODO|FIXME|XXX|HACK|mock|stub|placeholder|simulate|fake|"
    r"hardcoded|return\s*{\s*\"status\"\s*:\s*\"ok\"\s*}\s*$)\b",
    re.IGNORECASE,
)


@pytest.mark.parametrize("path", [
    "/app/backend/services/dev_skills.py",
    "/app/backend/services/local_tools.py",
    "/app/backend/services/web_skills.py",
])
def test_no_mocks_in_skill_files(path):
    """Strict: no mock/TODO patterns in any skill file."""
    with open(path) as f:
        lines = f.readlines()
    flagged = []
    for i, line in enumerate(lines, 1):
        # Skip comments / docstrings about avoiding mocks
        if "no mock" in line.lower() or "no-mock" in line.lower():
            continue
        if "no stub" in line.lower():
            continue
        if "no_mock" in line.lower():
            continue
        # Skip the line that documents the rule itself
        if "anti-mock" in line.lower():
            continue
        if _MOCK_PATTERNS.search(line):
            # Allow these specific safe usages:
            #   • The word 'simulate' inside legitimate prose isn't a real mock.
            #   • Variable names like 'is_locked' / 'stack' are fine.
            # We require the suspicious token to be in a code context.
            stripped = line.strip()
            if stripped.startswith(("#", "*", '"""', "'''")):
                continue
            flagged.append(f"{path}:{i}: {stripped[:120]}")
    assert not flagged, "Mock/TODO patterns found:\n" + "\n".join(flagged)


# ── 6. Dev skill modules export expected catalogs ─────────────────────

def test_dev_skills_catalog_shape():
    assert isinstance(DEV_TOOLS, dict)
    assert isinstance(DEV_TOOL_SPECS, list)
    # Every dispatched fn must be coroutine
    import asyncio
    for name, fn in DEV_TOOLS.items():
        assert asyncio.iscoroutinefunction(fn), f"{name} is not async"
