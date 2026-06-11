"""
services/dev_skills.py — Iter 123 senior-dev skill pack for AUREM ORA.

Closes the capability gap vs Cursor / Claude Code / Emergent: lets ORA
reason about a repo at the dependency / framework / history / lint
level, not just file-text level.

Every skill is REAL — no mocks, no stubs:
  • GitHub API for commits / issues / PR comments / usages
  • npm + PyPI registries for package docs
  • Python AST for syntax validation
  • e2b sandbox for snippet execution

All skills fail-soft: a missing key or 404 returns
{"ok": False, "error": "..."} — never raises into the orchestrator.
"""
from __future__ import annotations

import ast
import asyncio
import logging
from typing import Optional

import httpx

from cto_services.db import get_db
from .repo_context import _fetch_file as _gh_fetch_file
from .sandbox_runner import run_python_check

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"
NPM_REGISTRY = "https://registry.npmjs.org"
PYPI_API = "https://pypi.org/pypi"


# ── Helper: resolve project (copy from local_tools to avoid cycle) ────

async def _resolve_project(user_id: str, project_id: str) -> dict | None:
    if not user_id or not project_id or project_id == "home":
        return None
    db = get_db()
    if db is None:
        return None
    return await db.cto_projects.find_one(
        {"project_id": project_id, "user_id": user_id}
    )


def _gh_headers(token: Optional[str]) -> dict:
    h = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


# ── SKILL 1: find_usages — search for callers/imports of a symbol ─────

async def find_usages(ctx: dict, args: dict) -> dict:
    """Find every place a function/class/variable is referenced in the
    repo. Uses GitHub code-search index (fast, repo-wide) plus a fallback
    to the recursive tree + grep for private repos that aren't indexed.

    args:
      symbol   str   — function or class name e.g. 'verify_jwt'
      kind?    str   — 'function' | 'class' | 'variable' (hint only)
      max?     int   — max results (default 15, cap 30)
    """
    user_id    = ctx.get("user_id")
    project_id = ctx.get("project_id")
    symbol     = ((args or {}).get("symbol") or "").strip()
    max_hits   = min(int((args or {}).get("max") or 15), 30)

    if not symbol or not symbol.replace("_", "").isalnum():
        return {"ok": False, "error": "symbol required (alphanumeric/underscore)"}

    proj = await _resolve_project(user_id, project_id)
    if not proj:
        return {"ok": False, "error": "No project connected"}

    owner = proj.get("github_owner") or ""
    repo  = proj.get("github_repo") or ""
    token = proj.get("github_token") or None
    if not owner or not repo:
        return {"ok": False, "error": "Project missing github_owner/repo"}

    gh_query = f"{symbol} repo:{owner}/{repo}"
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.get(
                f"{GITHUB_API}/search/code",
                params={"q": gh_query, "per_page": max_hits},
                headers=_gh_headers(token),
            )
    except httpx.RequestError as e:
        return {"ok": False, "error": f"GitHub search network error: {e}"}

    usages: list[dict] = []
    if r.status_code == 200:
        data = r.json() or {}
        for item in (data.get("items") or [])[:max_hits]:
            usages.append({
                "path":   item.get("path", ""),
                "score":  round(item.get("score", 0), 2),
                "source": "github_code_search",
            })

    # Fallback: if GitHub code-search rate-limited or empty, grep the tree.
    if not usages:
        try:
            async with httpx.AsyncClient(timeout=20.0) as c:
                tree_r = await c.get(
                    f"{GITHUB_API}/repos/{owner}/{repo}/git/trees/"
                    f"{proj.get('branch') or 'main'}?recursive=1",
                    headers=_gh_headers(token),
                )
            tree_r.raise_for_status()
            files = [
                it["path"] for it in (tree_r.json().get("tree") or [])
                if it.get("type") == "blob"
                and it["path"].endswith((".py", ".js", ".jsx", ".ts", ".tsx"))
            ][:80]   # cap so we don't blow rate-limit
        except Exception:
            files = []

        branch = proj.get("branch") or "main"

        async def _grep_one(path: str) -> dict | None:
            content = await _gh_fetch_file(owner, repo, path, branch, token)
            if not content or symbol not in content:
                return None
            # First matching line as evidence
            for ln, line in enumerate(content.splitlines(), 1):
                if symbol in line:
                    return {
                        "path":   path,
                        "line":   ln,
                        "snippet": line.strip()[:140],
                        "source": "tree_grep",
                    }
            return None

        # Limit concurrency
        for batch_start in range(0, len(files), 8):
            if len(usages) >= max_hits:
                break
            batch = files[batch_start:batch_start + 8]
            results = await asyncio.gather(*[_grep_one(p) for p in batch])
            usages.extend(r for r in results if r)

    return {
        "ok":      True,
        "symbol":  symbol,
        "count":   len(usages),
        "usages":  usages[:max_hits],
        "hint":    "Use read_repo_files to open the top hits in parallel." if usages else "",
    }


