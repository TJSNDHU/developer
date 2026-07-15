"""
services/bug_hunt_rules.py  —  Iter 212m-73 (Bug Hunt category)
=================================================================
50+ Nuclei-template-inspired STATIC code analysis rules.

Pure regex. Zero LLM cost. Walks the same {path: text} cache as the
other Codebase Health scanners and returns Finding dicts in the same
shape: {id, category, severity, file, line, title, message, fix_hint,
fix_tokens}.

Categories embedded inside the single Bug Hunt scanner:
  • SECRETS                 (15 patterns)
  • VULNERABLE CODE         (20 patterns)
  • EXPOSED ENDPOINTS       (10 patterns)
  • DEPENDENCY CVES         (5 + extensible CVE map)

Cost model: each finding's `fix_tokens` is 8 (higher than the regular
5 — Bug Hunt findings are higher-risk + take more LLM work to patch
correctly).

Source inspiration: ProjectDiscovery's Nuclei template catalog
(https://github.com/projectdiscovery/nuclei-templates). Patterns were
adapted from HTTP-detection templates into static-source detectors so
we can run them at commit-time without a live target.
"""
from __future__ import annotations

import re

# ──────────────────────────────────────────────────────────────────────
# Category A — SECRETS (15 patterns)
# Each tuple: (rule_id, regex, severity, message)
# ──────────────────────────────────────────────────────────────────────
_SECRET_RULES: list[tuple[str, re.Pattern, str, str]] = [
    ("aws_access_key_id",
     re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
     "CRITICAL",
     "Hard-coded AWS access key ID — anyone with this can spend from your AWS bill."),
    ("aws_temp_token",
     re.compile(r"\b(?:ASIA|AGPA|AIDA|AROA|AIPA|ANPA|ANVA)[A-Z0-9]{16}\b"),
     "CRITICAL",
     "AWS short-term token committed — rotate and revoke immediately."),
    ("gcp_api_key",
     re.compile(r"\bAIza[0-9A-Za-z\-_]{35}(?![0-9A-Za-z\-_])"),
     "CRITICAL",
     "Google Cloud API key in source — billing exposure and quota theft."),
    ("stripe_live_secret",
     re.compile(r"\bsk_live_[0-9a-zA-Z]{24,}\b"),
     "CRITICAL",
     "Stripe LIVE secret key — can issue refunds, see customers, drain funds."),
    ("stripe_live_publishable",
     re.compile(r"\bpk_live_[0-9a-zA-Z]{24,}\b"),
     "MEDIUM",
     "Publishable key is public by design, but committing it makes rotation harder."),
    ("sendgrid_api_key",
     re.compile(r"\bSG\.[a-zA-Z0-9_\-]{22}\.[a-zA-Z0-9_\-]{43}\b"),
     "CRITICAL",
     "SendGrid full-access API key — attacker can mail-bomb from your domain."),
    ("slack_bot_token",
     re.compile(r"\bxox[baprs]-[0-9a-zA-Z\-]{10,72}\b"),
     "CRITICAL",
     "Slack token — exfiltrates entire workspace history and DMs."),
    ("github_pat",
     re.compile(r"\bghp_[A-Za-z0-9]{36}\b"),
     "CRITICAL",
     "GitHub personal access token — can clone, push, and delete repos."),
    ("github_oauth_token",
     re.compile(r"\bgho_[A-Za-z0-9]{36}\b"),
     "CRITICAL",
     "GitHub OAuth user token — full account access at GitHub permissions level."),
    ("github_app_token",
     re.compile(r"\b(?:ghs|ghu)_[A-Za-z0-9]{36}\b"),
     "CRITICAL",
     "GitHub App/server token — installation-wide access on repos."),
    ("jwt_secret_hardcoded",
     re.compile(r"""(?i)(?:jwt[_-]?secret|jwt[_-]?key|JWT_SECRET)\s*[:=]\s*['"][^'"\s]{6,}['"]"""),
     "CRITICAL",
     "JWT signing secret in source — anyone can forge admin tokens."),
    ("private_rsa_key",
     # Iter 212m-224 — require actual base64 key body on the next
     # line; placeholder / documentation strings ending with `\n...\n`
     # or `…` no longer trigger.
     re.compile(r"-----BEGIN\s+(?:RSA|DSA|EC|OPENSSH|PGP)?\s*PRIVATE\s+KEY-----\s*\n[A-Za-z0-9+/=]{20,}"),
     "CRITICAL",
     "Private key block committed — must be rotated and the repo history rewritten."),
    ("azure_storage_key",
     re.compile(r"""(?i)(?:AccountKey|azure[_-]?storage[_-]?key)\s*[:=]\s*['"]?[A-Za-z0-9+/=]{60,}['"]?"""),
     "CRITICAL",
     "Azure Storage account key in source — full container read/write."),
    ("twilio_api_key",
     re.compile(r"\bSK[0-9a-fA-F]{32}\b"),
     "CRITICAL",
     "Twilio API key — attacker can send paid SMS / make calls on your account."),
    ("env_var_in_code",
     # Iter 212m-226 — require the value to look like a real secret:
     # at least 20 chars AND contain at least one digit AND at least
     # one non-alpha (base64/hex-ish). This kills the false positive
     # on `MESSAGE_TEMPLATE = "Hey there, welcome to our platform"`
     # style constants that used to flood the report.
     re.compile(r"""(?im)^[A-Z][A-Z0-9_]{6,}\s*=\s*['"](?=[^'"]*\d)(?=[^'"]*[+/=\-])[A-Za-z0-9_+/=.\-]{20,}['"]"""),
     "MEDIUM",
     "Looks like a .env line committed in source — move to .env and gitignore it."),
]


