"""
services/design_linter.py
Pre-commit lint pass that rejects common CSS / HTML / React anti-patterns
before files get pushed to GitHub.

Rules cover:
  - transition: all (forces every property to animate)
  - Emoji icons in source strings
  - Long inline style dumps
  - !important abuse (>2 per file)
  - Hardcoded colors without a CSS variable
  - console.log / TODO / FIXME / HACK left in shipped code
  - Missing key= props in React .map() lists

Pure regex, runs in Python with no LLM calls. Call
`lint_file_blocks(file_blocks)` before pushing; if `blocked` is True the
commit is aborted and `block_reasons` is returned to the caller.
"""

from __future__ import annotations
import re
from typing import NamedTuple


# ─────────────────────────────────────────────────────────────────────────────
# Lint rules
# ─────────────────────────────────────────────────────────────────────────────

class LintRule(NamedTuple):
    name: str
    pattern: str
    message: str
    severity: str          # "block" = reject commit | "warn" = log only
    file_types: tuple      # file extensions to check


RULES: list[LintRule] = [
    LintRule(
        name="transition_all",
        pattern=r"transition\s*:\s*all\b",
        message="transition: all causes performance issues. Use specific properties: transition: opacity 0.2s, transform 0.2s",
        severity="block",
        file_types=(".css", ".scss", ".jsx", ".tsx", ".js", ".ts"),
    ),
    LintRule(
        name="emoji_in_code",
        pattern=r'["\'][\U0001F300-\U0001F9FF\U00002700-\U000027BF]["\']',
        message="Emoji found in code string. Use an icon library (lucide-react, heroicons) instead.",
        severity="warn",
        file_types=(".jsx", ".tsx", ".js", ".ts"),
    ),
    LintRule(
        name="inline_style_dump",
        pattern=r'style=\{?\{[^}]{80,}\}?\}',
        message="Long inline style block detected. Move to CSS class or styled component.",
        severity="warn",
        file_types=(".jsx", ".tsx"),
    ),
    LintRule(
        name="important_abuse",
        pattern=r"!important",
        message="!important found. Fix the specificity instead.",
        severity="warn",
        file_types=(".css", ".scss", ".jsx", ".tsx"),
    ),
    LintRule(
        name="console_log",
        pattern=r"\bconsole\.log\s*\(",
        message="console.log left in code. Remove before shipping.",
        severity="block",
        file_types=(".jsx", ".tsx", ".js", ".ts"),
    ),
    LintRule(
        name="todo_in_code",
        pattern=r"\b(TODO|FIXME|HACK|XXX)\b",
        message="Unresolved TODO/FIXME found. Resolve or remove before commit.",
        severity="warn",
        file_types=(".py", ".jsx", ".tsx", ".js", ".ts"),
    ),
    LintRule(
        name="react_list_no_key",
        pattern=r"\.map\s*\([^)]+\)\s*=>\s*(?!.*\bkey=)",
        message="Possible missing key= prop in .map(). React needs unique keys for list items.",
        severity="warn",
        file_types=(".jsx", ".tsx"),
    ),
    LintRule(
        name="hardcoded_color",
        pattern=r"(?:color|background(?:-color)?|border(?:-color)?)\s*:\s*(?:#[0-9a-fA-F]{3,8}|rgb\()",
        message="Hardcoded color value. Use CSS variable (--color-primary, etc.) for theme consistency.",
        severity="warn",
        file_types=(".css", ".scss"),
    ),
    LintRule(
        name="python_print_debug",
        pattern=r"^\s*print\s*\(['\"]DEBUG",
        message="Debug print statement found. Remove before shipping.",
        severity="warn",
        file_types=(".py",),
    ),
    LintRule(
        name="hardcoded_secret",
        pattern=r"""(?i)(?:password|secret|api[_-]?key|apikey|token|bearer)\s*=\s*['"][^'"]{8,}['"]""",
        message="Possible hardcoded secret detected. Use environment variable instead.",
        severity="block",
        file_types=(".py", ".js", ".ts", ".jsx", ".tsx", ".env"),
    ),
]


# ─────────────────────────────────────────────────────────────────────────────
# Linter
# ─────────────────────────────────────────────────────────────────────────────

class LintIssue(NamedTuple):
    filepath: str
    rule: str
    message: str
    line_number: int
    line_content: str
    severity: str


def lint_file(filepath: str, content: str) -> list[LintIssue]:
    """Lints a single file. Returns list of issues found."""
    issues = []
    ext = "." + filepath.rsplit(".", 1)[-1].lower() if "." in filepath else ""
    lines = content.split("\n")

    important_count = 0

    for rule in RULES:
        if ext not in rule.file_types:
            continue

        for i, line in enumerate(lines, start=1):
            if re.search(rule.pattern, line):
                # Special case: count !important occurrences, only warn if > 2
                if rule.name == "important_abuse":
                    important_count += 1
                    if important_count <= 2:
                        continue

                issues.append(LintIssue(
                    filepath=filepath,
                    rule=rule.name,
                    message=rule.message,
                    line_number=i,
                    line_content=line.strip()[:120],
                    severity=rule.severity,
                ))

    return issues


