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

from cto_services.auth import current_dev, require_admin
from cto_services.db import get_db
from routers.security_scan import (
    _decrypt_pat, _list_repo_tree, _list_repo_tree_with_sha, _fetch_file,
    _MAX_FILES, _MAX_BYTES_PER_FILE, _SCAN_EXTS, _SKIP_DIRS,
    _CONCURRENT_FETCHES,
)
from services.vanguard_scanner import scan_text
from services.bug_hunt_rules import scan_bug_hunt
from services.full_scan_scanners import (
    scan_docker_cis as _scan_docker_cis_service,
    scan_http_headers as _scan_http_headers_service,
)
from services.scan_cache import (
    get_cached_text_cache, put_cached_text_cache,
)

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
# Category 6 — Docker CIS Benchmark
# Iter 212m-190 — Rule bodies extracted to
# `services/full_scan_scanners.py` so Loop-Mode Full Scan can call
# them without importing the router layer. This wrapper preserves
# the existing `_scan_docker_cis(text_cache)` call site in this file.
# ──────────────────────────────────────────────────────────────────────
def _scan_docker_cis(text_cache: dict[str, str]) -> list[dict]:
    return _scan_docker_cis_service(text_cache)


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
    """0-100 health score using a diminishing-returns curve.

    Iter 212m-164 — replaced the old linear `100 - sum(weights)` formula
    which cliff-edged at 4 criticals (4 × 25 = 100 → score 0).  Real
    repos routinely surface 5-15 critical findings on first scan and
    the founder needs to see the score MOVE as they fix issues, not
    sit stuck at 0.

    New curve:  score = round(100 · exp(-raw / 60))

    Reference points:
        0  issues   → score 100  (HEALTHY)
        5  medium   → raw 15  → score 78  (GOOD)
        5  high     → raw 40  → score 51  (NEEDS ATTENTION)
        2  critical → raw 50  → score 44  (NEEDS ATTENTION)
        4  critical → raw 100 → score 19  (CRITICAL RISK)
        9  critical → raw 225 → score 2   (CRITICAL RISK)

    The curve preserves severity ordering (criticals still dominate)
    but every fix produces a visible score delta, which is the whole
    point of a health gauge during pre-launch.
    """
    raw = sum(SEV_WEIGHTS.get(f.get("severity") or "low", 0) for f in findings)
    if raw <= 0:
        return 100
    import math
    return max(0, min(100, round(100 * math.exp(-raw / 60))))


def _category_label(score: int) -> tuple[str, str]:
    # Iter 212m-164 — thresholds re-tuned for the diminishing-returns
    # curve so the bands still split the score space roughly into
    # quarters under the new compression.
    if score <  20:
        return ("CRITICAL RISK",     "critical")
    if score <  50:
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
    "docker":       _scan_docker_cis,
}


async def _build_text_cache(owner: str, repo: str, pat: str) -> dict[str, str]:
    """Walk the repo tree + fetch every scannable file.  Cached for the
    duration of a single /scan request so all 5 categories share the
    same fetch budget.

    Iter 212m-79 — also checks Redis for a previously-built bundle
    keyed on `owner/repo@tree_sha`.  Cross-pod cache hits skip the
    ~50-600 GitHub calls entirely (~60 s saved on large repos).  TTL
    24 h; key invalidates automatically on the next commit because the
    tree SHA changes."""
    async with httpx.AsyncClient() as client:
        blobs, tree_sha = await _list_repo_tree_with_sha(
            client, owner, repo, pat,
        )

        # ── Redis-backed dedup lookup ──────────────────────────────
        if tree_sha:
            cached = await get_cached_text_cache(owner, repo, tree_sha)
            if cached is not None:
                # Hit — skip GitHub entirely.  Re-apply the path
                # candidate filter in case _SCAN_EXTS changed between
                # writes (cheap; pure-Python loop over keys).
                filtered: dict[str, str] = {}
                for path, txt in cached.items():
                    if not path:
                        continue
                    if any(p in _SKIP_DIRS for p in path.split("/")):
                        continue
                    lower = path.lower()
                    if not (any(lower.endswith(ext) for ext in _SCAN_EXTS)
                            or lower.endswith("requirements.txt")
                            or lower.endswith("package.json")
                            or _is_dockerfile(lower)):
                        continue
                    filtered[path] = txt
                return filtered

        text_cache: dict[str, str] = {}
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
                    or lower.endswith("package.json")
                    or _is_dockerfile(lower)):
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

        # ── Best-effort write-back; never blocks the response ─────
        if tree_sha and text_cache:
            try:
                await put_cached_text_cache(owner, repo, tree_sha, text_cache)
            except Exception as e:
                logger.debug("scan_cache put_cached failed: %r", e)

    return text_cache