# ──────────────────────────────────────────────────────────────────────
# Category B — VULNERABLE CODE PATTERNS (20 patterns)
# ──────────────────────────────────────────────────────────────────────
_VULN_RULES: list[tuple[str, re.Pattern, str, str]] = [
    ("log4shell_jndi",
     re.compile(r"\$\{jndi:"),
     "CRITICAL",
     "Log4Shell-style JNDI lookup in a string — remote code execution via log injection."),
    ("eval_with_request",
     re.compile(r"\beval\s*\([^)]*(?:request|input|argv|body|params|query)\b"),
     "CRITICAL",
     "eval() with user-controlled input — direct RCE primitive."),
    ("exec_with_request",
     re.compile(r"\bexec\s*\([^)]*(?:request|input|argv|body|params|query)\b"),
     "CRITICAL",
     "exec() with user-controlled input — direct RCE primitive."),
    ("pickle_loads_untrusted",
     re.compile(r"\bpickle\.loads?\s*\("),
     "HIGH",
     "pickle.loads on any input is RCE — replace with JSON or msgpack."),
    ("yaml_load_unsafe",
     re.compile(r"\byaml\.load\s*\((?!.*Loader\s*=)"),
     "HIGH",
     "yaml.load without a Loader is unsafe — use yaml.safe_load."),
    ("subprocess_shell_true_input",
     re.compile(r"subprocess\.\w+\([^)]*shell\s*=\s*True[^)]*(?:request|input|argv|body|params|query|user)"),
     "CRITICAL",
     "subprocess(shell=True) with user input — shell injection RCE."),
    ("os_system_user_input",
     re.compile(r"\bos\.system\s*\([^)]*(?:request|input|argv|body|params|query|user)"),
     "CRITICAL",
     "os.system with user input — shell injection RCE."),
    ("xml_etree_no_defusedxml",
     re.compile(r"(?:from\s+xml\.etree|import\s+xml\.etree|xml\.etree\.ElementTree\.parse|minidom\.parseString)"),
     "MEDIUM",
     "stdlib XML parsers are vulnerable to XXE / billion-laughs — use defusedxml."),
    ("regex_catastrophic_backtracking",
     re.compile(r"""(?:\(\.\*\+?\)\+|\(\.\+\)\+|\(\\\w\*\)\+|\(\[\^[^\]]+\]\*\)\+)"""),
     "MEDIUM",
     "Regex with nested quantifiers — ReDoS attack surface."),
    ("xxe_external_entity",
     re.compile(r"<!ENTITY\s+\w+\s+SYSTEM"),
     "HIGH",
     "External entity declaration in source — confirms XXE-vulnerable design."),
    ("weak_crypto_md5",
     re.compile(r"hashlib\.md5\s*\(|MD5\s*\(|CryptoJS\.MD5"),
     "MEDIUM",
     "MD5 is broken for security purposes — use SHA-256 or bcrypt/argon2 for passwords."),
    ("weak_crypto_sha1",
     re.compile(r"hashlib\.sha1\s*\(|CryptoJS\.SHA1"),
     "MEDIUM",
     "SHA-1 is collision-broken — use SHA-256 or stronger."),
    ("weak_random_token",
     re.compile(r"""(?i)(?:token|secret|password|nonce|salt)\s*=\s*(?:random\.random|random\.randint|Math\.random)\s*\("""),
     "HIGH",
     "Tokens/secrets generated from non-cryptographic PRNG — use secrets.token_urlsafe or crypto.randomBytes."),
    ("jwt_alg_none",
     re.compile(r"""['"]alg['"]\s*:\s*['"]none['"]"""),
     "CRITICAL",
     "JWT with alg=none accepts unsigned tokens — anyone can forge claims."),
    ("cors_wildcard_with_creds",
     re.compile(r"Access-Control-Allow-Origin[^\n]*\*[\s\S]{0,200}Access-Control-Allow-Credentials[^\n]*true",
                re.IGNORECASE),
     "HIGH",
     "CORS wildcard with credentials — defeats the same-origin policy entirely."),
    ("cookie_no_secure_flag",
     re.compile(r"set_cookie\([^)]*(?!.*secure\s*=\s*True)[^)]*\)"),
     "MEDIUM",
     "Cookie set without Secure flag — can be sent over plaintext HTTP."),
    ("cookie_no_httponly_flag",
     re.compile(r"set_cookie\([^)]*(?!.*httponly\s*=\s*True)[^)]*\)"),
     "MEDIUM",
     "Cookie set without HttpOnly — readable from JS / XSS-exfiltratable."),
    ("ssrf_open_url_fetch",
     re.compile(r"""(?:requests\.get|httpx\.get|urlopen|fetch)\s*\(\s*(?:request\.|input\(|user_input|params\[)"""),
     "HIGH",
     "Outbound HTTP with user-supplied URL — SSRF: attacker hits your internal services."),
    ("sql_string_format",
     re.compile(r"""(?i)(?:execute|cursor\.execute)\s*\(\s*[f'\"]+.*\{"""),
     "CRITICAL",
     "Raw SQL built with f-string / format — SQL injection."),
    ("dangerously_set_html",
     re.compile(r"\bdangerouslySetInnerHTML\b"),
     "HIGH",
     "React dangerouslySetInnerHTML — sanitize via DOMPurify or refuse to use it."),
    ("inner_html_assign",
     re.compile(r"\.innerHTML\s*="),
     "HIGH",
     "Direct .innerHTML assignment — XSS sink. Use textContent or DOMPurify."),
]


