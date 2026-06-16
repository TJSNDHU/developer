"""Iter 167 — post-task scanner unit tests.

Pure unit tests — no network, no DB. Covers:
  • secret regex catches known-bad lines
  • placeholder markers prevent false positives
  • test/mock files are skipped
  • Python + JS import scanners flag obvious red flags
  • MAX_ISSUES cap is honoured
  • clean code returns []
"""
import pytest

from services.post_task_scanner import (
    MAX_ISSUES,
    scan_changed_files,
    _scan_secrets,
    _scan_python_imports,
    _scan_js_imports,
)


def test_secret_regex_catches_openai_key():
    bad = 'api_key = "sk-' + "A" * 40 + '"'
    issues = _scan_secrets(bad, "backend/services/foo.py")
    assert len(issues) >= 1
    assert issues[0]["severity"] == "HIGH"
    assert issues[0]["type"] == "security"


def test_secret_regex_catches_aws_key():
    bad = 'AWS_KEY = "AKIA' + "A" * 16 + '"'
    issues = _scan_secrets(bad, "config.py")
    assert any("AWS" in i["message"] for i in issues)


def test_placeholder_secrets_ignored():
    bad = 'api_key = "your_openai_key_here_xxx"'
    assert _scan_secrets(bad, "config.py") == []


def test_test_files_skipped():
    bad = 'api_key = "sk-' + "A" * 40 + '"'
    assert _scan_secrets(bad, "tests/test_config.py") == []
    assert _scan_secrets(bad, "src/__mocks__/foo.py") == []


def test_comment_lines_skipped():
    bad = '# api_key = "sk-' + "A" * 40 + '"'
    assert _scan_secrets(bad, "config.py") == []


def test_python_import_scanner_flags_undefined():
    bad = "from undefined_thing import x"
    issues = _scan_python_imports(bad, "foo.py")
    assert len(issues) == 1
    assert issues[0]["type"] == "import"


def test_python_import_scanner_ignores_stdlib():
    src = "from os import path\nimport sys\nfrom fastapi import APIRouter"
    assert _scan_python_imports(src, "foo.py") == []


def test_js_import_scanner_flags_deep_relative():
    bad = 'import x from "../../../../../foo";'
    issues = _scan_js_imports(bad, "foo.jsx")
    assert len(issues) == 1
    assert issues[0]["type"] == "import"


def test_js_import_scanner_skips_bare_modules():
    src = 'import React from "react";\nimport { api } from "../lib/api";'
    assert _scan_js_imports(src, "foo.jsx") == []


@pytest.mark.asyncio
async def test_scan_changed_files_clean_code():
    contents = {
        "backend/services/clean.py": "from os import path\n# all good\n",
        "frontend/src/Foo.jsx": 'import React from "react";\n',
    }
    issues = await scan_changed_files(list(contents.keys()), contents)
    assert issues == []


@pytest.mark.asyncio
async def test_scan_changed_files_max_3():
    # Five files, each with a real secret — scanner must cap at MAX_ISSUES.
    bad = 'api_key = "sk-' + "A" * 40 + '"'
    contents = {f"backend/svc{i}.py": bad for i in range(5)}
    issues = await scan_changed_files(list(contents.keys()), contents)
    assert len(issues) == MAX_ISSUES == 3
    assert all(i["severity"] == "HIGH" for i in issues)


@pytest.mark.asyncio
async def test_scan_changed_files_sorts_by_severity():
    """HIGH security issue must appear before MEDIUM import issue."""
    contents = {
        "frontend/src/Foo.jsx": 'import x from "../../../../../foo";',  # MEDIUM
        "backend/svc.py":       'api_key = "sk-' + "A" * 40 + '"',  # HIGH
    }
    issues = await scan_changed_files(list(contents.keys()), contents)
    assert issues, "expected at least one issue"
    assert issues[0]["severity"] == "HIGH"


@pytest.mark.asyncio
async def test_scan_empty_files_ignored():
    contents = {"foo.py": "", "bar.jsx": ""}
    issues = await scan_changed_files(list(contents.keys()), contents)
    assert issues == []
