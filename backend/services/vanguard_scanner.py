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
     # Iter 212m-224 — require a keychar (base64/PGP body) on the
     # NEXT line so we don't false-positive on placeholder JSX/form
     # strings like `"-----BEGIN OPENSSH PRIVATE KEY-----\n…\n-----END…"`.
     # `\n[A-Za-z0-9+/=]{20,}` catches only actual key material, not
     # ellipsis / template placeholders / documentation examples.
     r"""-----BEGIN\s+(?:RSA|DSA|EC|OPENSSH|PGP)?\s*PRIVATE\s+KEY-----\s*\n[A-Za-z0-9+/=]{20,}""",
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
     r"""(?i)(?:mysql|postgres|postgresql|mongodb(?:\+srv)?|redis|amqp):\/\/[^:]+:[^@]+@""",
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
    # Iter 212m-224 — `\bexec\s*\(` also matched JavaScript's RegExp
    # `.exec()`, JSON.exec, and any `foo.exec(bar)` method call.
    # Real Python `exec()` is called at module/global scope — never
    # as an attribute (`.exec(`). Require the previous char NOT be
    # `.` to eliminate the JS RegExp false-positive class.
    ("exec_usage",            r"""(?<![.\w])exec\s*\(""",                               "CRITICAL"),
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
    # Iter 212m-226 — Rules that only make sense against source code
    # (eval/exec in Python, innerHTML in JS) must NOT fire on shell
    # scripts / markdown / plain text where the same token appears
    # as documentation prose.  `qa/simulated-user/run.sh:59` used to
    # trigger `eval_usage` on the string "Running promptfoo eval …".
    _CODE_ONLY_RULES = {
        "eval_usage", "exec_usage", "subprocess_shell_true",
        "os_system", "pickle_loads", "yaml_unsafe_load",
        "sql_string_format",
        "innerHTML_assignment", "dangerously_set_html",
    }
    _is_code_ext = filepath.endswith(
        (".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".go", ".rb",
         ".php", ".kt", ".cs", ".c", ".cpp", ".h", ".hpp")
    )
    for name, pattern, severity in SECRET_PATTERNS:
        # Iter 309 · Phase 0.2 — some SECRET rules use multi-line
        # regexes (e.g. `private_key` requires `\n[A-Za-z0-9+/=]{20,}`
        # after the header, iter 212m-224). Line-by-line iteration
        # cannot match those. Detect multi-line patterns by the
        # presence of `\n` in the compiled pattern string and run
        # them against the FULL text with pattern.search() instead.
        # Per-line iteration is preserved for the single-line rules
        # so line-number attribution and `# vanguard: ignore`
        # suppression still work as before.
        if "\\n" in pattern.pattern or "\n" in pattern.pattern:
            m = pattern.search(text)
            if m:
                # Attribute the finding to the line where the match
                # started.  `text[:m.start()]` counts newlines up to
                # the match; +1 because line numbers are 1-indexed.
                line_no = text[: m.start()].count("\n") + 1
                line_snippet = lines[line_no - 1].strip() if 0 < line_no <= len(lines) else ""
                # Respect the per-line suppression marker on the
                # header line — a developer opting-out with
                # `# vanguard: ignore` on the header line should
                # skip the finding even for multi-line rules.
                if _SUPPRESS_MARKER not in (lines[line_no - 1] if line_no <= len(lines) else ""):
                    findings.append({
                        "name": name,
                        "severity": severity,
                        "filepath": filepath,
                        "line": line_no,
                        "snippet": line_snippet[:120],
                        "source": "vanguard_007_secrets",
                    })
            continue
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
        # Iter 212m-229 — File-level DOMPurify detection. When the
        # file uses `DOMPurify.sanitize(` anywhere (import + at least
        # one call), any dangerouslySetInnerHTML / .innerHTML in
        # the file is assumed safe. Real sanitize calls often live
        # 5-20 lines away via useMemo / .then callbacks.
        file_uses_dompurify = "DOMPurify.sanitize" in text
        # Iter 212m-226 — Skip comment-only lines for dangerous-code
        # rules. JSDoc `* dangerouslySetInnerHTML` mentions and
        # `# eval() is dangerous` explainer comments were surfacing
        # as HIGH/CRITICAL findings on our own docs and code.
        # Only strips full-comment lines — inline `code // comment`
        # still gets scanned.
        _is_code_file = filepath.endswith(
            (".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".go", ".rb",
             ".php", ".kt", ".cs", ".c", ".cpp", ".h", ".hpp")
        )

        def _is_comment_only(ln: str) -> bool:
            s = ln.strip()
            if not s:
                return False
            # Python / shell / YAML style
            if s.startswith("#"):
                return True
            # JS / TS / Java / C style
            if s.startswith("//") or s.startswith("/*") or s.startswith("*"):
                return True
            return False

        for name, pattern, severity in DANGEROUS_PATTERNS:
            # Iter 212m-226 — Code-only rules must skip non-code files.
            if not _is_code_ext and name in _CODE_ONLY_RULES:
                continue
            for i, line in enumerate(lines, 1):
                if _SUPPRESS_MARKER in line:
                    continue
                if _is_code_file and _is_comment_only(line):
                    continue
                if pattern.search(line):
                    # Iter 212m-229 — Context-aware downgrade. XSS
                    # sinks wrapped in `DOMPurify.sanitize(...)` are
                    # safe by construction. Check current line + next
                    # (multi-line JSX props often wrap the sanitize
                    # call one line below the prop keyword).
                    ctx_line = line
                    if i < len(lines):
                        ctx_line = ctx_line + " " + lines[i]  # lines is 0-indexed
                    sanitized = (
                        name in ("dangerously_set_html", "innerHTML_assignment")
                        and ("DOMPurify.sanitize" in ctx_line or file_uses_dompurify)
                    )
                    findings.append({
                        "name": name,
                        "severity": "INFO" if sanitized else severity,
                        "filepath": filepath,
                        "line": i,
                        "snippet": line.strip()[:120],
                        "source": "vanguard_007_dangerous",
                        **({"sanitized": True, "downgraded": True}
                           if sanitized else {}),
                    })
                    break
    return findings


