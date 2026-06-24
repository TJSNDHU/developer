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
     r"""(?i)(?:bearer|auth[_-]?token|access[_-]?token|refresh[_-]?token)\s*[:=]\s*['\"][^'\"]{16,}['\"]""",
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
     r"""(?i)(?<![_a-z])(?:secret|signing[_-]?key|encryption[_-]?key)\s*[:=]\s*['\"][^'\"]{16,}['\"]""",
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
     r"""sk-(?!aurem[-_])(?!test[-_])[A-Za-z0-9]{20,}""",
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
    ("requests_no_verify",    r"""(?:requests|httpx|urllib)\b.*\bverify\s*=\s*False""",          "HIGH"),
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

    # Python AST check — catches the syntax errors grep cannot find.
    # Treated as a CRITICAL finding so the pre-push gate blocks the commit.
    if filepath.endswith(".py"):
        try:
            import ast as _ast
            _ast.parse(text)
        except SyntaxError as _se:
            line_no = _se.lineno or 1
            lines = text.split("\n")
            snippet = (lines[line_no - 1] if 0 < line_no <= len(lines) else "").strip()[:120]
            findings.append({
                "name": "python_syntax_error",
                "rule": "python_syntax_error",
                "severity": "CRITICAL",
                "filepath": filepath,
                "line": line_no,
                "snippet": snippet,
                "message": f"SyntaxError: {_se.msg}",
                "source": "ast",
            })

    lines = text.split("\n")
    # Iter 212m-11 — per-line suppression. Any line carrying a
    # `# vanguard: ignore` / `// vanguard: ignore` marker is skipped
    # entirely by both the secret and dangerous-pattern sweeps so
    # developers can opt-out individual false-positives (e.g. a
    # placeholder demo creds line in production code) without having
    # to whitelist the whole file path.
    _SUPPRESS_MARKER = "vanguard: ignore"
    for name, pattern, severity in SECRET_PATTERNS:
        for i, line in enumerate(lines, 1):
            if _SUPPRESS_MARKER in line:
                continue
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
                if _SUPPRESS_MARKER in line:
                    continue
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
        findings = scan_text(content, filepath=path)
        # Iter 212m-6 — file-pattern whitelist for false-positive scope.
        # Doc / template / example files legitimately contain placeholder
        # "secrets" (e.g. `password: "changeme"` in .env.example, demo
        # tokens in tests). Downgrade CRITICAL → INFO on these paths so
        # the commit isn't blocked, but the finding still surfaces in
        # the report for review.
        if _is_safe_demo_path(path):
            for f in findings:
                if f.get("severity") in ("CRITICAL", "HIGH"):
                    f["severity"]     = "INFO"
                    f["downgraded"]   = True
                    f["downgrade_reason"] = "demo/test/example file"
        out.extend(findings)
    return out


# Iter 212m-6 — path-pattern whitelist for vanguard severity downgrade.
# Strings (not regex) for fast `in`-based matching — these are paths
# where placeholder credentials are EXPECTED and a commit blocker
# would create user frustration.
_SAFE_DEMO_PATH_TOKENS: tuple[str, ...] = (
    ".env.example",
    ".env.template",
    ".env.sample",
    ".env.dist",
    "/tests/",
    "/test/",
    "/__tests__/",
    "/spec/",
    "/fixtures/",
    "/mocks/",
    "/.github/",
    "/docs/",
    "/documentation/",
    "readme.md",
    "changelog.md",
    "contributing.md",
    "/examples/",
    "/sample/",
    "/samples/",
    ".storybook",
)
_SAFE_DEMO_NAME_SUFFIXES: tuple[str, ...] = (
    "_test.py", "_test.js", "_test.ts",
    ".test.js", ".test.ts", ".test.jsx", ".test.tsx",
    ".spec.js", ".spec.ts", ".spec.jsx", ".spec.tsx",
    ".stories.js", ".stories.ts", ".stories.jsx", ".stories.tsx",
)


def _is_safe_demo_path(path: str) -> bool:
    """Return True if `path` is a docs / test / example file where
    placeholder credentials are acceptable."""
    if not path:
        return False
    p = path.lower()
    if p.endswith(_SAFE_DEMO_NAME_SUFFIXES):
        return True
    if any(tok in p for tok in _SAFE_DEMO_PATH_TOKENS):
        return True
    # Top-level matches: `tests/foo.py`, `docs/foo.md`, etc. need a
    # second prefix sweep since the `/<dir>/` tokens above only catch
    # nested cases.
    for prefix in (
        "tests/", "test/", "__tests__/", "spec/", "fixtures/",
        "mocks/", "docs/", "documentation/", "examples/",
        "sample/", "samples/",
    ):
        if p.startswith(prefix):
            return True
    return False


def has_critical(findings: Iterable[dict]) -> bool:
    return any(f.get("severity") == "CRITICAL" for f in findings)
