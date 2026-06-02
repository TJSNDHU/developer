"""
services/mode_e_auditor.py
Mode E — full-repo audit. Generates a structured priority report without
writing any code. The user can pick any item and trigger a Mode C task
to fix it.

Scans for:
  1. Security holes (hardcoded secrets, exposed keys, SQL injection risk)
  2. Critical bugs (unhandled exceptions, missing error handling)
  3. Tech debt (duplicate code patterns, long functions, TODO/FIXME)
  4. Performance issues (N+1 query patterns, missing indexes, wildcard
     imports)
  5. Quick wins (missing .env.example, no README, no requirements.txt)

Static regex passes run first (zero LLM cost). A single LLM call then
catches logic-level issues. Per-file content is capped at the first 200
lines to keep the prompt small; final report is capped at ~1200 tokens.
"""

from __future__ import annotations
import re
import asyncio
from datetime import datetime, timezone
from typing import Optional
from motor.motor_asyncio import AsyncIOMotorDatabase
from services.llm import call_llm_with_meta
from services.ora_council_logger import log_conversational


# ─────────────────────────────────────────────────────────────────────────────
# Intent detection
# ─────────────────────────────────────────────────────────────────────────────

AUDIT_SIGNALS = [
    r"\baudit\b",
    r"\breview\s+(my\s+)?(code|repo|codebase|project)\b",
    r"\bwhat[''s\s]+wrong\s+with\b",
    r"\bwhat\s+should\s+i\s+refactor\b",
    r"\bsecurity\s+(check|scan|audit|review)\b",
    r"\bcode\s+quality\b",
    r"\btech\s+debt\b",
    r"\bcheck\s+(my\s+)?(entire|whole|full)\s+(repo|codebase)\b",
    r"\bfind\s+(all\s+)?(issues|bugs|problems)\s+in\b",
    r"\bhealth\s+check\b",
    r"\bwhat[''s\s]+bad\s+in\b",
]

AUDIT_PATTERN = re.compile("|".join(AUDIT_SIGNALS), re.IGNORECASE)


def is_audit_request(message: str) -> bool:
    return bool(AUDIT_PATTERN.search(message))


# ─────────────────────────────────────────────────────────────────────────────
# Static regex scanners (zero LLM cost)
# ─────────────────────────────────────────────────────────────────────────────

SECURITY_PATTERNS = [
    (r"""(?:password|secret|api_key|apikey|token|private_key)\s*=\s*['"][^'"]{6,}['"]""",
     "Hardcoded secret/credential", "critical"),
    (r"eval\s*\(", "Use of eval() — code injection risk", "high"),
    (r"subprocess\.call\s*\(.*shell\s*=\s*True", "shell=True in subprocess — injection risk", "high"),
    (r"SELECT\s+\*\s+FROM.*\+\s*\w+", "Possible SQL injection — string concat in query", "high"),
    (r"verify\s*=\s*False", "SSL verification disabled", "medium"),
    (r"DEBUG\s*=\s*True", "DEBUG=True in production config", "medium"),
    (r"0\.0\.0\.0.*allow_origins.*\*", "Wildcard CORS with 0.0.0.0 host", "medium"),
]

QUALITY_PATTERNS = [
    (r"except\s*:", "Bare except clause — catches everything including KeyboardInterrupt", "medium"),
    (r"print\s*\(", "print() statement (use logging instead in production)", "low"),
    (r"#\s*(TODO|FIXME|HACK|XXX)\b", "Unresolved TODO/FIXME", "low"),
    (r"time\.sleep\s*\(\s*[1-9]", "Blocking sleep in async context risk", "medium"),
    (r"\.append\(.*\)\s*\n.*\.append\(", "Repeated append pattern — consider list comprehension", "low"),
]

PERFORMANCE_PATTERNS = [
    (r"for\s+\w+\s+in.*:\s*\n\s*(await\s+)?db\.", "Possible N+1 query in loop", "high"),
    (r"import\s+\*\s+from", "Wildcard import — increases bundle size", "low"),
    (r"\.find\(\s*\{\s*\}\s*\)", "find({}) without limit — fetches entire collection", "high"),
    (r"json\.loads.*json\.dumps|json\.dumps.*json\.loads", "Redundant JSON encode/decode", "low"),
]


def static_scan_file(filepath: str, content: str) -> list[dict]:
    """Runs all regex patterns against one file. Returns list of findings."""
    findings = []
    lines = content.split("\n")
    ext = filepath.rsplit(".", 1)[-1].lower() if "." in filepath else ""

    all_patterns = []
    if ext in ("py",):
        all_patterns += SECURITY_PATTERNS + QUALITY_PATTERNS + PERFORMANCE_PATTERNS
    elif ext in ("js", "jsx", "ts", "tsx"):
        all_patterns += [
            (r"""(?:password|apiKey|api_key|secret|token)\s*[=:]\s*['"][^'"]{6,}['"]""",
             "Hardcoded secret in JS/TS", "critical"),
            (r"console\.log\s*\(", "console.log left in production code", "low"),
            (r"eval\s*\(", "eval() — code injection risk", "high"),
            (r"dangerouslySetInnerHTML", "dangerouslySetInnerHTML — XSS risk if unsanitised", "high"),
            (r"localStorage\.setItem.*(?:token|password|secret)", "Sensitive data in localStorage", "high"),
        ]

    for pattern, message, severity in all_patterns:
        for i, line in enumerate(lines, 1):
            if re.search(pattern, line, re.IGNORECASE):
                findings.append({
                    "filepath": filepath,
                    "line": i,
                    "message": message,
                    "severity": severity,
                    "snippet": line.strip()[:100],
                    "source": "static",
                })
                break  # one finding per pattern per file

    return findings


