"""
services/full_scan_scanners.py  —  Directive Session 1 · Part B foundation
==========================================================================

Pure-function extraction of two scanners that previously lived inside
FastAPI routers (`routers/codebase_health.py` for Docker CIS,
`routers/security_scan.py` for HTTP security headers). Nothing here
depends on FastAPI request context — the extraction is done so the
Loop-Mode Full-Scan orchestrator (Session 2) can call these without
transitively importing the router layer, keeping the dependency graph
clean (services → services, not services → routers).

Public API (both scanners share the same input/output contract used
across the codebase for scan findings):

    scan_docker_cis(text_cache: dict[str, str]) -> list[dict]
    scan_http_headers(text_cache: dict[str, str]) -> list[dict]

`text_cache` is `{path: file_text}` from the shared repo walker.
Return dicts follow the finding shape documented in
`routers/codebase_health.py` (`id`, `category`, `severity`, `file`,
`line`, `title`, `message`, `fix_hint`, `fix_tokens`).

The router modules re-export these functions to preserve their public
endpoint behaviour — this file is the single source of truth going
forward.
"""
from __future__ import annotations

import re

# ══════════════════════════════════════════════════════════════════════
# DOCKER CIS BENCHMARK
# Moved verbatim from routers/codebase_health.py to keep the exact
# rule bodies + severities intact. Any behaviour change here would be
# a scope creep against the "no behaviour changes in Session 1"
# discipline.
# ══════════════════════════════════════════════════════════════════════

_DOCKER_SECRET_RX = re.compile(
    r"^\s*(?:ENV|ARG)\s+\w*(?:PASSWORD|SECRET|TOKEN|API_?KEY)\w*\s*[= ]\s*\S+",
    re.IGNORECASE,
)
_CURL_PIPE_RX = re.compile(r"\b(?:curl|wget)\b[^\n|]*\|\s*(?:ba)?sh\b")


def _norm_sev_docker(sev: str) -> str:
    s = (sev or "").upper()
    if s == "WARNING":
        return "medium"
    return s.lower() if s in {"CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"} else "medium"


def _is_dockerfile(lower_path: str) -> bool:
    base = lower_path.rsplit("/", 1)[-1]
    return base.startswith("dockerfile") or base.endswith(".dockerfile")


def _is_compose(lower_path: str) -> bool:
    base = lower_path.rsplit("/", 1)[-1]
    return base.startswith("docker-compose") and base.endswith((".yml", ".yaml"))


def _docker_finding(path: str, line: int, rid: str, sev: str,
                    msg: str, hint: str) -> dict:
    return {
        "id":         f"docker::{path}:{line}:{rid}",
        "category":   "docker",
        "severity":   _norm_sev_docker(sev),
        "file":       path, "line": line,
        "title":      rid,
        "message":    msg,
        "fix_hint":   hint,
        "fix_tokens": 5,
    }