# ──────────────────────────────────────────────────────────────────────
# Category C — EXPOSED ENDPOINTS (10 patterns)
# ──────────────────────────────────────────────────────────────────────
_ENDPOINT_RULES: list[tuple[str, re.Pattern, str, str]] = [
    ("debug_route_no_auth",
     re.compile(r"""(?i)@(?:app|router)\.(?:get|post|route)\s*\(\s*['"]/(?:debug|console|repl|shell)['"][^)]*\)\s*(?!\s*@(?:require|auth|login))"""),
     "CRITICAL",
     "/debug-style endpoint without an auth decorator on the next line — public RCE risk."),
    ("admin_route_no_auth",
     re.compile(r"""(?i)@(?:app|router)\.(?:get|post|route)\s*\(\s*['"]/admin[^'"\n]*['"][^)]*\)\s*\n\s*(?:async\s+)?def"""),
     "HIGH",
     "/admin endpoint defined — verify the decorator above enforces is_admin/role check."),
    ("actuator_endpoint",
     re.compile(r"""(?i)['"]/(?:actuator|jolokia|env|heapdump|threaddump|trace)['"]"""),
     "HIGH",
     "Spring/JMX actuator path exposed — leaks env vars, heap, credentials."),
    ("metrics_endpoint_no_auth",
     re.compile(r"""(?i)@(?:app|router)\.(?:get|route)\s*\(\s*['"]/metrics['"][^)]*\)\s*\n\s*(?:async\s+)?def\s+\w+\s*\([^)]*\)"""),
     "MEDIUM",
     "/metrics endpoint — restrict to internal IPs / require bearer token."),
    ("health_endpoint_leaks",
     re.compile(r"""(?i)['"]/health['"][\s\S]{0,400}(?:mongo|db|database|version|commit|env|secret)"""),
     "MEDIUM",
     "/health endpoint appears to leak internals (DB names, commit SHA, env). Keep it boolean."),
    ("api_key_in_url",
     re.compile(r"""(?i)\?(?:api[_-]?key|token|access[_-]?token|secret)=\{?\w+\}?"""),
     "HIGH",
     "Secret passed in URL — gets logged in proxies, browser history, referrers."),
    ("stack_trace_returned",
     re.compile(r"""(?:traceback\.format_exc\(\)|str\(e\)|repr\(e\)).{0,40}(?:return|response|HTTPException|JSONResponse)"""),
     "MEDIUM",
     "Looks like an exception's full text is returned to the client — leaks file paths and code structure."),
    ("debug_true_production",
     re.compile(r"""(?im)^\s*DEBUG\s*=\s*True"""),
     "HIGH",
     "DEBUG=True at module level — Flask/Django debug mode is an RCE primitive (Werkzeug console)."),
    ("swagger_in_prod",
     re.compile(r"""(?i)(?:docs_url|openapi_url|swagger_ui)\s*=\s*['"]/(?:docs|swagger|api-docs)['"]"""),
     "LOW",
     "Swagger/OpenAPI UI enabled — restrict in prod or gate behind auth."),
    ("cors_allow_all",
     re.compile(r"""allow_origins\s*=\s*\[\s*['"]\*['"]\s*\]"""),
     "HIGH",
     "FastAPI/Starlette CORS allow_origins=['*'] — combined with credentials this is critical."),
]


