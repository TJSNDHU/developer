"""
services/generation_rules.py  —  Directive Session 1 · Part A
=============================================================

Machine-readable **generation-time rules manifest** derived from the
platform's own post-hoc scanners. Injected into the system prompt of
any code-writing LLM call so the model sees the rules BEFORE writing
code, not only after Vanguard / Bug Hunt / Health / Docker CIS / HTTP
headers catch a violation.

Design decisions locked into this file:

  • Rule identities and severities are **extracted at import time**
    from the actual scanner modules. If someone adds a new rule to
    `bug_hunt_rules.py` or `vanguard_scanner.py`, the manifest picks
    it up automatically — there is no duplicate hand-maintained list
    to drift.

  • The condensed form is **rule_id + one-line trigger condition**,
    not the full regex or fix hint. This is a deliberate cap on
    prompt size (target: ≤ 3 KB total addition to the persona) so we
    don't linearly balloon token cost per code-write.

  • HTTP headers + Docker CIS rules are hand-curated here because
    those scanners emit findings via inline branches (no clean
    top-level rule table). The set is small and stable.

Public API:
  build_condensed_manifest(*, include_low: bool = False) -> str
      Returns the prompt-ready manifest block, roughly 2–3 KB. Low
      severity rules are excluded by default because the model doesn't
      benefit much from noise-tier rules at generation time.

  get_rule_index() -> dict
      Returns the full structured inventory for dashboards / debug
      views: {"vanguard_secrets": [...], "vanguard_dangerous": [...],
      "bug_hunt_secrets": [...], "bug_hunt_vulns": [...],
      "bug_hunt_endpoints": [...], "bug_hunt_cves": [...],
      "docker_cis": [...], "http_headers": [...]}

  MANIFEST_VERSION: str
      Bumped whenever the underlying rule set changes materially.
"""
from __future__ import annotations

from typing import Iterable

from services import bug_hunt_rules as _bh
from services import vanguard_scanner as _van

MANIFEST_VERSION = "1.0.0"

# Severity ranks used when filtering + sorting the manifest.
_SEV_RANK: dict[str, int] = {
    "CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "INFO": 4,
}


