"""
services/codebase_indexer.py — Cached, role-tagged repo indexer.

Pulls the latest files from a customer's GitHub repo using their saved
PAT, walks the tree, tags each file as routes / models / components /
deps / other, and exposes `build_context_block(user_id)` which returns
the system-prompt block the AUREM chat injects before every turn.

Storage: `cto_codebase_index` — one document per (user_id, project_id):
  { user_id, project_id, repo_owner, repo_name, default_branch,
    refreshed_at, file_count, total_bytes,
    files: [{ path, sha, size, lang, role, snippet }],
    deps:  { python: [...], node: [...] } }

Why role-tagged + capped: we never want to blow the LLM context budget
on a 5,000-file monorepo. The cap is 80 files, 12 kB each, and the
prompt block trims to `max_chars=6000` by default.
"""
from __future__ import annotations

import asyncio
import base64 as _b64
import json as _json
import logging
import re
import time
from typing import Any, Optional

import httpx
from fastapi import APIRouter, HTTPException, Header

from cto_services.auth import current_dev
from cto_services.db import get_db, require_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/codebase", tags=["AUREM Codebase"])


GITHUB_API = "https://api.github.com"
INDEX_TTL_SECONDS = 600           # don't hammer GitHub
MAX_FILES = 80                    # LLM context budget cap
MAX_BYTES_PER_FILE = 12_000       # only first chunk goes into the index

ROLE_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(^|/)(routers?|api|server|app)\.py$"),                  "routes"),
    (re.compile(r"(^|/)routers/.+\.py$"),                                  "routes"),
    (re.compile(r"(^|/)(models|schemas)/.+\.py$"),                         "models"),
    (re.compile(r"(^|/)(components|pages|app)/.+\.(tsx?|jsx?)$"),          "components"),
    (re.compile(r"(^|/)(requirements\.txt|pyproject\.toml|package\.json)$"), "deps"),
]


def _detect_role(path: str) -> str:
    for rx, role in ROLE_RULES:
        if rx.search(path):
            return role
    return "other"


def _detect_lang(path: str) -> str:
    if path.endswith(".py"):
        return "python"
    if path.endswith((".ts", ".tsx", ".js", ".jsx")):
        return "js"
    if path.endswith((".json",)):
        return "json"
    if path.endswith((".md",)):
        return "md"
    if path.endswith((".yml", ".yaml")):
        return "yaml"
    return "other"


async def _gh_get(client: httpx.AsyncClient, url: str, pat: str) -> Any:
    r = await client.get(url, headers={
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {pat}",
        "X-GitHub-Api-Version": "2022-11-28",
    }, timeout=15.0)
    if r.status_code == 401:
        raise HTTPException(401, "github_pat_invalid")
    if r.status_code == 404:
        raise HTTPException(404, "github_repo_not_found")
    if r.status_code >= 500:
        raise HTTPException(502, f"github_upstream_{r.status_code}")
    r.raise_for_status()
    return r.json()


_REPO_RE = re.compile(r"github\.com[:/](?P<owner>[^/]+)/(?P<name>[^/.]+)")


def _parse_repo_url(repo_url: str) -> tuple[str, str]:
    m = _REPO_RE.search(repo_url or "")
    if not m:
        raise HTTPException(400, "invalid_repo_url")
    return m.group("owner"), m.group("name")


async def refresh_index(user_id: str, project_id: str,
                        repo_url: str, pat: str) -> dict[str, Any]:
    """Walks the tree, fetches up to MAX_FILES blob contents in parallel,
    writes the cached index doc, returns a summary."""
    db = get_db()
    if db is None:
        raise HTTPException(503, "db_not_ready")
    owner, name = _parse_repo_url(repo_url)
    async with httpx.AsyncClient() as c:
        repo = await _gh_get(c, f"{GITHUB_API}/repos/{owner}/{name}", pat)
        branch = repo.get("default_branch", "main")
        tree = await _gh_get(
            c, f"{GITHUB_API}/repos/{owner}/{name}/git/trees/{branch}?recursive=1",
            pat,
        )
        entries = [t for t in (tree.get("tree") or [])
                   if t.get("type") == "blob"][:MAX_FILES]

        sem = asyncio.Semaphore(10)   # be polite to GitHub
        files: list[dict] = []
        deps = {"python": [], "node": []}

        async def fetch_one(entry):
            path = entry["path"]
            sha  = entry["sha"]
            size = entry.get("size", 0)
            role = _detect_role(path)
            lang = _detect_lang(path)
            async with sem:
                try:
                    blob = await _gh_get(c,
                        f"{GITHUB_API}/repos/{owner}/{name}/contents/{path}?ref={branch}",
                        pat,
                    )
                    content = blob.get("content") or ""
                    if blob.get("encoding", "base64") == "base64":
                        try:
                            txt = _b64.b64decode(content).decode("utf-8", errors="replace")
                        except Exception:
                            txt = ""
                    else:
                        txt = content
                    snippet = txt[:MAX_BYTES_PER_FILE]
                except HTTPException:
                    snippet = ""
                except Exception as e:
                    logger.debug(f"[indexer] {path} skipped: {e}")
                    snippet = ""
            files.append({
                "path": path, "sha": sha, "size": size,
                "lang": lang, "role": role, "snippet": snippet,
            })
            if path.endswith("requirements.txt"):
                deps["python"] = [
                    ln.split("==")[0].strip()
                    for ln in snippet.splitlines()
                    if ln.strip() and not ln.startswith("#")
                ][:80]
            elif path.endswith("package.json"):
                try:
                    pj = _json.loads(snippet) if snippet else {}
                    deps["node"] = sorted(
                        list((pj.get("dependencies") or {}).keys())
                        + list((pj.get("devDependencies") or {}).keys())
                    )[:80]
                except Exception:
                    pass

        await asyncio.gather(*(fetch_one(e) for e in entries))

    doc = {
        "user_id":        user_id,
        "project_id":     project_id,
        "repo_owner":     owner,
        "repo_name":      name,
        "default_branch": branch,
        "refreshed_at":   time.time(),
        "file_count":     len(files),
        "total_bytes":    sum(f["size"] or 0 for f in files),
        "files":          files,
        "deps":           deps,
    }
    await db.cto_codebase_index.update_one(
        {"user_id": user_id, "project_id": project_id},
        {"$set": doc}, upsert=True,
    )
    return {
        "ok": True, "owner": owner, "name": name, "branch": branch,
        "file_count": len(files),
        "python_deps": deps["python"][:10],
        "node_deps":   deps["node"][:10],
    }


