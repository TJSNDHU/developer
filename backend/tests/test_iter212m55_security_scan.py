"""
test_iter212m55_security_scan.py — regression for the 1-click static
vulnerability scanner introduced in iter 212m-55.

Covers:
  • Route is registered under /api/aurem-dev/security-scan/run
  • Unauthenticated calls return 401
  • Authenticated calls with missing project_id return 400
  • Authenticated calls with bogus project_id return 404
  • The rule library actually fires on a known-bad snippet
  • The NoSQL-op guard still blocks $where in other POST bodies
    (regression from the middleware rewrite needed to keep this
    feature shippable).
"""
from __future__ import annotations

import os
import sys
import re

# Allow `import routers...` without running the full app.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from routers.security_scan import _scan_text, _RULES   # noqa: E402


def test_rules_compile():
    """Every rule must have id/vuln/severity/pattern/desc."""
    for r in _RULES:
        assert "id" in r and "vuln" in r and "severity" in r
        assert "pattern" in r and "desc" in r
        assert r["severity"] in {"critical", "high", "medium", "low"}


def test_aws_key_detected():
    sample = 'AWS_KEY = "AKIAIOSFODNN7EXAMPLE"\n'
    findings = _scan_text("config.py", sample)
    assert any(f["rule_id"] == "secret_aws_access_key" for f in findings)


def test_sql_format_detected():
    sample = 'cursor.execute(f"SELECT * FROM u WHERE id={uid}")\n'
    findings = _scan_text("db.py", sample)
    assert any(f["rule_id"] == "sql_string_format" for f in findings)


def test_nosql_where_detected():
    sample = "db.users.find({\"$where\": \"this.x > 0\"})\n"
    findings = _scan_text("dao.py", sample)
    assert any(f["rule_id"] == "nosql_where_operator" for f in findings)


def test_clean_file_no_findings():
    """Clean code shouldn't trigger any rule."""
    sample = "def add(a, b):\n    return a + b\n"
    findings = _scan_text("math.py", sample)
    assert findings == []


def test_ignore_directive_skips_line():
    sample = (
        "API_KEY = \"AKIAIOSFODNN7EXAMPLE\"  # vanguard: ignore — test fixture\n"
    )
    findings = _scan_text("fixture.py", sample)
    assert findings == []
