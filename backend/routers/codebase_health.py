"""
routers/codebase_health.py  —  Iter 212m-72 (Phase 2)
=====================================================
Five-category codebase health scanner.  Founder-facing endpoint
powering `/codebase-health` dashboard.

Endpoints (all founder-gated, project_id required):

  POST /api/aurem-dev/codebase-health/scan
       Body: { project_id, categories: [security, performance,
                                       code_quality, dependencies,
                                       database] }
       Returns: { score: 0-100, summary, breakdown: {<cat>: {...}} }

  POST /api/aurem-dev/codebase-health/fix
       Body: { project_id, finding_id, category }
       Creates a `cto_task` with the fix prompt + auto-runs it
       through the existing Loop pipeline.  Returns task_id.

All five category scanners are PURE deterministic static analysers
that walk the user's connected GitHub repo via the existing
`_list_repo_tree` + `_fetch_file` helpers from `security_scan.py`.
Zero LLM cost on the scan path — only the Fix button pays an LLM call
when the user actually wants ORA to write the patch.

The repo walk + file fetch is the shared bottleneck across all 5
scanners, so `scan()` does it ONCE then dispatches the cached
`{path: text}` dict to each requested category.  This means a
"Full scan" (all 5) costs the same GitHub-API budget as a single
category — just 4× more CPU on the user's static analysis.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Header
from fastapi.responses import JSONResponse

from cto_services.auth import current_dev
from cto_services.db import get_db
from routers.security_scan import (
    _decrypt_pat, _list_repo_tree, _fetch_file,
    _MAX_FILES, _MAX_BYTES_PER_FILE, _SCAN_EXTS, _SKIP_DIRS,
    _CONCURRENT_FETCHES,
)
from services.vanguard_scanner import scan_text
from services.bug_hunt_rules import scan_bug_hunt

router = APIRouter(prefix="/codebase-health", tags=["Codebase Health"])
logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────
# Category 1 — Security (delegates to Vanguard scan_text catalog)
# ──────────────────────────────────────────────────────────────────────
def _scan_security(text_cache: dict[str, str]) -> list[dict]:
    out: list[dict] = []
    for path, text in text_cache.items():
        for f in scan_text(text or "", filepath=path):
            sev = (f.get("severity") or "").upper()
            out.append({
                "id":         f"sec::{path}:{f.get('line')}:{f.get('name')}",
                "category":   "security",
                "severity":   _norm_sev(sev),
                "file":       path,
                "line":       int(f.get("line") or 0),
                "title":      f.get("name") or "security_issue",
                "message":    f.get("desc") or f.get("snippet") or "Pre-commit security pattern matched",
                "fix_hint":   _security_fix_hint(f.get("name") or ""),
                "fix_tokens": 5,
            })
    return out


def _security_fix_hint(rule_id: str) -> str:
    mapping = {
        "secret_openai_key": "Move TOKEN to an environment variable and rotate the leaked key immediately.",
        "secret_github_pat": "Revoke this PAT, regenerate with fine-grained scopes, store in .env.",
        "sql_string_format": 'Use parameterised queries: cursor.execute("... WHERE id=%s", (uid,))',
        "eval_usage":        "Replace eval() with `ast.literal_eval` or an explicit parser.",
        "exec_usage":        "Replace exec() with a safer dispatch (dict of callables).",
        "requests_no_verify":"Remove `verify=False` — fix the certificate chain instead.",
        "innerHTML_assignment": "Use textContent or a sanitiser (DOMPurify / xss).",
        "dangerously_set_html": "Sanitise the HTML via DOMPurify before injection.",
    }
    return mapping.get(rule_id, "Apply the standard secure-coding pattern for this rule.")


# ──────────────────────────────────────────────────────────────────────
# Category 2 — Performance
# ──────────────────────────────────────────────────────────────────────
_PERF_RULES: list[tuple[re.Pattern, str, str, str]] = [
    (re.compile(r'\.to_list\(None\)'),
     "unbounded_tolist", "HIGH",
     "Cursor returns ALL documents — will crash as the collection grows."),
    (re.compile(r'\.to_list\((\d{4,})\)'),
     "high_cap_tolist",  "MEDIUM",
     "Hard cap >= 1000 docs in memory.  Add pagination."),
    (re.compile(r'\.find_one\(\s*\{[^{}]+\}\s*\)'),
     "select_star",      "LOW",
     "find_one without projection — fetches every field.  Add `, {'_id': 0, ...}`."),
    (re.compile(r'for\s+\w+\s+in\s+[^:]+:[\s\S]{0,300}?await\s+\w*db\.\w+\.(find|find_one|count)'),
     "n_plus_one",       "HIGH",
     "Database call inside a for-loop — collapse with `$in` batch query."),
]

def _scan_performance(text_cache: dict[str, str]) -> list[dict]:
    out: list[dict] = []
    for path, text in text_cache.items():
        if not (path.endswith(".py") or path.endswith(".js")
                or path.endswith(".jsx") or path.endswith(".ts")
                or path.endswith(".tsx")):
            continue
        for rx, rid, sev, desc in _PERF_RULES:
            for m in rx.finditer(text or ""):
                line = (text[:m.start()].count("\n") + 1) if text else 0
                out.append({
                    "id":         f"perf::{path}:{line}:{rid}",
                    "category":   "performance",
                    "severity":   _norm_sev(sev),
                    "file":       path,
                    "line":       line,
                    "title":      rid,
                    "message":    desc,
                    "fix_hint":   _perf_fix_hint(rid),
                    "fix_tokens": 5,
                })
    return out


def _perf_fix_hint(rid: str) -> str:
    return {
        "unbounded_tolist":
            "Replace `.to_list(None)` with `.skip(skip).limit(limit).to_list(limit)`.",
        "high_cap_tolist":
            "Use page/limit query params instead of a hard-coded cap.",
        "select_star":
            "Add a projection arg: `{'_id': 0, 'field_a': 1, 'field_b': 1}`.",
        "n_plus_one":
            "Collect the keys first, then do ONE `$in` batch query.",
    }.get(rid, "")


# ──────────────────────────────────────────────────────────────────────
# Category 3 — Code Quality
# ──────────────────────────────────────────────────────────────────────
_LARGE_FN_THRESHOLD = 80   # lines of body
_LARGE_FILE_THRESHOLD = 1000

def _scan_code_quality(text_cache: dict[str, str]) -> list[dict]:
    out: list[dict] = []
    fn_re = re.compile(r'^\s*(?:async\s+)?def\s+(\w+)\s*\(', re.MULTILINE)
    todo_re = re.compile(r'#\s*(?:TODO|FIXME|HACK|XXX)\b', re.IGNORECASE)
    bare_except = re.compile(r'^\s*except\s*:\s*$', re.MULTILINE)

    for path, text in text_cache.items():
        if not text:
            continue
        n_lines = text.count("\n") + 1
        if n_lines > _LARGE_FILE_THRESHOLD and (
            path.endswith(".py") or path.endswith(".jsx")
            or path.endswith(".tsx") or path.endswith(".js")
        ):
            out.append({
                "id":       f"q::{path}:0:large_file",
                "category": "code_quality",
                "severity": _norm_sev("MEDIUM"),
                "file":     path, "line": 0,
                "title":    "large_file",
                "message":  f"{n_lines} lines — ORA cannot refactor this safely.",
                "fix_hint": "Split into focused modules of <500 lines each.",
                "fix_tokens": 5,
            })
        # TODO comments
        for m in todo_re.finditer(text):
            line = text[:m.start()].count("\n") + 1
            out.append({
                "id":       f"q::{path}:{line}:todo",
                "category": "code_quality",
                "severity": _norm_sev("LOW"),
                "file":     path, "line": line,
                "title":    "todo_comment",
                "message":  "Open TODO/FIXME — easy to forget.",
                "fix_hint": "Either fix now or convert to a GitHub issue.",
                "fix_tokens": 3,
            })
        # Bare except (Python only)
        if path.endswith(".py"):
            for m in bare_except.finditer(text):
                line = text[:m.start()].count("\n") + 1
                out.append({
                    "id":       f"q::{path}:{line}:bare_except",
                    "category": "code_quality",
                    "severity": _norm_sev("MEDIUM"),
                    "file":     path, "line": line,
                    "title":    "bare_except",
                    "message":  "Bare `except:` swallows KeyboardInterrupt + SystemExit too.",
                    "fix_hint": "Catch `Exception` and log the traceback.",
                    "fix_tokens": 5,
                })
        # Large functions (Python only — JS is harder without an AST)
        if path.endswith(".py"):
            fn_starts = [(m.start(), m.group(1)) for m in fn_re.finditer(text)]
            for i, (start, name) in enumerate(fn_starts):
                end = fn_starts[i+1][0] if i+1 < len(fn_starts) else len(text)
                body_lines = text[start:end].count("\n")
                if body_lines > _LARGE_FN_THRESHOLD:
                    line = text[:start].count("\n") + 1
                    out.append({
                        "id":       f"q::{path}:{line}:large_fn",
                        "category": "code_quality",
                        "severity": _norm_sev("MEDIUM"),
                        "file":     path, "line": line,
                        "title":    "large_function",
                        "message":  f"`{name}` is {body_lines} lines — too big to test/refactor.",
                        "fix_hint": "Split into smaller helpers; each function should do one thing.",
                        "fix_tokens": 5,
                    })
    return out


# ──────────────────────────────────────────────────────────────────────
# Category 4 — Dependencies
# ──────────────────────────────────────────────────────────────────────
# Hard-coded CVE map for known-vulnerable versions. In a future iter
# this should hit the OSV.dev API live.
_CVE_DB: dict[str, list[tuple[str, str, str]]] = {
    # package: [(matched_version_prefix, CVE_id, severity)]
    "requests":  [("2.28.", "CVE-2023-32681", "HIGH")],
    "fastapi":   [("0.95.", "outdated", "MEDIUM")],
    "pyjwt":     [("1.",    "CVE-2022-29217", "CRITICAL")],
    "axios":     [("0.21.", "CVE-2021-3749",  "HIGH")],
    "lodash":    [("4.17.20", "CVE-2021-23337", "HIGH")],
    "next":      [("13.4.", "CVE-2024-46982", "HIGH")],
    "vite":      [("4.",    "outdated", "LOW")],
}


def _scan_dependencies(text_cache: dict[str, str]) -> list[dict]:
    out: list[dict] = []
    # requirements.txt
    for path, text in text_cache.items():
        if path.endswith("requirements.txt") or path.endswith("requirements-dev.txt"):
            for i, line in enumerate((text or "").splitlines(), start=1):
                m = re.match(r'^\s*([A-Za-z0-9_\-]+)\s*[=<>]+\s*([0-9][0-9A-Za-z.\-]*)', line)
                if not m:
                    continue
                pkg, ver = m.group(1).lower(), m.group(2)
                for prefix, cve, sev in _CVE_DB.get(pkg, []):
                    if ver.startswith(prefix):
                        out.append(_dep_finding(path, i, pkg, ver, cve, sev))
                        break
        if path.endswith("package.json"):
            try:
                import json
                pkg_json = json.loads(text or "{}")
                deps = {**(pkg_json.get("dependencies") or {}),
                        **(pkg_json.get("devDependencies") or {})}
                for pkg, ver_raw in deps.items():
                    ver = re.sub(r'^[^\d]*', '', str(ver_raw))
                    for prefix, cve, sev in _CVE_DB.get(pkg.lower(), []):
                        if ver.startswith(prefix):
                            out.append(_dep_finding(path, 0, pkg, ver, cve, sev))
                            break
            except Exception:
                pass
    return out


def _dep_finding(path: str, line: int, pkg: str,
                 ver: str, cve: str, sev: str) -> dict:
    msg_extra = (
        f"{cve} — upgrade required."
        if cve.startswith("CVE-")
        else "This version is past EOL and missing security patches."
    )
    return {
        "id":         f"dep::{path}:{line}:{pkg}",
        "category":   "dependencies",
        "severity":   _norm_sev(sev),
        "file":       path, "line": line,
        "title":      f"vulnerable: {pkg}=={ver}",
        "message":    f"{pkg}=={ver} → {msg_extra}",
        "fix_hint":   f"Bump `{pkg}` to the latest stable release.",
        "fix_tokens": 5,
    }


# ──────────────────────────────────────────────────────────────────────
# Category 5 — Database
# ──────────────────────────────────────────────────────────────────────
def _scan_database(text_cache: dict[str, str]) -> list[dict]:
    out: list[dict] = []
    for path, text in text_cache.items():
        if not path.endswith(".py") or not text:
            continue
        # 1. AsyncIOMotorClient without pool config
        for m in re.finditer(r'AsyncIOMotorClient\([^)]*\)', text):
            inner = m.group(0)
            line = text[:m.start()].count("\n") + 1
            if ("maxPoolSize" not in inner) or ("maxIdleTimeMS" not in inner):
                out.append({
                    "id":       f"db::{path}:{line}:no_pool",
                    "category": "database",
                    "severity": _norm_sev("HIGH"),
                    "file":     path, "line": line,
                    "title":    "no_connection_pool",
                    "message":  "Motor client missing pool config — starves under traffic.",
                    "fix_hint": "Add maxPoolSize=50, minPoolSize=5, maxIdleTimeMS=30_000, connectTimeoutMS=10_000.",
                    "fix_tokens": 5,
                })
        # 2. Hard caps >2000 with no pagination
        for m in re.finditer(r'\.to_list\((\d{4,})\)', text):
            cap = int(m.group(1))
            if cap >= 2000:
                line = text[:m.start()].count("\n") + 1
                out.append({
                    "id":       f"db::{path}:{line}:hard_cap_{cap}",
                    "category": "database",
                    "severity": _norm_sev("MEDIUM"),
                    "file":     path, "line": line,
                    "title":    f"hard_cap_{cap}",
                    "message":  f"Loading {cap} documents at once — one request can kill the DB.",
                    "fix_hint": "Accept ?page=&limit= query params instead.",
                    "fix_tokens": 5,
                })
        # 3. No TTL on session/log/etc indices — detect collections
        # mentioned without an `expireAfterSeconds`. Best-effort heuristic.
        for m in re.finditer(r'await\s+db\.(\w*(?:session|log|temp|cache)\w*)\.insert', text):
            coll = m.group(1)
            line = text[:m.start()].count("\n") + 1
            # only flag once per file+collection
            key = f"db::{path}:0:no_ttl_{coll}"
            if any(o["id"] == key for o in out):
                continue
            out.append({
                "id":       key,
                "category": "database",
                "severity": _norm_sev("LOW"),
                "file":     path, "line": line,
                "title":    f"no_ttl_{coll}",
                "message":  f"`{coll}` writes detected — confirm a TTL index exists.",
                "fix_hint": f'Add `await db.{coll}.create_index("created_at", expireAfterSeconds=86400)`.',
                "fix_tokens": 5,
            })
    return out


# ──────────────────────────────────────────────────────────────────────
# Shared helpers
# ──────────────────────────────────────────────────────────────────────
def _norm_sev(sev: str) -> str:
    s = (sev or "").upper()
    if s == "WARNING":
        return "medium"
    return s.lower() if s in {"CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"} else "medium"


SEV_WEIGHTS = {"critical": 25, "high": 8, "medium": 3, "low": 1, "info": 0}


def _score_for_findings(findings: list[dict]) -> int:
    """0-100 health score.  Starts at 100, deducted per finding.
    The first CRITICAL alone takes you below 80 — we want users to
    feel the urgency."""
    raw = sum(SEV_WEIGHTS.get(f.get("severity") or "low", 0) for f in findings)
    return max(0, min(100, 100 - raw))


def _category_label(score: int) -> tuple[str, str]:
    if score <= 40:
        return ("CRITICAL RISK",     "critical")
    if score <= 60:
        return ("NEEDS ATTENTION",   "warn")
    if score <= 80:
        return ("GOOD",              "good")
    return                  ("HEALTHY",           "healthy")


SCANNERS = {
    "security":     _scan_security,
    "performance":  _scan_performance,
    "code_quality": _scan_code_quality,
    "dependencies": _scan_dependencies,
    "database":     _scan_database,
    "bug_hunt":     scan_bug_hunt,
}


async def _build_text_cache(owner: str, repo: str, pat: str) -> dict[str, str]:
    """Walk the repo tree + fetch every scannable file.  Cached for the
    duration of a single /scan request so all 5 categories share the
    same fetch budget."""
    text_cache: dict[str, str] = {}
    async with httpx.AsyncClient() as client:
        blobs = await _list_repo_tree(client, owner, repo, pat)
        candidates: list[dict] = []
        for b in blobs:
            path = b.get("path", "")
            if not path:
                continue
            if any(p in _SKIP_DIRS for p in path.split("/")):
                continue
            lower = path.lower()
            if not (any(lower.endswith(ext) for ext in _SCAN_EXTS)
                    or lower.endswith("requirements.txt")
                    or lower.endswith("package.json")):
                continue
            if b.get("size", 0) > _MAX_BYTES_PER_FILE:
                continue
            candidates.append(b)
            if len(candidates) >= _MAX_FILES:
                break

        sem = asyncio.Semaphore(_CONCURRENT_FETCHES)

        async def _one(blob):
            async with sem:
                t = await _fetch_file(client, owner, repo, blob["path"], pat)
            if t:
                text_cache[blob["path"]] = t

        await asyncio.gather(*[_one(b) for b in candidates])
    return text_cache


@router.post("/scan")
async def scan(
    body: dict, authorization: Optional[str] = Header(None),
) -> dict:
    user = await current_dev(authorization)
    user_id = user["user_id"]
    project_id = (body or {}).get("project_id")
    categories = (body or {}).get("categories") or list(SCANNERS.keys())
    categories = [c for c in categories if c in SCANNERS]
    if not project_id:
        raise HTTPException(400, "project_id required")
    if not categories:
        raise HTTPException(400, "At least one category required")

    db = get_db()
    if db is None:
        raise HTTPException(503, "DB not connected")

    # Iter 212m-75 — sliding-window rate limit (10 scans / hour / user / category).
    # Admins are exempt via JWT `is_admin` claim. Each call writes one
    # log row to `scan_rate_limits`; the prune step deletes rows older
    # than the window so the collection stays small. Returns 429 with
    # `retry_after_seconds` on the first denied category so the client
    # can wait the right amount.
    is_admin = bool(user.get("is_admin"))
    if not is_admin:
        denied_cat, retry_secs, remaining = await _check_scan_rate_limit(
            db, user_id, categories,
        )
        if denied_cat is not None:
            mins = max(1, int(round(retry_secs / 60.0)))
            raise HTTPException(429, {
                "error":               "scan_rate_limited",
                "category":            denied_cat,
                "message":             (f"You have used 10/10 scans for "
                                        f"'{denied_cat}' this hour. Try again "
                                        f"in {mins} minutes."),
                "retry_after_seconds": int(retry_secs),
            })
    else:
        remaining = {c: 999 for c in categories}

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

    try:
        text_cache = await _build_text_cache(owner, repo, pat)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"GitHub fetch failed: {e!r}")

    breakdown: dict[str, dict] = {}
    all_findings: list[dict] = []
    for cat in categories:
        findings = SCANNERS[cat](text_cache)
        # Cap to top 100 per category to keep the response tight.
        sev_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
        findings.sort(key=lambda f: (sev_rank.get(f["severity"], 9),
                                      f.get("file", ""), f.get("line", 0)))
        capped = findings[:100]
        counts = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
        for f in findings:
            counts[f["severity"]] = counts.get(f["severity"], 0) + 1
        breakdown[cat] = {
            "score":    _score_for_findings(findings),
            "counts":   counts,
            "total":    len(findings),
            "findings": capped,
        }
        all_findings.extend(findings)

    overall_score = _score_for_findings(all_findings)
    label, tone = _category_label(overall_score)
    total = sum(b["total"] for b in breakdown.values())
    payload = {
        "ok":            True,
        "score":         overall_score,
        "label":         label,
        "tone":          tone,
        "total":         total,
        "scanned_files": len(text_cache),
        "summary":       (
            f"{total} issues found across {len(categories)} categories — "
            f"{sum(1 for f in all_findings if f['severity']=='critical')} critical."
        ),
        "breakdown":     breakdown,
        "scan_remaining": remaining,
    }
    # Iter 212m-75 — surface remaining quota per category in a header so
    # callers can render an inline counter without parsing the body.
    headers = {
        "X-Scan-Remaining": str(min(remaining.values()) if remaining else 0),
        "X-Scan-Remaining-Per-Category": ",".join(
            f"{c}:{n}" for c, n in remaining.items()
        ),
    }
    return JSONResponse(content=payload, headers=headers)


# ──────────────────────────────────────────────────────────────────────
# Iter 212m-75 — Sliding-window scan rate limiter.
#   • Bucket: (user_id, category)
#   • Window: 3600 seconds (1 hour, rolling)
#   • Cap:    10 successful scan starts per bucket
#   • Storage: scan_rate_limits collection (one doc per scan call)
#   • TTL: prune-on-read — every check deletes window-expired rows for
#         the caller so the collection stays bounded.
# ──────────────────────────────────────────────────────────────────────
_SCAN_RATE_WINDOW = 3600
_SCAN_RATE_CAP    = 10


async def _check_scan_rate_limit(
    db, user_id: str, categories: list[str],
) -> tuple[Optional[str], int, dict[str, int]]:
    """Returns (denied_category, retry_after_seconds, remaining_per_cat).

    If any requested category is over cap, returns the *first* one that
    is denied + the seconds until its oldest hit ages out of the window.
    On success, writes one entry per category and returns (None, 0,
    remaining-per-category dict).
    """
    now = time.time()
    cutoff = now - _SCAN_RATE_WINDOW
    coll = db.scan_rate_limits

    # Prune expired entries for this user (cheap — indexed).
    try:
        await coll.delete_many({"user_id": user_id, "ts": {"$lt": cutoff}})
    except Exception as e:
        logger.debug("scan_rate prune failed: %r", e)

    # Count hits per requested category in the current window.
    counts: dict[str, int] = {}
    oldest: dict[str, float] = {}
    for cat in categories:
        cur = coll.find(
            {"user_id": user_id, "category": cat, "ts": {"$gte": cutoff}},
            {"_id": 0, "ts": 1},
        ).sort("ts", 1)
        ts_list = [d["ts"] async for d in cur]
        counts[cat] = len(ts_list)
        if ts_list:
            oldest[cat] = ts_list[0]

    # First over-cap category wins the denial.
    for cat in categories:
        if counts.get(cat, 0) >= _SCAN_RATE_CAP:
            o = oldest.get(cat, now)
            retry = max(1, int((o + _SCAN_RATE_WINDOW) - now))
            remaining = {c: max(0, _SCAN_RATE_CAP - counts.get(c, 0))
                         for c in categories}
            return cat, retry, remaining

    # Allowed — log one entry per category atomically.
    try:
        await coll.insert_many([
            {"user_id": user_id, "category": cat, "ts": now}
            for cat in categories
        ])
    except Exception as e:
        # Storage failure must NEVER block a paying user's scan.
        logger.warning("scan_rate insert failed: %r", e)
    remaining = {
        c: max(0, _SCAN_RATE_CAP - (counts.get(c, 0) + 1)) for c in categories
    }
    return None, 0, remaining


# ──────────────────────────────────────────────────────────────────────
# Fix-button endpoint — creates a cto_task that fixes one finding.
# ──────────────────────────────────────────────────────────────────────
@router.post("/fix")
async def request_fix(
    body: dict, authorization: Optional[str] = Header(None),
) -> dict:
    user = await current_dev(authorization)
    user_id = user["user_id"]
    project_id  = (body or {}).get("project_id")
    finding_id  = (body or {}).get("finding_id") or ""
    title       = (body or {}).get("title") or "security_issue"
    file_path   = (body or {}).get("file") or ""
    line        = int((body or {}).get("line") or 0)
    message     = (body or {}).get("message") or ""
    fix_hint    = (body or {}).get("fix_hint") or ""
    tokens_cost = int((body or {}).get("tokens") or 5)
    if not project_id or not finding_id:
        raise HTTPException(400, "project_id and finding_id required")
    db = get_db()
    if db is None:
        raise HTTPException(503, "DB not connected")
    # Token deduction — simple model: dev_users.tokens_remaining.
    me = await db.dev_users.find_one(
        {"user_id": user_id}, {"_id": 0, "tokens_remaining": 1},
    )
    if not me:
        raise HTTPException(404, "User not found")
    bal = int(me.get("tokens_remaining") or 0)
    if bal < tokens_cost:
        raise HTTPException(402, {
            "error":   "insufficient_tokens",
            "needed":  tokens_cost,
            "balance": bal,
        })
    # Deduct atomically.
    upd = await db.dev_users.update_one(
        {"user_id": user_id, "tokens_remaining": {"$gte": tokens_cost}},
        {"$inc": {"tokens_remaining": -tokens_cost}},
    )
    if upd.modified_count == 0:
        raise HTTPException(402, "Concurrent token deduction — try again")
    new_balance = bal - tokens_cost

    # Build the fix prompt and enqueue as a regular cto_task.
    prompt = (
        f"Fix the following codebase health finding:\n\n"
        f"  • file: {file_path}\n"
        f"  • line: {line}\n"
        f"  • issue: {title}\n"
        f"  • details: {message}\n"
        f"  • suggested approach: {fix_hint}\n\n"
        "Apply the minimum-diff fix.  Open a PR titled "
        f"'fix(health): {title} @ {file_path}:{line}'.  "
        "Run the standard verification + Vanguard scan before committing."
    )
    import uuid
    import time
    task_id = f"task_{uuid.uuid4().hex[:10]}"
    await db.cto_tasks.insert_one({
        "task_id":         task_id,
        "user_id":         user_id,
        "project_id":      project_id,
        "kind":            "health_fix",
        "status":          "queued",
        "prompt":          prompt,
        "finding_id":      finding_id,
        "finding_title":   title,
        "finding_file":    file_path,
        "finding_line":    line,
        "created_at":      time.time(),
        "tokens_charged":  tokens_cost,
    })
    return {
        "ok":              True,
        "task_id":         task_id,
        "tokens_charged":  tokens_cost,
        "new_balance":     new_balance,
        "message":         f"Fix queued — {tokens_cost} tokens charged.",
    }