# ── SKILL 2: get_dependencies — package.json + requirements.txt ───────

async def get_dependencies(ctx: dict, args: dict) -> dict:
    """Return the project's declared dependencies. Tries (in order):
      • package.json (frontend / Node)
      • requirements.txt (Python)
      • pyproject.toml (modern Python)
      • backend/requirements.txt + frontend/package.json (monorepo)

    args: none required.
    """
    user_id    = ctx.get("user_id")
    project_id = ctx.get("project_id")

    proj = await _resolve_project(user_id, project_id)
    if not proj:
        return {"ok": False, "error": "No project connected"}

    owner  = proj.get("github_owner")
    repo   = proj.get("github_repo")
    branch = proj.get("branch") or "main"
    token  = proj.get("github_token") or None

    # Try common dep-manifest locations
    paths_to_try = [
        "package.json", "requirements.txt", "pyproject.toml",
        "backend/requirements.txt", "frontend/package.json",
        "Pipfile", "poetry.lock", "yarn.lock",
    ]

    results: dict[str, dict] = {}

    async def _fetch(p: str) -> tuple[str, str | None]:
        return p, await _gh_fetch_file(owner, repo, p, branch, token)

    fetched = await asyncio.gather(*[_fetch(p) for p in paths_to_try])

    for path, content in fetched:
        if not content:
            continue
        if path.endswith("package.json"):
            try:
                import json as _json
                pkg = _json.loads(content)
                results[path] = {
                    "type": "node",
                    "name": pkg.get("name"),
                    "version": pkg.get("version"),
                    "dependencies": pkg.get("dependencies") or {},
                    "devDependencies": pkg.get("devDependencies") or {},
                    "scripts": list((pkg.get("scripts") or {}).keys()),
                }
            except Exception as e:
                results[path] = {"type": "node", "error": f"parse failed: {e}"}
        elif path.endswith("requirements.txt"):
            deps = []
            for line in content.splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    deps.append(line)
            results[path] = {"type": "python", "deps": deps, "count": len(deps)}
        elif path.endswith("pyproject.toml"):
            # Simple TOML scan — no tomli dep needed; grep deps section.
            deps_block = []
            in_deps = False
            for line in content.splitlines():
                if "[tool.poetry.dependencies]" in line or "[project]" in line or "dependencies = [" in line:
                    in_deps = True
                    continue
                if in_deps and line.startswith("[") and "]" in line and "[tool" not in line:
                    break
                if in_deps and line.strip():
                    deps_block.append(line.strip())
            results[path] = {"type": "python_toml", "raw": deps_block[:50]}
        else:
            # Pipfile / lockfiles — just record presence
            results[path] = {"type": "lockfile", "present": True}

    if not results:
        return {
            "ok": False,
            "error": "No dependency manifest found. Checked: " + ", ".join(paths_to_try),
        }
    return {"ok": True, "manifests": results, "count": len(results)}