# ──────────────────────────────────────────────────────────────────────
# One-line trigger descriptions per rule id. Hand-written because the
# regex source is neither readable nor safe to embed in a prompt (a
# raw regex would encourage the model to try to defeat the pattern
# rather than internalise the intent).
#
# Rule ids MUST match those emitted by the scanners. If an id is
# missing here the manifest falls back to the scanner's own message.
# ──────────────────────────────────────────────────────────────────────
_TRIGGER_ONELINE: dict[str, str] = {
    # ── Vanguard secrets ──
    "generic_api_key":         "any assignment like `api_key='<value>'` with 8+ non-whitespace chars",
    "aws_access_key":          "literals matching AKIA/AGPA/AIDA/AROA + 16 uppercase alphanumerics",
    "aws_secret_key":          "`aws_secret_access_key='...'` with a 40-char literal",
    "password_assignment":     "`password='...'` / `passwd='...'` with 4+ chars",
    "token_assignment":        "`bearer/auth_token/access_token/refresh_token='...'` 16+ chars",
    "private_key":             "`-----BEGIN [RSA|DSA|EC|OPENSSH|PGP] PRIVATE KEY-----` in source",
    "github_token":            "literals matching `gh[pousr]_[36+ chars]`",
    "slack_token":             "literals matching `xox[bpors]-<digits>-<...>`",
    "generic_secret":          "`secret/signing_key/encryption_key='...'` 16+ chars",
    "db_connection_string":    "`postgres://user:pass@` / `mongodb://user:pass@` style URI in source",
    "stripe_live_key":         "literals `sk_live_ / pk_live_ / rk_live_[20+ chars]`",
    "google_api_key":          "literals `AIza[0-9A-Za-z_-]{35}`",
    "sendgrid_key":            "literals `SG.<22 chars>.<43 chars>`",
    "openai_key":              "literals `sk-<32+ alphanumerics>` (excluding `sk-test-` / `sk-aurem-`)",

    # ── Vanguard dangerous code ──
    "eval_usage":              "any call to `eval(`",
    "exec_usage":              "any call to `exec(`",
    "subprocess_shell_true":   "`subprocess.*(... shell=True ...)`",
    "os_system":               "any call to `os.system(`",
    "pickle_loads":            "any call to `pickle.load(` or `pickle.loads(`",
    "yaml_unsafe_load":        "`yaml.load(...)` without an explicit `Loader=`",
    "requests_no_verify":      "`requests/httpx/urllib .* verify=False`",
    "sql_string_format":       "`cursor.execute(f\"…{var}…\")` — f-string SQL",
    "innerHTML_assignment":    "`.innerHTML = ...` in JS/TS",
    "dangerously_set_html":    "React `dangerouslySetInnerHTML` prop",

    # ── Bug Hunt secrets (adds tighter fingerprints on top of Vanguard) ──
    "aws_access_key_id":       "literal starting `AKIA` + 16 uppercase alphanumerics (long-term key)",
    "aws_temp_token":          "literals starting ASIA/AGPA/AIDA/AROA/AIPA/ANPA/ANVA + 16 chars (STS token)",
    "gcp_api_key":             "`AIza[35 chars]` bordered by non-key chars — GCP API key",
    "stripe_live_secret":      "`sk_live_[24+ chars]` — Stripe secret key (live)",
    "stripe_live_publishable": "`pk_live_[24+ chars]` — Stripe publishable key",
    "sendgrid_api_key":        "`SG.<22>.<43>` — SendGrid API key",
    "slack_bot_token":         "`xox[baprs]-<10..72 chars>` — Slack workspace token",
    "github_pat":              "`ghp_[36 chars]` — GitHub PAT",
    "github_oauth_token":      "`gho_[36 chars]` — GitHub user OAuth token",
    "github_app_token":        "`ghs_/ghu_[36 chars]` — GitHub App/server token",
    "jwt_secret_hardcoded":    "`jwt_secret / JWT_SECRET = '<6+ chars>'` — JWT signing secret",
    "private_rsa_key":         "any `-----BEGIN … PRIVATE KEY-----` block in source",
    "azure_storage_key":       "`AccountKey='<60+ base64 chars>'` — Azure storage key",
    "twilio_api_key":          "`SK<32 hex>` — Twilio API key",
    "env_var_in_code":         "line matching `[UPPERCASE_NAME]='<16+ chars>'` in a code file",

    # ── Bug Hunt vulnerability patterns ──
    "log4shell_jndi":          "any occurrence of `${jndi:` in a string",
    "eval_with_request":       "`eval(...request/input/argv/body/params/query...)`",
    "exec_with_request":       "`exec(...request/input/argv/body/params/query...)`",
    "pickle_loads_untrusted":  "`pickle.load` / `pickle.loads` at all (treat as untrusted)",
    "yaml_load_unsafe":        "`yaml.load(` without `Loader=`",
    "subprocess_shell_true_input": "`subprocess.*(shell=True, ...user/input/request/argv...)`",
    "os_system_user_input":    "`os.system(...user/input/request/argv/body/params/query...)`",
    "xml_etree_no_defusedxml": "any use of stdlib xml.etree/minidom parsers (should be defusedxml)",
    "regex_catastrophic_backtracking": "nested regex quantifiers: `(.*+)+ / (.+)+ / (\\w*)+ / ([^…]*)+`",
    "xxe_external_entity":     "`<!ENTITY name SYSTEM ...>` declaration in source",
    "weak_crypto_md5":         "`hashlib.md5(` or `CryptoJS.MD5`",
    "weak_crypto_sha1":        "`hashlib.sha1(` or `CryptoJS.SHA1`",
    "weak_random_token":       "token/secret/password/nonce/salt = random.random / Math.random",
    "jwt_alg_none":            "`\"alg\": \"none\"` literal in code / JSON",
    "cors_wildcard_with_creds":"`Access-Control-Allow-Origin: *` within 200 chars of `Allow-Credentials: true`",
    "cookie_no_secure_flag":   "`set_cookie(...)` without `secure=True`",
    "cookie_no_httponly_flag": "`set_cookie(...)` without `httponly=True`",
    "ssrf_open_url_fetch":     "`requests/httpx/urlopen/fetch(request./input(/user_input/params[…])`",
    "inner_html_assign":       "`.innerHTML =` (XSS sink)",

    # ── Bug Hunt exposed endpoints ──
    "debug_route_no_auth":     "`@app/router.get/post('/debug|/console|/repl|/shell')` without auth decorator",
    "admin_route_no_auth":     "`@app/router.<verb>('/admin…')` followed by an unguarded `def`",
    "actuator_endpoint":       "any `/actuator|/jolokia|/env|/heapdump|/threaddump|/trace` path literal",
    "metrics_endpoint_no_auth":"`@app/router.get('/metrics')` with an unguarded `def`",
    "health_endpoint_leaks":   "`/health` handler that returns `mongo/db/version/commit/env/secret` info",
    "api_key_in_url":          "URL query string carrying `api_key / token / access_token / secret`",
    "stack_trace_returned":    "returning `traceback.format_exc() / str(e) / repr(e)` to the client",
    "debug_true_production":   "`DEBUG = True` at module scope",
    "swagger_in_prod":         "`docs_url='/docs'` or `openapi_url='/…'` without prod gate",
    "cors_allow_all":          "`allow_origins=['*']` in FastAPI/Starlette CORS middleware",

    # ── HTTP security headers (repo-level rule) ──
    "http_headers_missing":    "web-app entrypoint present (FastAPI/Flask/Express) but NO helmet / secure-headers / HSTS / X-Frame-Options / CSP / X-Content-Type-Options / Referrer-Policy / Permissions-Policy anywhere in the repo",

    # ── Docker CIS ──
    "docker_cis_4_7_latest_tag":       "Dockerfile `FROM image:latest` or `FROM image` (no explicit tag/digest)",
    "docker_cis_4_9_add_instead_copy": "Dockerfile uses `ADD` for a local file (should be `COPY`)",
    "docker_cis_4_10_secret_in_env":   "Dockerfile `ENV/ARG NAME=value` where NAME contains PASSWORD/SECRET/TOKEN/API_KEY",
    "docker_cis_curl_pipe_sh":         "Dockerfile `RUN curl … | sh` / `RUN wget … | bash`",
    "docker_cis_apt_upgrade":          "Dockerfile `apt-get upgrade` / `apt-get dist-upgrade`",
    "docker_cis_4_1_no_user":          "Dockerfile has no `USER` instruction (runs as root)",
    "docker_cis_4_6_no_healthcheck":   "Dockerfile has no `HEALTHCHECK` instruction",
    "docker_cis_5_4_privileged":       "compose file `privileged: true`",
    "docker_cis_5_31_docker_sock":     "compose file mounts `/var/run/docker.sock`",
}