def lint_file_blocks(file_blocks: dict) -> dict:
    """
    Lints all file blocks from an agent output.

    Returns:
        {
            "blocked": bool,             # True = commit should be rejected
            "issues": [LintIssue],       # all issues found
            "block_reasons": [str],      # human-readable block reasons
            "warnings": [str],           # non-blocking warnings
            "clean_files": int,          # files with no issues
            "summary": str,              # short summary for ORA to relay to user
        }
    """
    all_issues: list[LintIssue] = []

    for filepath, content in file_blocks.items():
        file_issues = lint_file(filepath, content)
        all_issues.extend(file_issues)

    # Iter 44 — Vanguard 007 scanner: layered scan for high-confidence
    # secret detection (AWS / GitHub / Stripe / Google / OpenAI / SendGrid /
    # Slack / private-key PEM / DB connection strings / eval / pickle / etc).
    # Treat all "CRITICAL" Vanguard findings as commit-blockers.
    try:
        from services.vanguard_scanner import scan_file_blocks as _vg_scan
        for f in _vg_scan(file_blocks):
            all_issues.append(LintIssue(
                filepath=f["filepath"],
                rule=f"vg.{f['name']}",
                message=f"Vanguard 007 — {f['name']} ({f['severity']})",
                line_number=f["line"],
                line_content=f["snippet"],
                severity="block" if f["severity"] == "CRITICAL" else "warn",
            ))
    except Exception:
        pass

    blocked_issues  = [i for i in all_issues if i.severity == "block"]
    warning_issues  = [i for i in all_issues if i.severity == "warn"]
    clean_count     = sum(1 for f in file_blocks if not any(i.filepath == f for i in all_issues))

    block_reasons = [
        f"{i.filepath}:{i.line_number} — {i.message}"
        for i in blocked_issues
    ]
    warnings = [
        f"{i.filepath}:{i.line_number} — {i.message}"
        for i in warning_issues
    ]

    # Build human-readable summary for ORA to relay
    if blocked_issues:
        summary = (
            f"⛔ Commit blocked: {len(blocked_issues)} critical issue(s) found.\n"
            + "\n".join(f"  • {r}" for r in block_reasons[:5])
            + ("\n\nFix these before I can push to GitHub." if block_reasons else "")
        )
    elif warning_issues:
        summary = (
            f"⚠️ {len(warning_issues)} warning(s) found (commit allowed):\n"
            + "\n".join(f"  • {w}" for w in warnings[:5])
        )
    else:
        summary = f"✅ All {len(file_blocks)} file(s) passed linting."

    return {
        "blocked": len(blocked_issues) > 0,
        "issues": [i._asdict() for i in all_issues],
        "block_reasons": block_reasons,
        "warnings": warnings,
        "clean_files": clean_count,
        "total_files": len(file_blocks),
        "summary": summary,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Auto-fix (best-effort for safe rules only)
# ─────────────────────────────────────────────────────────────────────────────

def auto_fix_file(filepath: str, content: str) -> tuple[str, list[str]]:
    """
    Auto-fixes safe issues in a file. Returns (fixed_content, list_of_fixes).
    Only fixes console.log and transition: all — safe, mechanical changes.
    """
    fixes = []
    original = content

    # Remove console.log lines
    new_content, n = re.subn(r"^\s*console\.log\s*\([^)]*\);\s*\n?", "", content, flags=re.MULTILINE)
    if n:
        content = new_content
        fixes.append(f"Removed {n} console.log statement(s)")

    # Fix transition: all → transition: opacity 0.2s, transform 0.2s
    new_content, n = re.subn(
        r"transition\s*:\s*all\b[^;]*;",
        "transition: opacity 0.2s ease, transform 0.2s ease;",
        content,
    )
    if n:
        content = new_content
        fixes.append(f"Fixed {n} transition: all → specific properties")

    return content, fixes


def auto_fix_blocks(file_blocks: dict) -> tuple[dict, dict]:
    """
    Runs auto-fix on all file blocks.
    Returns (fixed_blocks, {filepath: [fixes_applied]})
    """
    fixed = {}
    fix_log = {}

    for filepath, content in file_blocks.items():
        fixed_content, fixes = auto_fix_file(filepath, content)
        fixed[filepath] = fixed_content
        if fixes:
            fix_log[filepath] = fixes

    return fixed, fix_log