# ── SKILL 3: get_env_vars — list required env vars ────────────────────

async def get_env_vars(ctx: dict, args: dict) -> dict:
    """Discover env vars the project expects. Reads:
      • .env.example, .env.sample, .env.template
      • backend/.env.example
      • frontend/.env.example
    Returns deduplicated key list.
    """
    user_id    = ctx.get("user_id")
    project_id = ctx.get("project_id")

    proj = await _resolve_project(user_id, project_id)
    if not proj:
        return {"ok": False, "error": "No project connected"}

    owner  = proj.get("github_owner")
    repo   = proj.get("github_repo")
    branch = proj.get("branch") or "main"
    token  = proj.get("github_token") or None

    candidates = [
        ".env.example", ".env.sample", ".env.template",
        "backend/.env.example", "frontend/.env.example",
        "config/.env.example",
    ]

    keys: dict[str, dict] = {}

    async def _fetch(p: str) -> tuple[str, str | None]:
        return p, await _gh_fetch_file(owner, repo, p, branch, token)

    fetched = await asyncio.gather(*[_fetch(p) for p in candidates])
    sources_found = []

    for path, content in fetched:
        if not content:
            continue
        sources_found.append(path)
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key = line.split("=", 1)[0].strip()
            if not key or not key.replace("_", "").isalnum():
                continue
            if key not in keys:
                example_val = line.split("=", 1)[1].strip()
                keys[key] = {
                    "source": path,
                    "example": example_val[:120],
                }

    if not keys:
        return {
            "ok": False,
            "error": "No .env.example or similar template found.",
            "checked": candidates,
        }
    return {
        "ok":      True,
        "keys":    sorted(keys.keys()),
        "details": keys,
        "sources": sources_found,
        "count":   len(keys),
    }


# ── SKILL 4: detect_framework — auto-detect tech stack ────────────────