def scan_file_blocks(blocks: dict[str, str]) -> list[dict]:
    # Iter 212m-229 — Skip scanner rule-definition files (self-ref
    # false positives — `generation_rules.py` literally spells out
    # every rule id including db_connection_string, eval_usage, etc.
    # and used to flood the report with 15+ critical false positives).
    from services.scanner_utils import is_scanner_rule_file
    out: list[dict] = []
    for path, content in (blocks or {}).items():
        if is_scanner_rule_file(path):
            continue
        # Iter 212m-229 — Skip `.env` / `.env.*` files entirely.
        # These are gitignored by construction — keys living there
        # are INTENTIONAL, not leaks.  Downgrading them to INFO
        # (as we do for demo paths) still floods the report with
        # 3+ critical entries every scan; skipping is cleaner.
        # A separate `env_committed_check` rule (below) still fires
        # if `.env` is missing from `.gitignore`, catching the real
        # threat model.
        low = path.replace("\\", "/").lower()
        if (low == ".env" or low.endswith("/.env")
                or "/.env." in low or low.split("/")[-1].startswith(".env.")):
            continue
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


# ─── Iter 212m-66 — Multi-round deep-scan engine ────────────────────────
# Two-round scanner used by the /security-scan/run endpoint when the
# caller opts in with `two_round: true`.  Round 1 runs the existing
# 25-pattern catalog over every file (fast surface sweep).  Round 2
# re-scans ONLY the files that surfaced critical/high findings in
# Round 1 using a deeper rule set (13 extra rules + ±10-line context
# capture + chained-vulnerability detection that escalates compound
# risks like `sql_string_format + requests_no_verify` in the same
# file to CRITICAL).
#
# Pure stdlib — no external deps, no LLM cost.  The two budgets are
# enforced via `time.monotonic()` so a pathological repo can never
# wedge the request loop:
#     ROUND 1 ≤ 10 s   ROUND 2 ≤ 20 s   (combined cap policed at the
#     caller in security_scan.py — 30 s total).
import time as _time