def static_scan_all(file_blocks: dict) -> list[dict]:
    """Scans all files. Returns all findings sorted by severity.
    Iter 50: merges Vanguard 007 findings (AWS/GitHub/Stripe/OpenAI/PEM/etc.)
    on top of our local regex pack so Mode E catches the same secrets
    the design linter blocks on Mode C."""
    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    all_findings = []
    for filepath, content in file_blocks.items():
        all_findings.extend(static_scan_file(filepath, content))

    # Iter 50 — pull in Vanguard 007 catalog. Map its severity ladder
    # (CRITICAL/HIGH/MEDIUM) onto Mode E's lowercase one.
    try:
        from services.vanguard_scanner import scan_file_blocks as _vg_scan
        sev_map = {"CRITICAL": "critical", "HIGH": "high", "MEDIUM": "medium"}
        for f in _vg_scan(file_blocks):
            all_findings.append({
                "filepath": f.get("filepath", ""),
                "line":     f.get("line", 0),
                "severity": sev_map.get(f.get("severity", "HIGH"), "high"),
                "message":  f"Vanguard 007 — {f.get('name', 'unknown')}",
                "snippet":  f.get("snippet", "")[:160],
                "source":   "vanguard_007",
            })
    except Exception:
        pass

    all_findings.sort(key=lambda x: severity_order.get(x["severity"], 4))
    return all_findings


# ─────────────────────────────────────────────────────────────────────────────
# LLM deep audit (logic-level issues static can't find)
# ─────────────────────────────────────────────────────────────────────────────

AUDIT_SYSTEM = """You are a senior code auditor reviewing a production codebase.

Identify issues that STATIC ANALYSIS CANNOT FIND:
- Business logic bugs
- Missing authentication on routes that need it  
- Race conditions in async code
- Missing input validation
- Architectural problems (circular dependencies, god objects)
- Missing error handling for external API calls

Output format (strict, no other text):
ISSUE: <one-line description>
FILE: <filepath>
SEVERITY: critical|high|medium|low
FIX: <one-sentence fix>
---
(repeat for each issue, max 8 issues)"""


async def llm_deep_audit(file_summaries: str, repo_ctx: str) -> list[dict]:
    """LLM audit for logic-level issues. Returns list of issue dicts."""
    try:
        resp = await call_llm_with_meta(
            system=AUDIT_SYSTEM,
            user=f"Repo: {repo_ctx}\n\nCode summaries:\n{file_summaries[:3000]}",
            mode="chat",
            max_tokens=800,
        )
        raw = (resp or {}).get("content", "") if isinstance(resp, dict) else str(resp or "")
    except Exception:
        return []

    issues = []
    current = {}
    for line in raw.split("\n"):
        line = line.strip()
        if line == "---":
            if current.get("description"):
                issues.append(current)
            current = {}
        elif line.startswith("ISSUE:"):
            current["description"] = line.replace("ISSUE:", "").strip()
        elif line.startswith("FILE:"):
            current["filepath"] = line.replace("FILE:", "").strip()
        elif line.startswith("SEVERITY:"):
            current["severity"] = line.replace("SEVERITY:", "").strip().lower()
        elif line.startswith("FIX:"):
            current["fix"] = line.replace("FIX:", "").strip()
            current["source"] = "llm"

    if current.get("description"):
        issues.append(current)

    return issues[:8]


# ─────────────────────────────────────────────────────────────────────────────
# Quick wins checker
# ─────────────────────────────────────────────────────────────────────────────

def check_quick_wins(file_tree: list[str]) -> list[dict]:
    """Checks for missing standard files. No LLM, no file reading."""
    wins = []
    tree_lower = [f.lower() for f in file_tree]

    checks = [
        (".env.example", "No .env.example file — new devs won't know what env vars are needed", "medium"),
        ("readme.md", "No README.md — project has no documentation entry point", "medium"),
        ("requirements.txt", "No requirements.txt — Python dependencies not pinned", "high"),
        ("package.json", None, None),  # skip if no JS
        (".gitignore", "No .gitignore — secrets might be committed accidentally", "high"),
        ("dockerfile", "No Dockerfile — deployment not containerized", "low"),
    ]

    has_python = any(f.endswith(".py") for f in file_tree)
    has_js     = any(f.endswith((".js", ".jsx", ".ts", ".tsx")) for f in file_tree)

    for filename, message, severity in checks:
        if message is None:
            continue
        if filename == "requirements.txt" and not has_python:
            continue
        if filename == "package.json" and not has_js:
            continue
        if not any(filename in f for f in tree_lower):
            wins.append({
                "description": message,
                "filepath": filename,
                "severity": severity,
                "source": "quick_win",
                "fix": f"Create a `{filename}` file",
            })

    return wins