async def detect_framework(ctx: dict, args: dict) -> dict:
    """Detect the project's framework(s) from package.json / requirements.txt
    / file layout. Returns ranked stack labels.
    """
    user_id    = ctx.get("user_id")
    project_id = ctx.get("project_id")

    proj = await _resolve_project(user_id, project_id)
    if not proj:
        return {"ok": False, "error": "No project connected"}

    owner  = proj.get("github_owner")
    repo   = proj.get("github_repo")
    branch = proj.get("branch") or "main"
    token  = proj.get("github_token") or None

    # Pull a few key files in parallel
    paths = ["package.json", "requirements.txt", "backend/requirements.txt",
             "frontend/package.json", "next.config.js", "vite.config.js",
             "vite.config.ts", "manage.py", "main.py", "app.py",
             "server.py", "backend/main.py", "backend/server.py"]
    fetched = await asyncio.gather(
        *[_gh_fetch_file(owner, repo, p, branch, token) for p in paths]
    )
    by_path = {p: c for p, c in zip(paths, fetched) if c}

    detected: list[dict] = []

    # Frontend
    pkg_txt = by_path.get("package.json") or by_path.get("frontend/package.json") or ""
    pkg_lower = pkg_txt.lower()
    if pkg_txt:
        try:
            import json as _json
            pkg = _json.loads(pkg_txt)
            deps = {**(pkg.get("dependencies") or {}), **(pkg.get("devDependencies") or {})}
            if "next" in deps:
                detected.append({"layer": "frontend", "stack": "Next.js",
                                 "version": deps.get("next"), "evidence": "package.json: next"})
            elif "vite" in deps and "react" in deps:
                detected.append({"layer": "frontend", "stack": "Vite + React",
                                 "version": deps.get("react"), "evidence": "package.json: vite+react"})
            elif "react" in deps:
                detected.append({"layer": "frontend", "stack": "React (CRA)",
                                 "version": deps.get("react"), "evidence": "package.json: react"})
            elif "vue" in deps:
                detected.append({"layer": "frontend", "stack": "Vue",
                                 "version": deps.get("vue"), "evidence": "package.json: vue"})
            elif "svelte" in deps:
                detected.append({"layer": "frontend", "stack": "Svelte",
                                 "version": deps.get("svelte"), "evidence": "package.json: svelte"})
        except Exception:
            if "react" in pkg_lower:
                detected.append({"layer": "frontend", "stack": "React (parse failed)",
                                 "evidence": "package.json mentions react"})

    # Backend Python
    req_txt = (by_path.get("requirements.txt") or by_path.get("backend/requirements.txt") or "").lower()
    has_main_py = bool(by_path.get("main.py") or by_path.get("backend/main.py"))
    has_manage = "manage.py" in by_path
    if "fastapi" in req_txt:
        detected.append({"layer": "backend", "stack": "FastAPI",
                         "evidence": "requirements.txt: fastapi"})
    elif has_manage or "django" in req_txt:
        detected.append({"layer": "backend", "stack": "Django",
                         "evidence": "manage.py or requirements.txt: django"})
    elif "flask" in req_txt:
        detected.append({"layer": "backend", "stack": "Flask",
                         "evidence": "requirements.txt: flask"})
    elif has_main_py and "uvicorn" in req_txt:
        detected.append({"layer": "backend", "stack": "Python (uvicorn)",
                         "evidence": "main.py + uvicorn dep"})

    # Database
    if "motor" in req_txt or "pymongo" in req_txt:
        detected.append({"layer": "database", "stack": "MongoDB",
                         "evidence": "requirements.txt: motor/pymongo"})
    if "psycopg" in req_txt or "asyncpg" in req_txt:
        detected.append({"layer": "database", "stack": "PostgreSQL",
                         "evidence": "requirements.txt: psycopg/asyncpg"})
    if "redis" in req_txt:
        detected.append({"layer": "cache", "stack": "Redis",
                         "evidence": "requirements.txt: redis"})

    if not detected:
        return {"ok": False, "error": "Could not detect framework — no recognised "
                                       "manifest files found in repo root or backend/."}

    return {
        "ok":       True,
        "detected": detected,
        "count":    len(detected),
        "hint":     "Use this to guide code-generation conventions.",
    }


# ── SKILL 5: get_commit_history — recent commits list ─────────────────

async def get_commit_history(ctx: dict, args: dict) -> dict:
    """List recent commits on the connected branch.

    args:
      max?     int — number of commits (default 10, cap 30)
      path?    str — limit to commits touching this file/folder
    """
    user_id    = ctx.get("user_id")
    project_id = ctx.get("project_id")
    max_n      = min(int((args or {}).get("max") or 10), 30)
    path_filter = (args or {}).get("path") or ""

    proj = await _resolve_project(user_id, project_id)
    if not proj:
        return {"ok": False, "error": "No project connected"}

    owner  = proj.get("github_owner")
    repo   = proj.get("github_repo")
    branch = proj.get("branch") or "main"
    token  = proj.get("github_token") or None
    if not owner or not repo:
        return {"ok": False, "error": "Project missing github_owner/repo"}

    params = {"sha": branch, "per_page": max_n}
    if path_filter:
        params["path"] = path_filter

    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.get(
                f"{GITHUB_API}/repos/{owner}/{repo}/commits",
                params=params, headers=_gh_headers(token),
            )
    except httpx.RequestError as e:
        return {"ok": False, "error": f"GitHub network error: {e}"}

    if r.status_code == 404:
        return {"ok": False, "error": f"Repo or branch not found: {owner}/{repo}@{branch}"}
    if r.status_code >= 400:
        return {"ok": False, "error": f"GitHub {r.status_code}: {r.text[:200]}"}

    commits = []
    for c in (r.json() or [])[:max_n]:
        commit = c.get("commit") or {}
        author = commit.get("author") or {}
        commits.append({
            "sha":     (c.get("sha") or "")[:7],
            "message": (commit.get("message") or "").split("\n")[0][:200],
            "author":  author.get("name") or "",
            "date":    author.get("date") or "",
            "url":     c.get("html_url") or "",
        })

    return {
        "ok":      True,
        "branch":  branch,
        "count":   len(commits),
        "commits": commits,
        "hint":    "Use get_commit_diff with a SHA to see exactly what changed.",
    }