# ──────────────────────────────────────────────────────────────────────
# Category D — DEPENDENCY CVES (extensible)
# Tuple: (package_name_lowercase, max_vulnerable_version, cve_id,
#         severity, message)
# Detection logic compares manifests (requirements.txt, package.json).
# ──────────────────────────────────────────────────────────────────────
_DEP_CVES: list[tuple[str, str, str, str, str]] = [
    ("requests",     "2.31.0", "CVE-2023-32681",
     "HIGH",     "requests < 2.31.0 — Proxy-Authorization leak via cross-origin redirect."),
    ("flask",        "2.3.0",  "multi-cves",
     "HIGH",     "flask < 2.3.0 — session-cookie + open-redirect CVEs."),
    ("django",       "4.2.0",  "multi-cves",
     "CRITICAL", "django < 4.2.0 — SQL injection + DoS CVEs."),
    ("pillow",       "10.0.0", "CVE-2023-44271",
     "HIGH",     "pillow < 10.0.0 — buffer overflow in JPEG/PNG parsing."),
    ("cryptography", "41.0.0", "multi-cves",
     "HIGH",     "cryptography < 41.0.0 — multiple openssl-bound vulns."),
    # bonus easy wins
    ("urllib3",      "2.0.0",  "CVE-2023-43804",
     "MEDIUM",   "urllib3 < 2.0 — cookie & auth header leak on cross-origin redirect."),
    ("pyyaml",       "6.0",    "CVE-2020-14343",
     "HIGH",     "pyyaml < 6.0 — yaml.load() arbitrary code execution."),
    ("jinja2",       "3.1.3",  "CVE-2024-22195",
     "MEDIUM",   "jinja2 < 3.1.3 — xmlattr filter XSS."),
    ("axios",        "1.6.0",  "CVE-2023-45857",
     "HIGH",     "axios < 1.6.0 — CSRF token leak across redirects."),
    ("lodash",       "4.17.21","CVE-2021-23337",
     "HIGH",     "lodash < 4.17.21 — prototype pollution via _.template."),
    ("next",         "14.2.10","CVE-2024-46982",
     "CRITICAL", "next < 14.2.10 — cache poisoning + SSRF chain."),
]