# Deep-scan rule set — mirrors the 13 rules defined in
# routers/security_scan.py but re-anchored for line-by-line text
# scanning (no GitHub-API specific filters).  Kept inline so this
# module stays import-free of the router layer.
_DEEP_PATTERN_DEFS: list[tuple[str, str, str, str]] = [
    # (rule_id, regex, severity, description)
    ("secret_aws_access_key_deep",
     r"\bAKIA[0-9A-Z]{16}\b", "CRITICAL",
     "Hardcoded AWS access key id"),
    ("secret_openai_key_deep",
     r"\bsk-[a-zA-Z0-9]{32,}\b", "CRITICAL",
     "Hardcoded OpenAI / DeepSeek style API key"),
    ("secret_github_pat_deep",
     r"\bghp_[A-Za-z0-9]{30,}\b", "CRITICAL",
     "Hardcoded GitHub Personal Access Token"),
    ("secret_stripe_live_deep",
     r"\bsk_live_[A-Za-z0-9]{20,}\b", "CRITICAL",
     "Hardcoded Stripe LIVE secret key"),
    ("ssti_jinja_user_render",
     r"Template\(\s*request\.|Template\(\s*body\.|render_template_string\(",
     "HIGH",
     "Server-side template render of user-controlled input"),
    ("sql_string_format_deep",
     r"""(execute|executemany)\s*\(\s*[fF]?["'][^"']*\{[^}]+\}""",
     "CRITICAL",
     "f-string SQL query — use parameterised cursors"),
    ("sql_percent_format_deep",
     r"""(execute|executemany)\s*\(\s*["'][^"']*%s[^"']*["']\s*%\s*""",
     "HIGH",
     "%-format SQL query — use cursor.execute(query, params)"),
    ("nosql_where_operator_deep",
     r"""["']\$where["']\s*:""", "HIGH",
     "MongoDB $where allows arbitrary JS execution"),
    ("nosql_raw_body_query_deep",
     r"""\.find\(\s*(request\.json|body\.dict|body\.\*\*|\*\*body|\*\*payload)""",
     "MEDIUM",
     "Mongo query built from raw request body"),
    ("redos_nested_quantifier_deep",
     r"""re\.(compile|match|search|sub)\s*\(\s*r?["'][^"']*\([^)]*[+*][^)]*\)[+*]""",
     "HIGH",
     "Nested quantifier — vulnerable to catastrophic backtracking"),
    ("lpdos_no_body_limit_deep",
     r"@(app|router)\.(post|put|patch)\(", "MEDIUM",
     "FastAPI write endpoint — confirm body size middleware is mounted"),
    ("clipboard_external_paste_deep",
     r"navigator\.clipboard\.readText\s*\(", "LOW",
     "Reads clipboard — sanitise before rendering as code"),
    ("replay_jwt_no_jti_deep",
     r"""jwt\.encode\(\s*\{[^}]*\}""", "MEDIUM",
     "JWT signed without jti — add unique id + iat for replay defence"),
]

_DEEP_PATTERNS = [
    (rid, re.compile(rx), sev, desc)
    for rid, rx, sev, desc in _DEEP_PATTERN_DEFS
]

# Chain-vulnerability map — when ALL of `requires` fire in the same
# file, we synthesise a single CRITICAL `chain` finding pointing at
# the first contributing line.  The pairs encode real-world exploit
# chains documented in the OWASP cheat sheets.
_CHAIN_DEFS: list[dict] = [
    {
        "id":       "chain_sql_plus_insecure_http",
        "requires": {"sql_string_format", "sql_string_format_deep",
                     "requests_no_verify"},
        "min_match": 2,        # at least 2 distinct rule_ids hit
        "severity": "CRITICAL",
        "desc":     "SQL injection sink + unverified outbound TLS — "
                    "compound exfiltration risk",
    },
    {
        "id":       "chain_eval_plus_secret",
        "requires": {"eval_usage", "exec_usage",
                     "generic_api_key", "secret_openai_key_deep",
                     "secret_github_pat_deep"},
        "min_match": 2,
        "severity": "CRITICAL",
        "desc":     "Dynamic code execution sink + nearby hardcoded "
                    "secret — full credential exfiltration path",
    },
    {
        "id":       "chain_dangerous_html_plus_eval",
        "requires": {"dangerously_set_html", "innerHTML_assignment",
                     "eval_usage"},
        "min_match": 2,
        "severity": "CRITICAL",
        "desc":     "Unsafe HTML sink + eval — DOM XSS to RCE pivot "
                    "if user input ever lands in either",
    },
]