# ── SKILL 6: list_issues — GitHub issues ──────────────────────────────

async def list_issues(ctx: dict, args: dict) -> dict:
    """List open (or recent) GitHub issues on the connected repo.

    args:
      state?  'open'|'closed'|'all'  default 'open'
      label?  str — filter by label
      max?    int — default 10, cap 30
    """
    user_id    = ctx.get("user_id")
    project_id = ctx.get("project_id")
    state      = (args or {}).get("state") or "open"
    label      = (args or {}).get("label") or ""
    max_n      = min(int((args or {}).get("max") or 10), 30)

    if state not in ("open", "closed", "all"):
        state = "open"

    proj = await _resolve_project(user_id, project_id)
    if not proj:
        return {"ok": False, "error": "No project connected"}

    owner = proj.get("github_owner")
    repo  = proj.get("github_repo")
    token = proj.get("github_token") or None
    if not owner or not repo:
        return {"ok": False, "error": "Project missing github_owner/repo"}

    params = {"state": state, "per_page": max_n}
    if label:
        params["labels"] = label

    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.get(
                f"{GITHUB_API}/repos/{owner}/{repo}/issues",
                params=params, headers=_gh_headers(token),
            )
    except httpx.RequestError as e:
        return {"ok": False, "error": f"GitHub network error: {e}"}

    if r.status_code >= 400:
        return {"ok": False, "error": f"GitHub {r.status_code}: {r.text[:200]}"}

    issues = []
    for it in (r.json() or [])[:max_n]:
        # GitHub /issues endpoint returns PRs too — filter them out
        if it.get("pull_request"):
            continue
        issues.append({
            "number":  it.get("number"),
            "title":   (it.get("title") or "")[:200],
            "state":   it.get("state"),
            "author":  (it.get("user") or {}).get("login"),
            "labels":  [lb.get("name") for lb in (it.get("labels") or [])],
            "created": it.get("created_at"),
            "url":     it.get("html_url"),
            "body_preview": (it.get("body") or "")[:240],
        })

    return {
        "ok":     True,
        "state":  state,
        "count":  len(issues),
        "issues": issues,
    }


# ── SKILL 7: get_pr_comments — PR review feedback ─────────────────────