def scan_docker_cis(text_cache: dict[str, str]) -> list[dict]:
    """CIS Docker Benchmark checks on Dockerfiles + compose files."""
    out: list[dict] = []
    for path, text in text_cache.items():
        lower = path.lower()
        lines = (text or "").splitlines()
        if _is_dockerfile(lower):
            has_user = has_healthcheck = False
            for i, ln in enumerate(lines, start=1):
                s = ln.strip()
                if re.match(r"^USER\s+\S+", s, re.IGNORECASE):
                    has_user = True
                if s.upper().startswith("HEALTHCHECK"):
                    has_healthcheck = True
                m = re.match(r"^FROM\s+(\S+)", s, re.IGNORECASE)
                if m:
                    img = m.group(1)
                    if img.endswith(":latest") or (":" not in img and "@" not in img and img.lower() != "scratch"):
                        out.append(_docker_finding(path, i, "docker_cis_4_7_latest_tag", "MEDIUM",
                            f"CIS 4.7 — base image `{img}` is unpinned (latest/no tag): builds are not reproducible and can pull vulnerable updates.",
                            "Pin the base image to a specific version tag or digest, e.g. `python:3.11-slim@sha256:…`."))
                if re.match(r"^ADD\s+(?!--)", s, re.IGNORECASE) and "http" not in s.lower():
                    out.append(_docker_finding(path, i, "docker_cis_4_9_add_instead_copy", "LOW",
                        "CIS 4.9 — `ADD` auto-extracts archives and fetches URLs; prefer the explicit `COPY`.",
                        "Replace `ADD` with `COPY` unless you specifically need archive extraction."))
                if _DOCKER_SECRET_RX.match(s):
                    out.append(_docker_finding(path, i, "docker_cis_4_10_secret_in_env", "CRITICAL",
                        "CIS 4.10 — secret baked into the image via ENV/ARG; anyone with the image can read it.",
                        "Pass secrets at runtime (env vars / secret manager / BuildKit `--mount=type=secret`), never in the Dockerfile."))
                if s.upper().startswith("RUN") and _CURL_PIPE_RX.search(s):
                    out.append(_docker_finding(path, i, "docker_cis_curl_pipe_sh", "HIGH",
                        "Piping curl/wget straight into a shell executes unverified remote code at build time.",
                        "Download the script, verify its checksum/signature, then execute it."))
                if re.search(r"apt(-get)?\s+(dist-)?upgrade", s):
                    out.append(_docker_finding(path, i, "docker_cis_apt_upgrade", "LOW",
                        "CIS 4.7 — `apt upgrade` in a Dockerfile makes builds non-deterministic.",
                        "Use a newer pinned base image instead of upgrading packages at build time."))
            if lines and not has_user:
                out.append(_docker_finding(path, 1, "docker_cis_4_1_no_user", "HIGH",
                    "CIS 4.1 — no `USER` instruction: the container runs as root.",
                    "Create an unprivileged user and add `USER appuser` before the final CMD/ENTRYPOINT."))
            if lines and not has_healthcheck:
                out.append(_docker_finding(path, 1, "docker_cis_4_6_no_healthcheck", "LOW",
                    "CIS 4.6 — no `HEALTHCHECK` instruction: orchestrators can't detect a hung container.",
                    "Add e.g. `HEALTHCHECK CMD curl -f http://localhost:8000/health || exit 1`."))
        elif _is_compose(lower):
            for i, ln in enumerate(lines, start=1):
                s = ln.strip()
                if re.match(r"^privileged\s*:\s*true", s, re.IGNORECASE):
                    out.append(_docker_finding(path, i, "docker_cis_5_4_privileged", "HIGH",
                        "CIS 5.4 — `privileged: true` gives the container full host access.",
                        "Remove `privileged: true`; grant only the specific `cap_add` capabilities needed."))
                if "/var/run/docker.sock" in s:
                    out.append(_docker_finding(path, i, "docker_cis_5_31_docker_sock", "CRITICAL",
                        "CIS 5.31 — mounting the Docker socket lets the container control the host's Docker daemon (root escape).",
                        "Remove the docker.sock mount; use a scoped proxy (e.g. tecnativa/docker-socket-proxy) if API access is required."))
    return out


# ══════════════════════════════════════════════════════════════════════
# HTTP SECURITY HEADERS
# Moved verbatim from routers/security_scan.py.
# Return shape here matches the security_scan finding contract (with
# `rule_id` / `vuln`) since that endpoint's callers depend on those
# exact keys. The Full-Scan orchestrator normalises across shapes at
# aggregation time.
# ══════════════════════════════════════════════════════════════════════

_HDR_SIGNALS = re.compile(
    r"helmet\s*\(|secure_headers|SecureHeaders|SecurityMiddleware"
    r"|Strict-Transport-Security|X-Frame-Options|Content-Security-Policy"
    r"|X-Content-Type-Options|Referrer-Policy|Permissions-Policy",
    re.IGNORECASE,
)
_APP_ENTRIES: list[tuple[re.Pattern, tuple, str]] = [
    (re.compile(r"\bFastAPI\s*\("), (".py",), "FastAPI app"),
    (re.compile(r"\bFlask\s*\(\s*__name__"), (".py",), "Flask app"),
    (re.compile(r"\bexpress\s*\(\s*\)"), (".js", ".ts", ".mjs"), "Express app"),
]


def scan_http_headers(text_cache: dict[str, str]) -> list[dict]:
    """Repo-level finding: web app without HTTP security headers."""
    if any(_HDR_SIGNALS.search(t or "") for t in text_cache.values()):
        return []
    findings: list[dict] = []
    for path, text in text_cache.items():
        if not text:
            continue
        lower = path.lower()
        for rx, exts, label in _APP_ENTRIES:
            if not lower.endswith(exts):
                continue
            m = rx.search(text)
            if not m:
                continue
            line = text[:m.start()].count("\n") + 1
            findings.append({
                "rule_id":  "http_headers_missing",
                "vuln":     "http_headers",
                "severity": "medium",
                "file":     path,
                "line":     line,
                "snippet":  text.splitlines()[line - 1].strip()[:200],
                "desc":     (f"{label} found but no HTTP security headers set anywhere "
                             "in the repo (HSTS, X-Frame-Options, CSP, "
                             "X-Content-Type-Options). Add a security-headers "
                             "middleware (helmet / secure-headers)."),
            })
            if len(findings) >= 3:
                return findings
            break
    return findings
