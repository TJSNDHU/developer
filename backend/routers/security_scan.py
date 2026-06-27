"""
routers/security_scan.py — Iter 212m-55
Lightweight, regex-based vulnerability scanner for the user's
connected GitHub repository. Findings-only (no auto-apply, per
founder's explicit decision in iter 212m-55 planning).

Covers 7 vuln classes, all static analysis (zero LLM, zero E2B):
  • SSTI               — Jinja2/Mako/Tornado template render of user input
  • ReDoS              — known catastrophic regex patterns
  • LPDoS              — endpoints without body size limits
  • Secret-key leak    — hardcoded keys in source
  • NoSQL injection    — raw query dicts from request body
  • SQL injection      — string-concat SQL queries
  • Clipboard / Replay — minor heuristic detectors

Designed to finish in <15 s on a 5K-file repo by walking the GitHub
tree via the same PAT helpers the indexer already uses.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import re
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Header

from cto_services.auth import current_dev
from cto_services.db import get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/security-scan", tags=["Security Scan"])


# ─── Static rule library — each rule is a (id, severity, pattern) ───
# Severity: critical / high / medium / low — purely advisory; the UI
# colour-codes by this. Patterns are kept tight (anchors, word
# boundaries) to keep false-positive rate down on real-world code.

_RULES: list[dict] = [
    # ── Secret-key leak ──
    {"id": "secret_aws_access_key",   "vuln": "secret_leak", "severity": "critical",
     "pattern": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
     "desc": "Hardcoded AWS access key id"},
    {"id": "secret_openai_key",       "vuln": "secret_leak", "severity": "critical",
     "pattern": re.compile(r"\bsk-[a-zA-Z0-9]{32,}\b"),
     "desc": "Hardcoded OpenAI / DeepSeek style API key"},
    {"id": "secret_github_pat",       "vuln": "secret_leak", "severity": "critical",
     "pattern": re.compile(r"\bghp_[A-Za-z0-9]{30,}\b"),
     "desc": "Hardcoded GitHub Personal Access Token"},
    {"id": "secret_stripe_live",      "vuln": "secret_leak", "severity": "critical",
     "pattern": re.compile(r"\bsk_live_[A-Za-z0-9]{20,}\b"),
     "desc": "Hardcoded Stripe LIVE secret key"},
    {"id": "secret_private_key",      "vuln": "secret_leak", "severity": "critical",
     "pattern": re.compile(r"-----BEGIN (RSA |EC |OPENSSH |)?PRIVATE KEY-----"),
     "desc": "Embedded private key block"},

    # ── SSTI ──
    {"id": "ssti_jinja_user_render",  "vuln": "ssti", "severity": "high",
     "pattern": re.compile(r"Template\(\s*request\.|Template\(\s*body\.|render_template_string\("),
     "desc": "Server-side template render of user-controlled input"},

    # ── SQL injection ──
    {"id": "sql_string_format",       "vuln": "sql_injection", "severity": "critical",
     "pattern": re.compile(r"""(execute|executemany)\s*\(\s*[fF]?["'][^"']*\{[^}]+\}"""),
     "desc": "f-string SQL query — use parameterised cursors"},
    {"id": "sql_percent_format",      "vuln": "sql_injection", "severity": "high",
     "pattern": re.compile(r"""(execute|executemany)\s*\(\s*["'][^"']*%s[^"']*["']\s*%\s*"""),
     "desc": "%-format SQL query — use cursor.execute(query, params)"},

    # ── NoSQL injection ──
    {"id": "nosql_where_operator",    "vuln": "nosql_injection", "severity": "high",
     "pattern": re.compile(r"""["']\$where["']\s*:"""),
     "desc": "MongoDB $where allows arbitrary JS execution"},
    {"id": "nosql_raw_body_query",    "vuln": "nosql_injection", "severity": "medium",
     "pattern": re.compile(r"""\.find\(\s*(request\.json|body\.dict|body\.\*\*|\*\*body|\*\*payload)"""),
     "desc": "Mongo query built from raw request body"},

    # ── ReDoS — known catastrophic patterns ──
    {"id": "redos_nested_quantifier", "vuln": "redos", "severity": "high",
     "pattern": re.compile(r"""re\.(compile|match|search|sub)\s*\(\s*r?["'][^"']*\([^)]*[+*][^)]*\)[+*]"""),
     "desc": "Nested quantifier — vulnerable to catastrophic backtracking"},

    # ── LPDoS ──
    {"id": "lpdos_no_body_limit_fastapi", "vuln": "lpdos", "severity": "medium",
     "pattern": re.compile(r"@(app|router)\.(post|put|patch)\("),
     "desc": "FastAPI write endpoint — confirm body size middleware is mounted",
     "max_per_file": 1},

    # ── Clipboard ──
    {"id": "clipboard_external_paste", "vuln": "clipboard", "severity": "low",
     "pattern": re.compile(r"navigator\.clipboard\.readText\s*\("),
     "desc": "Reads clipboard — sanitise before rendering as code"},

    # ── Replay attack ──
    {"id": "replay_jwt_no_jti",       "vuln": "replay", "severity": "medium",
     "pattern": re.compile(r"""jwt\.encode\(\s*\{[^}]*\}"""),
     "desc": "JWT signed without jti — add unique id + iat for replay defence",
     "post_filter": lambda m: ('"jti"' not in m and "'jti'" not in m)},
]