async def get_pr_comments(ctx: dict, args: dict) -> dict:
    """Fetch comments + review threads on a Pull Request.

    args:
      pr_number   int (required)
    """
    user_id    = ctx.get("user_id")
    project_id = ctx.get("project_id")
    pr_number  = (args or {}).get("pr_number")

    if not pr_number:
        return {"ok": False, "error": "pr_number is required"}
    try:
        pr_number = int(pr_number)
    except Exception:
        return {"ok": False, "error": "pr_number must be int"}

    proj = await _resolve_project(user_id, project_id)
    if not proj:
        return {"ok": False, "error": "No project connected"}

    owner = proj.get("github_owner")
    repo  = proj.get("github_repo")
    token = proj.get("github_token") or None
    if not owner or not repo:
        return {"ok": False, "error": "Project missing github_owner/repo"}

    async with httpx.AsyncClient(timeout=15.0) as c:
        # Issue comments (top-level PR conversation)
        try:
            ic = await c.get(
                f"{GITHUB_API}/repos/{owner}/{repo}/issues/{pr_number}/comments",
                headers=_gh_headers(token), params={"per_page": 30},
            )
            rc = await c.get(
                f"{GITHUB_API}/repos/{owner}/{repo}/pulls/{pr_number}/comments",
                headers=_gh_headers(token), params={"per_page": 30},
            )
        except httpx.RequestError as e:
            return {"ok": False, "error": f"GitHub network error: {e}"}

    if ic.status_code == 404 or rc.status_code == 404:
        return {"ok": False, "error": f"PR #{pr_number} not found"}

    issue_comments = [
        {
            "author":  (it.get("user") or {}).get("login"),
            "body":    (it.get("body") or "")[:600],
            "created": it.get("created_at"),
        }
        for it in (ic.json() or [])
    ] if ic.status_code == 200 else []

    review_comments = [
        {
            "author":   (it.get("user") or {}).get("login"),
            "file":     it.get("path"),
            "line":     it.get("line") or it.get("original_line"),
            "body":     (it.get("body") or "")[:600],
            "created":  it.get("created_at"),
        }
        for it in (rc.json() or [])
    ] if rc.status_code == 200 else []

    return {
        "ok":              True,
        "pr_number":       pr_number,
        "issue_comments":  issue_comments,
        "review_comments": review_comments,
        "count":           len(issue_comments) + len(review_comments),
    }


# ── SKILL 8: find_package_docs — npm / PyPI lookup ────────────────────

async def find_package_docs(ctx: dict, args: dict) -> dict:
    """Look up an npm or PyPI package's metadata + docs.

    args:
      name      str (required)
      registry? 'npm' | 'pypi'  — auto-detected if omitted
    """
    name = ((args or {}).get("name") or "").strip()
    registry = ((args or {}).get("registry") or "").strip().lower()

    if not name:
        return {"ok": False, "error": "name required"}
    # Sanitise
    if not all(ch.isalnum() or ch in "-_./@" for ch in name):
        return {"ok": False, "error": "invalid package name"}

    async def _npm() -> dict | None:
        try:
            async with httpx.AsyncClient(timeout=10.0) as c:
                r = await c.get(f"{NPM_REGISTRY}/{name}")
            if r.status_code != 200:
                return None
            d = r.json() or {}
            latest = (d.get("dist-tags") or {}).get("latest")
            return {
                "registry": "npm",
                "name":     d.get("name"),
                "latest":   latest,
                "description": (d.get("description") or "")[:400],
                "homepage": d.get("homepage"),
                "repository": (d.get("repository") or {}).get("url"),
                "license":  d.get("license"),
                "keywords": (d.get("keywords") or [])[:10],
                "npm_url":  f"https://www.npmjs.com/package/{name}",
            }
        except Exception:
            return None

    async def _pypi() -> dict | None:
        try:
            async with httpx.AsyncClient(timeout=10.0) as c:
                r = await c.get(f"{PYPI_API}/{name}/json")
            if r.status_code != 200:
                return None
            d = (r.json() or {}).get("info") or {}
            return {
                "registry":    "pypi",
                "name":        d.get("name"),
                "latest":      d.get("version"),
                "description": (d.get("summary") or "")[:400],
                "homepage":    d.get("home_page") or d.get("project_url"),
                "repository":  (d.get("project_urls") or {}).get("Source")
                               or (d.get("project_urls") or {}).get("Repository"),
                "license":     d.get("license"),
                "author":      d.get("author"),
                "pypi_url":    f"https://pypi.org/project/{name}/",
            }
        except Exception:
            return None

    if registry == "npm":
        info = await _npm()
    elif registry == "pypi":
        info = await _pypi()
    else:
        # Try both in parallel — pick whichever responds 200.
        npm_info, pypi_info = await asyncio.gather(_npm(), _pypi())
        info = npm_info or pypi_info

    if not info:
        return {
            "ok":    False,
            "error": f"Package '{name}' not found on npm or PyPI",
        }
    return {"ok": True, **info}


# ── SKILL 9: validate_syntax — Python AST check ───────────────────────