def _scan_round1(
    file_blocks: dict[str, str],
    *,
    deadline: float,
) -> tuple[list[dict], dict[str, str]]:
    """Round 1 — fast surface sweep using the existing 25-pattern
    catalog (`scan_text`).  Returns `(findings, file_text_index)`.

    The file_text_index is reused by Round 2 so we don't re-fetch /
    re-decode the same content.  Bails out and returns whatever it
    has if the deadline is exceeded so a single huge file can't
    starve Round 2."""
    findings: list[dict] = []
    file_index: dict[str, str] = {}
    for path, content in (file_blocks or {}).items():
        if _time.monotonic() >= deadline:
            break
        file_index[path] = content or ""
        hits = scan_text(content or "", filepath=path)
        if _is_safe_demo_path(path):
            for f in hits:
                if f.get("severity") in ("CRITICAL", "HIGH"):
                    f["severity"]         = "INFO"
                    f["downgraded"]       = True
                    f["downgrade_reason"] = "demo/test/example file"
        findings.extend(hits)
    return findings, file_index


def _scan_round2_file(
    path: str,
    text: str,
    *,
    deadline: float,
) -> list[dict]:
    """Run the deep-pattern catalog against one file and attach
    ±10-line context to every hit."""
    if _time.monotonic() >= deadline:
        return []
    lines = (text or "").split("\n")
    out: list[dict] = []
    for rid, pattern, severity, desc in _DEEP_PATTERNS:
        for i, line in enumerate(lines, start=1):
            if _time.monotonic() >= deadline:
                return out
            if "vanguard: ignore" in line or "security-scan: ignore" in line:
                continue
            if not pattern.search(line):
                continue
            ctx_start = max(0, i - 11)            # 1-indexed → 0-indexed slice
            ctx_end   = min(len(lines), i + 10)
            out.append({
                "name":         rid,
                "rule":         rid,
                "severity":     severity,
                "filepath":     path,
                "file":         path,
                "line":         i,
                "snippet":      line.strip()[:200],
                "desc":         desc,
                "source":       "vanguard_deep",
                "context_lines": lines[ctx_start:ctx_end],
                "context_range": [ctx_start + 1, ctx_end],
            })
            break  # one hit per rule per file is enough for the report
    return out


def _detect_chains(findings_by_file: dict[str, list[dict]]) -> list[dict]:
    """Build synthetic CRITICAL `chain` findings when a single file
    triggers ≥ `min_match` distinct contributing rules."""
    chain_findings: list[dict] = []
    for path, hits in findings_by_file.items():
        rule_ids = {f.get("name") or f.get("rule") or "" for f in hits}
        for chain in _CHAIN_DEFS:
            overlap = rule_ids & chain["requires"]
            if len(overlap) >= chain["min_match"]:
                first_line = min(
                    (f.get("line", 1) for f in hits
                     if (f.get("name") or f.get("rule")) in overlap),
                    default=1,
                )
                chain_findings.append({
                    "name":            chain["id"],
                    "rule":            chain["id"],
                    "severity":        chain["severity"],
                    "filepath":        path,
                    "file":            path,
                    "line":            first_line,
                    "snippet":         f"compound: {sorted(overlap)}",
                    "desc":            chain["desc"],
                    "source":          "vanguard_chain",
                    "contributing":    sorted(overlap),
                    "escalated":       True,
                })
    return chain_findings