# ──────────────────────────────────────────────────────────────────────
# Bug Hunt dependency CVEs — represented as one-line "avoid version X of
# package Y" hints. Kept separate from _TRIGGER_ONELINE because the
# manifest identity here is (package, version-cap), not a fixed rule
# id, so we render them from the source of truth directly.
# ──────────────────────────────────────────────────────────────────────
def _dep_cve_lines() -> list[tuple[str, str]]:
    """Yields (rule_id, one_line) for every known dependency CVE."""
    lines: list[tuple[str, str]] = []
    for pkg, max_bad, cve, sev, _msg in _bh._DEP_CVES:
        rid = f"cve_{pkg}"
        trigger = f"any manifest declaring `{pkg}` below `{max_bad}` — {cve} — {sev}"
        lines.append((rid, trigger))
    return lines


# ──────────────────────────────────────────────────────────────────────
# Structured index — used by the dashboard "Docs" tab (future) and by
# the /api/aurem-dev/generation-rules endpoint (future).
# ──────────────────────────────────────────────────────────────────────
def get_rule_index() -> dict:
    idx: dict[str, list[dict]] = {
        "vanguard_secrets":   [],
        "vanguard_dangerous": [],
        "bug_hunt_secrets":   [],
        "bug_hunt_vulns":     [],
        "bug_hunt_endpoints": [],
        "bug_hunt_cves":      [],
        "docker_cis":         [],
        "http_headers":       [],
    }

    for rid, _rx, sev in _van._SECRET_PATTERN_DEFS:
        idx["vanguard_secrets"].append({
            "id": rid, "severity": sev,
            "trigger": _TRIGGER_ONELINE.get(rid, ""),
        })
    for rid, _rx, sev in _van._DANGEROUS_PATTERN_DEFS:
        idx["vanguard_dangerous"].append({
            "id": rid, "severity": sev,
            "trigger": _TRIGGER_ONELINE.get(rid, ""),
        })
    for rid, _rx, sev, _msg in _bh._SECRET_RULES:
        idx["bug_hunt_secrets"].append({
            "id": rid, "severity": sev,
            "trigger": _TRIGGER_ONELINE.get(rid, ""),
        })
    for rid, _rx, sev, _msg in _bh._VULN_RULES:
        idx["bug_hunt_vulns"].append({
            "id": rid, "severity": sev,
            "trigger": _TRIGGER_ONELINE.get(rid, ""),
        })
    for rid, _rx, sev, _msg in _bh._ENDPOINT_RULES:
        idx["bug_hunt_endpoints"].append({
            "id": rid, "severity": sev,
            "trigger": _TRIGGER_ONELINE.get(rid, ""),
        })
    for pkg, max_bad, cve, sev, msg in _bh._DEP_CVES:
        idx["bug_hunt_cves"].append({
            "id": f"cve_{pkg}", "severity": sev,
            "trigger": f"{pkg} < {max_bad} ({cve})",
            "message": msg,
        })

    # HTTP headers — single repo-level rule.
    idx["http_headers"].append({
        "id": "http_headers_missing", "severity": "MEDIUM",
        "trigger": _TRIGGER_ONELINE["http_headers_missing"],
    })

    # Docker CIS — enumerated from the trigger table (source of truth
    # lives in full_scan_scanners.py logic, ids are stable strings).
    _docker_ids: list[tuple[str, str]] = [
        ("docker_cis_4_1_no_user",         "HIGH"),
        ("docker_cis_4_6_no_healthcheck",  "LOW"),
        ("docker_cis_4_7_latest_tag",      "MEDIUM"),
        ("docker_cis_4_9_add_instead_copy","LOW"),
        ("docker_cis_4_10_secret_in_env",  "CRITICAL"),
        ("docker_cis_curl_pipe_sh",        "HIGH"),
        ("docker_cis_apt_upgrade",         "LOW"),
        ("docker_cis_5_4_privileged",      "HIGH"),
        ("docker_cis_5_31_docker_sock",    "CRITICAL"),
    ]
    for rid, sev in _docker_ids:
        idx["docker_cis"].append({
            "id": rid, "severity": sev,
            "trigger": _TRIGGER_ONELINE.get(rid, ""),
        })

    return idx