async def validate_syntax(ctx: dict, args: dict) -> dict:
    """Validate a Python code snippet using ast.parse() — no execution.

    args:
      code      str (required)
      language? 'python' (default; only python supported locally)
    """
    code = (args or {}).get("code") or ""
    lang = ((args or {}).get("language") or "python").lower()

    if not code or not isinstance(code, str):
        return {"ok": False, "error": "code required (string)"}
    if lang != "python":
        return {
            "ok": False,
            "error": f"language '{lang}' not supported locally — "
                     "use e2b_run_code for JS/TS validation.",
        }

    try:
        tree = ast.parse(code)
        # Count top-level defs/imports for a "what's in here" summary
        funcs   = sum(1 for n in ast.walk(tree) if isinstance(n, ast.FunctionDef))
        a_funcs = sum(1 for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef))
        classes = sum(1 for n in ast.walk(tree) if isinstance(n, ast.ClassDef))
        imports = sum(1 for n in ast.walk(tree)
                      if isinstance(n, (ast.Import, ast.ImportFrom)))
        return {
            "ok":        True,
            "valid":     True,
            "language":  "python",
            "summary":   {
                "functions":       funcs,
                "async_functions": a_funcs,
                "classes":         classes,
                "imports":         imports,
                "lines":           len(code.splitlines()),
            },
        }
    except SyntaxError as e:
        return {
            "ok":        True,
            "valid":     False,
            "language":  "python",
            "error":     str(e),
            "line":      e.lineno,
            "offset":    e.offset,
            "hint":      "Fix the syntax error before shipping.",
        }


# ── SKILL 10: e2b_run_code — sandbox execution ────────────────────────

async def e2b_run_code(ctx: dict, args: dict) -> dict:
    """Execute a Python snippet in an isolated e2b.dev sandbox.

    Use ONLY when you need to PROVE a snippet runs (e.g. validate a
    regex, test an algorithm). Not for repo-wide tests — that needs
    the full source tree, which the worker handles after Ship.

    args:
      code      str (required) — Python source to run
      timeout?  int            — sandbox timeout sec (default 10, cap 30)
    """
    code = (args or {}).get("code") or ""
    timeout = min(int((args or {}).get("timeout") or 10), 30)

    if not code or not isinstance(code, str):
        return {"ok": False, "error": "code required (string)"}
    if len(code) > 8000:
        return {
            "ok": False,
            "error": "code too long for sandbox (cap 8000 chars). "
                     "Inline only the snippet under test.",
        }

    res = await run_python_check(code, timeout=timeout)
    # Normalise: sandbox returns {ok, skipped, stdout, stderr, ...}
    if res.get("skipped"):
        return {
            "ok":      False,
            "skipped": True,
            "reason":  res.get("reason", "sandbox unavailable"),
            "hint":    "E2B_API_KEY may be missing on server. "
                       "Use validate_syntax for AST-only checks instead.",
        }
    return {
        "ok":        bool(res.get("ok")),
        "stdout":    res.get("stdout", ""),
        "stderr":    res.get("stderr", ""),
        "exit_code": res.get("exit_code"),
    }


# ── Tool catalog ──────────────────────────────────────────────────────

