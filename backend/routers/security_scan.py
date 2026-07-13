"""
routers/security_scan.py — Iter 212m-55 / 212m-66
Lightweight, regex-based vulnerability scanner for the user's
connected GitHub repository.

Iter 212m-66 — adds the optional Vanguard TWO-ROUND deep-scan mode
plus an AI-generated structured remediation report and optional
draft-PR creation. Both new features are opt-in via request flags
(`two_round`, `auto_pr`) — existing callers see ZERO behavioural
change when the flags are omitted (response shape only gains new
fields, never loses any).

Single-pass scanner findings-only (no auto-apply, per founder's
explicit decision in iter 212m-55 planning).

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
import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Header

from cto_services.auth import current_dev, require_admin
from cto_services.db import get_db
# Iter 212m-66 — two-round deep scanner.
from services.vanguard_scanner import run_two_round_scan

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


# ─── HTTP Security Headers — repo-level check ─────────────────────────
# Iter 212m-190 — Rule body extracted to
# `services/full_scan_scanners.py` so Loop-Mode Full Scan can call it
# without importing the router layer. This module keeps the same
# public `_scan_http_headers` symbol so existing call sites in
# security_scan endpoints continue to work.
from services.full_scan_scanners import scan_http_headers as _scan_http_headers_service


def _scan_http_headers(text_cache: dict[str, str]) -> list[dict]:
    return _scan_http_headers_service(text_cache)


async def _gh_get(client: httpx.AsyncClient, url: str, pat: str):
    """GitHub GET with meaningful error propagation.

    Iter 212m-216 — Before this iter, ANY non-200 that wasn't 401/
    404/5xx (notably 403 secondary rate-limits and 422 empty-repo
    errors) hit `r.raise_for_status()`, which threw an
    `HTTPStatusError`.  That was then caught by the outer wrap in
    `codebase_health.scan()` (`except Exception → HTTPException(502)`),
    and Cloudflare's 5xx intercept replaced our JSON body with a
    branded HTML "Bad gateway" page.  Users saw a raw 1.3s 502 with
    no clue why — the actual reason (rate limit, empty repo, etc.)
    never reached the browser.

    We now branch on every meaningful GH status explicitly.  Rate
    limits become 429 with `retry_after` so the frontend can back
    off.  Empty / mis-branched repos surface 422.  Auth / permission
    failures surface 401/403 with the real GitHub reason.  Only
    genuine upstream 5xx bubbles as 502.
    """
    try:
        r = await client.get(url, headers=_gh_headers(pat), timeout=_GH_TIMEOUT)
    except httpx.TimeoutException as e:
        raise HTTPException(504, f"github_upstream_timeout: {e!s}")
    except httpx.RequestError as e:
        # DNS / TLS / connection reset — never our fault, never the
        # caller's fault.  Bubble as 502 with the real error class so
        # a founder can debug from prod logs.
        raise HTTPException(502, f"github_transport_{type(e).__name__}: {e!s}")

    sc = r.status_code
    if sc == 200:
        try:
            return r.json()
        except Exception as e:
            raise HTTPException(502, f"github_bad_json: {e!s}")

    # ── Meaningful GH statuses — surface the actual reason ─────────
    # Extract GitHub's own error message so the client sees
    # `"detail": "github_rate_limited: API rate limit exceeded ..."`
    # not a blanket "Bad gateway".
    gh_msg = ""
    try:
        j = r.json()
        gh_msg = (j.get("message") or "")[:200]
    except Exception:
        gh_msg = (r.text or "")[:200]

    if sc == 401:
        raise HTTPException(401, f"github_pat_invalid: {gh_msg}"
                            if gh_msg else "github_pat_invalid")
    if sc == 403:
        # 403 on GH is nearly always a rate limit (primary or
        # secondary) or SSO-restricted org.  Distinguish so the UI
        # can render the right toast.
        remaining = r.headers.get("x-ratelimit-remaining")
        reset     = r.headers.get("x-ratelimit-reset")
        if remaining == "0" and reset:
            try:
                import time as _t
                retry_after = max(1, int(reset) - int(_t.time()))
            except Exception:
                retry_after = 60
            raise HTTPException(429, {
                "error":               "github_rate_limited",
                "message":             f"GitHub API rate limit exhausted. "
                                        f"Retry in ~{retry_after}s.",
                "retry_after_seconds": retry_after,
                "github_message":      gh_msg,
            })
        # Secondary rate limit or org-restricted PAT
        raise HTTPException(403, f"github_forbidden: {gh_msg}"
                            if gh_msg else "github_forbidden")
    if sc == 404:
        raise HTTPException(404, f"github_repo_not_found: {gh_msg}"
                            if gh_msg else "github_repo_not_found")
    if sc == 409:
        # Empty repo — /git/trees fails with 409 "Git Repository is empty"
        raise HTTPException(422, f"github_repo_empty: {gh_msg}"
                            if gh_msg else "github_repo_empty")
    if sc == 422:
        # Bad ref, missing branch, or default_branch missing.
        raise HTTPException(422, f"github_bad_ref: {gh_msg}"
                            if gh_msg else "github_bad_ref")
    if sc == 451:
        raise HTTPException(451, f"github_unavailable_for_legal: {gh_msg}"
                            if gh_msg else "github_unavailable_for_legal")
    if sc >= 500:
        raise HTTPException(502, f"github_upstream_{sc}: {gh_msg}"
                            if gh_msg else f"github_upstream_{sc}")
    # Anything else — treat as an actionable client error, but include
    # the exact code so we can debug from a screenshot.
    raise HTTPException(sc if 400 <= sc < 500 else 502,
                        f"github_unexpected_{sc}: {gh_msg}"
                        if gh_msg else f"github_unexpected_{sc}")


async def _list_repo_tree(client: httpx.AsyncClient, owner: str, repo: str, pat: str) -> list[dict]:
    """One GitHub API hit for tree; we cap at _MAX_FILES below."""
    blobs, _sha = await _list_repo_tree_with_sha(client, owner, repo, pat)
    return blobs


async def _list_repo_tree_with_sha(
    client: httpx.AsyncClient, owner: str, repo: str, pat: str,
) -> tuple[list[dict], str]:
    """Same as `_list_repo_tree` but also returns the GitHub tree SHA
    so callers can key a content-addressed cache.  Iter 212m-79."""
    repo_meta = await _gh_get(client, f"{_GH_API}/repos/{owner}/{repo}", pat)
    branch = repo_meta.get("default_branch") or "main"
    tree = await _gh_get(
        client,
        f"{_GH_API}/repos/{owner}/{repo}/git/trees/{branch}?recursive=1",
        pat,
    )
    sha = tree.get("sha") or ""
    blobs = [t for t in (tree.get("tree") or []) if t.get("type") == "blob"]
    return blobs, sha


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
    """One-click static scanner.

    Body shape (all optional except project_id):
      • project_id  — str   — required, the connected repo we scan
      • two_round   — bool  — Iter 212m-66: opt into deep two-round
                              Vanguard scan (R1 surface + R2 deep w/
                              ±10-line context + chain detection).
                              Default False → legacy single-pass.
      • auto_pr     — bool  — Iter 212m-66: when True AND the report
                              contains at least one `pr_ready` fix
                              AND the project has a valid GitHub PAT,
                              open a DRAFT pull-request listing every
                              fix.  Never force-merges.

    Returns: {"ok": True, "summary": {…counts…}, "findings": [...]}.
    No auto-apply — caller renders + asks the user to fix manually.

    Iter 212m-158 — Admin/founder gate.  Non-admin callers get a
    clean 403 here instead of leaking the scan UX through.  Matches
    the frontend route guards shipped in iter 212m-157.
    """
    user = await require_admin(authorization)
    user_id = user["user_id"]
    project_id = (body or {}).get("project_id")
    two_round  = bool((body or {}).get("two_round", False))
    auto_pr    = bool((body or {}).get("auto_pr", False))
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
    # Iter 212m-102 — Fallback to the user's GitHub OAuth access_token
    # when the project row has no per-project PAT. OAuth tokens carry
    # `repo, read:user, user:email` scopes (see services/github_oauth.py
    # SCOPES) which is enough for the static scanner's tree/contents
    # reads. Without this fallback, every OAuth-only user hits
    # "Project missing GitHub linkage / PAT" even when their GitHub
    # account is fully connected.
    if not pat:
        try:
            u = await db.dev_users.find_one(
                {"user_id": user_id}, {"_id": 0, "github": 1},
            )
            pat = ((u or {}).get("github") or {}).get("access_token") or None
        except Exception:
            pat = None
    if not (owner and repo):
        raise HTTPException(
            400,
            "Project is not linked to a GitHub repo. Connect a repo in Settings.",
        )
    if not pat:
        raise HTTPException(
            400,
            "No GitHub credentials found. Add a PAT to this project, "
            "or connect GitHub in Settings.",
        )

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

        # Cache the text we fetch so two_round mode can reuse it for
        # the deep R2 pass without re-hitting the GitHub API.
        text_cache: dict[str, str] = {}

        async def _scan_one(blob: dict) -> list[dict]:
            async with sem:
                text = await _fetch_file(client, owner, repo, blob["path"], pat)
            if not text:
                return []
            text_cache[blob["path"]] = text
            return _scan_text(blob["path"], text)

        results = await asyncio.gather(
            *[_scan_one(b) for b in candidates], return_exceptions=False,
        )

    findings: list[dict] = [f for sub in results for f in sub]

    # 3a-bis — HTTP Security Headers repo-level check (vuln class
    # `http_headers`). Uses the already-fetched text_cache; zero extra
    # GitHub calls.
    try:
        findings.extend(_scan_http_headers(text_cache))
    except Exception as e:
        logger.warning("http_headers check failed: %r", e)

    # 3b. Iter 212m-66 — opt-in deep two-round Vanguard scan.
    # Runs the new vanguard_scanner.run_two_round_scan() over the
    # already-fetched text_cache (no extra GitHub calls).  We MERGE
    # its `combined` findings into the existing list (deduplicated
    # by file+line+rule), so the response stays a single unified
    # list.  Each new finding carries `source="vanguard_deep"` or
    # `source="vanguard_chain"` so callers can distinguish.
    two_round_block: Optional[dict] = None
    if two_round and text_cache:
        try:
            tr_result = run_two_round_scan(text_cache)
            tr_combined = _normalize_findings(tr_result.get("combined") or [])
            existing_keys = {
                (f.get("file") or "", int(f.get("line") or 0),
                 f.get("rule_id") or f.get("name") or "")
                for f in findings
            }
            for f in tr_combined:
                key = (f.get("file") or "", int(f.get("line") or 0),
                       f.get("rule_id") or "")
                if key in existing_keys:
                    continue
                findings.append(f)
                existing_keys.add(key)
            two_round_block = {
                "round1_count":    len(tr_result.get("round1_findings") or []),
                "round2_count":    len(tr_result.get("round2_findings") or []),
                "chain_count":     len(tr_result.get("chain_findings") or []),
                "round2_skipped":  bool(tr_result.get("round2_skipped")),
                "files_round1":    int(tr_result.get("files_round1") or 0),
                "files_round2":    int(tr_result.get("files_round2") or 0),
                "elapsed_seconds": float(tr_result.get("elapsed_seconds") or 0),
            }
        except Exception as e:                       # never block the scan
            logger.warning("two_round scan failed: %r", e)
            two_round_block = {"error": f"two_round_failed: {e!r}"}

    # Iter 212m-193 — split out findings already fixed (draft-PR
    # commits) so a re-run doesn't resurrect them.
    from services.fixed_findings import get_fixed_map, split_findings
    _fixed_map = await get_fixed_map(db, user_id=user_id, project_id=project_id)
    findings, fixed_findings = split_findings(findings, _fixed_map)

    # 4. Summary counts per vuln class for the UI.
    summary: dict = {"total": len(findings), "by_severity": {}, "by_vuln": {},
                     "fixed": len(fixed_findings)}
    for f in findings:
        sev = (f.get("severity") or "").lower()
        vuln = f.get("vuln") or f.get("source") or "other"
        summary["by_severity"][sev]  = summary["by_severity"].get(sev, 0) + 1
        summary["by_vuln"][vuln]     = summary["by_vuln"].get(vuln, 0) + 1

    # 5. Sort findings — critical → high → medium → low, then by file.
    sev_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
    findings.sort(key=lambda x: (
        sev_rank.get((x.get("severity") or "").lower(), 9),
        x.get("file") or "", int(x.get("line") or 0),
    ))

    logger.info(
        "security_scan project=%s files_scanned=%d findings=%d two_round=%s",
        project_id, len(candidates), len(findings), two_round,
    )

    # Iter 212m-129 — Learning hook: record per-rule + per-severity
    # histogram for this Vanguard scan run.  Best-effort, never raises.
    try:
        from services import ora_fix_learning as _ofl
        _rule_counts: dict[str, int] = {}
        _sev_counts:  dict[str, int] = {
            "critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0,
        }
        for _f in findings:
            _rid = (_f.get("rule_id") or _f.get("vuln")
                    or _f.get("title") or "unknown")
            _rule_counts[_rid] = _rule_counts.get(_rid, 0) + 1
            _sv = (_f.get("severity") or "").lower()
            if _sv in _sev_counts:
                _sev_counts[_sv] += 1
        await _ofl.record_scan_run(
            db, user_id=user_id, project_id=project_id,
            scanner="vanguard",
            categories=[],
            files_scanned=len(candidates),
            counts=_sev_counts,
            rule_counts=_rule_counts,
            duration_ms=None,
            score=None,
        )
    except Exception as _e:
        logger.debug("learning scan-run hook (vanguard) soft-failed: %r", _e)

    response: dict = {
        "ok":              True,
        "scan_mode":       "two_round" if two_round else "single_round",
        "scanned_files":   len(candidates),
        "summary":         summary,
        "findings":        findings[:500],   # cap UI payload
        "fixed_findings":  fixed_findings[:200],
        "fixed_count":     len(fixed_findings),
        "truncated":       len(findings) > 500,
    }
    if two_round_block is not None:
        response["two_round"] = two_round_block

    # 6. Iter 212m-66 — AI remediation report.
    # Runs unconditionally for two_round and ONLY when there is at
    # least one finding to fix.  10-second timeout — failure is soft
    # (we add `report_status: failed` and never block the scan).
    if findings and (two_round or auto_pr):
        report, report_status = await _generate_remediation_report(
            findings,
            repo_context={"owner": owner, "repo": repo,
                          "project_id": project_id,
                          "scanned_files": len(candidates)},
        )
        response["remediation_report"] = report
        response["report_status"]      = report_status

        # 7. Iter 212m-66 — Optional draft-PR creation.
        # We only attempt the PR when caller asked for it, the LLM
        # produced a usable report and the repo has a writeable PAT
        # (already verified above).  Failures degrade gracefully —
        # `pr_url: null` + `pr_error` in the response.
        if auto_pr:
            pr_url, pr_error = await _create_draft_pr(
                owner=owner, repo=repo, pat=pat,
                report=report,
                fallback_findings=findings,
            )
            response["pr_url"]   = pr_url
            if pr_error:
                response["pr_error"] = pr_error
    elif auto_pr and not findings:
        response["pr_url"] = None
        response["pr_error"] = "no findings to fix"

    return response


# ─── Iter 212m-66 — Internal helpers ──────────────────────────────────

def _normalize_findings(findings: list[dict]) -> list[dict]:
    """Normalise Vanguard-format findings into the security_scan
    response shape (rule_id / vuln / file / line / severity / desc).

    The two scanners emit slightly different key names — this helper
    smooths them so the UI sees one consistent payload regardless of
    which round produced the hit."""
    out: list[dict] = []
    for f in findings or []:
        rule_id = (f.get("rule_id") or f.get("name") or f.get("rule")
                   or "unknown").strip()
        # Heuristic vuln-class mapping: secret_* → secret_leak,
        # sql_*  → sql_injection, nosql_* → nosql_injection,
        # redos_* → redos, lpdos_* → lpdos, ssti_* → ssti,
        # chain_* → chain, eval/exec/yaml/os → dangerous_code
        if rule_id.startswith("secret_") or rule_id in (
            "generic_api_key", "aws_access_key", "aws_secret_key",
            "password_assignment", "token_assignment", "private_key",
            "github_token", "slack_token", "generic_secret",
            "db_connection_string", "stripe_live_key", "stripe_test_key",
            "google_api_key", "sendgrid_key", "openai_key",
        ):
            vuln = "secret_leak"
        elif rule_id.startswith("sql_"):
            vuln = "sql_injection"
        elif rule_id.startswith("nosql_"):
            vuln = "nosql_injection"
        elif rule_id.startswith("redos_"):
            vuln = "redos"
        elif rule_id.startswith("lpdos_"):
            vuln = "lpdos"
        elif rule_id.startswith("ssti_"):
            vuln = "ssti"
        elif rule_id.startswith("chain_"):
            vuln = "chain"
        elif rule_id.startswith("clipboard"):
            vuln = "clipboard"
        elif rule_id.startswith("replay"):
            vuln = "replay"
        elif rule_id.startswith("python_syntax_error"):
            vuln = "syntax_error"
        else:
            vuln = "dangerous_code"
        sev = (f.get("severity") or "").lower() or "medium"
        out.append({
            "rule_id":       rule_id,
            "vuln":          vuln,
            "severity":      sev,
            "file":          f.get("file") or f.get("filepath") or "",
            "line":          int(f.get("line") or 0),
            "snippet":       f.get("snippet") or "",
            "desc":          f.get("desc") or f.get("message") or "",
            "source":        f.get("source") or "vanguard",
            **({"context_lines": f.get("context_lines")}
               if f.get("context_lines") else {}),
            **({"contributing": f.get("contributing")}
               if f.get("contributing") else {}),
            **({"escalated": True} if f.get("escalated") else {}),
        })
    return out


async def _generate_remediation_report(
    findings: list[dict],
    repo_context: dict,
) -> tuple[dict, str]:
    """Call ORA (Swift mode = cheapest GLM path) to produce a
    structured JSON remediation report.

    Returns `(report_dict, status_string)`.  `status_string` is one of
    `"ok"`, `"failed"`, `"timeout"` — never raises.

    The LLM is given a hard 10-second budget; on any error we return
    an empty stub so the caller can still respond with the scan
    findings untouched.
    """
    # Cap the findings sent to the LLM so the prompt stays small.
    # The UI already shows the full list; the LLM only needs the
    # top-N to write a useful PR body.
    MAX_FINDINGS_FOR_LLM = 25
    trimmed = findings[:MAX_FINDINGS_FOR_LLM]
    summary_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
    for f in findings:
        sev = (f.get("severity") or "").lower()
        if sev in summary_counts:
            summary_counts[sev] += 1
    summary_str = (
        f"{summary_counts['critical']} critical, "
        f"{summary_counts['high']} high, "
        f"{summary_counts['medium']} medium, "
        f"{summary_counts['low']} low"
    )

    system_prompt = (
        "You are a senior security engineer reviewing the output of a "
        "static code scanner. Given the JSON list of findings below, "
        "produce a structured remediation report. For EACH finding "
        "provide: the exact code fix (a unified diff snippet or a "
        "specific line replacement), a one-sentence explanation of "
        "why the current code is dangerous, and your severity rating. "
        "Be specific to the file path and line number provided. Mark "
        "`pr_ready: true` only when the fix is mechanical and safe to "
        "apply without further context (e.g. parameterise a SQL query, "
        "remove a hardcoded key). Output VALID JSON ONLY — no prose, "
        "no markdown fences. The exact schema is:\n"
        "{\n"
        '  "summary": "<X critical, Y high, Z warnings found>",\n'
        '  "risk_score": <integer 0-100>,\n'
        '  "findings": [\n'
        "    {\n"
        '      "file": "<path>",\n'
        '      "line": <integer>,\n'
        '      "pattern": "<rule_id>",\n'
        '      "severity": "<critical|high|medium|low>",\n'
        '      "what_is_wrong": "<one sentence>",\n'
        '      "fix": "<exact code change or diff>",\n'
        '      "pr_ready": <true|false>\n'
        "    }\n"
        "  ],\n"
        '  "pr_draft_title": "<short Conventional-Commits title>",\n'
        '  "pr_draft_body": "<markdown body listing all fixes>"\n'
        "}"
    )
    user_prompt = (
        f"Repo context: {repo_context.get('owner')}/{repo_context.get('repo')} "
        f"(scanned {repo_context.get('scanned_files')} files).\n"
        f"Pre-computed summary: {summary_str}.\n\n"
        f"Findings JSON:\n{json.dumps(trimmed, ensure_ascii=False)}"
    )

    # Build the empty stub up front so any failure path returns it.
    empty_report: dict = {
        "summary":         summary_str,
        "risk_score":      _heuristic_risk_score(summary_counts),
        "findings":        [],
        "pr_draft_title":  f"Security: fix {summary_counts['critical']} critical vulnerabilities found by Vanguard",
        "pr_draft_body":   "_AI remediation report unavailable — see raw scan findings._",
    }

    try:
        from services.llm import call_llm_with_meta
        res = await asyncio.wait_for(
            call_llm_with_meta(
                system=system_prompt,
                user=user_prompt,
                max_tokens=1200,
                mode="chat",
                review_mode="swift",       # cheapest path — GLM-only
            ),
            timeout=30.0,  # Iter 212m-106 — bumped from 10s (user saw timeouts on the health page).
        )
        if not isinstance(res, dict) or not res.get("ok"):
            return empty_report, "failed"
        content = (res.get("content") or "").strip()
        if not content:
            return empty_report, "failed"
        # Strip ```json fences if the model added them anyway.
        if content.startswith("```"):
            content = re.sub(r"^```(?:json)?\s*", "", content)
            content = re.sub(r"\s*```$", "", content)
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            # Last-ditch — try to find the outermost JSON object.
            m = re.search(r"\{.*\}", content, flags=re.DOTALL)
            if not m:
                return empty_report, "failed"
            try:
                parsed = json.loads(m.group(0))
            except json.JSONDecodeError:
                return empty_report, "failed"
        # Ensure the required keys exist; fill defaults from the
        # heuristic if the model skipped any.
        report = {
            "summary":        parsed.get("summary") or summary_str,
            "risk_score":     int(parsed.get("risk_score")
                                  or _heuristic_risk_score(summary_counts)),
            "findings":       parsed.get("findings") or [],
            "pr_draft_title": parsed.get("pr_draft_title")
                              or empty_report["pr_draft_title"],
            "pr_draft_body":  parsed.get("pr_draft_body")
                              or _fallback_pr_body(trimmed, summary_counts),
        }
        return report, "ok"
    except asyncio.TimeoutError:
        return empty_report, "timeout"
    except Exception as e:
        logger.warning("remediation report generation failed: %r", e)
        return empty_report, "failed"


def _heuristic_risk_score(counts: dict) -> int:
    """Weighted score: critical=20, high=8, medium=3, low=1, capped at 100."""
    raw = (counts.get("critical", 0) * 20
           + counts.get("high", 0) * 8
           + counts.get("medium", 0) * 3
           + counts.get("low", 0) * 1)
    return min(100, raw)


def _fallback_pr_body(findings: list[dict], counts: dict) -> str:
    """Generate a basic markdown PR body when the LLM is unavailable.
    Keeps the auto_pr flow useful even if the report failed."""
    lines = [
        "## Vanguard automated security review",
        "",
        f"**Summary:** {counts.get('critical', 0)} critical · "
        f"{counts.get('high', 0)} high · "
        f"{counts.get('medium', 0)} medium · "
        f"{counts.get('low', 0)} low",
        "",
        "### Findings",
    ]
    for f in findings[:25]:
        sev = (f.get("severity") or "").upper()
        lines.append(
            f"- **{sev}** · `{f.get('file', '?')}:{f.get('line', '?')}` · "
            f"{f.get('rule_id') or f.get('vuln') or 'rule'} — "
            f"{f.get('desc') or ''}"
        )
    lines.append("")
    lines.append("_Generated by ORA Vanguard (Iter 212m-66)._")
    return "\n".join(lines)


# Inline GitHub draft-PR helper.  Lives in this file (not in
# github_api_writer.py) so the change-budget for this task stays at
# two files.  Uses the GitHub Git Data API + /pulls endpoint, both
# already required by the existing project.  Branch naming follows
# the spec: `vanguard/auto-fix-{unix_ts}`.
_GH_PR_TIMEOUT = 30.0

async def _create_draft_pr(
    *, owner: str, repo: str, pat: str,
    report: dict,
    fallback_findings: list[dict],
) -> tuple[Optional[str], Optional[str]]:
    """Open a DRAFT pull-request listing the proposed fixes.

    We don't push any code (that would force-merge auto-applied fixes
    into the user's repo without review — explicitly forbidden by the
    spec). Instead we create an empty marker commit on a new branch
    with the report body as its commit message AND the PR body, so
    the user can review every fix inline and either accept or close
    the PR with one click.

    Returns `(pr_url, error_string)`.  On success `error_string` is
    None.  On failure `pr_url` is None.
    """
    title = (report or {}).get("pr_draft_title") or "Security: Vanguard automated review"
    body  = (report or {}).get("pr_draft_body")
    if not body:
        # Defensive — should never happen because we always return a
        # body from _generate_remediation_report.  Fall back to the
        # raw findings list so the PR is still useful.
        body = _fallback_pr_body(
            fallback_findings,
            {"critical": sum(1 for f in fallback_findings
                             if (f.get("severity") or "").lower() == "critical"),
             "high":     sum(1 for f in fallback_findings
                             if (f.get("severity") or "").lower() == "high"),
             "medium":   sum(1 for f in fallback_findings
                             if (f.get("severity") or "").lower() == "medium"),
             "low":      sum(1 for f in fallback_findings
                             if (f.get("severity") or "").lower() == "low")},
        )

    branch_name = f"vanguard/auto-fix-{int(time.time())}"
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {pat}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        async with httpx.AsyncClient(timeout=_GH_PR_TIMEOUT) as client:
            # 1. Get default branch + its HEAD SHA + tree SHA.
            repo_meta = await client.get(
                f"{_GH_API}/repos/{owner}/{repo}", headers=headers,
            )
            if repo_meta.status_code != 200:
                return None, f"repo_meta_{repo_meta.status_code}"
            default_branch = repo_meta.json().get("default_branch") or "main"
            ref = await client.get(
                f"{_GH_API}/repos/{owner}/{repo}/git/ref/heads/{default_branch}",
                headers=headers,
            )
            if ref.status_code != 200:
                return None, f"ref_read_{ref.status_code}"
            head_sha = ref.json()["object"]["sha"]
            head_commit = await client.get(
                f"{_GH_API}/repos/{owner}/{repo}/git/commits/{head_sha}",
                headers=headers,
            )
            if head_commit.status_code != 200:
                return None, f"commit_read_{head_commit.status_code}"
            tree_sha = head_commit.json()["tree"]["sha"]

            # 2. Write a small marker file under .vanguard/ so the PR
            #    diff has at least one change (GitHub refuses to open
            #    a PR with zero commits ahead).  This file is the
            #    machine-readable report, not the user's source code,
            #    so we never touch their actual project files.
            marker_path = f".vanguard/{branch_name.split('/')[-1]}.md"
            marker_text = (
                f"# Vanguard security review — {datetime.now(timezone.utc).isoformat()}\n\n"
                f"{body}\n\n"
                "---\n"
                "This file is the machine-readable record of the "
                "scan. It can be safely deleted once the PR is "
                "reviewed and closed.\n"
            )
            blob = await client.post(
                f"{_GH_API}/repos/{owner}/{repo}/git/blobs",
                headers=headers,
                json={
                    "content": base64.b64encode(
                        marker_text.encode("utf-8"),
                    ).decode("ascii"),
                    "encoding": "base64",
                },
            )
            if blob.status_code not in (200, 201):
                return None, f"blob_create_{blob.status_code}"
            new_tree = await client.post(
                f"{_GH_API}/repos/{owner}/{repo}/git/trees",
                headers=headers,
                json={
                    "base_tree": tree_sha,
                    "tree": [{
                        "path": marker_path,
                        "mode": "100644",
                        "type": "blob",
                        "sha":  blob.json()["sha"],
                    }],
                },
            )
            if new_tree.status_code not in (200, 201):
                return None, f"tree_create_{new_tree.status_code}"
            new_commit = await client.post(
                f"{_GH_API}/repos/{owner}/{repo}/git/commits",
                headers=headers,
                json={
                    "message": title,
                    "tree":    new_tree.json()["sha"],
                    "parents": [head_sha],
                    "author":  {"name": "Aurem Vanguard",
                                "email": "vanguard@auremcto.com"},
                },
            )
            if new_commit.status_code not in (200, 201):
                return None, f"commit_create_{new_commit.status_code}"
            new_commit_sha = new_commit.json()["sha"]

            # 3. Create the new branch ref.
            ref_create = await client.post(
                f"{_GH_API}/repos/{owner}/{repo}/git/refs",
                headers=headers,
                json={"ref": f"refs/heads/{branch_name}",
                      "sha": new_commit_sha},
            )
            if ref_create.status_code not in (200, 201):
                return None, f"ref_create_{ref_create.status_code}"

            # 4. Open the DRAFT PR.
            pr = await client.post(
                f"{_GH_API}/repos/{owner}/{repo}/pulls",
                headers=headers,
                json={
                    "title": title,
                    "body":  body,
                    "head":  branch_name,
                    "base":  default_branch,
                    "draft": True,            # NEVER force-merge
                },
            )
            if pr.status_code not in (200, 201):
                # Some legacy repos disallow draft PRs — retry as
                # a regular PR before giving up.
                pr_retry = await client.post(
                    f"{_GH_API}/repos/{owner}/{repo}/pulls",
                    headers=headers,
                    json={
                        "title": title,
                        "body":  body,
                        "head":  branch_name,
                        "base":  default_branch,
                    },
                )
                if pr_retry.status_code not in (200, 201):
                    msg = (pr.json() or {}).get("message") or str(pr.status_code)
                    return None, f"pr_create_failed: {msg}"
                return pr_retry.json().get("html_url"), None
            return pr.json().get("html_url"), None
    except httpx.TimeoutException:
        return None, "github_timeout"
    except Exception as e:
        logger.warning("draft PR creation failed: %r", e)
        return None, f"exception: {e!r}"



# ─── Iter 212m-114 — REAL Fix endpoint ──────────────────────────────
# Replaces the previous "no auto-apply" stance from iter 212m-55 with
# an explicit, user-triggered REAL fix flow: the user clicks the Fix
# button on a specific finding, we LLM-generate a patch, RE-VALIDATE
# the patch (the original rule_id must no longer fire), and commit
# directly to their repo via the same Git Data API path Loop Mode
# uses for Ship. Founder / admin / is_unlimited accounts bypass the
# token cost; everyone else pays per-fix. On patch-rejection or any
# other failure, deducted tokens are REFUNDED atomically and no
# commit is pushed.

@router.post("/fix")
async def apply_security_fix(
    body: dict,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Apply a REAL fix for a single Vanguard finding.

    Iter 212m-190 — task-quota model: Vanguard fixes available from
    Starter tier up, 1 task per successful fix, no token pricing."""
    from services.scan_fix_quota import assert_can_fix, record_scan_fixes
    me = await current_dev(authorization)
    user_id   = me["user_id"]
    project_id = (body or {}).get("project_id") or ""
    finding   = (body or {}).get("finding") or {}

    if not (project_id and finding and finding.get("file")):
        raise HTTPException(400, "project_id + finding.file required")

    db = get_db()
    if db is None:
        raise HTTPException(503, "DB not connected")

    is_unlimited_user = bool(
        me.get("is_admin")
        or me.get("is_unlimited")
        or (me.get("tier") == "founder")
    )

    # Gate BEFORE any work: tool access + 1 task remaining.
    await assert_can_fix(me, "vanguard-scan", count=1)

    user_row = await db.dev_users.find_one(
        {"user_id": user_id}, {"_id": 0, "tokens_remaining": 1},
    )
    if not user_row:
        raise HTTPException(404, "User not found")
    deducted    = 0
    new_balance = int(user_row.get("tokens_remaining") or 0)

    from services.finding_fix_applier import apply_finding_fix
    from services import ora_fix_learning as _ofl
    import time as _t
    _t_start = _t.time()
    try:
        res = await apply_finding_fix(
            db=db, user=me, project_id=project_id, finding=finding,
        )
    except Exception as e:
        logger.exception("apply_finding_fix raised")
        res = {"ok": False, "error": f"unhandled: {e}"}
    _dur_ms = int((_t.time() - _t_start) * 1000)

    # Iter 212m-129 — Learning hook (single-finding security/vanguard fix).
    # Best-effort: never blocks or fails the user-visible response.
    try:
        await _ofl.record_fix_outcome(
            db, user_id=user_id, project_id=project_id,
            finding={**finding, "scanner": "vanguard"},
            result=res, attempts=1, duration_ms=_dur_ms,
            tokens_charged=(deducted if res.get("ok") else 0),
            scanner="vanguard",
        )
    except Exception as _e:
        logger.debug("learning hook (security) soft-failed: %r", _e)

    # Iter 212m-190 — deduct exactly 1 task ONLY on success.
    if res.get("ok") and not is_unlimited_user:
        try:
            await record_scan_fixes(user_id, "vanguard-scan", 1)
        except Exception as _e:
            logger.warning("task record failed (vanguard fix): %r", _e)
    # Iter 212m-193 — persist fixed state so a re-run of the scan
    # doesn't resurrect this finding (fix lives on a draft-PR branch).
    if res.get("ok"):
        from services.fixed_findings import record_fixed as _record_fixed
        await _record_fixed(
            db, user_id=user_id, project_id=project_id,
            finding=finding,
            commit_sha=res.get("commit_sha") or "",
            html_url=res.get("html_url") or "",
            tool="vanguard-scan",
        )

    if not res.get("ok"):
        err_code = res.get("error") or "unknown_error"
        if err_code == "patch_did_not_resolve_finding":
            raise HTTPException(422, {
                "error":          err_code,
                "message":        "AI patch did not resolve the finding — no commit pushed, tokens refunded.",
                "tokens_refunded": True,
            })
        if err_code in ("project_not_found_or_not_yours",):
            raise HTTPException(404, err_code)
        if err_code in ("github_credentials_missing", "github_unauthorized"):
            raise HTTPException(401, {
                "error":          err_code,
                "message":        "Connect your GitHub PAT / OAuth before applying fixes.",
                "tokens_refunded": True,
            })
        if err_code in ("file_not_found", "file_empty_or_missing"):
            raise HTTPException(404, {
                "error":          err_code,
                "tokens_refunded": True,
            })
        raise HTTPException(500, {
            "error":          err_code,
            "tokens_refunded": True,
        })

    return {
        "ok":             True,
        "commit_sha":     res["commit_sha"],
        "full_sha":       res["full_sha"],
        "html_url":       res["html_url"],
        "file":           res["file"],
        "rule_id":        res["rule_id"],
        "message":        res["message"],
        "tokens_charged": 0,
        "tasks_charged":  0 if is_unlimited_user else 1,
        "new_balance":    new_balance,
    }