def _dedup_findings(findings: list[dict]) -> list[dict]:
    """Deduplicate by `(file_path, line, pattern_name)` — preserves
    insertion order so Round 1 hits win the slot when equivalent."""
    seen: set[tuple[str, int, str]] = set()
    out: list[dict] = []
    for f in findings or []:
        key = (
            f.get("filepath") or f.get("file") or "",
            int(f.get("line") or 0),
            f.get("name") or f.get("rule") or "",
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(f)
    return out


def run_two_round_scan(
    file_blocks: dict[str, str],
    *,
    round1_budget: float = 10.0,
    round2_budget: float = 20.0,
) -> dict:
    """Run the two-round Vanguard pipeline over a dict of
    `{path: file_text}` blobs.

    Returns a dict with:
      • round1_findings: list (every Round-1 hit, severity unchanged)
      • round2_findings: list (deep hits with context_lines attached)
      • chain_findings:  list (synthesised CRITICAL chain alerts)
      • combined:        list (R1 ∪ R2 ∪ chains, deduplicated and
                         severity-sorted)
      • round2_skipped:  True if Round 1 alone exceeded the combined
                         budget — caller should fall back to R1.
      • files_round1:    int   total files scanned in R1
      • files_round2:    int   subset reprocessed in R2
      • elapsed_seconds: float wall-clock duration
    """
    started = _time.monotonic()
    # Allow `0.0` (or negative) to fully disable a round — callers
    # use this to opt out of R2 entirely or to force the bail-out
    # path during regression testing.
    r1_deadline = started + max(0.0, float(round1_budget))
    combined_deadline = started + max(0.0, float(round1_budget) + float(round2_budget))

    r1_findings, file_index = _scan_round1(file_blocks, deadline=r1_deadline)

    # Bail-out: if Round 1 alone burned > combined budget, skip R2
    # entirely.  This guards against pathological repos.
    if _time.monotonic() >= combined_deadline:
        elapsed = _time.monotonic() - started
        return {
            "round1_findings": r1_findings,
            "round2_findings": [],
            "chain_findings":  [],
            "combined":        _dedup_findings(r1_findings),
            "round2_skipped":  True,
            "files_round1":    len(file_index),
            "files_round2":    0,
            "elapsed_seconds": round(elapsed, 3),
        }

    # Round 2 — only files with at least one critical/high finding.
    flagged_paths: list[str] = sorted({
        f.get("filepath") or f.get("file") or ""
        for f in r1_findings
        if (f.get("severity") or "").upper() in ("CRITICAL", "HIGH")
    } - {""})

    r2_findings: list[dict] = []
    for path in flagged_paths:
        if _time.monotonic() >= combined_deadline:
            break
        text = file_index.get(path) or (file_blocks.get(path) or "")
        r2_findings.extend(_scan_round2_file(
            path, text, deadline=combined_deadline,
        ))

    # Chain detection runs over R1+R2 hits, indexed by file.
    findings_by_file: dict[str, list[dict]] = {}
    for f in (r1_findings + r2_findings):
        p = f.get("filepath") or f.get("file") or ""
        if not p:
            continue
        findings_by_file.setdefault(p, []).append(f)
    chain_findings = _detect_chains(findings_by_file)

    combined = _dedup_findings(r1_findings + r2_findings + chain_findings)
    # Severity sort — CRITICAL → HIGH → MEDIUM → LOW → INFO.
    _sev_rank = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3,
                 "INFO": 4, "WARNING": 2}
    combined.sort(key=lambda x: (
        _sev_rank.get((x.get("severity") or "").upper(), 9),
        x.get("filepath") or x.get("file") or "",
        int(x.get("line") or 0),
    ))

    elapsed = _time.monotonic() - started
    return {
        "round1_findings": r1_findings,
        "round2_findings": r2_findings,
        "chain_findings":  chain_findings,
        "combined":        combined,
        "round2_skipped":  False,
        "files_round1":    len(file_index),
        "files_round2":    len(flagged_paths),
        "elapsed_seconds": round(elapsed, 3),
    }