@router.get("/cache-stats")
async def cache_stats(authorization: Optional[str] = Header(None)) -> dict:
    """Iter 212m-79 — surface Redis scan-cache hit-rate to founders.
    Iter 212m-158 — was a custom is_admin check; now routed through
    the shared `require_admin` helper for consistency."""
    await require_admin(authorization)
    from services.scan_cache import get_scan_cache_stats
    return get_scan_cache_stats()


@router.post("/scan")
async def scan(
    body: dict, authorization: Optional[str] = Header(None),
) -> dict:
    # Iter 212m-158 — Health scan is now admin/founder-only (matches
    # the frontend route guard shipped in iter 212m-157).
    user = await require_admin(authorization)
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
    # Iter 212m-110 — admins, founders and is_unlimited accounts are
    # ALL exempt. Each call writes one log row to `scan_rate_limits`;
    # the prune step deletes rows older than the window so the
    # collection stays small. Returns 429 with `retry_after_seconds`
    # on the first denied category so the client can wait the right
    # amount.
    is_admin = bool(
        user.get("is_admin")
        or user.get("is_unlimited")
        or (user.get("tier") == "founder")
    )
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
        # Iter 212m-216 — meaningful GH errors already carry the
        # right status + detail from `_gh_get`.  Do NOT re-wrap them
        # as 502 (that's what caused Cloudflare to intercept and
        # replace the body with its own "Bad gateway" HTML on prod
        # for months).  Just log + propagate.
        raise
    except Exception as e:
        # Genuine unexpected crash — log full context for founder
        # monitoring, but return a caller-actionable 502 with the
        # actual exception class in the detail so a screenshot alone
        # is enough to root-cause.
        logger.exception(
            "codebase_health.scan crashed inside _build_text_cache "
            "(user=%s, project=%s, owner=%s, repo=%s)",
            user_id, project_id, owner, repo,
        )
        raise HTTPException(
            502,
            f"github_fetch_crashed: {type(e).__name__}: {str(e)[:200]}",
        )

    breakdown: dict[str, dict] = {}
    all_findings: list[dict] = []
    # Iter 212m-193 — findings already fixed (commits on draft-PR
    # branches) must not resurrect on rescan: split them out, score
    # and count ACTIVE findings only.
    from services.fixed_findings import get_fixed_map, split_findings
    fixed_map = await get_fixed_map(db, user_id=user_id, project_id=project_id)
    total_fixed = 0
    for cat in categories:
        raw_findings = SCANNERS[cat](text_cache)
        findings, fixed_findings = split_findings(raw_findings, fixed_map)
        total_fixed += len(fixed_findings)
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
            "fixed_count": len(fixed_findings),
            "fixed":       fixed_findings[:100],
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
        "total_fixed":   total_fixed,
        "scanned_files": len(text_cache),
        "summary":       (
            f"{total} issues found across {len(categories)} categories — "
            f"{sum(1 for f in all_findings if f['severity']=='critical')} critical."
            + (f" {total_fixed} already fixed." if total_fixed else "")
        ),
        "breakdown":     breakdown,
        "scan_remaining": remaining,
    }
    # Iter 212m-127 — Persist the scan result so the Dashboard health
    # ring can read the most-recent score via GET /last without paying
    # the full scan cost on every page mount.  Best-effort: a Mongo
    # failure must NEVER block the user-visible scan response.
    try:
        # Iter 212m-177 — P1-5: a scan that read ZERO files scores 100
        # trivially and later contradicts real scans (PROD showed
        # 100-HEALTHY vs 0-CRITICAL for the same repo). Never persist it.
        if len(text_cache) > 0:
            await db.codebase_health_scans.insert_one({
                "user_id":       user_id,
                "project_id":    project_id,
                "score":         overall_score,
                "label":         label,
                "tone":          tone,
                "total":         total,
                "scanned_files": len(text_cache),
                "summary":       payload["summary"],
                "categories":    list(categories),
                "breakdown":     breakdown,
                "created_at":    time.time(),
            })
    except Exception as e:
        logger.debug("codebase_health_scans persist failed: %r", e)
    # Iter 212m-129 — Learning hook: persist a per-rule histogram of
    # this scan run so analytics can later answer "which rules trigger
    # most often for this user / project / across the platform".
    try:
        from services import ora_fix_learning as _ofl
        rule_counts: dict[str, int] = {}
        sev_counts:  dict[str, int] = {
            "critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0,
        }
        for _f in all_findings:
            _rid = (_f.get("rule_id") or _f.get("rule")
                    or _f.get("title") or "unknown")
            rule_counts[_rid] = rule_counts.get(_rid, 0) + 1
            _sv = (_f.get("severity") or "").lower()
            if _sv in sev_counts:
                sev_counts[_sv] += 1
        await _ofl.record_scan_run(
            db, user_id=user_id, project_id=project_id,
            scanner="codebase_health",
            categories=list(categories),
            files_scanned=len(text_cache),
            counts=sev_counts,
            rule_counts=rule_counts,
            duration_ms=None,
            score=overall_score,
        )
    except Exception as _e:
        logger.debug("learning scan-run hook (health) soft-failed: %r", _e)
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
# Iter 212m-127 — Dashboard health-ring lookup.  Returns the most recent
# persisted scan for the active project so the ring renders instantly
# without re-walking the GitHub tree.  Returns `score: null` (200, not
# 404) when the user hasn't scanned the project yet — the Dashboard
# already treats `null` as "ring hidden".
# ──────────────────────────────────────────────────────────────────────
@router.get("/last")
async def last_scan(
    project_id: Optional[str] = None,
    authorization: Optional[str] = Header(None),
) -> dict:
    if not project_id:
        raise HTTPException(400, "project_id required")
    # Iter 212m-158 — Admin/founder-only.  Non-admins shouldn't be
    # able to poll last-scan state for any project (would leak the
    # admin-only Health Scanner UX through a side channel).
    user = await require_admin(authorization)
    user_id = user["user_id"]
    db = get_db()
    if db is None:
        # Don't 503 — frontend silently hides the ring on errors.
        return {"ok": True, "score": None}
    try:
        doc = await db.codebase_health_scans.find_one(
            {"user_id": user_id, "project_id": project_id,
             "scanned_files": {"$gt": 0}},   # Iter 212m-177 P1-5
            {"_id": 0},
            sort=[("created_at", -1)],
        )
    except Exception as e:
        logger.debug("codebase_health_scans read failed: %r", e)
        return {"ok": True, "score": None}
    if not doc:
        # Empty state — 200 with `score: null` instead of 404 noise.
        return {"ok": True, "score": None}
    # Iter 212m-147 — Defensive guard: a persisted (score=0, total=0)
    # row is logically impossible from a real scan (0 findings yields
    # score=100), so it can only come from a legacy bad write or a
    # crashed scan. Treat it as "no scan yet" so the top-bar ring
    # stays hidden instead of misleading the user with a red "0".
    _score = doc.get("score")
    _total = doc.get("total")
    if _score == 0 and (not _total or _total == 0):
        return {"ok": True, "score": None}
    return {
        "ok":            True,
        "score":         doc.get("score"),
        "label":         doc.get("label"),
        "tone":          doc.get("tone"),
        "total":         doc.get("total"),
        "scanned_files": doc.get("scanned_files"),
        "summary":       doc.get("summary"),
        "categories":    doc.get("categories") or [],
        # Iter 212m-176 — return the persisted breakdown so the
        # Codebase Health page can restore the last scan on reload
        # instead of showing "unscanned" after the user already paid.
        "breakdown":     doc.get("breakdown") or {},
        "created_at":    doc.get("created_at"),
    }


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
    # Iter 212m-190 — task-quota model: tier gate (health-scan fixes
    # need Pro+), 1 task per successful fix. No token pricing.
    from services.scan_fix_quota import assert_can_fix, record_scan_fixes
    user = await current_dev(authorization)
    user_id = user["user_id"]
    project_id  = (body or {}).get("project_id")
    finding_id  = (body or {}).get("finding_id") or ""
    title       = (body or {}).get("title") or "security_issue"
    file_path   = (body or {}).get("file") or ""
    line        = int((body or {}).get("line") or 0)
    message     = (body or {}).get("message") or ""
    fix_hint    = (body or {}).get("fix_hint") or ""
    if not project_id or not finding_id:
        raise HTTPException(400, "project_id and finding_id required")
    db = get_db()
    if db is None:
        raise HTTPException(503, "DB not connected")
    is_unlimited_user = bool(
        user.get("is_admin")
        or user.get("is_unlimited")
        or (user.get("tier") == "founder")
    )
    # Gate BEFORE any work: tool access + 1 task remaining. Raises
    # 403 fix_not_available_on_tier / 402 insufficient_tasks.
    await assert_can_fix(user, "health-scan", count=1)
    tokens_cost = 0
    me = await db.dev_users.find_one(
        {"user_id": user_id}, {"_id": 0, "tokens_remaining": 1},
    )
    if not me:
        raise HTTPException(404, "User not found")
    new_balance = int(me.get("tokens_remaining") or 0)

    # Iter 212m-114 — REAL fix path. Previously this endpoint just
    # enqueued a cto_tasks record with kind:"health_fix" and returned
    # "Fix queued" (effectively a dummy — no background worker ever
    # consumed it). Now we run the same apply_finding_fix() pipeline
    # the Security Scan /fix uses: fetch file via PAT → LLM patch →
    # re-validate → commit. Tokens are REFUNDED on any failure.
    finding_payload = {
        "rule_id":   finding_id,
        "file":      file_path,
        "line":      line,
        "severity":  "medium",
        "title":     title,
        "message":   message,
        "snippet":   fix_hint,
    }
    from services.finding_fix_applier import apply_finding_fix
    from services import ora_fix_learning as _ofl
    import time as _t
    _t_start = _t.time()
    try:
        res = await apply_finding_fix(
            db=db, user=user, project_id=project_id, finding=finding_payload,
        )
    except Exception as e:
        logger.exception("health apply_finding_fix raised")
        res = {"ok": False, "error": f"unhandled: {e}"}
    _dur_ms = int((_t.time() - _t_start) * 1000)

    # Iter 212m-129 — Learning hook (single-finding codebase-health fix).
    try:
        await _ofl.record_fix_outcome(
            db, user_id=user_id, project_id=project_id,
            finding={**finding_payload, "category": "codebase_health",
                     "scanner": "codebase_health"},
            result=res, attempts=1, duration_ms=_dur_ms,
            tokens_charged=(tokens_cost if res.get("ok") else 0),
            scanner="codebase_health",
        )
    except Exception as _e:
        logger.debug("learning hook (health) soft-failed: %r", _e)

    if not res.get("ok"):
        # Refund tokens if deduction happened (founders deducted=0).
        if not is_unlimited_user and tokens_cost:
            try:
                await db.dev_users.update_one(
                    {"user_id": user_id},
                    {"$inc": {"tokens_remaining": tokens_cost}},
                )
                new_balance += tokens_cost
            except Exception as e:
                logger.warning("health refund failed: %r", e)
        err_code = res.get("error") or "unknown_error"
        if err_code == "patch_did_not_resolve_finding":
            raise HTTPException(422, {
                "error":          err_code,
                "message":        "AI patch did not resolve the finding — no commit pushed, tokens refunded.",
                "tokens_refunded": True,
            })
        if err_code in ("github_credentials_missing", "github_unauthorized"):
            raise HTTPException(401, {
                "error":          err_code,
                "message":        "Connect your GitHub PAT / OAuth before applying fixes.",
                "tokens_refunded": True,
            })
        # Iter 212m-114 (iter_26 follow-up) — match /security-scan/fix:
        # ownership-mismatch and missing-file should be 404, not 500.
        if err_code in ("project_not_found_or_not_yours",
                        "file_not_found", "file_empty_or_missing"):
            raise HTTPException(404, {
                "error":          err_code,
                "tokens_refunded": True,
            })
        raise HTTPException(500, {
            "error":          err_code,
            "tokens_refunded": True,
        })

    # Also persist a row to cto_tasks so the existing audit-log UI
    # surfaces this fix in the activity feed.
    # Iter 212m-190 — deduct exactly 1 task for the successful fix.
    if not is_unlimited_user:
        try:
            await record_scan_fixes(user_id, "health-scan", 1)
        except Exception as _e:
            logger.warning("task record failed (health fix): %r", _e)
    # Iter 212m-193 — persist fixed state so rescans don't resurrect it.
    from services.fixed_findings import record_fixed as _record_fixed
    await _record_fixed(
        db, user_id=user_id, project_id=project_id,
        finding=finding_payload,
        commit_sha=res.get("commit_sha") or "",
        html_url=res.get("html_url") or "",
        tool="health-scan",
    )
    import uuid as _uuid, time as _time
    task_id = f"task_{_uuid.uuid4().hex[:10]}"
    await db.cto_tasks.insert_one({
        "task_id":         task_id,
        "user_id":         user_id,
        "project_id":      project_id,
        "kind":            "health_fix",
        "status":          "completed",
        "finding_id":      finding_id,
        "finding_title":   title,
        "finding_file":    file_path,
        "finding_line":    line,
        "commit_sha":      res["full_sha"],
        "html_url":        res["html_url"],
        "created_at":      _time.time(),
        "completed_at":    _time.time(),
        "tokens_charged":  tokens_cost,
    })
    return {
        "ok":              True,
        "task_id":         task_id,
        "commit_sha":      res["commit_sha"],
        "full_sha":        res["full_sha"],
        "html_url":        res["html_url"],
        "tokens_charged":  tokens_cost,
        "new_balance":     new_balance,
        "message":         res["message"],
    }
