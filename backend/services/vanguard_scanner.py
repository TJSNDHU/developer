"""
services/vanguard_scanner.py
============================

Iter 44 — pulls in 007's battle-tested secret + dangerous-code regex
catalog from the Vanguard skill collection. Pure stdlib regex — no
external dependencies, no LLM cost.

Used by:
  - services/design_linter.py    → blocks commits with critical secrets
  - services/mode_e_auditor.py   → static-scan pass during repo audit

We embed the regex defs inline (rather than importing the original 007
config.py from a non-package path) so the scanner stays self-contained
and ships with the AUREM repo. Source: 007 v1.0 — Antigravity Awesome
Skills, May 2026.
"""
from __future__ import annotations
import re
from typing import Iterable


# ─── Secret patterns (Vanguard 007 catalog) ─────────────────────────────
_SECRET_PATTERN_DEFS = [
    ("generic_api_key",
     r"""(?i)(?:api[_-]?key|apikey|api[_-]?secret|api[_-]?token)\s*[:=]\s*['\"]\S{8,}['\"]""",
     "CRITICAL"),
    ("aws_access_key",
     r"""(?:A3T[A-Z0-9]|AKIA|AGPA|AIDA|AROA|AIPA|ANPA|ANVA|ASIA)[A-Z0-9]{16}""",
     "CRITICAL"),
    ("aws_secret_key",
     r"""(?i)aws[_-]?secret[_-]?access[_-]?key\s*[:=]\s*['\"]\S{40}['\"]""",
     "CRITICAL"),
    ("password_assignment",
     r"""(?i)(?:password|passwd|pwd|senha)\s*[:=]\s*['\"][^'\"]{4,}['\"]""",
     "CRITICAL"),
    ("token_assignment",
     r"""(?i)(?:token|bearer|auth[_-]?token|access[_-]?token|refresh[_-]?token)\s*[:=]\s*['\"][^'\"]{8,}['\"]""",
     "CRITICAL"),
    ("private_key",
     r"""-----BEGIN\s+(?:RSA|DSA|EC|OPENSSH|PGP)?\s*PRIVATE\s+KEY-----""",
     "CRITICAL"),
    ("github_token",
     r"""(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{36,}""",
     "CRITICAL"),
    ("slack_token",
     r"""xox[bpors]-[0-9]{10,}-[A-Za-z0-9-]+""",
     "CRITICAL"),
    ("generic_secret",
     r"""(?i)(?:secret|client[_-]?secret|signing[_-]?key|encryption[_-]?key)\s*[:=]\s*['\"][^'\"]{8,}['\"]""",
     "HIGH"),
    ("db_connection_string",
     r"""(?i)(?:mysql|postgres|postgresql|mongodb|redis|amqp):\/\/[^:]+:[^@]+@""",
     "CRITICAL"),
    ("stripe_live_key",
     r"""(?:sk|pk|rk)_live_[A-Za-z0-9]{20,}""",
     "CRITICAL"),
    ("stripe_test_key",
     r"""(?:sk|pk|rk)_test_[A-Za-z0-9]{20,}""",
     "MEDIUM"),
    ("google_api_key",
     r"""AIza[0-9A-Za-z_-]{35}""",
     "CRITICAL"),
    ("sendgrid_key",
     r"""SG\.[A-Za-z0-9_-]{22}\.[A-Za-z0-9_-]{43}""",
     "CRITICAL"),
    ("openai_key",
     r"""sk-[A-Za-z0-9]{20,}""",
     "CRITICAL"),
]

SECRET_PATTERNS = [
    (name, re.compile(p), sev) for name, p, sev in _SECRET_PATTERN_DEFS
]


# ─── Dangerous code patterns ────────────────────────────────────────────
_DANGEROUS_PATTERN_DEFS = [
    ("eval_usage",            r"""\beval\s*\(""",                                       "CRITICAL"),
    ("exec_usage",            r"""\bexec\s*\(""",                                       "CRITICAL"),
    ("subprocess_shell_true", r"""subprocess\.\w+\(.*shell\s*=\s*True""",               "CRITICAL"),
    ("os_system",             r"""\bos\.system\s*\(""",                                 "HIGH"),
    ("pickle_loads",          r"""\bpickle\.loads?\s*\(""",                             "HIGH"),
    ("yaml_unsafe_load",      r"""\byaml\.load\s*\((?!.*Loader\s*=)""",                 "HIGH"),
    ("requests_no_verify",    r"""verify\s*=\s*False""",                                "HIGH"),
    ("sql_string_format",     r"""(?i)(?:execute|cursor\.execute)\s*\(\s*[f'\"]+.*\{""", "CRITICAL"),
    ("innerHTML_assignment",  r"""\.innerHTML\s*=""",                                   "HIGH"),
    ("dangerously_set_html",  r"""dangerouslySetInnerHTML""",                           "HIGH"),
]

DANGEROUS_PATTERNS = [
    (name, re.compile(p), sev) for name, p, sev in _DANGEROUS_PATTERN_DEFS
]


# ─── Public API ─────────────────────────────────────────────────────────
def scan_text(
    text: str,
    filepath: str = "",
    *,
    include_dangerous: bool = True,
) -> list[dict]:
    """Returns list of findings: {name, severity, line, snippet, source}."""
    if not text:
        return []
    findings: list[dict] = []
    lines = text.split("\n")
    for name, pattern, severity in SECRET_PATTERNS:
        for i, line in enumerate(lines, 1):
            if pattern.search(line):
                findings.append({
                    "name": name,
                    "severity": severity,
                    "filepath": filepath,
                    "line": i,
                    "snippet": line.strip()[:120],
                    "source": "vanguard_007_secrets",
                })
                break
    if include_dangerous:
        for name, pattern, severity in DANGEROUS_PATTERNS:
            for i, line in enumerate(lines, 1):
                if pattern.search(line):
                    findings.append({
                        "name": name,
                        "severity": severity,
                        "filepath": filepath,
                        "line": i,
                        "snippet": line.strip()[:120],
                        "source": "vanguard_007_dangerous",
                    })
                    break
    return findings


def scan_file_blocks(blocks: dict[str, str]) -> list[dict]:
    out: list[dict] = []
    for path, content in (blocks or {}).items():
        out.extend(scan_text(content, filepath=path))
    return out


def has_critical(findings: Iterable[dict]) -> bool:
    return any(f.get("severity") == "CRITICAL" for f in findings)