def _format_context_block(doc: dict, max_chars: int = 6000) -> str:
    """Renders the cached index into the system-prompt block AUREM reads
    at the top of every turn. Trims to `max_chars` so we never blow the
    context budget."""
    parts: list[str] = []
    parts.append(
        f"\n\nCUSTOMER CODEBASE CONTEXT (auto-injected — repo "
        f"{doc.get('repo_owner')}/{doc.get('repo_name')}@"
        f"{doc.get('default_branch','main')}, "
        f"{doc.get('file_count', 0)} files):"
    )
    deps = doc.get("deps") or {}
    if deps.get("python"):
        parts.append("Python deps: " + ", ".join(deps["python"][:30]))
    if deps.get("node"):
        parts.append("Node deps: " + ", ".join(deps["node"][:30]))

    by_role: dict[str, list[dict]] = {}
    for f in (doc.get("files") or []):
        by_role.setdefault(f.get("role", "other"), []).append(f)

    role_caps = {"routes": 6, "models": 4, "components": 4, "deps": 0, "other": 0}
    for role in ("routes", "models", "components"):
        items = by_role.get(role, [])[: role_caps[role]]
        if not items:
            continue
        parts.append(f"\n— {role.upper()} ({len(items)} shown):")
        for f in items:
            snippet = (f.get("snippet") or "").strip()
            if not snippet:
                continue
            preview = snippet[:600].replace("```", "´´´")
            parts.append(f"### {f['path']}\n```{f.get('lang','')}\n{preview}\n```")

    block = "\n".join(parts)
    if len(block) > max_chars:
        block = block[:max_chars] + "\n…(context trimmed)"
    return block


async def build_context_block(user_id: str, project_id: str,
                              max_chars: int = 6000) -> Optional[str]:
    """Returns the system-prompt context block, or None if no fresh
    index exists for (user, project)."""
    db = get_db()
    if db is None:
        return None
    doc = await db.cto_codebase_index.find_one(
        {"user_id": user_id, "project_id": project_id},
        {"_id": 0},
    )
    if not doc:
        return None
    return _format_context_block(doc, max_chars=max_chars)


# ─── HTTP surface ────────────────────────────────────────────────────

@router.post("/refresh/{project_id}")
async def refresh_route(project_id: str,
                        authorization: str = Header(None)) -> dict:
    """Refresh the cached index for the given AUREM project. Reads the
    project's `github_url` + `github_token` (already stored encrypted /
    plain in `cto_projects`)."""
    me = await current_dev(authorization)
    db = require_db()
    proj = await db.cto_projects.find_one(
        {"project_id": project_id, "user_id": me["user_id"]},
        {"_id": 0},
    )
    if not proj:
        raise HTTPException(404, "project_not_found")
    pat = proj.get("github_token") or ""
    repo_url = proj.get("github_url") or ""
    if not pat:
        raise HTTPException(400, "no_github_pat_saved")
    if not repo_url:
        raise HTTPException(400, "no_github_repo_saved")
    return await refresh_index(me["user_id"], project_id, repo_url, pat)


@router.get("/index/{project_id}")
async def get_index(project_id: str,
                    authorization: str = Header(None)) -> dict:
    me = await current_dev(authorization)
    db = require_db()
    doc = await db.cto_codebase_index.find_one(
        {"user_id": me["user_id"], "project_id": project_id},
        {"_id": 0, "files.snippet": 0},   # keep payload light for UI
    )
    if not doc:
        return {"indexed": False}
    return {"indexed": True, **doc}