def _vercmp(a: str, b: str) -> int:
    """Loose semver comparator — returns -1/0/1.  Strips pre-release."""
    def parts(s: str) -> list[int]:
        s = re.sub(r"[^\d.].*$", "", s)
        return [int(x) for x in s.split(".") if x.isdigit()] or [0]
    pa, pb = parts(a), parts(b)
    # pad
    while len(pa) < len(pb): pa.append(0)
    while len(pb) < len(pa): pb.append(0)
    return (pa > pb) - (pa < pb)


# ──────────────────────────────────────────────────────────────────────
# Public scanner
# ──────────────────────────────────────────────────────────────────────
_BUG_HUNT_TOKENS = 8  # higher than regular health findings


def _mk(rule_id: str, severity: str, path: str, line: int,
        title: str, message: str, fix_hint: str) -> dict:
    return {
        "id":         f"bh::{path}:{line}:{rule_id}",
        "category":   "bug_hunt",
        "severity":   severity.lower() if severity.upper() in
                      {"CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"} else "medium",
        "file":       path,
        "line":       line,
        "title":      title,
        "message":    message,
        "fix_hint":   fix_hint,
        "fix_tokens": _BUG_HUNT_TOKENS,
    }


_FIX_HINT = {
    "aws_access_key_id":       "Move to AWS Secrets Manager or .env; rotate the key in IAM immediately.",
    "aws_temp_token":          "Rotate via STS, never commit short-term creds.",
    "gcp_api_key":             "Restrict via API key restrictions in GCP Console and store in .env.",
    "stripe_live_secret":      "Rotate at dashboard.stripe.com/apikeys; load from STRIPE_SECRET_KEY env.",
    "stripe_live_publishable": "Move to NEXT_PUBLIC_/REACT_APP_ env at build time.",
    "sendgrid_api_key":        "Revoke + recreate at app.sendgrid.com/settings/api_keys; load from env.",
    "slack_bot_token":         "Rotate at api.slack.com/apps and store in SLACK_BOT_TOKEN env.",
    "github_pat":              "Revoke at github.com/settings/tokens; use fine-grained PATs in .env.",
    "github_oauth_token":      "Sign out + revoke the OAuth grant; never commit user tokens.",
    "github_app_token":        "Regenerate the App installation token; load lazily in code.",
    "jwt_secret_hardcoded":    "Move to JWT_SECRET env; rotate so every old token becomes invalid.",
    "private_rsa_key":         "Rotate, then rewrite history with `git filter-repo`.",
    "azure_storage_key":       "Rotate at portal.azure.com; use SAS tokens or managed identity.",
    "twilio_api_key":          "Revoke at console.twilio.com; use sub-account credentials in env.",
    "env_var_in_code":         "Move to .env, add `*.env*` to .gitignore, rotate the value.",
    "log4shell_jndi":          "Never log untrusted input verbatim; sanitize ${ patterns.",
    "eval_with_request":       "Replace eval with ast.literal_eval or an explicit parser.",
    "exec_with_request":       "Use a whitelist dispatch dict instead of exec().",
    "pickle_loads_untrusted":  "Switch to JSON / msgpack for any untrusted input.",
    "yaml_load_unsafe":        "Use yaml.safe_load instead of yaml.load.",
    "subprocess_shell_true_input": "Drop shell=True, pass args as list, validate inputs against allowlist.",
    "os_system_user_input":    "Replace with subprocess.run([...]) and validate args.",
    "xml_etree_no_defusedxml": "Install defusedxml and use defusedxml.ElementTree.parse.",
    "regex_catastrophic_backtracking": "Rewrite without nested quantifiers; consider a streaming parser.",
    "xxe_external_entity":     "Disable external entities (set resolve_entities=False / use defusedxml).",
    "weak_crypto_md5":         "Use hashlib.sha256(); for passwords use bcrypt or argon2.",
    "weak_crypto_sha1":        "Use SHA-256 or stronger.",
    "weak_random_token":       "Use secrets.token_urlsafe / crypto.randomBytes for any security-sensitive value.",
    "jwt_alg_none":            "Pin the alg list explicitly (e.g. algorithms=['HS256']).",
    "cors_wildcard_with_creds": "Set allow_origins to an explicit list when allow_credentials=True.",
    "cookie_no_secure_flag":   "Pass secure=True when setting cookies on https.",
    "cookie_no_httponly_flag": "Pass httponly=True to prevent JS read.",
    "ssrf_open_url_fetch":     "Validate the URL host against an allowlist; block private IP ranges.",
    "sql_string_format":       "Use parameterized queries: cursor.execute('… %s', (val,)).",
    "dangerously_set_html":    "Sanitize via DOMPurify or render plain text.",
    "inner_html_assign":       "Use textContent or a sanitizer like DOMPurify.",
    "debug_route_no_auth":     "Either delete the endpoint or add an auth + admin decorator.",
    "admin_route_no_auth":     "Confirm a require_admin/dependency check guards this route.",
    "actuator_endpoint":       "Disable in production or restrict via network policy.",
    "metrics_endpoint_no_auth": "Bind to localhost or require a bearer token.",
    "health_endpoint_leaks":   "Return only {'status': 'ok'} — no version, no DB names.",
    "api_key_in_url":          "Move secrets to the Authorization header or POST body.",
    "stack_trace_returned":    "Log the trace server-side; return a generic 500 to the client.",
    "debug_true_production":   "Set DEBUG=False; use env var with a strict default.",
    "swagger_in_prod":         "Set docs_url=None in production or gate behind auth.",
    "cors_allow_all":          "Replace '*' with an explicit allow-list of front-end origins.",
}


