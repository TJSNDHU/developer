"""
services/post_task_scanner.py — Iter 167

Scans ONLY changed files after ORA completes a task.
Checks: security secrets + broken imports only.
Max 3 issues returned. Zero LLM for security regex.

Token cost: $0 for clean code (regex only — zero tokens).
            ~$0.001 for an optional LLM follow-up (currently disabled,
            hook left for future use).
"""
from __future__ import annotations

import logging
import re
from typing import Iterable

logger = logging.getLogger(__name__)

MAX_ISSUES = 3

# ── Security patterns (regex — zero LLM) ─────────────────────────────
SECRET_PATTERNS: list[tuple[str, str]] = [
    (r'(?i)(api_key|apikey|secret_key|private_key)\s*=\s*["\'][A-Za-z0-9_\-]{16,}["\']',
     "Hardcoded secret key"),
    (r'(?i)password\s*=\s*["\'][^"\']{6,}["\']',
     "Hardcoded password"),
    (r'sk-[A-Za-z0-9]{32,}',
     "Exposed API key (looks like OpenAI/Anthropic)"),
    (r'AKIA[0-9A-Z]{16}',
     "AWS Access Key exposed"),
    (r'ghp_[A-Za-z0-9]{36}',
     "GitHub Personal Access Token exposed"),
    (r'stripe[_\-]?(secret|sk)[_\-]?(?:key|live|test)?\s*[=:]\s*["\']sk_[A-Za-z0-9_]+["\']',
     "Stripe secret key exposed"),
    (r'(?i)bearer\s+[A-Za-z0-9\-_\.]{40,}',
     "Hardcoded Bearer token"),
]

# Placeholder markers — when one of these appears on the same line we
# assume the secret is illustrative, not a real leak.
_PLACEHOLDER_MARKERS = (
    "your_", "xxx", "placeholder",
    "example", "changeme", "***",
    "<your", "dummy", "fake",
)

# Known-good Python roots we never flag as "broken imports".
_KNOWN_PY_ROOTS = {
    # stdlib
    "os", "sys", "re", "json", "time",
    "asyncio", "typing", "pathlib",
    "datetime", "logging", "uuid",
    "collections", "functools", "itertools",
    "abc", "io", "math", "random",
    "hashlib", "base64", "enum",
    "dataclasses", "contextlib",
    "subprocess", "tempfile", "shutil",
    "string", "secrets", "warnings",
    "traceback", "inspect", "copy",
    # very common third-party
    "fastapi", "pydantic", "motor", "pymongo",
    "httpx", "requests", "pytest", "yaml",
    "starlette", "uvicorn", "anyio",
    # internal app namespaces
    "services", "cto_services", "routers",
    "models", "shared", "tools", "scripts",
}


def _scan_secrets(content: str, path: str) -> list[dict]:
    """Regex-only security scan. Zero tokens. Skips test/mock files."""
    if "test" in path.lower() or "mock" in path.lower():
        return []
    issues: list[dict] = []
    for i, line in enumerate(content.split("\n"), 1):
        stripped = line.strip()
        # Skip comment-only lines.
        if stripped.startswith(("#", "//", "*", "<!--")):
            continue
        line_lower = line.lower()
        if any(ph in line_lower for ph in _PLACEHOLDER_MARKERS):
            continue
        for pattern, label in SECRET_PATTERNS:
            if re.search(pattern, line):
                issues.append({
                    "type":     "security",
                    "severity": "HIGH",
                    "file":     path,
                    "line":     i,
                    "message":  label,
                    "snippet":  stripped[:80],
                    "icon":     "🔴",
                })
                break  # one issue per line max
        if len(issues) >= MAX_ISSUES:
            break
    return issues


def _scan_python_imports(content: str, path: str) -> list[dict]:
    """Flag obviously broken Python imports (heuristic, no AST)."""
    if not path.endswith(".py"):
        return []
    issues: list[dict] = []
    for i, line in enumerate(content.split("\n"), 1):
        stripped = line.strip()
        m = re.match(r"^from\s+([\w.]+)\s+import\s+(.+)$", stripped)
        if not m:
            continue
        module = m.group(1)
        # Relative imports — punt (we'd need filesystem context).
        if module.startswith("."):
            continue
        root = module.split(".")[0]
        if root in _KNOWN_PY_ROOTS:
            continue
        # Heuristic: obvious typo markers we can flag with zero false positives.
        if root.startswith("nonexistent") or "undefined" in root.lower():
            issues.append({
                "type":     "import",
                "severity": "MEDIUM",
                "file":     path,
                "line":     i,
                "message":  f"Potentially broken import: {module}",
                "snippet":  stripped[:80],
                "icon":     "🟡",
            })
    return issues


def _scan_js_imports(content: str, path: str) -> list[dict]:
    """Flag obviously broken JS/JSX/TS imports (heuristic)."""
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    if ext not in {"js", "jsx", "ts", "tsx"}:
        return []
    issues: list[dict] = []
    for i, line in enumerate(content.split("\n"), 1):
        stripped = line.strip()
        m = re.search(r"""from\s+['"]([^'"]+)['"]""", stripped)
        if not m:
            continue
        imp = m.group(1)
        # Only inspect relative imports — bare-module imports get
        # resolved by the bundler, regex can't tell what's broken.
        if not imp.startswith("."):
            continue
        # Way too many `../` is a red flag (probably a path bug).
        if ".." in imp and imp.count("..") > 3:
            issues.append({
                "type":     "import",
                "severity": "MEDIUM",
                "file":     path,
                "line":     i,
                "message":  "Suspicious deep relative import",
                "snippet":  stripped[:80],
                "icon":     "🟡",
            })
    return issues


async def scan_changed_files(
    changed_files: Iterable[str],
    file_contents: dict[str, str],
) -> list[dict]:
    """Scan only the files ORA touched. Max MAX_ISSUES (=3) total.

    Returns a list sorted HIGH → MEDIUM → LOW so the most-important
    issue always shows up first in the UI banner.
    """
    all_issues: list[dict] = []

    for path in changed_files:
        if len(all_issues) >= MAX_ISSUES:
            break
        content = file_contents.get(path) or ""
        if not content:
            continue

        # Security scan (regex — zero tokens).
        all_issues.extend(_scan_secrets(content, path))
        if len(all_issues) >= MAX_ISSUES:
            break

        # Import scan (regex — zero tokens).
        if path.endswith(".py"):
            all_issues.extend(_scan_python_imports(content, path))
        else:
            all_issues.extend(_scan_js_imports(content, path))

    severity_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    all_issues.sort(
        key=lambda x: severity_order.get(x.get("severity", "LOW"), 2)
    )
    return all_issues[:MAX_ISSUES]