DEV_TOOL_SPECS: list[dict] = [
    {
        "name": "find_usages",
        "description": (
            "Find every place a function/class/variable is referenced in the "
            "connected repo. USE BEFORE refactoring a symbol so you know every "
            "caller that must be updated. Returns file paths + first matching "
            "line per file."
        ),
        "args_spec": {
            "symbol": "string — function/class name (alphanumeric + underscore)",
            "kind":   "optional 'function'|'class'|'variable' (hint only)",
            "max":    "optional int — default 15, cap 30",
        },
    },
    {
        "name": "get_dependencies",
        "description": (
            "Return the project's declared dependencies — reads package.json, "
            "requirements.txt, pyproject.toml across root + backend/ + frontend/. "
            "USE when the user asks 'can we use library X' or 'what version of "
            "Y do we have' or before suggesting a new dep."
        ),
        "args_spec": {},
    },
    {
        "name": "get_env_vars",
        "description": (
            "List every environment variable the project expects — discovers "
            "keys from .env.example / .env.sample / .env.template files. USE "
            "when the user asks about setup, deployment, missing config, or "
            "before suggesting code that reads a new env var."
        ),
        "args_spec": {},
    },
    {
        "name": "detect_framework",
        "description": (
            "Auto-detect the project's tech stack — frontend framework, backend "
            "framework, database. Returns ranked stack labels with evidence. "
            "USE if you're unsure of the conventions to follow (e.g. is this "
            "Next.js or Vite? FastAPI or Django?)."
        ),
        "args_spec": {},
    },
    {
        "name": "get_commit_history",
        "description": (
            "List recent commits on the connected branch — sha, message, author, "
            "date, GitHub URL. USE to understand recent activity, find regressions, "
            "or trace when a file last changed. Pair with get_commit_diff for "
            "a specific SHA."
        ),
        "args_spec": {
            "max":  "optional int — default 10, cap 30",
            "path": "optional string — limit to commits touching this path",
        },
    },
    {
        "name": "list_issues",
        "description": (
            "List GitHub issues on the connected repo (default: open). USE when "
            "the user asks 'what bugs do we have?' or 'show me the backlog' or "
            "to find related issues before shipping a fix."
        ),
        "args_spec": {
            "state": "optional 'open'|'closed'|'all' — default 'open'",
            "label": "optional string — filter by label",
            "max":   "optional int — default 10, cap 30",
        },
    },
    {
        "name": "get_pr_comments",
        "description": (
            "Fetch every comment + review thread on a Pull Request. USE when "
            "iterating on a PR you opened (e.g. via push_fix) and need to read "
            "reviewer feedback before shipping changes."
        ),
        "args_spec": {
            "pr_number": "int — PR number",
        },
    },
    {
        "name": "find_package_docs",
        "description": (
            "Look up an npm or PyPI package's latest version, description, "
            "homepage, license, and source URL. USE before suggesting a new dep "
            "so you cite real version + license info, not training-data hallucination."
        ),
        "args_spec": {
            "name":     "string — package name (e.g. 'fastapi', 'react-query')",
            "registry": "optional 'npm'|'pypi' — auto-detected if omitted",
        },
    },
    {
        "name": "validate_syntax",
        "description": (
            "Validate a Python code snippet using ast.parse() — fast, no "
            "execution, no sandbox. USE before pasting a Python snippet into "
            "the user's repo via the handoff brief, to PROVE the syntax is "
            "valid. For JS/TS or runtime checks use e2b_run_code instead."
        ),
        "args_spec": {
            "code":     "string — Python source to validate",
            "language": "optional 'python' (default)",
        },
    },
    {
        "name": "e2b_run_code",
        "description": (
            "Execute a small Python snippet inside an isolated e2b.dev sandbox "
            "to prove it actually runs. USE for short proofs (regex test, "
            "algorithm sanity-check) — NOT for full repo tests. Returns "
            "stdout/stderr/exit_code. Cap 8000 chars."
        ),
        "args_spec": {
            "code":    "string — Python source",
            "timeout": "optional int sec — default 10, cap 30",
        },
    },
]


DEV_TOOLS = {
    "find_usages":        find_usages,
    "get_dependencies":   get_dependencies,
    "get_env_vars":       get_env_vars,
    "detect_framework":   detect_framework,
    "get_commit_history": get_commit_history,
    "list_issues":        list_issues,
    "get_pr_comments":    get_pr_comments,
    "find_package_docs":  find_package_docs,
    "validate_syntax":    validate_syntax,
    "e2b_run_code":       e2b_run_code,
}
