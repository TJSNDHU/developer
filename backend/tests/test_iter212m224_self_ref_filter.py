"""
Iter 212m-224 — Self-referential false-positive filter.

Our scanner-rule definition files (bug_hunt_rules.py, vanguard_scanner.py,
etc.) literally contain the very regex/string patterns they detect.
When _scan_security or scan_bug_hunt walks its own source, it flags
those pattern strings as vulnerabilities — but they're definitions,
not exploitable code.

The self-scan report (test_reports/self_scan.md) showed 13/23 CRITICAL
findings were exactly this false-positive class. This suite locks the
filter so a future refactor cannot silently drop it.
"""

from __future__ import annotations


def test_is_scanner_rule_file_matches_definition_files():
    from routers.codebase_health import _is_scanner_rule_file
    # Every listed scanner-rule source MUST match.
    for p in [
        "backend/services/bug_hunt_rules.py",
        "backend/services/vanguard_scanner.py",
        "backend/services/generation_rules.py",
        "backend/services/mode_e_auditor.py",
        "backend/services/full_scan_scanners.py",
        "backend/routers/codebase_health.py",
        "backend/routers/security_scan.py",
        "frontend/src/pages/BugHunt.jsx",
    ]:
        assert _is_scanner_rule_file(p) is True, p


def test_is_scanner_rule_file_does_not_match_regular_source():
    from routers.codebase_health import _is_scanner_rule_file
    # Real code files MUST NOT match (else we'd hide real bugs).
    for p in [
        "backend/services/loop_engine.py",
        "backend/routers/auth.py",
        "backend/main.py",
        "frontend/src/pages/Dashboard.jsx",
        "frontend/src/components/ChatPanel.jsx",
        "README.md",
        "",
    ]:
        assert _is_scanner_rule_file(p) is False, p


def test_security_scanner_skips_own_rule_files():
    """Feed the security scanner a text_cache containing a scanner-
    definition file with obvious eval() / exec() strings inside; it
    MUST return zero findings for that path."""
    from routers.codebase_health import _scan_security
    poisoned = {
        "backend/services/bug_hunt_rules.py":
            'RULES = [("eval_usage", r"\\beval\\s*\\("),\n'
            '         ("exec_usage", r"\\bexec\\s*\\(")]\n',
        # Same content in a NORMAL file — this one SHOULD be flagged.
        "backend/services/some_real_module.py":
            "def do_thing():\n    return eval(user_input)\n",
    }
    findings = _scan_security(poisoned)
    paths_flagged = {f.get("file") for f in findings}
    assert "backend/services/bug_hunt_rules.py" not in paths_flagged, (
        "scanner-rule file must be excluded from _scan_security output"
    )
    assert "backend/services/some_real_module.py" in paths_flagged, (
        "real code with eval() must still be flagged — filter is over-broad"
    )


def test_bug_hunt_scanner_skips_own_rule_files():
    from services.bug_hunt_rules import scan_bug_hunt
    poisoned = {
        "backend/services/vanguard_scanner.py":
            'PATTERN_PASSWORD = re.compile(r\'password\\s*=\\s*"[^"]+"\')\n',
        "backend/scripts/setup.py":
            'PASSWORD = "hunter2"\n',
    }
    findings = scan_bug_hunt(poisoned)
    paths_flagged = {f.get("file") for f in findings}
    assert "backend/services/vanguard_scanner.py" not in paths_flagged
    # (setup.py may or may not trigger depending on rule; the point is
    # that filter didn't nuke non-scanner files. Assert filter runs
    # only against the definition file.)