# File extensions worth scanning. Skipping binary, vendored, build.
_SCAN_EXTS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".env", ".env.example",
    ".yml", ".yaml", ".json", ".sh", ".sql", ".php", ".rb", ".go",
}
_SKIP_DIRS = {"node_modules", ".git", "dist", "build", ".next",
              "venv", ".venv", "__pycache__", ".cache", "coverage"}
_MAX_FILES = 600              # safety cap for huge repos
_MAX_BYTES_PER_FILE = 256_000  # skip files larger than 256 KB
_GH_API = "https://api.github.com"
_GH_TIMEOUT = 20.0
_CONCURRENT_FETCHES = 8        # parallel raw-content fetches


# ─── PAT decrypt (shared with cto_projects router) ────────────────────
async def _decrypt_pat(user_id: str, token: Optional[str]) -> Optional[str]:
    if not token:
        return token
    if not token.startswith("v1:"):
        return token
    try:
        from services.vault import decrypt
        return await decrypt(user_id, token, kind="github_token")
    except Exception:
        return None


def _gh_headers(pat: str) -> dict:
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {pat}",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _scan_text(path: str, text: str) -> list[dict]:
    """Run all rules over one file's content; return findings list."""
    findings: list[dict] = []
    per_file_count: dict[str, int] = {}
    for line_idx, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        # Skip lines flagged with vanguard:ignore (same convention as
        # the commit-time scanner).
        if "vanguard: ignore" in line or "security-scan: ignore" in line:
            continue
        for rule in _RULES:
            m = rule["pattern"].search(line)
            if not m:
                continue
            pf = rule.get("post_filter")
            if pf and not pf(m.group(0)):
                continue
            cap = rule.get("max_per_file")
            if cap:
                seen = per_file_count.get(rule["id"], 0)
                if seen >= cap:
                    break
                per_file_count[rule["id"]] = seen + 1
            findings.append({
                "rule_id":  rule["id"],
                "vuln":     rule["vuln"],
                "severity": rule["severity"],
                "file":     path,
                "line":     line_idx,
                "snippet":  line.strip()[:200],
                "desc":     rule["desc"],
            })
            break
    return findings


async def _gh_get(client: httpx.AsyncClient, url: str, pat: str):
    r = await client.get(url, headers=_gh_headers(pat), timeout=_GH_TIMEOUT)
    if r.status_code == 401:
        raise HTTPException(401, "github_pat_invalid")
    if r.status_code == 404:
        raise HTTPException(404, "github_repo_not_found")
    if r.status_code >= 500:
        raise HTTPException(502, f"github_upstream_{r.status_code}")
    r.raise_for_status()
    return r.json()


async def _list_repo_tree(client: httpx.AsyncClient, owner: str, repo: str, pat: str) -> list[dict]:
    """One GitHub API hit for tree; we cap at _MAX_FILES below."""
    repo_meta = await _gh_get(client, f"{_GH_API}/repos/{owner}/{repo}", pat)
    branch = repo_meta.get("default_branch") or "main"
    tree = await _gh_get(
        client,
        f"{_GH_API}/repos/{owner}/{repo}/git/trees/{branch}?recursive=1",
        pat,
    )
    return [t for t in (tree.get("tree") or []) if t.get("type") == "blob"]