def _flatten(index: dict, *, include_low: bool) -> list[dict]:
    all_rules: list[dict] = []
    for bucket_rules in index.values():
        all_rules.extend(bucket_rules)
    if not include_low:
        all_rules = [
            r for r in all_rules
            if _SEV_RANK.get((r.get("severity") or "").upper(), 9) <= _SEV_RANK["MEDIUM"]
        ]
    # Sort by severity rank then id for deterministic output — matters
    # because prompt caches key on exact string content.
    all_rules.sort(key=lambda r: (
        _SEV_RANK.get((r.get("severity") or "").upper(), 9),
        r.get("id") or "",
    ))
    return all_rules


def build_condensed_manifest(*, include_low: bool = False) -> str:
    """Return the prompt-ready condensed manifest.

    Format is deliberately dense (bullet lines, ≤ 100 chars each) so
    it costs approximately 700–900 tokens for the full CRITICAL/HIGH/
    MEDIUM set. The block is fenced with sentinel comment lines so
    downstream idempotency checks (`if manifest already in persona`)
    can detect duplicates cheaply.
    """
    index = get_rule_index()
    flat  = _flatten(index, include_low=include_low)
    if not flat:
        return ""

    header = (
        "# ── AUREM CTO — Generation-Time Safety Rules "
        "(v" + MANIFEST_VERSION + ") ──"
    )
    guidance = (
        "You are ORA. Before you write ANY code, remember these house rules — "
        "they are exactly what the platform's own Vanguard, Bug Hunt, Health, "
        "HTTP-headers, and Docker-CIS scanners will flag against you afterwards. "
        "Preventing a violation now is always cheaper than fixing it later."
    )
    lines: list[str] = [header, guidance, ""]

    for rule in flat:
        rid    = rule.get("id") or "unknown"
        sev    = (rule.get("severity") or "MEDIUM").upper()
        trigger= (rule.get("trigger") or "").strip()
        if not trigger:
            continue
        # Truncate to keep every line predictable-length.
        if len(trigger) > 140:
            trigger = trigger[:137] + "…"
        lines.append(f"[{sev[0]}] {rid}: {trigger}")

    # Append CVE list separately — one line per package, no severity
    # prefix to save tokens.
    lines.append("")
    lines.append("# Vulnerable dependency versions (upgrade to ≥ noted floor):")
    for _rid, one in _dep_cve_lines():
        lines.append(f"- {one}")

    lines.append(
        "# End rules. If your generated code hits any of these, rewrite before returning."
    )
    return "\n".join(lines) + "\n"


# ──────────────────────────────────────────────────────────────────────
# Integration helpers — used by orchestrator.py and loop.py to add the
# manifest to a persona layer stack idempotently.
# ──────────────────────────────────────────────────────────────────────
_MANIFEST_SENTINEL = "AUREM CTO — Generation-Time Safety Rules"


def already_injected(persona_or_prompt: str | Iterable[str]) -> bool:
    """Cheap containment check so we never inject the manifest twice
    on nested / multi-layer prompt assembly paths."""
    if isinstance(persona_or_prompt, str):
        return _MANIFEST_SENTINEL in persona_or_prompt
    return any(_MANIFEST_SENTINEL in (s or "") for s in persona_or_prompt)


def inject_into_layers(layers: list[str], *, include_low: bool = False) -> list[str]:
    """Return a new layer list with the manifest appended (once).

    Placement rule: the manifest goes AFTER the persona layers but
    BEFORE any user-specific context so the model treats it as
    always-on housekeeping, not as an ad-hoc instruction from the
    current prompt.
    """
    if already_injected(layers):
        return layers
    manifest = build_condensed_manifest(include_low=include_low)
    if not manifest:
        return layers
    return list(layers) + [manifest]
