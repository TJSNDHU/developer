"""
services/scanner_utils.py — Iter 212m-225

Small helpers shared by both the codebase-health scanner router and
the bug_hunt scanner service.  Kept in `services/` so callers on
either side of the router/service boundary can import from a single
canonical location.

`is_scanner_rule_file(path) -> bool`
    True when `path` is one of AUREM's OWN scanner-rule-definition
    source files (bug_hunt_rules.py, vanguard_scanner.py, etc.).
    Callers use it to skip self-referential false-positives — the
    rule regexes for eval_usage / private_key / etc. literally
    appear inside their own definition files.
"""
from __future__ import annotations

# The tuple below is imported by BOTH the security scanner in
# routers/codebase_health.py AND the bug_hunt scanner in
# services/bug_hunt_rules.py.  Keep it strict — only files that
# unambiguously define scanner rules.
_SCANNER_RULE_FILES: tuple[str, ...] = (
    "services/bug_hunt_rules.py",
    "services/vanguard_scanner.py",
    "services/vanguard_verify_agent.py",
    "services/generation_rules.py",
    "services/mode_e_auditor.py",
    "services/full_scan_scanners.py",
    "routers/codebase_health.py",
    "routers/security_scan.py",
    # Frontend UI that renders rule strings for the /bug-hunt panel:
    "frontend/src/pages/BugHunt.jsx",
)


def is_scanner_rule_file(path: str) -> bool:
    """True if `path` is one of the AUREM scanner rule-definition
    source files. Callers use this to skip self-referential false
    positives that would otherwise flood the report."""
    if not path:
        return False
    lower = path.replace("\\", "/").lower()
    return any(lower.endswith(suf.lower()) for suf in _SCANNER_RULE_FILES)


__all__ = ["is_scanner_rule_file", "_SCANNER_RULE_FILES"]