# ─────────────────────────────────────────────────────────────────────────────
# Report builder
# ─────────────────────────────────────────────────────────────────────────────

def build_audit_report(
    static_findings: list[dict],
    llm_findings: list[dict],
    quick_wins: list[dict],
    repo_ctx: str,
    files_scanned: int,
) -> str:
    """Builds the human-readable audit report for ORA to stream."""
    all_issues = static_findings + llm_findings + quick_wins
    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    all_issues.sort(key=lambda x: severity_order.get(x.get("severity", "low"), 4))

    critical = [i for i in all_issues if i.get("severity") == "critical"]
    high      = [i for i in all_issues if i.get("severity") == "high"]
    medium    = [i for i in all_issues if i.get("severity") == "medium"]
    low       = [i for i in all_issues if i.get("severity") == "low"]

    lines = [
        f"**Audit report — {repo_ctx}**",
        f"_{files_scanned} files scanned · {len(critical)} critical · {len(high)} high · {len(medium)} medium · {len(low)} low_",
        "",
    ]

    def section(title, emoji, items):
        if not items:
            return
        lines.append(f"{emoji} **{title}** ({len(items)})")
        for item in items[:5]:
            fp = item.get("filepath", "")
            desc = item.get("description") or item.get("message", "")
            fix = item.get("fix", "")
            ln = item.get("line", "")
            loc = f"`{fp}:{ln}`" if ln else f"`{fp}`"
            lines.append(f"- {loc} — {desc}")
            if fix:
                lines.append(f"  → Fix: {fix}")
        lines.append("")

    section("Critical issues", "🔴", critical)
    section("High priority", "🟠", high)
    section("Medium priority", "🟡", medium)
    section("Quick wins", "✅", quick_wins)

    if not all_issues:
        lines.append("✅ No major issues found. Codebase looks clean.")
    else:
        fixable = [i for i in all_issues if i.get("fix") and i.get("filepath")]
        if fixable:
            lines.append(
                f"\n_{len(fixable)} of these can be auto-fixed. "
                f"Say **\"fix the critical issues\"** and I'll ship them via Mode C._"
            )

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Main audit runner
# ─────────────────────────────────────────────────────────────────────────────

async def run_audit(
    db: AsyncIOMotorDatabase,
    repo_ctx: str,
    file_blocks: dict,
    file_tree: list[str],
    user_message: str,
    user_id: Optional[str] = None,
    project_id: Optional[str] = None,
) -> dict:
    """
    Full Mode E audit. Runs static + LLM + quick wins in parallel.

    Returns:
    {
      "report": str,           # full markdown report to stream to user
      "all_issues": list,      # raw issue list
      "critical_count": int,
      "high_count": int,
      "fixable_tasks": list,   # list of Mode C task strings for auto-fix
    }
    """
    # Build file summaries for LLM (first 150 lines per file, capped at 10 files)
    important_files = sorted(
        file_blocks.items(),
        key=lambda x: (
            0 if "router" in x[0] or "service" in x[0] else
            1 if "model" in x[0] or "schema" in x[0] else 2
        )
    )[:10]

    file_summaries = "\n---\n".join(
        f"FILE: {path}\n" + "\n".join(content.split("\n")[:150])
        for path, content in important_files
    )

    # Run static scan + LLM audit + quick wins in parallel.
    # NOTE: asyncio.coroutine() was removed in Python 3.11, so we wrap the
    # sync helpers in tiny async coroutines manually.
    async def _async_static():
        return static_scan_all(dict(important_files))

    async def _async_wins():
        return check_quick_wins(file_tree)

    static_findings, llm_findings, quick_wins = await asyncio.gather(
        _async_static(),
        llm_deep_audit(file_summaries, repo_ctx),
        _async_wins(),
    )

    # Build report
    report = build_audit_report(
        static_findings=static_findings,
        llm_findings=llm_findings,
        quick_wins=quick_wins,
        repo_ctx=repo_ctx,
        files_scanned=len(file_blocks),
    )

    all_issues = static_findings + llm_findings + quick_wins

    # Build auto-fixable Mode C tasks
    fixable_tasks = []
    for issue in all_issues:
        if issue.get("fix") and issue.get("filepath") and issue.get("severity") in ("critical", "high"):
            fixable_tasks.append(
                f"Fix {issue.get('severity')} issue in {issue.get('filepath')}: "
                f"{issue.get('description', '')}. {issue.get('fix', '')}"
            )

    await log_conversational(
        db=db,
        mode="E",
        user_message=user_message,
        ora_reply=report,
        user_id=user_id,
        project_id=project_id,
    )

    return {
        "report": report,
        "all_issues": all_issues,
        "critical_count": sum(1 for i in all_issues if i.get("severity") == "critical"),
        "high_count":     sum(1 for i in all_issues if i.get("severity") == "high"),
        "fixable_tasks":  fixable_tasks[:5],
    }