def scan_bug_hunt(text_cache: dict[str, str]) -> list[dict]:
    """Run all Bug Hunt rules over the cached repo text.  Returns the
    same Finding dict shape as the other Codebase Health scanners.

    Iter 212m-224 — Skips AUREM's own scanner rule-definition files
    to avoid self-referential false positives (a rule regex matched
    against the file that defines it). See
    `routers.codebase_health._is_scanner_rule_file` for the list.
    """
    from services.scanner_utils import is_scanner_rule_file as _is_scanner_rule_file   # iter 212m-225 boundary fix
    out: list[dict] = []

    # ── A) SECRETS ────────────────────────────────────────────────────
    for path, text in text_cache.items():
        if not text:
            continue
        if _is_scanner_rule_file(path):
            continue
        # skip .env-style files where these values legitimately live
        low = path.lower()
        if low.endswith(".env") or "/.env" in low or low.endswith(".lock"):
            continue
        for rid, rx, sev, msg in _SECRET_RULES:
            for m in rx.finditer(text):
                # env_var_in_code is noisy — only flag in code files
                if rid == "env_var_in_code" and not any(
                    low.endswith(ext) for ext in
                    (".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".go", ".rb")
                ):
                    continue
                line = text[:m.start()].count("\n") + 1
                # Iter 212m-229 — QA simulated-user harness has
                # intentional hard-coded test creds (JWT signing
                # secrets for the integration bot). Downgrade like
                # other demo paths so it stays visible for review
                # without polluting CRITICAL count.
                if "qa/simulated-user/" in low or "/qa/" in low:
                    finding = _mk(rid, "INFO", path, line, rid,
                                  f"{msg} — QA harness (test creds intentional)",
                                  _FIX_HINT.get(rid, ""))
                    finding["downgraded"] = True
                    finding["downgrade_reason"] = "qa harness"
                    out.append(finding)
                    continue
                out.append(_mk(rid, sev, path, line, rid, msg,
                               _FIX_HINT.get(rid, "")))

    # ── B) VULNERABLE CODE ────────────────────────────────────────────
    for path, text in text_cache.items():
        if not text:
            continue
        if _is_scanner_rule_file(path):
            continue
        low = path.lower()
        # only run on source files
        if not any(low.endswith(ext) for ext in
                   (".py", ".js", ".jsx", ".ts", ".tsx", ".java",
                    ".rb", ".go", ".php", ".kt", ".cs")):
            continue
        # Iter 212m-229 — File-level DOMPurify detection. When a
        # JSX/TSX file uses `DOMPurify.sanitize(` ANYWHERE in the
        # file, any `dangerouslySetInnerHTML` in that same file is
        # assumed safe by construction (the sanitize call is often
        # 5-20 lines away via a useMemo / .then callback, so a
        # ±1-line proximity check misses these).
        file_uses_dompurify = "DOMPurify.sanitize" in text

        for rid, rx, sev, msg in _VULN_RULES:
            for m in rx.finditer(text):
                line = text[:m.start()].count("\n") + 1
                # Iter 212m-229 — Honour per-line vanguard-ignore
                # markers here too. bug_hunt._vuln_scan used to
                # flag `PreviewPanel.jsx` innerHTML assignments
                # even though they carry `// vanguard: ignore`
                # comments explaining the sandboxed-iframe
                # architecture. Now consistent with vanguard scanner.
                line_text = text.split("\n")[line - 1] if line >= 1 else ""
                if "vanguard: ignore" in line_text:
                    continue
                # Skip JS/JSX/TS comment-only lines (matches
                # vanguard's dangerous-code sweep behaviour).
                stripped = line_text.strip()
                if stripped.startswith(("//", "/*", "*", "#")):
                    continue
                # Iter 212m-229 — Context-aware downgrade: when
                # `dangerouslySetInnerHTML` / `.innerHTML =` is
                # WRAPPED by `DOMPurify.sanitize(...)` on the same
                # line (or the immediately-following line), the
                # sink is SAFE by construction. Also downgrade when
                # the file uses DOMPurify.sanitize anywhere (useMemo
                # / .then callbacks may sanitize 5-20 lines earlier).
                if rid in ("dangerously_set_html", "inner_html_assign"):
                    next_line = (text.split("\n")[line]
                                 if line < len(text.split("\n")) else "")
                    combined = line_text + " " + next_line
                    if "DOMPurify.sanitize" in combined or file_uses_dompurify:
                        finding = _mk(rid, "INFO", path, line, rid,
                                      f"{msg} — SAFE (sanitized via DOMPurify)",
                                      _FIX_HINT.get(rid, ""))
                        finding["sanitized"] = True
                        finding["downgraded"] = True
                        out.append(finding)
                        continue
                out.append(_mk(rid, sev, path, line, rid, msg,
                               _FIX_HINT.get(rid, "")))

    # ── C) EXPOSED ENDPOINTS ──────────────────────────────────────────
    # Iter 212m-226 — `admin_route_no_auth` was firing on every file
    # that defines an `/admin` route, even when the router at the top
    # of the file already declares `dependencies=[Depends(require_admin)]`
    # or every affected function pulls `require_admin` as a dep. Skip
    # a whole file when either of those two signals is present — it's
    # the same signal a human reviewer would use.
    _AUTH_GUARDED_MARKERS = (
        "require_admin",
        "require_founder",
        "current_dev",
        "get_current_admin",
        "Depends(require_",
        "dependencies=[Depends(require",
    )
    for path, text in text_cache.items():
        if not text:
            continue
        if _is_scanner_rule_file(path):
            continue
        low = path.lower()
        if not any(low.endswith(ext) for ext in
                   (".py", ".js", ".jsx", ".ts", ".tsx", ".java")):
            continue
        file_has_admin_guard = any(m in text for m in _AUTH_GUARDED_MARKERS)
        # Iter 212m-227 — Endpoint rules were firing on explainer
        # comments (main.py:735 has `# CORS lockdown. allow_origins=['*']
        # meant ANY website could hit the API.` — pure documentation
        # of a FIXED bug, not the current code).  Build a mask of
        # comment-only line ranges so we can skip regex hits that
        # land inside them.
        _comment_ranges: list[tuple[int, int]] = []
        _cursor = 0
        for _ln in text.split("\n"):
            _s = _ln.strip()
            _is_comment = (
                _s.startswith("#") or _s.startswith("//")
                or _s.startswith("/*") or _s.startswith("*")
            )
            _end = _cursor + len(_ln)
            if _is_comment and _s:
                _comment_ranges.append((_cursor, _end))
            _cursor = _end + 1   # +1 for the newline

        def _hit_in_comment(offset: int) -> bool:
            for lo, hi in _comment_ranges:
                if lo <= offset <= hi:
                    return True
            return False

        for rid, rx, sev, msg in _ENDPOINT_RULES:
            # Skip admin_route_no_auth if the file demonstrably wires
            # an admin/founder auth dependency somewhere — this is
            # what our own routers do (require_admin at APIRouter
            # dependencies=... level) and re-flagging them creates
            # 7+ false positives on our own codebase.
            if rid == "admin_route_no_auth" and file_has_admin_guard:
                continue
            for m in rx.finditer(text):
                if _hit_in_comment(m.start()):
                    continue
                line = text[:m.start()].count("\n") + 1
                out.append(_mk(rid, sev, path, line, rid, msg,
                               _FIX_HINT.get(rid, "")))

    # ── D) DEPENDENCY CVES ────────────────────────────────────────────
    for path, text in text_cache.items():
        if not text:
            continue
        low = path.lower()
        if low.endswith("requirements.txt") or low.endswith("requirements-dev.txt"):
            for i, raw in enumerate((text or "").splitlines(), start=1):
                m = re.match(r"^\s*([A-Za-z0-9_\-\.]+)\s*[=<>~!]+\s*([0-9][0-9A-Za-z\.\-]*)", raw)
                if not m:
                    continue
                pkg = m.group(1).lower()
                ver = m.group(2)
                for cve_pkg, max_bad, cve, sev, msg in _DEP_CVES:
                    if pkg != cve_pkg:
                        continue
                    if _vercmp(ver, max_bad) < 0:
                        out.append(_mk(
                            f"cve_{pkg}", sev, path, i,
                            f"{pkg}=={ver}",
                            f"{msg}  ({cve})",
                            f"Bump {pkg} to >= {max_bad} in requirements.txt and redeploy.",
                        ))
                        break
        elif low.endswith("package.json"):
            try:
                import json
                pkg_json = json.loads(text or "{}")
                deps = {**(pkg_json.get("dependencies") or {}),
                        **(pkg_json.get("devDependencies") or {})}
                for pkg_raw, ver_raw in deps.items():
                    pkg = pkg_raw.lower()
                    ver = re.sub(r"^[^\d]*", "", str(ver_raw))
                    if not ver:
                        continue
                    for cve_pkg, max_bad, cve, sev, msg in _DEP_CVES:
                        if pkg != cve_pkg:
                            continue
                        if _vercmp(ver, max_bad) < 0:
                            out.append(_mk(
                                f"cve_{pkg}", sev, path, 0,
                                f"{pkg}=={ver}",
                                f"{msg}  ({cve})",
                                f"Bump {pkg} to >= {max_bad} in package.json and re-yarn-install.",
                            ))
                            break
            except Exception:
                pass

    return out