async def _fetch_file(
    client: httpx.AsyncClient, owner: str, repo: str, path: str, pat: str,
) -> str:
    """Fetch raw content via contents API. Returns "" on failure so the
    scan keeps going for the rest of the repo."""
    try:
        r = await client.get(
            f"{_GH_API}/repos/{owner}/{repo}/contents/{path}",
            headers=_gh_headers(pat), timeout=_GH_TIMEOUT,
        )
        if r.status_code != 200:
            return ""
        body = r.json()
        if body.get("encoding") == "base64" and body.get("content"):
            return base64.b64decode(body["content"]).decode("utf-8", errors="ignore")
    except Exception as e:
        logger.debug("fetch failed %s: %r", path, e)
    return ""


@router.post("/run")
async def run_security_scan(
    body: dict, authorization: Optional[str] = Header(None),
) -> dict:
    """One-click static scanner. Body shape: {"project_id": "..."}.
    Returns: {"ok": True, "summary": {…counts…}, "findings": [...]}.
    No auto-apply — caller renders + asks the user to fix manually."""
    user = await current_dev(authorization)
    user_id = user["user_id"]
    project_id = (body or {}).get("project_id")
    if not project_id:
        raise HTTPException(400, "project_id required")
    db = get_db()
    if db is None:
        raise HTTPException(503, "DB not connected")
    proj = await db.cto_projects.find_one(
        {"project_id": project_id, "user_id": user_id},
        {"_id": 0, "github_owner": 1, "github_repo": 1, "github_token": 1},
    )
    if not proj:
        raise HTTPException(404, "Project not found")
    owner = proj.get("github_owner") or ""
    repo  = proj.get("github_repo") or ""
    pat   = await _decrypt_pat(user_id, proj.get("github_token"))
    if not (owner and repo and pat):
        raise HTTPException(400, "Project missing GitHub linkage / PAT")

    async with httpx.AsyncClient() as client:
        # 1. List repo tree.
        try:
            blobs = await _list_repo_tree(client, owner, repo, pat)
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(502, f"GitHub tree read failed: {e!r}")

        # 2. Filter to scannable files.
        candidates: list[dict] = []
        for b in blobs:
            path = b.get("path", "")
            if not path:
                continue
            parts = path.split("/")
            if any(p in _SKIP_DIRS for p in parts):
                continue
            lower = path.lower()
            if not any(lower.endswith(ext) for ext in _SCAN_EXTS):
                continue
            if b.get("size", 0) > _MAX_BYTES_PER_FILE:
                continue
            candidates.append(b)
            if len(candidates) >= _MAX_FILES:
                break

        # 3. Fetch + scan each file with bounded concurrency.
        sem = asyncio.Semaphore(_CONCURRENT_FETCHES)

        async def _scan_one(blob: dict) -> list[dict]:
            async with sem:
                text = await _fetch_file(client, owner, repo, blob["path"], pat)
            if not text:
                return []
            return _scan_text(blob["path"], text)

        results = await asyncio.gather(
            *[_scan_one(b) for b in candidates], return_exceptions=False,
        )

    findings: list[dict] = [f for sub in results for f in sub]

    # 4. Summary counts per vuln class for the UI.
    summary: dict = {"total": len(findings), "by_severity": {}, "by_vuln": {}}
    for f in findings:
        summary["by_severity"][f["severity"]] = summary["by_severity"].get(f["severity"], 0) + 1
        summary["by_vuln"][f["vuln"]] = summary["by_vuln"].get(f["vuln"], 0) + 1

    # 5. Sort findings — critical → high → medium → low, then by file.
    sev_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    findings.sort(key=lambda x: (sev_rank.get(x["severity"], 9), x["file"], x["line"]))

    logger.info(
        "security_scan project=%s files_scanned=%d findings=%d",
        project_id, len(candidates), len(findings),
    )
    return {
        "ok":              True,
        "scanned_files":   len(candidates),
        "summary":         summary,
        "findings":        findings[:500],   # cap UI payload
        "truncated":       len(findings) > 500,
    }
