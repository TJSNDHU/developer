"""
routers/cto_projects.py — AUREM CTO multi-project system.
Connect existing client GitHub repos, run AI tasks (git pull → fix → push).
Mounted under /api/aurem-dev/cto/* to avoid clashing with /projects/* (new-project flow).
"""
from __future__ import annotations
import asyncio
import json
import logging
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from cto_services.auth import current_dev
from cto_services.db import get_db, require_db
from services.llm import call_llm
from services.usage import assert_has_budget, assert_has_task_budget, get_usage
from services.github_api_writer import (
    commit_files as gh_api_commit,
    revert_commit as gh_api_revert,
    fetch_file as gh_api_fetch_file,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/cto", tags=["AUREM CTO Projects"])

# Detect whether `git` binary is available — production containers don't
# have it (Iter 21). When missing we route to the pure-HTTP GitHub API path.
_GIT_AVAILABLE = shutil.which("git") is not None
if not _GIT_AVAILABLE:
    logger.warning(
        "`git` binary not found on this server. CTO tasks will use the "
        "GitHub REST API path (no clone, no push subprocess)."
    )

WORKSPACE = Path(os.getenv("WORKSPACE_PATH", "/tmp/aurem-dev-projects"))
WORKSPACE.mkdir(parents=True, exist_ok=True)


# ── Live progress streams (Iter 73) ──────────────────────────────────────
# In-memory per-task asyncio.Queue used to fan out worker steps to the
# /cto/tasks/{id}/stream SSE endpoint so chat bubbles render a live
# "worker tape" (reading files… thinking… committing…) instead of a
# silent spinner.  Queues are popped once the task emits a done/fail
# terminal frame, or when the stream times out (5 min wall-clock).
_task_queues: dict[str, asyncio.Queue] = {}


def _frontend_subset(edits: dict[str, str]) -> dict[str, str]:
    """Pick the files we persist on the task doc for the right-side
    `<PreviewPane />` to render after ship.

    Iter 169 — expanded beyond pure frontend files. Previously only
    html/css/js/jsx/ts/tsx were kept, which meant a backend-only ship
    (e.g. a Python services edit) left the user's "</> Code" button
    pointing at literally nothing. Now we also keep `.py`, `.json`,
    `.yaml`, `.yml`, `.md`, `.sql`, `.sh`, `.toml`, and `.env.example`
    so backend ships show their actual code, not just the live URL.

    Cap: 12 files × 32 KB each = ~384 KB max stored per task. Anything
    bigger gets dropped (the user can still view the live URL)."""
    out: dict[str, str] = {}
    _ALLOWED_EXT = (
        ".html", ".css", ".js", ".jsx", ".ts", ".tsx",
        ".py", ".json", ".yaml", ".yml", ".md",
        ".sql", ".sh", ".toml",
    )
    for path, body in (edits or {}).items():
        if not isinstance(body, str):
            continue
        path_l = path.lower()
        if path_l.endswith(".env.example"):
            pass  # explicit allow
        elif not path_l.endswith(_ALLOWED_EXT):
            continue
        if len(body) > 32_000:
            continue
        out[path] = body
        if len(out) >= 12:
            break
    return out


async def _emit(task_id: str, step: str,
                kind: str = "step", pct: Optional[int] = None,
                **extra) -> None:
    """Push one progress frame onto the task's live SSE queue.

    Non-blocking; safe to call even if no consumer is listening (the queue
    just buffers up to 256 frames then drops oldest).

    Any **extra keyword args are merged into the frame so callers can ship
    structured payloads (e.g. `agents=["backend","frontend"]` for the
    parallel-mode worker tape)."""
    if not task_id:
        return
    q = _task_queues.get(task_id)
    if q is None:
        q = asyncio.Queue(maxsize=256)
        _task_queues[task_id] = q
    frame = {"type": kind, "step": step, "pct": pct, "ts": time.time()}
    if extra:
        # Don't let callers overwrite the canonical fields.
        for k, v in extra.items():
            if k not in frame:
                frame[k] = v
    try:
        q.put_nowait(frame)
    except asyncio.QueueFull:
        # Drop the oldest frame to make room for the new one rather than
        # blocking the worker — the SSE client will see a small gap.
        try:
            q.get_nowait()
            q.put_nowait(frame)
        except Exception:
            pass


# ── Models ───────────────────────────────────────────────────────────────
class AddProject(BaseModel):
    name: str
    github_url: str
    github_token: Optional[str] = None  # PAT; fall back to user's OAuth token
    branch: str = "main"
    tech_stack: Optional[str] = None
    preview_url: Optional[str] = None   # public URL of the running site/app


class TaskBody(BaseModel):
    project_id: str
    task: str
    files: List[str] = []
    context: str = ""
    auto_deploy: bool = False
    maxx_mode: bool = False     # iter 40: enable Two-Agent (DeepSeek + Claude review)


# ── Helpers ──────────────────────────────────────────────────────────────
def _parse_repo(url: str) -> tuple[str, str]:
    p = url.rstrip("/").replace(".git", "").replace("https://github.com/", "").split("/")
    if len(p) < 2:
        raise HTTPException(400, "Bad GitHub URL — expected https://github.com/owner/repo")
    return p[0], p[1]


async def _user_gh_token(user_id: str) -> Optional[str]:
    db = get_db()
    if db is None:
        return None
    u = await db.dev_users.find_one({"user_id": user_id}, {"_id": 0, "github": 1})
    return ((u or {}).get("github") or {}).get("access_token")


# ── Iter 43 — PAT encryption helpers ──────────────────────────────────
# Tokens stored in cto_projects.github_token are encrypted at rest via
# services.vault (per-customer HKDF-Fernet, v1:-prefixed ciphertext).
# Legacy rows persisted before this migration may still hold plaintext
# tokens — the decrypt helper transparently passes those through so the
# pipeline keeps working until migrations/002_encrypt_pats.py is run.

async def _encrypt_pat(user_id: str, token: Optional[str]) -> Optional[str]:
    if not token:
        return token
    if token.startswith("v1:"):
        return token   # already encrypted
    try:
        from services.vault import encrypt, is_vault_available
        if not is_vault_available():
            return token
        return await encrypt(user_id, token, kind="github_token")
    except Exception:
        return token   # fail-open: never block project creation on crypto


async def _decrypt_pat(user_id: str, token: Optional[str]) -> Optional[str]:
    if not token:
        return token
    if not token.startswith("v1:"):
        return token   # legacy plaintext — pass through
    try:
        from services.vault import decrypt
        return await decrypt(user_id, token, kind="github_token")
    except Exception:
        return None    # tamper / wrong user → treat as missing token


# Iter 165 — Brain V2 endpoints (manual rebuild + read-only inspect)


@router.post("/projects/{project_id}/build-brain")
async def build_project_brain(
    project_id: str,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Trigger a full Brain V2 scan for `project_id`.

    Auto-called on `POST /projects/add`; also exposed manually so admin
    or settings UI can rebuild after a major refactor or branch swap.
    """
    me = await current_dev(authorization)
    user_id = me["user_id"]
    db = get_db()
    proj = await db.cto_projects.find_one(
        {"project_id": project_id, "user_id": user_id},
        {"_id": 0, "repo_index_summary": 0, "brain_text": 0,
         "repo_index_blocks": 0, "last_commit_diff": 0}
    )
    if not proj:
        raise HTTPException(404, "Project not found")

    gh_token = await _decrypt_pat(user_id, proj.get("github_token")) \
        or await _user_gh_token(user_id)
    gh_owner = proj.get("github_owner") or ""
    gh_repo  = proj.get("github_repo") or ""
    branch   = proj.get("branch") or "main"
    if not (gh_token and gh_owner and gh_repo):
        raise HTTPException(400, "GitHub not connected to this project")

    from services.project_brain import build_brain_v2
    brain = await build_brain_v2(
        db, project_id, user_id, gh_token, gh_owner, gh_repo, branch,
    )
    return {
        "ok": True,
        "brain_version":    brain.get("version"),
        "structure_keys":   list((brain.get("structure") or {}).keys()),
        "stack":            brain.get("stack") or {},
        "task_count":       brain.get("task_count", 0),
        "next_refresh_at":  brain.get("next_full_refresh_at"),
        "hot_paths":        brain.get("hot_paths") or [],
    }


@router.get("/projects/{project_id}/brain")
async def get_project_brain(
    project_id: str,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Read-only view of the V2 brain — for the Settings page so
    users can confirm what the agent knows about their repo."""
    me = await current_dev(authorization)
    user_id = me["user_id"]
    db = get_db()
    proj = await db.cto_projects.find_one(
        {"project_id": project_id, "user_id": user_id},
        {"_id": 0, "project_id": 1},
    )
    if not proj:
        raise HTTPException(404, "Project not found")
    from services.project_brain import get_brain_v2, format_brain_for_agent
    brain = await get_brain_v2(db, project_id, user_id)
    return {
        "ok": True,
        "exists": bool(brain),
        "brain": brain,
        "summary": format_brain_for_agent(brain) if brain else "",
    }


# Iter 165 — Warm Start endpoints. Fired on project SELECT so the next
# chat turn already has fresh commit history + file tree + stack
# context cached in MongoDB. 4 agents run in parallel, each capped at
# 8s so the slowest GitHub call can't stall the warm-start job.


@router.post("/projects/{project_id}/warm-start")
async def warm_start_project(
    project_id: str,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Trigger 4 parallel background agents to pre-load project context.

    Returns the `job_id` immediately so the frontend can stream progress
    via the status endpoint while the user types their first prompt.
    """
    import uuid as _uuid
    me = await current_dev(authorization)
    user_id = me["user_id"]
    db = get_db()
    proj = await db.cto_projects.find_one(
        {"project_id": project_id, "user_id": user_id},
        {"_id": 0, "repo_index_summary": 0, "brain_text": 0,
         "repo_index_blocks": 0, "last_commit_diff": 0}
    )
    if not proj:
        raise HTTPException(404, "Project not found")

    gh_token = await _decrypt_pat(user_id, proj.get("github_token")) \
        or await _user_gh_token(user_id)
    gh_owner = proj.get("github_owner") or ""
    gh_repo  = proj.get("github_repo") or ""
    branch   = proj.get("branch") or "main"
    if not (gh_token and gh_owner and gh_repo):
        return {
            "ok": True, "job_id": None,
            "message": "No GitHub connection — warm-start skipped",
            "status": "no_token",
        }

    job_id = f"ws_{_uuid.uuid4().hex[:10]}"
    started_at = time.time()
    await db.warm_start_jobs.insert_one({
        "job_id":        job_id,
        "project_id":    project_id,
        "user_id":       user_id,
        "status":        "running",
        "started_at":    started_at,
        "agents_done":   [],
        "agents_total":  ["brain", "recent", "structure", "stack", "graph"],
    })

    asyncio.create_task(_run_warm_agents(
        job_id=job_id, project_id=project_id, user_id=user_id,
        gh_token=gh_token, gh_owner=gh_owner, gh_repo=gh_repo,
        branch=branch, db=db,
    ))
    return {
        "ok": True, "job_id": job_id,
        "message": "Warming up — agents loading your project",
    }


async def _run_warm_agents(
    *, job_id: str, project_id: str, user_id: str,
    gh_token: str, gh_owner: str, gh_repo: str, branch: str, db,
) -> None:
    """Background: 4 agents in parallel. Each pushes its result into
    `warm_start_jobs` as soon as it finishes."""
    from services.project_brain import (
        _gh_list_files, _gh_read_small,
        build_brain_v2, get_brain_v2,
    )

    async def _mark_done(agent: str) -> None:
        try:
            # Iter 212m-15 — $addToSet (not $push) so the outer
            # `_bounded` wrapper can safely re-mark a timed-out agent
            # without double-counting and pushing the progress bar
            # past 100%.
            await db.warm_start_jobs.update_one(
                {"job_id": job_id},
                {"$addToSet": {"agents_done": agent}},
            )
        except Exception:
            pass

    # Agent 1 — Brain V2 build/refresh (skip if scanned <10 min ago)
    async def agent_brain() -> None:
        try:
            existing = await get_brain_v2(db, project_id, user_id)
            age = time.time() - float(existing.get("last_scan", 0) or 0)
            if not existing or age > 600:
                await build_brain_v2(
                    db, project_id, user_id,
                    gh_token, gh_owner, gh_repo, branch,
                )
        except Exception as e:
            logger.warning("warm-start brain agent: %r", e)
        finally:
            await _mark_done("brain")

    # Agent 2 — Recent commits via direct GitHub REST
    async def agent_recent() -> None:
        try:
            import httpx
            async with httpx.AsyncClient(timeout=8.0) as cx:
                r = await cx.get(
                    f"https://api.github.com/repos/{gh_owner}/{gh_repo}/commits",
                    params={"per_page": 5, "sha": branch},
                    headers={
                        "Authorization": f"token {gh_token}",
                        "Accept": "application/vnd.github+json",
                        "User-Agent": "aurem-warm-start",
                    },
                )
            commits_summary = ""
            if r.status_code == 200:
                items = r.json() or []
                lines: list[str] = []
                for c in items[:5]:
                    sha = (c.get("sha") or "")[:7]
                    commit_obj = c.get("commit") or {}
                    msg = (commit_obj.get("message") or "").splitlines()[0][:80]
                    author = (commit_obj.get("author") or {}).get("name", "")
                    lines.append(f"{sha} · {author} · {msg}")
                commits_summary = "\n".join(lines)[:600]
            if commits_summary:
                await db.warm_start_jobs.update_one(
                    {"job_id": job_id},
                    {"$set": {"recent_commits": commits_summary}},
                )
        except Exception as e:
            logger.warning("warm-start recent agent: %r", e)
        finally:
            await _mark_done("recent")

    # Agent 3 — File tree (root + backend + frontend, parallel)
    async def agent_structure() -> None:
        try:
            root, be, fe = await asyncio.gather(
                _gh_list_files(gh_token, gh_owner, gh_repo, "", branch),
                _gh_list_files(gh_token, gh_owner, gh_repo, "backend", branch),
                _gh_list_files(gh_token, gh_owner, gh_repo, "frontend/src", branch),
                return_exceptions=True,
            )
            def _l(x): return x if isinstance(x, list) else []
            tree_parts: list[str] = []
            if _l(root):
                tree_parts.append("ROOT: " + ", ".join(_l(root)[:15]))
            if _l(be):
                tree_parts.append("backend/: " + ", ".join(_l(be)[:15]))
            if _l(fe):
                tree_parts.append("frontend/src/: " + ", ".join(_l(fe)[:15]))
            tree = "\n".join(tree_parts)[:1000]
            if tree:
                await db.warm_start_jobs.update_one(
                    {"job_id": job_id},
                    {"$set": {"file_tree": tree}},
                )
        except Exception as e:
            logger.warning("warm-start structure agent: %r", e)
        finally:
            await _mark_done("structure")

    # Agent 4 — Stack (package.json + requirements.txt, parallel)
    async def agent_stack() -> None:
        try:
            pkg, req = await asyncio.gather(
                _gh_read_small(gh_token, gh_owner, gh_repo, "package.json", branch, 500),
                _gh_read_small(gh_token, gh_owner, gh_repo, "backend/requirements.txt", branch, 400),
                return_exceptions=True,
            )
            stack_parts: list[str] = []
            if isinstance(pkg, str) and pkg:
                stack_parts.append("package.json:\n" + pkg[:400])
            if isinstance(req, str) and req:
                stack_parts.append("requirements.txt:\n" + req[:300])
            stack_raw = "\n\n".join(stack_parts)[:800]
            if stack_raw:
                await db.warm_start_jobs.update_one(
                    {"job_id": job_id},
                    {"$set": {"stack_raw": stack_raw}},
                )
        except Exception as e:
            logger.warning("warm-start stack agent: %r", e)
        finally:
            await _mark_done("stack")

    # Agent 5 — Codebase Graph (hybrid regex + LLM top 20).
    # Skips the LLM step entirely if the graph was built < 1h ago.
    async def agent_graph() -> None:
        try:
            from services.graph_builder import build_graph, get_graph
            existing = await get_graph(db, project_id, user_id)
            age = time.time() - float(existing.get("built_at", 0) or 0)
            if not existing or age > 3600:
                await build_graph(
                    db, project_id, user_id,
                    gh_token, gh_owner, gh_repo,
                )
        except Exception as e:
            logger.warning("warm-start graph agent: %r", e)
        finally:
            await _mark_done("graph")

    try:
        # Iter 212m-15 — Cap every warm-start agent at 12 s so a slow LLM
        # call inside the graph builder can't keep the progress bar stuck
        # at 80 % (4/5 done). `_mark_done` always fires from each agent's
        # finally block; the outer wait_for here is the hard ceiling for
        # the whole job. Any agent that exceeds it is silently abandoned
        # (logged via the agent's own except path) — its data was never
        # critical for the next chat turn anyway, the brain/structure
        # agents that DID complete already populate the context cache.
        #
        # Iter 212m-127 — Per-agent timeout overrides. Production logs
        # showed `warm-start graph agent: timed out after 12s` whenever
        # all 20 top files needed re-LLM (first run for a new repo or
        # the file set rotated entirely). 12 s is fine for the brain/
        # structure/stack agents (single LLM call each) but the graph
        # builder makes one call per file in the worst case. Give it
        # 25 s; that still bounds the warm-start job but lets the
        # first-run case actually populate the graph instead of
        # logging a warning every time.
        _AGENT_TIMEOUTS = {
            "brain":     12.0,
            "recent":    12.0,
            "structure": 12.0,
            "stack":     12.0,
            "graph":     25.0,
        }

        async def _bounded(coro, label: str) -> None:
            timeout = _AGENT_TIMEOUTS.get(label, 12.0)
            try:
                await asyncio.wait_for(coro, timeout=timeout)
            except asyncio.TimeoutError:
                logger.warning(
                    "warm-start %s agent: timed out after %.0fs",
                    label, timeout,
                )
                await _mark_done(label)
            except Exception as e:
                logger.warning("warm-start %s agent: %r", label, e)
                await _mark_done(label)

        await asyncio.gather(
            _bounded(agent_brain(),     "brain"),
            _bounded(agent_recent(),    "recent"),
            _bounded(agent_structure(), "structure"),
            _bounded(agent_stack(),     "stack"),
            _bounded(agent_graph(),     "graph"),
            return_exceptions=True,
        )
        await db.warm_start_jobs.update_one(
            {"job_id": job_id},
            {"$set": {"status": "ready", "completed_at": time.time()}},
        )
    except Exception as e:
        logger.error("warm-start job %s crashed: %r", job_id, e)
        await db.warm_start_jobs.update_one(
            {"job_id": job_id},
            {"$set": {"status": "failed", "error": str(e)[:500]}},
        )


# Iter 165 — Codebase Graph endpoints (hybrid regex + LLM top-20).


@router.post("/projects/{project_id}/build-graph")
async def build_project_graph(
    project_id: str,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Trigger a hybrid (regex + LLM-top-20) graph build in background.
    Returns immediately — frontend polls `/projects/{id}/graph` until
    `status == 'ready'`."""
    me = await current_dev(authorization)
    user_id = me["user_id"]
    db = get_db()
    proj = await db.cto_projects.find_one(
        {"project_id": project_id, "user_id": user_id},
        {"_id": 0, "repo_index_summary": 0, "brain_text": 0,
         "repo_index_blocks": 0, "last_commit_diff": 0}
    )
    if not proj:
        raise HTTPException(404, "Project not found")
    gh_token = await _decrypt_pat(user_id, proj.get("github_token")) \
        or await _user_gh_token(user_id)
    gh_owner = proj.get("github_owner") or ""
    gh_repo  = proj.get("github_repo") or ""
    if not (gh_token and gh_owner and gh_repo):
        raise HTTPException(400, "GitHub not connected")
    from services.graph_builder import build_graph
    asyncio.create_task(build_graph(
        db, project_id, user_id, gh_token, gh_owner, gh_repo,
    ))
    return {"ok": True, "message": "Graph building in background"}


@router.get("/projects/{project_id}/graph")
async def get_project_graph(
    project_id: str,
    full: bool = False,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Light read by default (excludes heavy `nodes` field). Pass
    `?full=true` to fetch the full expanded graph for FE detail view.

    Iter 212m-113 — Per-project gating is enforced by the
    {project_id, user_id} compound key in `get_graph` / `get_graph_full`.
    Cross-repo data leak is impossible — a request with another user's
    project_id returns {status:'not_built'}."""
    me = await current_dev(authorization)
    user_id = me["user_id"]
    db = get_db()
    from services.graph_builder import get_graph, get_graph_full
    doc = (
        await get_graph_full(db, project_id, user_id)
        if full
        else await get_graph(db, project_id, user_id)
    )
    if not doc:
        return {"ok": True, "status": "not_built", "graph": None}
    return {"ok": True, "status": "ready", "graph": doc}


@router.get("/projects/{project_id}/graph/tour")
async def get_graph_tour(
    project_id: str,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Iter 212m-113 — Guided onboarding tour. Returns a
    dependency-ordered walkthrough of the project's most important
    files (API → Service → Data → UI). Gated by project_id+user_id
    just like /graph. Zero new LLM cost — reads cached descriptions
    from the existing graph doc."""
    me = await current_dev(authorization)
    user_id = me["user_id"]
    db = get_db()
    from services.graph_builder import get_graph_full
    doc = await get_graph_full(db, project_id, user_id)
    if not doc:
        return {"ok": True, "status": "not_built", "tour": []}
    nodes = doc.get("nodes") or {}
    layers = doc.get("layers") or {}
    # Dependency order: API entry-points first (they call services,
    # services call data). UI shown last for full-stack apps.
    order = ["Config", "Data", "Service", "API", "Hook", "UI", "Util"]
    tour: list[dict] = []
    for layer in order:
        for path in (layers.get(layer) or [])[:3]:
            node = nodes.get(path) or {}
            tour.append({
                "step":        len(tour) + 1,
                "layer":       layer,
                "path":        path,
                "description": node.get("description") or "",
                "symbols":     (node.get("symbols") or [])[:5],
            })
            if len(tour) >= 12:
                break
        if len(tour) >= 12:
            break
    return {"ok": True, "status": "ready", "tour": tour,
            "project_id": project_id, "file_count": doc.get("file_count", 0)}


@router.get("/projects/{project_id}/graph/search")
async def search_graph(
    project_id: str,
    q: str = "",
    limit: int = 20,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Iter 212m-113 — Fuzzy-ish search across paths + descriptions +
    symbols. Pure server-side scoring (no LLM). Gated by
    project_id+user_id. Returns top-N matches with a relevance score."""
    me = await current_dev(authorization)
    user_id = me["user_id"]
    db = get_db()
    from services.graph_builder import get_graph_full
    doc = await get_graph_full(db, project_id, user_id)
    if not doc:
        return {"ok": True, "status": "not_built", "results": []}
    q = (q or "").strip().lower()
    if not q:
        return {"ok": True, "results": []}
    nodes = doc.get("nodes") or {}
    hits: list[dict] = []
    for path, node in nodes.items():
        path_l   = path.lower()
        desc_l   = (node.get("description") or "").lower()
        syms_l   = [s.lower() for s in (node.get("symbols") or [])]
        score = 0
        if q in path_l.rsplit("/", 1)[-1]:
            score += 100
        if path_l.endswith(q):
            score += 50
        if q in path_l:
            score += 25
        if q in desc_l:
            score += 30
        if any(q == s for s in syms_l):
            score += 80
        if any(q in s for s in syms_l):
            score += 20
        if score > 0:
            hits.append({
                "path":        path,
                "layer":       node.get("layer"),
                "description": node.get("description") or "",
                "symbols":     (node.get("symbols") or [])[:5],
                "score":       score,
            })
    hits.sort(key=lambda h: h["score"], reverse=True)
    return {"ok": True, "results": hits[:max(1, min(limit, 100))],
            "query": q, "total_matches": len(hits)}


@router.post("/projects/{project_id}/graph/impact")
async def graph_impact(
    project_id: str,
    body: dict,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Iter 212m-113 — Diff Impact Analysis. Body: {files: [paths]}.
    Returns the transitive set of files that import the given set
    (one hop, capped at 50). Useful right before a Loop Mode ship to
    surface the blast radius. Gated by project_id+user_id, no LLM."""
    me = await current_dev(authorization)
    user_id = me["user_id"]
    db = get_db()
    files = list((body or {}).get("files") or [])
    if not files:
        raise HTTPException(400, "files[] required")
    from services.graph_builder import get_graph_full
    doc = await get_graph_full(db, project_id, user_id)
    if not doc:
        return {"ok": True, "status": "not_built", "impacted": []}
    edges = doc.get("edges") or []
    target_set = set(files)
    impacted: dict[str, list[str]] = {}
    for e in edges:
        src = e.get("from")
        dst = e.get("to")
        if dst in target_set and src not in target_set:
            impacted.setdefault(src, []).append(dst)
        if len(impacted) >= 50:
            break
    out = [
        {"path": p, "reason": f"imports {', '.join(deps[:3])}"
                              + (f" +{len(deps) - 3} more" if len(deps) > 3 else "")}
        for p, deps in impacted.items()
    ]
    return {"ok": True, "changed": files,
            "impacted": out, "blast_radius": len(out)}




@router.get("/projects/warm-start/{job_id}/status")
async def warm_start_status(
    job_id: str,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Poll endpoint: returns the warm-start job's progress so the UI
    can render a percentage bar."""
    me = await current_dev(authorization)
    db = get_db()
    job = await db.warm_start_jobs.find_one(
        {"job_id": job_id, "user_id": me["user_id"]},
        {"_id": 0},
    )
    if not job:
        raise HTTPException(404, "Job not found")
    agents_done  = job.get("agents_done") or []
    agents_total = job.get("agents_total") or []
    progress = (len(agents_done) / max(len(agents_total), 1)) if agents_total else 0.0
    return {
        "ok":           True,
        "job_id":       job_id,
        "status":       job.get("status"),
        "progress":     round(progress, 2),
        "agents_done":  agents_done,
        "agents_total": agents_total,
        "ready":        job.get("status") == "ready",
        "started_at":   job.get("started_at"),
        "completed_at": job.get("completed_at"),
    }




@router.get("/projects/{project_id}/check-pat")
async def check_project_pat(
    project_id: str,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Iter 153 FIX 3 — PAT health check.

    Decrypts the stored token, hits `GET https://api.github.com/user`,
    and reports `valid` / `expired` / `missing` along with the upstream
    `github-authentication-token-expiration` header if GitHub returned
    one. Used by Projects.jsx to toast the user if their PAT is gone
    or expiring within 7 days.
    """
    me = await current_dev(authorization)
    user_id = me["user_id"]
    db = get_db()
    proj = await db.cto_projects.find_one(
        {"project_id": project_id, "user_id": user_id},
        {"_id": 0, "github_token": 1},
    )
    if not proj:
        raise HTTPException(404, "project not found")
    token = await _decrypt_pat(user_id, proj.get("github_token")) \
        or await _user_gh_token(user_id)
    if not token:
        return {"ok": True, "state": "missing", "message": "No PAT configured"}
    try:
        import httpx
        async with httpx.AsyncClient(timeout=8.0) as cx:
            r = await cx.get(
                "https://api.github.com/user",
                headers={
                    "Authorization": f"token {token}",
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "aurem-cto-pat-check",
                },
            )
    except Exception as e:
        return {"ok": True, "state": "unknown",
                "message": f"check failed: {type(e).__name__}"}

    expires_at = r.headers.get("github-authentication-token-expiration")
    if r.status_code == 200:
        return {
            "ok": True, "state": "valid",
            "expires_at": expires_at,
            "login": (r.json() or {}).get("login"),
        }
    if r.status_code in (401, 403):
        return {
            "ok": True, "state": "expired",
            "message": "PAT rejected by GitHub — please rotate.",
            "expires_at": expires_at,
        }
    return {
        "ok": True, "state": "unknown",
        "message": f"GitHub returned HTTP {r.status_code}",
        "expires_at": expires_at,
    }




# ── Endpoints ────────────────────────────────────────────────────────────
@router.post("/projects/add")
async def add_project(body: AddProject, authorization: str = Header(None)) -> dict:
    me = await current_dev(authorization)
    db = require_db()
    owner, repo = _parse_repo(body.github_url)

    # Iter 211 — PAT compulsory at project creation (per user spec).
    # Per-project isolation: every project stores its own encrypted PAT
    # independently. We no longer fall back to the user's OAuth token
    # for repo work (OAuth is identity-only). User must explicitly
    # paste a PAT for each project.
    pat = (body.github_token or "").strip() or None
    if not pat:
        raise HTTPException(
            400,
            "A GitHub Personal Access Token is required for every project. "
            "Generate one at github.com/settings/personal-access-tokens/new "
            "with Contents: Read and write for this repo.",
        )
    if not (pat.startswith("ghp_") or pat.startswith("github_pat_")):
        raise HTTPException(
            400,
            "That doesn't look like a GitHub PAT — should start with "
            "ghp_ (classic) or github_pat_ (fine-grained).",
        )

    # Iter 211 — atomic verify: hit GitHub /repos/{owner}/{repo} with
    # the PAT BEFORE writing the project doc. If GitHub rejects the
    # token we never persist a broken project. Maps to the same
    # signals as `/projects/{id}/test-pat` (iter 207) for UI symmetry.
    import httpx as _httpx
    try:
        async with _httpx.AsyncClient(timeout=10.0) as _c:
            _r = await _c.get(
                f"https://api.github.com/repos/{owner}/{repo}",
                headers={
                    "Accept":              "application/vnd.github+json",
                    "Authorization":       f"Bearer {pat}",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
    except _httpx.RequestError as _e:
        raise HTTPException(
            502,
            f"Couldn't reach GitHub to verify the token ({type(_e).__name__}). "
            "Try again in a moment.",
        )
    if _r.status_code in (401, 403):
        raise HTTPException(
            400,
            "GitHub rejected the PAT (401/403). Regenerate it with "
            "Contents: Read and write for this repo, then try again.",
        )
    if _r.status_code == 404:
        raise HTTPException(
            400,
            f"Repo not found at github.com/{owner}/{repo} via this PAT. "
            "The repo may not be in the token's scope — re-pick it when "
            "generating a fine-grained PAT.",
        )
    if _r.status_code != 200:
        raise HTTPException(
            502,
            f"GitHub returned HTTP {_r.status_code} during verification. "
            "Try a fresh token.",
        )

    proj_id = f"p_{uuid.uuid4().hex[:10]}"
    encrypted_token = await _encrypt_pat(me["user_id"], pat)
    doc = {
        "project_id": proj_id, "user_id": me["user_id"],
        "name": body.name, "github_url": body.github_url,
        "github_owner": owner, "github_repo": repo,
        "github_token": encrypted_token,
        "auth_method": "pat",          # iter 211 — always PAT, never OAuth fallback.
        "branch": body.branch, "tech_stack": body.tech_stack or "auto",
        "preview_url": (body.preview_url or "").strip() or None,
        "status": "connected", "tasks_done": 0,
        # Iter 212m-75 — async indexing pipeline. Endpoint returns
        # immediately; background task flips status to ready/error.
        "indexing_status":  "indexing",
        "indexing_error":   None,
        "indexed_at":       None,
        "indexing_started_at": time.time(),
        "created_at": time.time(),
    }
    await db.cto_projects.insert_one(doc)
    # Iter 212m-75 — fire-and-forget indexing wrapper.  Wraps the legacy
    # build_brain_v2 with explicit status writes so the FE can poll
    # /indexing-status and show a progress spinner instead of guessing.
    try:
        asyncio.create_task(_run_project_indexing(
            db=db, project_id=proj_id, user_id=me["user_id"],
            github_token=pat, github_owner=owner, github_repo=repo,
            branch=body.branch or "main",
        ))
    except Exception as _bbe:
        logger.warning("indexing scheduler skipped: %r", _bbe)
    return {"ok": True, "project_id": proj_id,
            "owner": owner, "repo": repo,
            "auth_method": doc["auth_method"],
            "indexing_status": "indexing",
            "message": "Indexing your repository in the background...",
            # Iter 211 — surface that PAT verification already passed
            # during creation so the frontend can skip a redundant
            # `/test-pat` round-trip and show the green checkmark
            # immediately.
            "pat_verified": True}


async def _run_project_indexing(
    *, db, project_id: str, user_id: str,
    github_token: str, github_owner: str, github_repo: str, branch: str,
) -> None:
    """Background indexing wrapper for Iter 212m-75.

    Runs build_brain_v2 and writes the result to cto_projects so the
    FE polling endpoint /indexing-status can report progress.
    Errors are swallowed and persisted as `indexing_error`.
    """
    try:
        from services.project_brain import build_brain_v2
        await build_brain_v2(
            db=db, project_id=project_id, user_id=user_id,
            github_token=github_token, github_owner=github_owner,
            github_repo=github_repo, branch=branch,
        )
        await db.cto_projects.update_one(
            {"project_id": project_id, "user_id": user_id},
            {"$set": {
                "indexing_status": "ready",
                "indexed_at":      time.time(),
                "indexing_error":  None,
            }},
        )
        logger.info("project indexing complete: %s", project_id)
    except Exception as e:
        logger.warning("project indexing failed for %s: %r", project_id, e)
        await db.cto_projects.update_one(
            {"project_id": project_id, "user_id": user_id},
            {"$set": {
                "indexing_status": "error",
                "indexing_error":  str(e)[:500],
                "indexed_at":      time.time(),
            }},
        )


@router.get("/projects/{project_id}/indexing-status")
async def project_indexing_status(
    project_id: str,
    authorization: str = Header(None),
) -> dict:
    """Iter 212m-75 — poll endpoint for FE to track async indexing.
    Returns: {status: "indexing"|"ready"|"error", error, indexed_at}.
    """
    me = await current_dev(authorization)
    db = require_db()
    proj = await db.cto_projects.find_one(
        {"project_id": project_id, "user_id": me["user_id"]},
        {"_id": 0, "indexing_status": 1, "indexing_error": 1,
         "indexed_at": 1, "indexing_started_at": 1, "name": 1},
    )
    if not proj:
        raise HTTPException(404, "Project not found")
    status = proj.get("indexing_status") or "ready"  # legacy rows = ready
    return {
        "ok":          True,
        "project_id":  project_id,
        "name":        proj.get("name"),
        "status":      status,
        "error":       proj.get("indexing_error"),
        "indexed_at":  proj.get("indexed_at"),
        "started_at":  proj.get("indexing_started_at"),
        "ready":       status == "ready",
    }


# ─────────────────────────────────────────────────────────────────────
# Iter 212 — Pre-save PAT verification (stateless, no DB write).
# Called by AddProject Step 2 with a debounce after the user pastes a
# token, so they get inline green/red feedback BEFORE clicking Connect.
#
# Uses POST (not GET) so the raw PAT never lands in browser history or
# proxy access logs — small but real security win vs. query strings.
# ─────────────────────────────────────────────────────────────────────
class VerifyPatBody(BaseModel):
    repo: str  # "owner/name"
    pat:  str  # ghp_… or github_pat_…


@router.post("/projects/verify-pat")
async def verify_pat(
    body: VerifyPatBody,
    authorization: str = Header(None),
) -> dict:
    """Stateless PAT verification against a specific GitHub repo.

    Returns a uniform JSON shape so the frontend can render inline
    pills without branching on HTTP status:

      {ok: true,  scopes: ["repo", "read:org"], private: bool, full_name: "…"}
      {ok: false, error: "invalid_token",    detail: "…"}
      {ok: false, error: "missing_scope",    has_scopes: [...]}
      {ok: false, error: "repo_not_found",   detail: "…"}
      {ok: false, error: "network_error",    detail: "…"}

    HTTP status is always 200 — error is encoded in `ok`.
    """
    # Auth: must be a logged-in builder (PATs are user-scoped).
    await current_dev(authorization)

    repo = (body.repo or "").strip().lstrip("/")
    pat  = (body.pat  or "").strip()
    if "/" not in repo:
        return {"ok": False, "error": "bad_repo",
                "detail": "Repo must be in 'owner/name' format."}
    if not (pat.startswith("ghp_") or pat.startswith("github_pat_")):
        return {"ok": False, "error": "bad_format",
                "detail": "PAT must start with ghp_ or github_pat_."}

    import httpx
    url = f"https://api.github.com/repos/{repo}"
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {pat}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.get(url, headers=headers)
    except httpx.RequestError as e:
        logger.warning("verify-pat: network error for %s: %r", repo, e)
        return {"ok": False, "error": "network_error",
                "detail": f"Couldn't reach GitHub ({type(e).__name__})."}

    # GitHub returns granted scopes via X-OAuth-Scopes (classic PATs).
    # Fine-grained PATs return X-Accepted-OAuth-Scopes instead with a
    # different model — we treat HTTP 200 as proof-of-access for those.
    scopes_hdr = (r.headers.get("X-OAuth-Scopes") or "").strip()
    scopes = [s.strip() for s in scopes_hdr.split(",") if s.strip()]

    if r.status_code == 200:
        try:
            data = r.json() or {}
        except Exception:  # noqa: BLE001
            data = {}
        # Classic PATs: enforce `repo` scope. Fine-grained PATs send no
        # scope header so we trust the 200 (they're scoped at creation).
        if scopes and "repo" not in scopes and not any(
            s.startswith("repo:") for s in scopes
        ):
            return {
                "ok": False, "error": "missing_scope",
                "has_scopes": scopes,
                "detail": "Token is missing the `repo` scope. Regenerate "
                          "with **repo** checked (or, for fine-grained, "
                          "**Contents: Read and write**).",
            }

        # Iter 212m-5 — multi-project security check.
        # Probe `/user/repos?per_page=1` and use the `Link: …; rel="last"`
        # header (page index) to derive total accessible-repo count
        # cheaply. If the PAT can see > 1 repo while we're scoping it to
        # ONE project, surface a `warning` so the UI can show an amber
        # over-scoped pill. We don't fail the verification — user can
        # still proceed with a classic PAT, but they get an honest
        # signal that a per-repo fine-grained PAT would be safer.
        total_accessible = None
        try:
            async with httpx.AsyncClient(timeout=8.0) as c:
                ur = await c.get(
                    "https://api.github.com/user/repos",
                    headers=headers,
                    params={"per_page": 1, "affiliation": "owner,collaborator"},
                )
            if ur.status_code == 200:
                link = ur.headers.get("Link") or ""
                # Parse `…page=N>; rel="last"` to derive total.
                import re as _re
                m = _re.search(r'[?&]page=(\d+)>;\s*rel="last"', link)
                if m:
                    total_accessible = int(m.group(1))
                else:
                    # No `last` link = ≤ 1 page; count directly.
                    body = ur.json() or []
                    total_accessible = len(body) if isinstance(body, list) else None
        except Exception as e:  # noqa: BLE001
            logger.debug("verify-pat: total-repo probe skipped: %r", e)

        warning = None
        if total_accessible is not None and total_accessible > 1:
            warning = (
                f"This token has access to {total_accessible} repos, "
                "not just this one. For tighter security consider a "
                "fine-grained PAT scoped to only this repo."
            )

        return {
            "ok":        True,
            "full_name": data.get("full_name") or repo,
            "private":   bool(data.get("private", False)),
            "scopes":    scopes,  # may be [] for fine-grained PATs
            "total_accessible_repos": total_accessible,
            "warning":   warning,
            "fine_grained": not bool(scopes),
        }
    if r.status_code == 401:
        return {"ok": False, "error": "invalid_token",
                "detail": "Token invalid or expired — generate a new one."}
    if r.status_code == 403:
        return {"ok": False, "error": "missing_scope",
                "has_scopes": scopes,
                "detail": "Missing repo scope — regenerate with `repo` checked."}
    if r.status_code == 404:
        return {"ok": False, "error": "repo_not_found",
                "detail": f"Repo not found at github.com/{repo} — check the "
                          "URL, or for a fine-grained PAT make sure this "
                          "repo is in the token's allow-list."}
    return {"ok": False, "error": "github_error",
            "detail": f"GitHub returned HTTP {r.status_code}."}




@router.get("/projects/list")
async def list_projects(authorization: str = Header(None)) -> dict:
    me = await current_dev(authorization)
    db = require_db()
    projs = await db.cto_projects.find(
        {"user_id": me["user_id"]},
        {"_id": 0},        # need github_token presence; strip ciphertext below
    ).sort("created_at", -1).to_list(50)
    # Iter 206 — surface a boolean `has_pat` flag (without ever leaking
    # the encrypted token itself) so the Projects sidebar can render a
    # green/amber PAT pill per row.
    for p in projs:
        p["has_pat"] = bool(p.get("github_token"))
        p.pop("github_token", None)
    return {"ok": True, "projects": projs}


@router.delete("/projects/{project_id}")
async def remove_project(project_id: str, authorization: str = Header(None)) -> dict:
    me = await current_dev(authorization)
    db = require_db()
    r = await db.cto_projects.delete_one({"project_id": project_id, "user_id": me["user_id"]})
    return {"ok": True, "deleted": r.deleted_count}


# Iter 170c — Codebase browsing for the right-side </> Code preview.
#
# When the user hits the `</> Code` toggle in PreviewPanel and there's
# no recently-shipped task to display, the panel used to fall back to
# the project's `preview_url` (just a URL string). The new flow:
#
#   GET  /cto/projects/{id}/tree                 → paths only
#   GET  /cto/projects/{id}/file?path=src/x.py   → single file content
#
# Both endpoints scope to the project's connected GitHub PAT (decrypted
# from Mongo) and the project's branch. They are read-only and never
# touch the working tree on disk; everything goes through the GitHub
# REST API so no `git` binary is required.
_BROWSE_SKIP_DIRS = {
    ".git", "node_modules", ".next", "dist", "build", "__pycache__",
    ".venv", "venv", ".cache", ".pytest_cache", ".mypy_cache",
    "coverage", ".turbo", ".vercel", ".idea", ".vscode",
}
_BROWSE_SKIP_EXTS = {
    "lock", "log", "map",
    "png", "jpg", "jpeg", "gif", "webp", "ico", "svg", "bmp",
    "mp4", "mov", "mp3", "wav", "ogg",
    "ttf", "otf", "woff", "woff2", "eot",
    "zip", "tar", "gz", "7z", "rar",
    "pdf", "exe", "dll", "so",
}
_BROWSE_MAX_FILE_BYTES = 200 * 1024  # 200 KB cap per file


def _browse_keep_path(path: str, size: int) -> bool:
    """Return True if a tree blob should appear in the browseable list."""
    if not path:
        return False
    parts = path.split("/")
    if any(p in _BROWSE_SKIP_DIRS for p in parts):
        return False
    ext = parts[-1].rsplit(".", 1)[-1].lower() if "." in parts[-1] else ""
    if ext in _BROWSE_SKIP_EXTS:
        return False
    if size and size > _BROWSE_MAX_FILE_BYTES:
        return False
    return True



# ───────────────────────────────────────────────────────────────────
# Iter 207 — PAT connection test. Replaces the "save and pray" flow
# in the PatModal: after the user saves a token we hit GitHub's
# `/repos/{owner}/{repo}` and surface a definitive pass/fail back to
# the modal so they know immediately if the token works.
# ───────────────────────────────────────────────────────────────────
@router.get("/projects/{project_id}/test-pat")
async def test_project_pat(
    project_id: str,
    authorization: str = Header(None),
) -> dict:
    """Verify the project's stored PAT (or fallback OAuth token) can
    read the connected GitHub repo. Returns a uniform shape so the
    frontend never has to branch on HTTP status:

      {ok: true,  repo: "owner/name", private: bool}
      {ok: false, error: "<human-readable reason>"}

    HTTP status is always 200 — error is encoded in `ok`. This keeps
    the React Query / axios paths simple.
    """
    me = await current_dev(authorization)
    user_id = me["user_id"]
    db = require_db()
    proj = await db.cto_projects.find_one(
        {"project_id": project_id, "user_id": user_id},
        {"_id": 0, "repo_index_summary": 0, "brain_text": 0,
         "repo_index_blocks": 0, "last_commit_diff": 0}
    )
    if not proj:
        raise HTTPException(404, "Project not found")

    owner = (proj.get("github_owner") or "").strip()
    repo  = (proj.get("github_repo")  or "").strip()
    if not (owner and repo):
        return {"ok": False, "error": "Project has no repo configured."}

    gh_token = await _decrypt_pat(user_id, proj.get("github_token")) \
        or await _user_gh_token(user_id)
    if not gh_token:
        return {
            "ok": False,
            "error": "No PAT saved and no GitHub OAuth connection on file.",
        }

    import httpx
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {gh_token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    url = f"https://api.github.com/repos/{owner}/{repo}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.get(url, headers=headers)
    except httpx.RequestError as e:
        logger.warning("test-pat: network error %s/%s: %r", owner, repo, e)
        return {"ok": False, "error": f"Couldn't reach GitHub ({type(e).__name__})."}

    if r.status_code == 200:
        try:
            data = r.json() or {}
        except Exception:  # noqa: BLE001
            data = {}
        return {
            "ok":      True,
            "repo":    data.get("full_name") or f"{owner}/{repo}",
            "private": bool(data.get("private", False)),
        }
    if r.status_code in (401, 403):
        return {
            "ok":    False,
            "error": "Token invalid or missing repo scope. Regenerate the "
                     "PAT with **Contents: Read and write** for this repo.",
        }
    if r.status_code == 404:
        return {
            "ok":    False,
            "error": f"Repo not found at github.com/{owner}/{repo}. The repo "
                     "may be private, or your token doesn't include it. "
                     "Re-pick the repo when generating a fine-grained PAT.",
        }
    return {
        "ok":    False,
        "error": f"GitHub returned HTTP {r.status_code}. Try a new token.",
    }



@router.get("/projects/{project_id}/tree")
async def get_project_tree(
    project_id: str,
    authorization: str = Header(None),
) -> dict:
    """Return the list of source-file paths in the connected GitHub repo
    at the project's pinned branch. Filtered to source files only
    (no node_modules, no binaries, no >200KB blobs).

    Used by PreviewPanel's `</> Code` toggle to let the user browse
    the live codebase without leaving the chat. Results are capped at
    300 files; truncated=True is returned if the tree was deeper.
    """
    me = await current_dev(authorization)
    user_id = me["user_id"]
    db = require_db()
    proj = await db.cto_projects.find_one(
        {"project_id": project_id, "user_id": user_id},
        {"_id": 0, "repo_index_summary": 0, "brain_text": 0,
         "repo_index_blocks": 0, "last_commit_diff": 0}
    )
    if not proj:
        raise HTTPException(404, "Project not found")
    gh_token = await _decrypt_pat(user_id, proj.get("github_token")) \
        or await _user_gh_token(user_id)
    owner = proj.get("github_owner") or ""
    repo  = proj.get("github_repo") or ""
    branch = proj.get("branch") or "main"
    if not (owner and repo and gh_token):
        raise HTTPException(400, "GitHub not connected to this project")

    import httpx
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {gh_token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    url = (
        f"https://api.github.com/repos/{owner}/{repo}"
        f"/git/trees/{branch}?recursive=1"
    )
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.get(url, headers=headers)
        if r.status_code == 404:
            raise HTTPException(404, f"Branch {branch} not found on GitHub")
        if r.status_code == 401:
            raise HTTPException(401, "GitHub PAT invalid or expired")
        r.raise_for_status()
        data = r.json()
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"[tree] GitHub fetch failed: {e!r}")
        raise HTTPException(502, f"GitHub API error: {e}")

    items = []
    for node in (data.get("tree") or []):
        if node.get("type") != "blob":
            continue
        path = node.get("path") or ""
        size = int(node.get("size") or 0)
        if not _browse_keep_path(path, size):
            continue
        items.append({"path": path, "size": size})
    # Sort: README first, then root-level configs, then by depth, then alpha
    def _sort_key(it):
        p = it["path"].lower()
        depth = p.count("/")
        is_readme = 0 if p.startswith("readme") else 1
        is_root_config = 0 if depth == 0 and any(
            p.endswith(s) for s in ("package.json", "requirements.txt",
                                     "pyproject.toml", "dockerfile", ".env.example")
        ) else 1
        return (is_readme, is_root_config, depth, p)
    items.sort(key=_sort_key)
    truncated = bool(data.get("truncated")) or len(items) > 300
    items = items[:300]
    return {
        "ok": True, "project_id": project_id,
        "owner": owner, "repo": repo, "branch": branch,
        "files": items, "truncated": truncated,
    }


@router.get("/projects/{project_id}/file")
async def get_project_file(
    project_id: str,
    path: str,
    authorization: str = Header(None),
) -> dict:
    """Fetch a single file's content from the connected GitHub repo at
    the project's pinned branch. Capped at 200KB; bigger files return
    a truncated marker so the UI shows a clean message instead of OOM.
    """
    me = await current_dev(authorization)
    user_id = me["user_id"]
    db = require_db()
    proj = await db.cto_projects.find_one(
        {"project_id": project_id, "user_id": user_id},
        {"_id": 0, "repo_index_summary": 0, "brain_text": 0,
         "repo_index_blocks": 0, "last_commit_diff": 0}
    )
    if not proj:
        raise HTTPException(404, "Project not found")
    gh_token = await _decrypt_pat(user_id, proj.get("github_token")) \
        or await _user_gh_token(user_id)
    owner = proj.get("github_owner") or ""
    repo  = proj.get("github_repo") or ""
    branch = proj.get("branch") or "main"
    if not (owner and repo and gh_token):
        raise HTTPException(400, "GitHub not connected to this project")
    if not path or path.startswith("/") or ".." in path.split("/"):
        raise HTTPException(400, "Invalid path")

    import httpx
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            content = await gh_api_fetch_file(c, owner, repo, path, branch, gh_token)
    except Exception as e:
        logger.warning(f"[file] fetch failed for {path}: {e!r}")
        raise HTTPException(502, f"GitHub API error: {e}")
    if content is None:
        raise HTTPException(404, f"File not found: {path}")
    truncated = False
    if len(content.encode("utf-8", errors="replace")) > _BROWSE_MAX_FILE_BYTES:
        # Trim to byte budget without breaking utf-8 mid-codepoint.
        b = content.encode("utf-8", errors="replace")[:_BROWSE_MAX_FILE_BYTES]
        content = b.decode("utf-8", errors="replace") + "\n\n# … (truncated)"
        truncated = True
    return {
        "ok": True, "project_id": project_id,
        "path": path, "content": content, "truncated": truncated,
    }


class UpdateProject(BaseModel):
    github_token: Optional[str] = None
    branch: Optional[str] = None
    tech_stack: Optional[str] = None
    preview_url: Optional[str] = None


@router.patch("/projects/{project_id}")
async def update_project(
    project_id: str,
    body: UpdateProject,
    authorization: str = Header(None),
) -> dict:
    """Update PAT / branch / tech stack of an existing project."""
    me = await current_dev(authorization)
    db = require_db()
    updates = {k: v for k, v in body.model_dump().items() if v is not None and v != ""}
    if not updates:
        raise HTTPException(400, "Nothing to update")
    # BUG 2 fix — encrypt PAT at rest on update too (add_project already did
    # this; the PATCH path was storing it plaintext).
    if "github_token" in updates and updates["github_token"]:
        updates["github_token"] = await _encrypt_pat(me["user_id"], updates["github_token"])
    r = await db.cto_projects.update_one(
        {"project_id": project_id, "user_id": me["user_id"]},
        {"$set": updates},
    )
    if r.matched_count == 0:
        raise HTTPException(404, "Project not found")
    # PAT / branch changed → invalidate the cached repo context blob
    try:
        from services.repo_context import invalidate_repo_context
        await invalidate_repo_context(project_id)
    except Exception:
        pass
    return {"ok": True, "updated_fields": list(updates.keys())}


async def _enqueue_cto_task(
    user_id: str,
    project_id: Optional[str],
    task_text: str,
    bg: Optional[BackgroundTasks] = None,
    maxx_mode: bool = False,
) -> dict:
    """Iter 46 — programmatic Mode C trigger.

    Used by both /tasks/submit (HTTP) AND the chat-router Mode D→C handoff
    (so "yes fix it" actually queues a real ship task, not a friendly reply).

    Returns:
        {"ok": True, "task_id": "...", "project_id": "..."} on success
        {"ok": False, "reason": "no_project"|"no_pat"|"out_of_budget"} otherwise
    """
    import asyncio as _asyncio
    db = get_db()
    if db is None:
        return {"ok": False, "reason": "no_db"}

    proj = None
    if project_id and project_id != "home":
        proj = await db.cto_projects.find_one(
            {"project_id": project_id, "user_id": user_id},
        {"_id": 0, "repo_index_summary": 0, "brain_text": 0,
         "repo_index_blocks": 0, "last_commit_diff": 0}
        )
    if not proj:
        # Fall back to the user's most recently used project.
        proj = await db.cto_projects.find_one(
            {"user_id": user_id},
            sort=[("last_task", -1), ("created_at", -1)],
        )
    if not proj:
        return {"ok": False, "reason": "no_project"}

    task_id = f"t_{uuid.uuid4().hex[:12]}"
    await db.cto_tasks.insert_one({
        "task_id": task_id,
        "project_id": proj["project_id"],
        "user_id": user_id,
        "task": task_text,
        "files": [], "context": "",
        "status": "queued", "steps": [], "commit_sha": None,
        "result": None, "error": None,
        "maxx_mode": bool(maxx_mode),
        "source": "chat_handoff",
        "created_at": time.time(),
    })
    user_token = await _decrypt_pat(user_id, proj.get("github_token")) \
        or await _user_gh_token(user_id)
    if not user_token:
        await db.cto_tasks.update_one(
            {"task_id": task_id},
            {"$set": {"status": "failed",
                      "error": "No GitHub token configured.",
                      "completed_at": time.time()}},
        )
        return {"ok": False, "reason": "no_pat",
                "task_id": task_id, "project_id": proj["project_id"]}

    # Iter 212m-6 — loud surface when the project's encrypted PAT
    # could not be decrypted but we fell back to the user's OAuth
    # token. The fallback often has different scopes than the
    # project-scoped PAT (e.g. read-only) and silently using it
    # causes 403 on blob upload later. Log a warning + mark the task
    # row so the UI can show a "PAT decrypt fallback" advisory.
    try:
        _project_pat_raw = proj.get("github_token") or ""
        _project_pat_decoded = (
            await _decrypt_pat(user_id, _project_pat_raw)
            if _project_pat_raw else None
        )
        if _project_pat_raw and not _project_pat_decoded:
            logger.warning(
                "PAT decrypt fallback for project=%s — using OAuth token. "
                "User should re-add the project PAT.",
                proj.get("project_id"),
            )
            await db.cto_tasks.update_one(
                {"task_id": task_id},
                {"$set": {"pat_decrypt_fallback": True}},
            )
    except Exception:
        pass

    if bg is not None:
        bg.add_task(_run_task, task_id, proj, task_text, [], "",
                    user_token, bool(maxx_mode))
    else:
        # No BackgroundTasks in this caller — fire-and-forget asyncio task.
        _asyncio.create_task(_run_task(
            task_id, proj, task_text, [], "",
            user_token, bool(maxx_mode),
        ))
    return {"ok": True, "task_id": task_id, "project_id": proj["project_id"]}


@router.post("/tasks/submit")
async def submit_task(
    request: Request,
    body: TaskBody,
    bg: BackgroundTasks,
    authorization: str = Header(None),
) -> dict:
    me = await current_dev(authorization)
    # Iter 50.1 — Founders skip per-IP rate-limit. They run audits, ship
    # tests, retry tasks in bursts — locking them out at 10/min defeats
    # the whole "founder = full access" rule.
    _is_unlimited = bool(me.get("is_unlimited")) or me.get("tier") == "founder"
    if not _is_unlimited:
        from services.rate_limiter import check_rate_limit, client_ip_from_request
        if not check_rate_limit(f"submit:{client_ip_from_request(request)}", 10):
            raise HTTPException(429, "Rate limit exceeded: 10 code tasks/min/IP")
    # THING 1 — hard-stop token enforcement. Raises HTTP 402 if the user has
    # spent their plan_limit + any admin-granted bonus. The AI is NEVER
    # called and no row is written to `cto_tasks`.
    await assert_has_budget(me["user_id"])

    # Tier-based monthly task cap (free=10, starter=50, pro/team/founder
    # unlimited). Single source of truth — MONTHLY_TASK_LIMITS in
    # services/usage.py. Replaces the iter-45 free-only counter.
    await assert_has_task_budget(me["user_id"])

    # Tier-based feature gate — Maxx mode requires Pro / Team / Founder.
    if body.maxx_mode:
        from services.subscription_tiers import can_use_feature
        if not can_use_feature(me.get("tier"), "maxx_mode"):
            raise HTTPException(403, {
                "error": "feature_locked",
                "feature": "maxx_mode",
                "current_tier": me.get("tier", "free"),
                "upgrade_url": "/settings#pricing",
                "message": (
                    "Maxx mode (Claude reviewer) is a Pro feature. "
                    "Upgrade at auremcto.com/settings to enable it."
                ),
            })

    db = require_db()
    proj = await db.cto_projects.find_one(
        {"project_id": body.project_id, "user_id": me["user_id"]},
        {"_id": 0, "repo_index_summary": 0, "brain_text": 0,
         "repo_index_blocks": 0, "last_commit_diff": 0}
    )
    if not proj:
        raise HTTPException(404, "Project not found")
    task_id = f"t_{uuid.uuid4().hex[:12]}"
    await db.cto_tasks.insert_one({
        "task_id": task_id, "project_id": body.project_id,
        "user_id": me["user_id"], "task": body.task,
        "files": body.files, "context": body.context,
        "status": "queued", "steps": [], "commit_sha": None,
        "result": None, "error": None,
        "maxx_mode": bool(body.maxx_mode),
        "created_at": time.time(),
    })
    user_token = await _decrypt_pat(me["user_id"], proj.get("github_token")) \
        or await _user_gh_token(me["user_id"])
    bg.add_task(_run_task, task_id, proj, body.task, body.files, body.context,
                user_token, bool(body.maxx_mode))
    return {"ok": True, "task_id": task_id}


class RollbackBody(BaseModel):
    # User must echo "ROLLBACK" to confirm intent server-side too —
    # double safety on top of the two-click client confirmation.
    confirm: str


@router.post("/tasks/{task_id}/rollback")
async def rollback_task(
    task_id: str,
    body: RollbackBody,
    bg: BackgroundTasks,
    authorization: str = Header(None),
) -> dict:
    """Revert a previously-pushed AUREM CTO commit on the project's repo.
    Uses `git revert --no-edit <sha>` so the rollback is itself a new
    commit (no force-push, full history preserved). Idempotent: a task
    that's already been rolled back returns 409."""
    me = await current_dev(authorization)
    if (body.confirm or "").strip().upper() != "ROLLBACK":
        raise HTTPException(400, "Must confirm with 'ROLLBACK'")

    db = require_db()
    t = await db.cto_tasks.find_one(
        {"task_id": task_id, "user_id": me["user_id"]}
    )
    if not t:
        raise HTTPException(404, "Task not found")
    if t.get("status") != "done":
        raise HTTPException(
            400,
            f"Only completed tasks can be rolled back (current: {t.get('status')})",
        )
    if not t.get("commit_sha"):
        raise HTTPException(400, "Task has no commit to revert")
    if t.get("rollback_sha"):
        raise HTTPException(409, "Task already rolled back")
    if t.get("rollback_status") in ("queued", "running"):
        raise HTTPException(409, "Rollback already in progress")
    if t.get("rollback_status") == "failed":
        raise HTTPException(
            409,
            "Previous rollback failed — manual intervention required",
        )

    proj = await db.cto_projects.find_one(
        {"project_id": t["project_id"], "user_id": me["user_id"]},
        {"_id": 0, "repo_index_summary": 0, "brain_text": 0,
         "repo_index_blocks": 0, "last_commit_diff": 0}
    )
    if not proj:
        raise HTTPException(404, "Parent project not found")

    user_token = await _decrypt_pat(me["user_id"], proj.get("github_token")) \
        or await _user_gh_token(me["user_id"])
    if not user_token:
        raise HTTPException(
            400,
            "No PAT on file for this project — open Projects → Edit and add one.",
        )

    await db.cto_tasks.update_one(
        {"task_id": task_id},
        {"$set": {
            "rollback_status": "queued",
            "rollback_started_at": time.time(),
        }},
    )
    bg.add_task(_run_rollback, task_id, proj, t["commit_sha"], user_token)
    return {"ok": True, "task_id": task_id, "rollback_status": "queued"}


# ── Rollback worker ──────────────────────────────────────────────────────
async def _rollback_log(task_id: str, step: str, status: str = "info"):
    """Append a step to the task's `rollback_steps` array."""
    db = get_db()
    if db is None:
        return
    await db.cto_tasks.update_one(
        {"task_id": task_id},
        {"$push": {"rollback_steps": {"step": step, "status": status, "ts": time.time()}}},
    )


async def _run_rollback(task_id: str, proj: dict, commit_sha: str,
                         user_token: str) -> None:
    """Dispatcher — git-subprocess path locally, GitHub-API path in
    production where git isn't installed."""
    if _GIT_AVAILABLE:
        return await _run_rollback_with_git(task_id, proj, commit_sha, user_token)
    return await _run_rollback_via_api(task_id, proj, commit_sha, user_token)


async def _run_rollback_via_api(task_id: str, proj: dict, commit_sha: str,
                                  user_token: str) -> None:
    """Pure-API rollback — uses GitHub Git Data API to push a revert
    commit on top of branch HEAD. No force-push, full history preserved."""
    owner = proj["github_owner"]
    repo = proj["github_repo"]
    branch = proj.get("branch", "main")
    db = get_db()

    def _scrub(s: str) -> str:
        return (s or "").replace(user_token or "", "***PAT***") if user_token else (s or "")

    async def _set(**fields):
        if db is not None:
            await db.cto_tasks.update_one({"task_id": task_id}, {"$set": fields})

    async def _prog(step: str, status: str = "info"):
        await _rollback_log(task_id, step, status)

    try:
        await _set(rollback_status="running")
        result = await gh_api_revert(
            owner=owner, repo=repo, branch=branch, token=user_token,
            commit_sha=commit_sha, progress=_prog,
        )
        await _set(
            rollback_status="done",
            rollback_sha=result["sha"],
            rollback_completed_at=time.time(),
        )
    except Exception as e:
        logger.exception(f"[rollback-api {task_id}] failed")
        safe = _scrub(str(e))
        await _rollback_log(task_id, f"❌ {safe}", "error")
        await _set(
            rollback_status="failed",
            rollback_error=safe,
            rollback_completed_at=time.time(),
        )


async def _run_rollback_with_git(task_id: str, proj: dict, commit_sha: str,
                                   user_token: str) -> None:
    """Clone, `git revert --no-edit <sha>`, push the revert commit."""
    ws = WORKSPACE / f"rb_{task_id}"
    ws.mkdir(parents=True, exist_ok=True)
    repo_path = ws / "repo"
    owner = proj["github_owner"]
    repo = proj["github_repo"]
    branch = proj.get("branch", "main")
    clone_url = f"https://{user_token}@github.com/{owner}/{repo}.git"

    db = get_db()

    def _scrub(s: str) -> str:
        """Strip the PAT from any error/log string before we persist it."""
        if not s or not user_token:
            return s or ""
        return s.replace(user_token, "***PAT***")

    async def _set(**fields):
        if db is not None:
            await db.cto_tasks.update_one({"task_id": task_id}, {"$set": fields})

    try:
        await _set(rollback_status="running")
        await _rollback_log(task_id, f"Cloning {owner}/{repo}@{branch}…")
        # Full history needed (no --depth=1) so the revert can find the sha
        r = _sh(["git", "clone", "--branch", branch, clone_url, str(repo_path)],
                cwd=ws, timeout=120)
        if r.returncode != 0:
            raise RuntimeError(f"git clone failed: {_scrub(r.stderr)[:300]}")
        await _rollback_log(task_id, "✅ Cloned", "success")

        _sh(["git", "config", "user.email", "cto@auremcto.com"], repo_path)
        _sh(["git", "config", "user.name", "AUREM CTO"], repo_path)

        # Use `git revert` so we never force-push; it produces a new commit
        # that undoes the changes. `-m 1` lets us revert merge commits if
        # the original was a merge.
        revert = _sh(
            ["git", "revert", "--no-edit", "-m", "1", commit_sha],
            repo_path, timeout=60,
        )
        if revert.returncode != 0:
            # Plain (non-merge) commits don't accept `-m`; retry without it
            _sh(["git", "revert", "--abort"], repo_path)
            revert = _sh(
                ["git", "revert", "--no-edit", commit_sha],
                repo_path, timeout=60,
            )
        if revert.returncode != 0:
            raise RuntimeError(
                f"git revert failed (possibly conflicts): {_scrub(revert.stderr)[:300]}"
            )
        await _rollback_log(task_id, f"✏️ Reverted {commit_sha}", "success")

        push = _sh(["git", "push", "origin", branch], repo_path, timeout=90)
        if push.returncode != 0:
            raise RuntimeError(f"git push failed: {_scrub(push.stderr)[:300]}")

        new_sha = _sh(["git", "rev-parse", "--short", "HEAD"], repo_path).stdout.strip()
        await _rollback_log(task_id, f"🚀 pushed revert — {new_sha}", "success")
        await _set(
            rollback_status="done",
            rollback_sha=new_sha,
            rollback_completed_at=time.time(),
        )
    except Exception as e:
        logger.exception(f"[rollback {task_id}] failed")
        safe_msg = _scrub(str(e))
        await _rollback_log(task_id, f"❌ {safe_msg}", "error")
        await _set(
            rollback_status="failed",
            rollback_error=safe_msg,
            rollback_completed_at=time.time(),
        )
    finally:
        shutil.rmtree(ws, ignore_errors=True)


@router.get("/tasks/{task_id}")
async def get_task(task_id: str, authorization: str = Header(None)) -> dict:
    me = await current_dev(authorization)
    db = require_db()
    t = await db.cto_tasks.find_one(
        {"task_id": task_id, "user_id": me["user_id"]}, {"_id": 0}
    )
    if not t:
        raise HTTPException(404, "Task not found")
    return {"ok": True, "task": t}


@router.get("/tasks/{task_id}/scan")
async def get_task_scan(
    task_id: str,
    authorization: str = Header(None),
) -> dict:
    """Iter 167 — return the post-task regex scan for a completed task.

    Scan is populated by the worker right after `status=done`, so the
    frontend polls this endpoint for up to ~10s after the task finishes.
    Returns `{ok, status, scan}` where `scan` is null if no issues found.
    """
    me = await current_dev(authorization)
    db = require_db()
    t = await db.cto_tasks.find_one(
        {"task_id": task_id, "user_id": me["user_id"]},
        {"_id": 0, "post_scan": 1, "status": 1},
    )
    if not t:
        raise HTTPException(404, "Task not found")
    return {
        "ok":     True,
        "status": t.get("status"),
        "scan":   t.get("post_scan"),
    }


@router.post("/tasks/{task_id}/retry")
async def retry_task(
    task_id: str,
    bg: BackgroundTasks,
    authorization: str = Header(None),
) -> dict:
    """Iter 36: re-queue a FAILED task as a brand-new task with the same
    payload. We don't mutate the old row — easier audit + the user can
    see what error the original hit. Returns the new `task_id`."""
    me = await current_dev(authorization)
    await assert_has_budget(me["user_id"])
    await assert_has_task_budget(me["user_id"])
    db = require_db()
    old = await db.cto_tasks.find_one(
        {"task_id": task_id, "user_id": me["user_id"]}
    )
    if not old:
        raise HTTPException(404, "Task not found")
    if old.get("status") != "failed":
        raise HTTPException(400,
                            f"Only failed tasks can be retried "
                            f"(current: {old.get('status')})")

    proj = await db.cto_projects.find_one(
        {"project_id": old["project_id"], "user_id": me["user_id"]},
        {"_id": 0, "repo_index_summary": 0, "brain_text": 0,
         "repo_index_blocks": 0, "last_commit_diff": 0}
    )
    if not proj:
        raise HTTPException(404, "Parent project not found")

    new_task_id = "t_" + uuid.uuid4().hex[:12]
    _maxx = bool(old.get("maxx_mode", False))
    # Pattern #1 fix from RECURRING_ISSUES.md — the AI failed last time for a
    # reason. Carry that reason forward in the new task's context so the
    # model sees what to avoid. Without this, retry produces the exact same
    # output (especially for "empty file body" rejections).
    prev_err = (old.get("error") or "").strip()
    prev_steps = old.get("steps") or []
    # Surface last error step text too — often more specific than the
    # top-level error field (e.g. per-file Vanguard rejection list).
    last_err_step = next(
        (s.get("step", "") for s in reversed(prev_steps)
         if s.get("status") in ("error", "fail")),
        "",
    )
    augmented_context = old.get("context", "")
    failure_signals = [s for s in (prev_err, last_err_step) if s]
    if failure_signals:
        augmented_context = (
            (augmented_context + "\n\n" if augmented_context else "")
            + "Previous attempt failed:\n"
            + "\n".join(f"  • {s[:300]}" for s in failure_signals)
            + "\n\nDo NOT repeat that failure. If a file body was rejected as "
              "empty, write the FULL implementation (classes, functions, "
              "actual logic) — not just a docstring or `pass`."
        )
    await db.cto_tasks.insert_one({
        "task_id":      new_task_id,
        "user_id":      me["user_id"],
        "project_id":   old["project_id"],
        "task":         old.get("task", ""),
        "files":        old.get("files", []),
        "context":      augmented_context,
        "status":       "queued",
        "maxx_mode":    _maxx,
        "created_at":   time.time(),
        "retry_of":     task_id,
        "steps":        [{"step": f"🔁 retry of {task_id}"
                                  + (" (with failure context)" if failure_signals else ""),
                          "status": "info",
                          "ts": time.time()}],
    })
    user_token = await _decrypt_pat(me["user_id"], proj.get("github_token")) \
        or await _user_gh_token(me["user_id"])
    bg.add_task(
        _run_task,
        new_task_id, proj, old.get("task", ""),
        old.get("files", []), augmented_context, user_token, _maxx,
    )
    return {"ok": True, "task_id": new_task_id, "retry_of": task_id,
            "carried_failure_context": bool(failure_signals)}




@router.get("/tasks/project/{project_id}")
async def project_tasks(project_id: str, authorization: str = Header(None)) -> dict:
    me = await current_dev(authorization)
    db = require_db()
    tasks = await db.cto_tasks.find(
        {"project_id": project_id, "user_id": me["user_id"]}, {"_id": 0}
    ).sort("created_at", -1).limit(20).to_list(20)
    return {"ok": True, "tasks": tasks}


@router.get("/tasks/{task_id}/stream")
async def task_stream(task_id: str, authorization: str = Header(None)):
    """SSE stream of live worker steps for a single task (Iter 73).

    Used by the chat bubble's <TaskLiveTape> to render a terminal-style
    progress feed: reading files… → thinking… → committing → done.

    Closes on a `done` or `fail` frame, or after 5 min wall-clock.
    Sends a keepalive `ping` every 2 s of silence so the EventSource
    on slow networks doesn't auto-retry."""
    me = await current_dev(authorization)
    db = require_db()
    task = await db.cto_tasks.find_one(
        {"task_id": task_id, "user_id": me["user_id"]}, {"_id": 0}
    )
    if not task:
        raise HTTPException(404, "task not found")

    async def generate():
        q = _task_queues.get(task_id)
        if q is None:
            q = asyncio.Queue(maxsize=256)
            _task_queues[task_id] = q

        def _build_synthetic_handoff(t: dict) -> dict:
            """Iter 212m-10 — when the worker finishes before the SSE
            client connects (common for 1-2s commits), the queue is
            empty and we synthesise only a `done` frame from Mongo.
            Without a `task_handoff` frame the floating LiveTaskPopup
            never latches on, so we mint one here too."""
            return {
                "type": "task_handoff",
                "step": "task_handoff",
                "pct": None,
                "ts": time.time(),
                "kind": "task_handoff",
                "project_id": t.get("project_id") or "",
                "sha": (t.get("commit_sha") or "")[:7],
                "source": "task_stream_synthetic",
            }

        # If the task already terminated before the client connected,
        # emit a single synthetic final frame and exit immediately.
        if task.get("status") in ("done", "failed"):
            if task["status"] == "done":
                yield f"data: {json.dumps(_build_synthetic_handoff(task))}\n\n"
            final = {
                "type": "done" if task["status"] == "done" else "fail",
                "step": (f"Done — {task.get('commit_sha','')[:7]}"
                         if task["status"] == "done"
                         else f"Failed — {(task.get('error') or '')[:80]}"),
                "pct": 100,
                "ts": time.time(),
            }
            yield f"data: {json.dumps(final)}\n\n"
            _task_queues.pop(task_id, None)
            return

        deadline = time.time() + 300
        while time.time() < deadline:
            try:
                event = await asyncio.wait_for(q.get(), timeout=2.0)
            except asyncio.TimeoutError:
                yield "data: {\"type\":\"ping\"}\n\n"
                # Poll Mongo — covers the case where the worker finished
                # but its terminal _emit was dropped (e.g. queue full or
                # process restart).
                t = await db.cto_tasks.find_one(
                    {"task_id": task_id}, {"_id": 0, "status": 1,
                                            "commit_sha": 1, "error": 1,
                                            "project_id": 1},
                )
                if t and t.get("status") in ("done", "failed"):
                    if t["status"] == "done":
                        yield f"data: {json.dumps(_build_synthetic_handoff(t))}\n\n"
                    final = {
                        "type": "done" if t["status"] == "done" else "fail",
                        "step": (f"Done — {t.get('commit_sha','')[:7]}"
                                 if t["status"] == "done"
                                 else f"Failed — {(t.get('error') or '')[:80]}"),
                        "pct": 100,
                        "ts": time.time(),
                    }
                    yield f"data: {json.dumps(final)}\n\n"
                    _task_queues.pop(task_id, None)
                    return
                continue
            yield f"data: {json.dumps(event)}\n\n"
            if event.get("type") in ("done", "fail"):
                _task_queues.pop(task_id, None)
                return

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ── Background worker ────────────────────────────────────────────────────
def _classify_phase(step: str) -> Optional[str]:
    """Iter 168 — map a free-form step string to a coarse phase bucket
    so the live task popup can render phase chips without us having to
    touch every _log() callsite. Returns one of:
    phase_read / phase_think / phase_write / phase_verify / phase_commit
    or None if the step doesn't fit a phase (it just appears as a plain
    log line then)."""
    s = (step or "").lower()
    if any(k in s for k in (
        "📡", "📄", "reading", "fetched", "fetching",
        "cloning", "cloned", "injected", "🗂", "📋",
    )):
        return "phase_read"
    if any(k in s for k in (
        "🧠", "thinking", "plan:", "planning", "deepseek", "claude review",
    )):
        return "phase_think"
    if any(k in s for k in (
        "✏️", "💾", "writing", "regenerating", "auto-fixed", "linter",
        "validating", "sandbox",
    )):
        return "phase_write"
    if any(k in s for k in (
        "🛡", "vanguard", "verify", "verified",
    )):
        return "phase_verify"
    if any(k in s for k in (
        "🚀", "committing", "pushed", "commit", "pushing",
    )):
        return "phase_commit"
    return None


async def _log(task_id: str, step: str, status: str = "info"):
    db = get_db()
    # Iter 168 — persist phase bucket alongside the raw step text so
    # the LiveTaskPopup can render phase chips from polled steps[].
    phase = _classify_phase(step)
    if db is not None:
        doc = {"step": step, "status": status, "ts": time.time()}
        if phase:
            doc["kind"] = phase
        await db.cto_tasks.update_one(
            {"task_id": task_id},
            {"$push": {"steps": doc}},
        )
    # Also fan out to the live SSE queue so chat bubbles can render the
    # worker tape in real time (Iter 73).  status→kind: error→fail, others→step.
    # Phase classification overrides the generic step kind when found
    # so SSE consumers can drive phase UI too.
    kind = "fail" if status == "error" else (phase or "step")
    await _emit(task_id, step, kind=kind)


async def _set_status(task_id: str, **fields):
    db = get_db()
    if db is not None:
        # Iter 212m-12 — auto-translate failure errors into a
        # non-technical Hinglish explanation with concrete steps so
        # founders aren't staring at raw stack traces. We only run
        # the translator when the new status is `failed` AND a
        # raw error string is being set.
        if fields.get("status") == "failed" and fields.get("error"):
            try:
                from services.error_translator import translate
                friendly = await translate(fields["error"])
                fields["error_plain"]      = friendly.get("plain") or ""
                fields["error_steps"]      = friendly.get("steps") or []
                fields["error_suggestion"] = friendly.get("suggestion") or ""
                fields["error_source"]     = friendly.get("source") or "unknown"
            except Exception as _e:                  # noqa: BLE001
                # Translator must never block the failure write —
                # fall back to leaving only `error` populated.
                logger.warning("error_translator wedged: %r", _e)
        await db.cto_tasks.update_one({"task_id": task_id}, {"$set": fields})


def _sh(cmd: list, cwd: Path, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout)


_AI_SYS = (
    "You are AUREM CTO — a senior engineer who SHIPS production-grade code.\n"
    "\n"
    "OUTPUT CONTRACT (NON-NEGOTIABLE):\n"
    "  Line 1 must be exactly:  SUMMARY: <one line, <=120 chars>\n"
    "  Then, for EVERY file you change, output:\n"
    "    FILE: <relative/path/from/repo/root>\n"
    "    ```\n"
    "    <COMPLETE final file content — every single line, top to bottom>\n"
    "    ```\n"
    "  Use as many FILE blocks as needed. Nothing else outside SUMMARY + FILE blocks.\n"
    "\n"
    "HARD RULES — violations cause the commit to be REJECTED by the verifier:\n"
    "  1. Each FILE block MUST contain the complete final file, not a diff,\n"
    "     not a patch, not a snippet, not ellipses. Write every line, every\n"
    "     import, every closing brace, end-to-end.\n"
    "  2. NEVER use placeholder comments like '// ... rest of file ...',\n"
    "     '/* existing code */', '# ... unchanged ...', '... (truncated)',\n"
    "     '<keep the rest>', or any synonym. If you cannot fit the whole\n"
    "     file, split the task — do NOT abbreviate.\n"
    "  3. If editing a file you were shown, preserve everything you did\n"
    "     not intend to change. Copy lines verbatim if needed.\n"
    "  4. Do not invent file paths. Only emit paths that exist in the\n"
    "     context, OR paths you genuinely want to create.\n"
    "  5. Tests, configs and docs that need to change MUST also be emitted\n"
    "     as FILE blocks — do not just describe them in prose.\n"
    "  6. NO prose, NO markdown headings, NO 'Here is the change…' lines\n"
    "     outside the SUMMARY + FILE blocks.\n"
    "\n"
    "QUALITY BAR:\n"
    "  • Match the existing project's conventions (naming, indentation,\n"
    "    quote style, import order) exactly — you were shown those files.\n"
    "  • Prefer minimal, surgical edits over large refactors unless the\n"
    "    task explicitly asks for one.\n"
    "  • If the task is ambiguous, make the most defensible choice and\n"
    "    mention the tradeoff in the SUMMARY line."
)


def _load_design_system() -> str:
    """Load the AUREM design-system prompt once at module import. If the
    file is missing (e.g. fresh deploy), we degrade gracefully — the
    base _AI_SYS still ships."""
    try:
        import pathlib
        p = pathlib.Path(__file__).parent.parent / "prompts" / "aurem_design_system.md"
        if p.exists():
            return "\n\n# AUREM DESIGN SYSTEM — when emitting frontend code (.jsx/.tsx/.css/.html), follow EVERY rule below:\n\n" + p.read_text()
    except Exception:
        pass
    return ""


_AI_SYS = _AI_SYS + _load_design_system()


# Patterns the verifier rejects — AI sometimes sneaks placeholders past
# the prompt. We catch them client-side BEFORE pushing to GitHub so the
# user never sees a commit that silently truncates their file.
_TRUNCATION_PATTERNS = [
    "... rest of file",
    "... existing code",
    "... unchanged",
    "...(truncated)",
    "... (truncated)",
    "// rest of file",
    "/* existing code */",
    "/* ... */",
    "# ... existing",
    "# rest of file",
    "<keep the rest",
    "<rest of file",
    "<existing code",
    "[rest of file",
    "[existing code",
    "// keep existing",
    "// ... (",
    "/* TODO: keep",
]


def _looks_truncated(path: str, body: str) -> Optional[str]:
    """Return a human reason if `body` looks like an AI-truncated edit,
    else None. Run on every FILE block before we push."""
    if not body or not body.strip():
        return "empty file body"
    low = body.lower()
    for pat in _TRUNCATION_PATTERNS:
        if pat.lower() in low:
            return f"contains placeholder '{pat}'"
    # Very short edits to non-trivial files are suspicious too — but we
    # only flag them when the body has fewer than 3 non-blank lines AND
    # the extension suggests code (not config/markdown).
    non_blank = sum(1 for ln in body.splitlines() if ln.strip())
    is_codey = path.endswith((".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java"))
    if is_codey and non_blank < 3:
        return f"only {non_blank} non-blank lines for a code file"
    return None


# Iter 36: bulletproof retry wrapper for transient upstream failures.
# Wraps an async coroutine factory in exponential-backoff retry. Every
# failed attempt is logged to the task feed so the user sees WHAT went
# wrong, not just a silent "task failed". This is what makes Ship via
# CTO self-heal on rate-limit / 5xx / network blips instead of giving up.
async def _retry(coro_factory, *, what: str, task_id: str,
                 attempts: int = 3, base_sleep: float = 1.5):
    """Run `coro_factory()` up to `attempts` times with exp backoff
    (1.5s → 3s → 6s …). Re-raises the LAST exception if every attempt fails."""
    last_exc: Optional[Exception] = None
    for i in range(1, attempts + 1):
        try:
            return await coro_factory()
        except Exception as e:
            last_exc = e
            await _log(
                task_id,
                f"⏳ {what} failed (attempt {i}/{attempts}): "
                f"{type(e).__name__}: {str(e)[:140]}",
                "warning",
            )
            if i < attempts:
                await asyncio.sleep(base_sleep * (2 ** (i - 1)))
    assert last_exc is not None
    raise last_exc





async def _run_task(task_id, proj, task, files, context, user_token, maxx_mode: bool = False):
    """Dispatcher — uses git-subprocess path when git is installed, falls
    back to the pure GitHub-API path when it isn't (Iter 21)."""
    if _GIT_AVAILABLE:
        return await _run_task_with_git(
            task_id, proj, task, files, context, user_token, maxx_mode
        )
    return await _run_task_via_api(
        task_id, proj, task, files, context, user_token, maxx_mode
    )


async def _run_task_via_api(task_id, proj, task, files, context, user_token, maxx_mode: bool = False):
    """API-only worker — no `git` binary needed. Reads target files from
    GitHub, asks AUREM to generate edits, then commits everything as ONE
    atomic commit via the Git Data API."""
    import re
    import httpx
    owner = proj["github_owner"]
    repo = proj["github_repo"]
    branch = proj.get("branch", "main")
    if not user_token:
        await _set_status(task_id, status="failed",
                          error="No PAT on project — open Edit and add one",
                          completed_at=time.time())
        return

    # Resolve the project owner's current tier ONCE — drives every
    # feature gate downstream (parallel agents, priority queue, etc.).
    user_tier = "free"
    try:
        _db_for_tier = get_db()
        if _db_for_tier is not None:
            _u = await _db_for_tier.dev_users.find_one(
                {"user_id": proj.get("user_id")}, {"tier": 1},
            )
            if _u and _u.get("tier"):
                user_tier = _u["tier"]
    except Exception:
        pass

    try:
        await _set_status(task_id, status="pulling", started_at=time.time())
        await _emit(task_id, "Reading repository files…", kind="phase_read", pct=10)
        await _log(task_id, f"📡 Reading {owner}/{repo}@{branch} via API…")

        # 1) Read target files (or auto-pick a few likely ones) IN PARALLEL
        await _set_status(task_id, status="reading")
        target_files = list(files or [])
        if not target_files:
            target_files = [
                "main.py", "app.py", "server.py", "index.html",
                "src/App.jsx", "src/main.jsx", "pages/index.js",
                "README.md",
            ]
        async with httpx.AsyncClient(timeout=30.0) as client:
            fetched = await asyncio.gather(*[
                gh_api_fetch_file(client, owner, repo, f, branch, user_token)
                for f in target_files[:8]
            ])
        contents: dict = {}
        for path, body in zip(target_files[:8], fetched):
            if body is not None:
                contents[path] = body[:10000]
                await _log(task_id, f"📄 read {path}")
                # When user didn't specify files, keep first 4 hits
                if not files and len(contents) >= 4:
                    break
        # iter 114 — persist files_read for the live popup
        try:
            from services.task_diff import build_files_read
            await _set_status(task_id, files_read=build_files_read(contents))
        except Exception:
            pass

        # 2) AI codegen — augment the user message with the cached
        # repo index so AUREM sees the project's overall shape, not
        # just the 4-8 explicitly-fetched files. Falls back silently
        # if no index has been built yet for this project.
        await _set_status(task_id, status="fixing")
        try:
            from services.codebase_indexer import build_context_block
            repo_block = await build_context_block(
                proj.get("user_id", ""), proj.get("project_id", ""),
                max_chars=4500,
            )
            if repo_block:
                await _log(task_id, "🗂️ injected cached repo index")
        except Exception as _e:
            repo_block = None
        # iter 41 — Brain + Issues context (zero LLM cost, ~350 tokens)
        # iter 169 — switched from V1 (project_brains) to V2
        # (project_brains_v2). V1 retired — same call shape, denser
        # context format via format_brain_for_agent().
        brain_ctx = ""
        issues_ctx = ""
        try:
            from services.project_brain import (
                get_brain_v2, format_brain_for_agent,
            )
            _db = get_db()
            if _db is not None:
                _brain = await get_brain_v2(
                    _db, proj.get("project_id", ""), user_id,
                )
                if _brain:
                    brain_ctx = format_brain_for_agent(_brain)
        except Exception as _e:
            brain_ctx = ""
        try:
            from services.github_issues_context import get_relevant_issues_context
            _db = get_db()
            if _db is not None and user_token:
                issues_ctx = await get_relevant_issues_context(
                    db=_db, repo_owner=owner, repo_name=repo,
                    github_pat=user_token, task_description=task,
                )
        except Exception:
            issues_ctx = ""
        if brain_ctx:
            await _log(task_id, "🧠 injected project memory")
        if issues_ctx:
            await _log(task_id, "📋 injected relevant GitHub issues")

        await _emit(task_id, "ORA thinking…", kind="phase_think", pct=30)
        await _log(task_id, "🧠 DeepSeek thinking…")
        files_blob = "\n\n".join(
            f"FILE: {p}\n```\n{c}\n```" for p, c in contents.items()
        )
        extra_context_block = ""
        if brain_ctx:
            extra_context_block += f"\n\n[PROJECT MEMORY]\n{brain_ctx}"
        if issues_ctx:
            extra_context_block += f"\n\n[OPEN ISSUES]\n{issues_ctx}"
        # iter 44 — Vanguard skill injection. Pre-warms the AI with
        # battle-tested security patterns matching the task type
        # (auth/api/payments/react/backend). Zero LLM cost.
        try:
            from services.skill_context_injector import build_skill_context
            sk_ctx = build_skill_context(task)
            if sk_ctx:
                extra_context_block += f"\n\n{sk_ctx}"
                await _log(task_id, "🛡️ injected Vanguard security skills")
        except Exception:
            pass
        # Multi-file task detection — tells ORA to ship everything in
        # one turn instead of stopping after file 1 with "Next:".  Keyword
        # heuristic; the persona itself handles the ≥3-file checklist.
        _multi_file_keywords = (
            "all ", "every ", "each ", "multiple", "scaffold",
            "workers", "pillar", "4 files", "5 files", "3 files",
            "all files", "complete", "full implementation",
        )
        _is_multi = any(kw in task.lower() for kw in _multi_file_keywords)
        _multi_file_instruction = ""
        _promised_files: set[str] = set()
        if _is_multi:
            _multi_file_instruction = (
                "\n\nMULTI-FILE TASK DETECTED: You MUST generate ALL required "
                "files in this single response. Do NOT stop after the first "
                "file. Do NOT say 'Next:' or 'Reply to continue'. Use the "
                "checklist format: [ ] file → [x] done. Ship the complete "
                "implementation in one commit."
            )
            # Structural multi-file contract — extract every concrete file
            # path mentioned in the task/context.  If the LLM later returns
            # an `edits` dict missing any of these, we auto-retry with a
            # very specific "you promised N files, only M arrived" nudge.
            import re as _refm
            _promised_files = set(_refm.findall(
                r"[\w./-]+\.(?:py|jsx?|tsx?|css|json|md|html|yml|yaml)",
                f"{task}\n{context or ''}",
            ))
            if _promised_files:
                db_for_plan = get_db()
                if db_for_plan is not None:
                    _plan = [{"file": f, "status": "pending"}
                             for f in sorted(_promised_files)[:12]]
                    await db_for_plan.cto_tasks.update_one(
                        {"task_id": task_id},
                        {"$set": {"task_plan": _plan}},
                    )
                    await _emit(task_id, f"Plan: {len(_plan)} files",
                                kind="task_plan", plan=_plan, pct=18)

        user_msg = (
            f"TASK: {task}\n"
            f"{('CONTEXT: ' + context) if context else ''}\n\n"
            f"Tech: {proj.get('tech_stack','auto')}\n\n"
            f"{repo_block or ''}{extra_context_block}\n\n{files_blob}"
            f"{_multi_file_instruction}"
        )

        # iter 43 — Parallel multi-agent codegen for big tasks.
        # `should_parallelize()` decides automatically based on task scope
        # AND file tree. Single-file or small tasks fall through to the
        # existing single-call path below (which keeps SUMMARY parsing).
        edits: dict[str, str] = {}
        summary = "AI changes"
        parallelized = False
        agents_count = 1
        try:
            from services.parallel_agents import (
                should_parallelize, run_parallel_agents, decompose_task,
            )
            from services.subscription_tiers import can_use_feature
            file_tree_hint = list(contents.keys()) + (files or [])
            # Parallel agents are a Pro feature — Free / Starter fall
            # through to the single-agent path (no error, just slower).
            _parallel_allowed = can_use_feature(user_tier, "parallel_agents")
            if should_parallelize(task, file_tree_hint) and _parallel_allowed:
                # Pre-decompose so we know which agents are about to fire
                # — that lets the chat bubble render the badges + per-agent
                # mini progress bars BEFORE the LLM round-trip resolves.
                _agents_preview = decompose_task(task, f"{owner}/{repo}@{branch}", file_tree_hint)
                _agent_roles = [a.get("role", "agent") for a in _agents_preview]
                await _emit(
                    task_id,
                    f"Parallel mode — {len(_agent_roles)} agents working simultaneously",
                    kind="parallel", pct=30,
                    agents=[r.title() for r in _agent_roles],
                )
                await _log(task_id, "⚡ Task is multi-domain — splitting into parallel agents")
                gen_result = await run_parallel_agents(
                    task_description=user_msg,
                    repo_ctx=f"{owner}/{repo}@{branch}",
                    file_tree=file_tree_hint,
                )
                edits = gen_result.get("file_blocks", {}) or {}
                parallelized = bool(gen_result.get("parallelized"))
                agents_count = int(gen_result.get("agents_used", 1))
                if parallelized:
                    # Fan out one terminal frame per agent so the per-agent
                    # mini-bars can settle to ✓ / ✕ in the UI.
                    for r in gen_result.get("agent_results", []):
                        ok = not r.get("error")
                        await _emit(
                            task_id,
                            f"{r.get('role','agent').title()} agent {'done' if ok else 'failed'}",
                            kind="parallel_agent",
                            role=r.get("role", "agent").title(),
                            ok=ok,
                        )
                if parallelized and edits:
                    summary = f"Parallel codegen ({agents_count} agents) — {task[:120]}"
                    await _log(task_id,
                               f"✅ {agents_count} agents merged {len(edits)} file edits",
                               "success")
        except Exception as _pe:
            await _log(task_id, f"parallel codegen fell back to single agent: {_pe}", "warning")
            edits = {}
            parallelized = False
            agents_count = 1

        if not edits:
            # Single-agent legacy path — unchanged behaviour for small tasks
            # and as fallback when parallel returned empty.
            reply = await _retry(
                lambda: call_llm(
                    messages=[{"role": "user", "content": user_msg}],
                    system=_AI_SYS, max_tokens=3500, temperature=0.0,
                ),
                what="AI codegen", task_id=task_id,
            )
            # Coarse token estimate (chars/4) so P&L has real numbers
            approx_in = (len(_AI_SYS) + len(user_msg)) // 4
            approx_out = len(reply or "") // 4
            await _set_status(
                task_id,
                tokens_used=approx_in + approx_out,
                agent_used="deepseek",
            )
            summary_m = re.search(r"SUMMARY:\s*(.+)", reply)
            summary = (summary_m.group(1).strip() if summary_m else "AI changes")[:300]
            # Iter 212m-33 — tolerant FILE-block parser (was a rigid
            # single-line regex that silently dropped edits whenever
            # the model deviated by even one whitespace).
            from services.llm_file_parser import parse_file_blocks
            edits.update(parse_file_blocks(reply))
        else:
            # Parallel path produced edits — record token-equivalent + agent name
            await _set_status(
                task_id,
                tokens_used=(len(_AI_SYS) + len(user_msg)) // 4
                            + sum(len(c) for c in edits.values()) // 4,
                agent_used=f"deepseek-parallel-x{agents_count}",
            )

        if not edits:
            await _log(task_id, "⚠️ AI returned no file edits", "warning")
            await _set_status(task_id, status="done", result=summary,
                              completed_at=time.time())
            return
        await _emit(task_id, "Writing files…", kind="phase_write", pct=60)
        await _log(task_id, f"✏️ {len(edits)} files to update", "success")

        # PRE-PUSH GATE — reject AI output that looks truncated. We'd
        # rather fail loudly here than silently push a half-file that
        # later confuses Claude/users when they scan the repo.
        #
        # Before failing, give the model ONE chance to regenerate with
        # explicit guidance about what went wrong (Pattern #1 deep fix).
        # Without this, an empty-body output sent the user into a manual
        # Retry loop that did nothing different.
        async def _truncation_reasons(blocks: dict) -> list[str]:
            out: list[str] = []
            for path, body in blocks.items():
                reason = _looks_truncated(path, body)
                if reason:
                    out.append(f"{path} — {reason}")
            return out

        bad: list[str] = await _truncation_reasons(edits)
        if (not edits) or (bad and len(bad) == len(edits)):
            # No edits OR every edit was rejected — try once more with a
            # nudge. This is in-task auto-regenerate; the user has not
            # clicked Retry. If THIS also fails we surface an actionable
            # error rather than silently looping.
            await _log(task_id,
                       "no usable file edits — auto-regenerating with explicit guidance",
                       "warning")
            # Iter 212m-6 — include the exact paths that failed and WHY
            # so the model can target its retry. Previously the generic
            # nudge didn't tell the model which files it screwed up, so
            # it often produced the same broken output.
            _path_feedback = ""
            if bad:
                _path_feedback = (
                    "\n\nPRIOR ATTEMPT FAILED ON THESE FILES — fix each one:\n"
                    + "\n".join(f"  - {b}" for b in bad[:10])
                )
            nudge = (
                "Your previous response contained no usable file changes "
                "(empty file body or no FILE blocks).\n"
                "You MUST output complete file content using this exact format:\n"
                "FILE: <path>\n```\n<complete file body — real code, not "
                "a docstring or `pass`>\n```\n"
                "Do NOT just describe what you would do. Write the actual code."
                + _path_feedback
            )
            reply2 = await _retry(
                lambda: call_llm(
                    messages=[{"role": "user", "content": user_msg + "\n\n" + nudge}],
                    system=_AI_SYS, max_tokens=3500, temperature=0.0,
                ),
                what="AI codegen auto-retry", task_id=task_id,
            )
            edits = {}
            # Iter 212m-33 — tolerant FILE-block parser (see above).
            from services.llm_file_parser import parse_file_blocks
            edits.update(parse_file_blocks(reply2))
            bad = await _truncation_reasons(edits)

        if bad:
            err = ("AI returned suspect edits (refusing to push):\n  - "
                   + "\n  - ".join(bad)
                   + "\n\nTry rephrasing: specify which file to edit and "
                     "what to change. Example: 'Edit auth.py and add "
                     "rate limiting to the /login endpoint'.")
            await _log(task_id, f"🚫 {err}", "error")
            await _set_status(task_id, status="failed", error=err[:2000],
                              completed_at=time.time())
            return
        await _emit(task_id, "Running linter…", kind="phase_verify", pct=75)
        await _log(task_id, f"✅ {len(edits)} files passed truncation check", "success")

        # ── Multi-file contract — verify every file the user promised
        # actually arrived. If something is missing we ask the LLM to
        # fill the gap in one targeted retry and merge the result.
        if _is_multi and _promised_files:
            _delivered = {p.lstrip("./") for p in edits.keys()}
            _missing = {f for f in _promised_files
                        if f.lstrip("./") not in _delivered}
            if _missing and len(_missing) <= 4:
                await _emit(task_id,
                            f"Missing files — regenerating ({len(_missing)})…",
                            pct=77)
                await _log(task_id,
                           f"⚠️ Multi-file contract: missing "
                           f"{', '.join(sorted(_missing))}", "warning")
                _miss_nudge = (
                    "Your previous response was missing these files that "
                    "the task explicitly references:\n  - "
                    + "\n  - ".join(sorted(_missing))
                    + "\n\nGenerate the COMPLETE content for every missing "
                      "file now, in the same FILE: <path>\n```\n…\n``` format. "
                      "Output every missing file in one response — no "
                      "'Next:', no 'Reply to continue'."
                )
                try:
                    fill = await _retry(
                        lambda: call_llm(
                            messages=[{"role": "user",
                                       "content": user_msg + "\n\n" + _miss_nudge}],
                            system=_AI_SYS, max_tokens=3500, temperature=0.0,
                        ),
                        what="multi-file contract retry", task_id=task_id,
                    )
                    # Iter 212m-33 — tolerant FILE-block parser.
                    from services.llm_file_parser import parse_file_blocks
                    edits.update(parse_file_blocks(fill))
                except Exception as _fe:
                    logger.warning("multi-file contract retry soft-failed: %r", _fe)

        # ── Syntax validation — catch broken code before it reaches GitHub.
        # AST check for Python, `node --check` for JS/TS when node is
        # available (falls back silently if not — never blocks the
        # pipeline on an env-level missing binary).  On failure, one
        # auto-regen with the exact errors fed back (same nudge pattern
        # used by the truncation gate above).
        await _emit(task_id, "Validating generated code…", kind="phase_verify", pct=78)

        def _check_js_syntax(filepath: str, content: str) -> Optional[str]:
            """Return an error string for invalid JS/TS/JSX/TSX, None if
            valid OR if neither esbuild nor node is installed (so we
            degrade gracefully — never block on missing parsers).

            Tries `esbuild` first (understands JSX/TSX/decorators), then
            falls back to `node --check` (structural-only, no JSX)."""
            import subprocess as _sp_mod
            import tempfile as _tf
            import os as _os
            suffix = _os.path.splitext(filepath)[1] or ".js"
            tmp = None
            try:
                with _tf.NamedTemporaryFile(
                    suffix=suffix, mode="w", delete=False, encoding="utf-8",
                ) as fh:
                    fh.write(content)
                    tmp = fh.name
                # 1) esbuild — proper JSX-aware parser
                try:
                    r = _sp_mod.run(
                        ["esbuild", tmp, "--bundle=false", "--log-level=error"],
                        capture_output=True, text=True, timeout=5,
                    )
                    if r.returncode != 0 and (r.stderr or "").strip():
                        return (r.stderr or r.stdout).strip()[:300]
                    return None
                except FileNotFoundError:
                    pass   # fall through to node
                # 2) node --check — structural only, no JSX support
                r = _sp_mod.run(
                    ["node", "--check", tmp],
                    capture_output=True, text=True, timeout=5,
                )
                if r.returncode != 0:
                    return (r.stderr or r.stdout).strip()[:200]
                return None
            except FileNotFoundError:
                return None
            except Exception:
                return None
            finally:
                if tmp:
                    try:
                        _os.unlink(tmp)
                    except Exception:
                        pass

        def _syntax_errors(blocks: dict[str, str]) -> list[str]:
            out: list[str] = []
            for _spath, _scontent in blocks.items():
                if not _scontent or not _scontent.strip():
                    continue
                if _spath.endswith(".py"):
                    try:
                        import ast as _ast
                        _ast.parse(_scontent)
                    except SyntaxError as _se:
                        out.append(
                            f"{_spath}: SyntaxError line {_se.lineno or 1}: {_se.msg}"
                        )
                elif _spath.endswith((".js", ".jsx", ".ts", ".tsx")):
                    js_err = _check_js_syntax(_spath, _scontent)
                    if js_err:
                        out.append(f"{_spath}: {js_err}")
                elif _spath.endswith(".json"):
                    try:
                        import json as _jparse
                        _jparse.loads(_scontent)
                    except Exception as _je:
                        out.append(f"{_spath}: invalid JSON: {_je}")
            return out

        syntax_errors = _syntax_errors(edits)
        if syntax_errors:
            await _log(
                task_id,
                "⚠️ Syntax errors detected — auto-regenerating with feedback",
                "warning",
            )
            await _emit(task_id, "Syntax errors found — regenerating…", pct=79)
            _syn_nudge = (
                "Your previous response generated code with these syntax "
                "errors:\n  - "
                + "\n  - ".join(syntax_errors)
                + "\n\nRegenerate the COMPLETE corrected files in the same "
                "FILE: <path>\\n```\\n…\\n``` format. Ensure every function, "
                "class, and block is properly closed. Do not truncate any "
                "file. Output ALL files you edited, not just the broken ones."
            )
            reply3 = await _retry(
                lambda: call_llm(
                    messages=[{"role": "user",
                               "content": user_msg + "\n\n" + _syn_nudge}],
                    system=_AI_SYS, max_tokens=3500, temperature=0.0,
                ),
                what="AI syntax-fix auto-retry", task_id=task_id,
            )
            new_edits: dict[str, str] = {}
            # Iter 212m-33 — tolerant FILE-block parser.
            from services.llm_file_parser import parse_file_blocks
            new_edits.update(parse_file_blocks(reply3))
            if new_edits:
                # Merge — preserve any files the retry didn't include.
                edits = {**edits, **new_edits}
            syntax_errors = _syntax_errors(edits)

        if syntax_errors:
            _err_str = "\n  - ".join(syntax_errors[:3])
            err = (
                "Generated code has syntax errors after auto-retry:\n  - "
                + _err_str
                + "\n\nTry rephrasing: specify the exact function or class "
                "to change, or split the work into smaller files."
            )
            await _log(task_id, f"🚫 {err}", "error")
            await _set_status(task_id, status="failed", error=err[:2000],
                              completed_at=time.time())
            await _emit(task_id, "Syntax error — task failed",
                        kind="fail", pct=100)
            return

        # ── Sandbox validation (e2b) — runs generated Python in an
        # isolated container so ORA can verify its own code before
        # committing. Silently skipped if E2B_API_KEY isn't set.
        try:
            from services.sandbox_runner import validate_generated_files
            _sandbox = await validate_generated_files(edits, task)
            if not _sandbox.get("skipped"):
                if _sandbox.get("ok"):
                    _passed = (
                        _sandbox.get("checks", {})
                                 .get("tests", {})
                                 .get("passed", 0)
                    )
                    if _passed > 0:
                        await _emit(task_id, f"Sandbox tests passed: {_passed} ✓",
                                    pct=80)
                        await _log(task_id, f"Sandbox: {_passed} tests passed",
                                   "success")
                else:
                    _tout = ""
                    for _cn, _cr in (_sandbox.get("checks") or {}).items():
                        if not _cr.get("ok"):
                            _tout += (_cr.get("output") or _cr.get("stderr") or "")[:500]
                    if _tout:
                        await _log(task_id,
                                   f"⚠️ Sandbox flagged failures:\n{_tout[:300]}",
                                   "warning")
        except Exception as _se:
            logger.warning("sandbox validation soft-failed: %r", _se)

        # iter 41 — Design Linter (zero LLM cost, pure regex).
        # Auto-fixes safe issues first (console.log, transition: all), then
        # rejects commits with any "block" severity findings (hardcoded
        # secrets, leftover console.log, etc.).
        try:
            from services.design_linter import lint_file_blocks, auto_fix_blocks
            edits, fix_log = auto_fix_blocks(edits)
            if fix_log:
                total_fixes = sum(len(v) for v in fix_log.values())
                await _log(task_id, f"🛠️ Auto-fixed {total_fixes} safe lint issue(s) across {len(fix_log)} file(s)", "info")
            lint_result = lint_file_blocks(edits)
        except Exception as _le:
            lint_result = {"blocked": False, "issues": [], "warnings": [], "summary": ""}
        if lint_result.get("blocked"):
            await _log(task_id, f"⛔ Linter blocked the commit: {len(lint_result['issues'])} critical issue(s)", "error")
            for reason in lint_result.get("block_reasons", [])[:5]:
                await _log(task_id, f"  • {reason}", "error")
            await _set_status(task_id, status="failed",
                              error=("Design linter blocked commit:\n" + lint_result.get("summary", ""))[:2000],
                              completed_at=time.time())
            # Council log the blocked attempt
            try:
                from services.ora_council_logger import log_code_task as _log_code
                _db = get_db()
                if _db is not None:
                    await _log_code(
                        db=_db, user_message=task,
                        repo_context=f"{owner}/{repo}@{branch}",
                        deepseek_draft=str(edits)[:2000],
                        final_output="[BLOCKED BY LINTER]",
                        correction_applied=False, pass_result=False,
                        lint_blocked=True,
                        lint_issues=lint_result.get("issues", []),
                        task_id=task_id, user_id=proj.get("user_id"),
                        project_id=proj.get("project_id"),
                        maxx_mode=maxx_mode,
                    )
            except Exception:
                pass
            return
        if lint_result.get("warnings"):
            await _log(task_id, f"⚠️ Linter: {len(lint_result['warnings'])} non-blocking warning(s)", "warning")

        # iter 111 — VANGUARD VERIFY AGENT (separate-agent security pass)
        # ────────────────────────────────────────────────────────────
        # After ORA writes code but BEFORE we commit, run a SECOND
        # independent agent (Claude Sonnet 4.5 via Emergent LLM key) that
        # re-audits the patch for vulnerabilities. Plus if the patch
        # contains executable Python, smoke-import it inside E2B so we
        # catch SyntaxError / ImportError / NameError that the regex AST
        # check can't see. Architecture mirrors Anthropic's
        # defending-code-reference-harness "find → grader → judge"
        # pattern. Both passes must succeed for the commit to proceed.
        try:
            await _log(task_id, "🛡️ Vanguard verify agent reviewing patch…")
            from services.vanguard_verify_agent import verify_patch
            # Iter 212m-42 — derive the active mode from the task envelope
            # so the per-mode Vanguard config (set from /admin/vanguard)
            # can apply the right severity threshold per Swift / Pro / Maxx.
            # We only carry the maxx_mode boolean at this layer; treat
            # everything else as Swift (the safest, strictest default).
            _vg_mode = "maxx" if maxx_mode else "swift"
            # Iter 212m-132 — Diff-aware verify: pass `contents`
            # (the pre-edit content we fetched from GitHub at the
            # READ phase above) as `base_blocks` so Vanguard only
            # flags vulns on lines the patch ACTUALLY added or
            # modified.  Pre-existing issues in untouched lines are
            # surfaced in `verify_result.regex.skipped_preexisting`
            # for audit but do NOT block the commit.
            verify_result = await verify_patch(
                edits, repo_ctx=f"{owner}/{repo}@{branch}",
                mode=_vg_mode, base_blocks=contents,
            )
            await _log(task_id, f"🛡️ Verify: {verify_result['summary']}",
                       "info" if verify_result["pass"] else "error")
            if not verify_result["pass"]:
                # iter 112 — persist the blocked commit to vanguard_audit
                try:
                    from services.vanguard_audit import log_blocked_commit
                    _db = get_db()
                    if _db is not None:
                        await log_blocked_commit(
                            _db,
                            user_id=str(proj.get("user_id") or "unknown"),
                            project=f"{owner}/{repo}@{branch}",
                            verify_result=verify_result,
                            project_id=str(proj.get("project_id")) if proj.get("project_id") else None,
                            task_id=task_id,
                        )
                except Exception as _ae:
                    logger.warning("vanguard_audit log failed: %r", _ae)
                # Surface up to 5 critical/high findings in the log
                critical = [f for f in verify_result.get("findings", [])
                             if f.get("severity") in ("CRITICAL", "HIGH")][:5]
                for f in critical:
                    await _log(
                        task_id,
                        f"  • [{f.get('severity')}] {f.get('file','?')}"
                        f":{f.get('line','?')} — {f.get('rule', f.get('name','issue'))}"
                        f" — {f.get('message','')[:120]}",
                        "error",
                    )
                await _set_status(
                    task_id, status="failed",
                    error=("Vanguard verify agent blocked commit:\n"
                           + verify_result.get("summary", ""))[:2000],
                    completed_at=time.time(),
                )
                return
        except Exception as _ve:
            # Verify-agent infra error is NOT a security finding — fall
            # through but log loudly so we know it isn't gating commits.
            logger.warning("vanguard verify agent crashed: %r", _ve)
            await _log(task_id, f"⚠️ Vanguard verify agent crashed: {type(_ve).__name__}", "warning")

        # 2c) TWO-AGENT MAXX (iter 40) — Claude reviews DeepSeek's edits.
        # Gated on per-task `maxx_mode`. On PASS we commit DeepSeek's
        # output as-is. On FAIL we commit Claude's corrected version.
        # Claude outage → defaults to PASS so the pipeline never blocks.
        deepseek_draft = dict(edits)   # snapshot for the council log
        review_result = {"pass": True, "corrected": None, "issues": []}
        if maxx_mode:
            try:
                await _log(task_id, "🔍 Claude reviewing DeepSeek edits…")
                from services.code_reviewer import review_code_with_claude
                review_result = await review_code_with_claude(
                    file_blocks=edits,
                    user_intent=task,
                    repo_ctx=f"{owner}/{repo}@{branch}",
                )
                if review_result["pass"]:
                    await _log(task_id, "✅ Claude review: PASS", "success")
                else:
                    n_fixed = len(review_result.get("corrected") or {})
                    await _log(task_id, f"🩹 Claude review: corrected {n_fixed} file(s)", "warning")
                    edits = review_result["corrected"] or edits
                    await _set_status(task_id, agent_used="deepseek+claude")
            except Exception as _re:
                await _log(task_id, f"⚠️ reviewer error (committing original): {_re}", "warning")
                review_result = {"pass": True, "corrected": None, "issues": []}

        # 2d) Council log — fire-and-forget; never blocks the commit.
        try:
            from services.ora_council_logger import log_code_task
            _db = get_db()
            if _db is not None:
                await log_code_task(
                    db=_db,
                    user_message=task,
                    repo_context=f"{owner}/{repo}@{branch}",
                    deepseek_draft=str(deepseek_draft)[:4000],
                    final_output=str(edits)[:4000],
                    correction_applied=not review_result["pass"],
                    pass_result=bool(review_result["pass"]),
                    claude_correction=str(review_result.get("corrected") or "") or None,
                    lint_blocked=False,
                    lint_issues=lint_result.get("issues", []),
                    parallelized=parallelized,
                    agents_used_count=agents_count,
                    task_id=task_id,
                    user_id=proj.get("user_id"),
                    project_id=proj.get("project_id"),
                    maxx_mode=maxx_mode,
                )
        except Exception:
            pass

        # 3) Commit + push as one atomic API call
        await _set_status(task_id, status="pushing")
        # Per-file progress frames so the live tape can render the
        # "Writing 2/4 files" mini bar. gh_api_commit is atomic on the
        # remote side, so we narrate the writes locally before firing it.
        _file_list = list(edits.keys())
        _total = len(_file_list)
        for _i, _fp in enumerate(_file_list, 1):
            await _emit(
                task_id,
                f"Writing file {_i} of {_total}: {_fp}",
                kind="task_state",
                files_done=_i,
                files_total=_total,
                pct=85 + int((_i / max(_total, 1)) * 5),
            )
            # Flip the matching task_plan row → done so the UI's
            # TaskManagementPanel ticks off in real time.
            if _promised_files:
                _db_plan = get_db()
                if _db_plan is not None:
                    try:
                        await _db_plan.cto_tasks.update_one(
                            {"task_id": task_id, "task_plan.file": _fp},
                            {"$set": {"task_plan.$.status": "done"}},
                        )
                    except Exception:
                        pass
        await _emit(task_id, "Committing to GitHub…", kind="phase_commit", pct=90)

        async def _prog(step: str, status: str = "info"):
            await _log(task_id, step, status)

        result = await _retry(
            lambda: gh_api_commit(
                owner=owner, repo=repo, branch=branch, token=user_token,
                files=edits,
                commit_message=f"AUREM CTO: {task[:60]}",
                progress=_prog,
            ),
            what="GitHub commit", task_id=task_id, attempts=4, base_sleep=2.0,
        )
        sha = result["sha"]
        commit_full_sha = result.get("full_sha") or sha

        # POST-PUSH VERIFY — re-fetch every edited file at the new commit's
        # SHA and confirm the remote content equals what we just pushed.
        # This catches:
        #   • branch protection that silently rejected the ref update
        #   • partial / drift writes if a future GitHub API change ever
        #     broke our blob/tree pipeline
        #   • the original user complaint: "Claude says fix isn't in the
        #     repo even though our UI says shipped"
        # The verification proves on every task that the deployed code
        # actually contains the AI's edits — no more silent successes.
        await _log(task_id, f"🔎 Verifying {len(edits)} file(s) on remote @ {sha}…")

        async def _verify_one(path: str, expected: str) -> tuple[str, bool, str]:
            async with httpx.AsyncClient(timeout=20.0) as vc:
                remote = await gh_api_fetch_file(
                    vc, owner, repo, path, commit_full_sha, user_token,
                )
            if remote is None:
                return path, False, "remote returned 404"
            # Iter 212m-6 — Normalise line endings (CRLF/CR → LF) and
            # trailing whitespace BEFORE comparing. GitHub sometimes
            # serves files with normalised newlines that differ from
            # what we pushed even though the commit landed correctly.
            # Without this, an otherwise-successful commit gets marked
            # "failed" because the byte-for-byte comparison disagrees
            # on whitespace-only differences.
            def _norm(s: str) -> str:
                return (s or "").replace("\r\n", "\n").replace("\r", "\n").rstrip()
            if _norm(remote) != _norm(expected):
                a, b = expected.splitlines(), remote.splitlines()
                first_diff = next(
                    (i for i in range(min(len(a), len(b))) if a[i] != b[i]),
                    None,
                )
                hint = (f"differs from line {first_diff + 1}" if first_diff is not None
                        else f"length local={len(expected)} remote={len(remote)}")
                return path, False, hint
            return path, True, "ok"

        verify_results = await asyncio.gather(*[
            _verify_one(p, c) for p, c in edits.items()
        ])
        failed = [(p, reason) for p, ok, reason in verify_results if not ok]
        for p, ok, reason in verify_results:
            await _log(task_id,
                       f"   {'✅' if ok else '❌'} {p} ({reason})",
                       "success" if ok else "error")
        if failed:
            err = "Post-push verification FAILED for: " + ", ".join(
                f"{p} ({r})" for p, r in failed
            )
            await _log(task_id, f"🚫 {err}", "error")
            await _set_status(task_id, status="failed", error=err[:2000],
                              commit_sha=sha, completed_at=time.time())
            return
        await _log(task_id,
                   f"✅ Verified {len(edits)} file(s) live on {branch}@{sha}",
                   "success")

        await _set_status(task_id, status="done", result=summary,
                          commit_sha=sha,
                          files_changed_simple=list(edits.keys()),
                          edits=_frontend_subset(edits),
                          verified=True,
                          completed_at=time.time())
        # iter 114 — rich diff + popup data
        try:
            from services.task_diff import build_files_changed, shape_vanguard_findings
            rich_changes = build_files_changed(contents, edits)
            findings_clean = shape_vanguard_findings(
                (verify_result.get("findings", []) if "verify_result" in locals() else []),
                status=("blocked" if "verify_result" in locals()
                        and not verify_result.get("pass", True)
                        else "fixed"),
            )
            _started = (await db.cto_tasks.find_one(
                {"task_id": task_id}, {"started_at": 1, "_id": 0}
            )) or {}
            elapsed = max(0, int(time.time() - (_started.get("started_at") or time.time())))
            await _set_status(
                task_id,
                files_changed=rich_changes,
                vanguard_findings=findings_clean,
                time_taken_seconds=elapsed,
                github_url=f"https://github.com/{owner}/{repo}/commit/{commit_full_sha}",
            )
        except Exception as _diff_e:
            logger.warning("task_diff/popup persistence failed: %r", _diff_e)
        # Iter 184 — fire a `task_handoff` frame on the task SSE stream
        # immediately before the terminal `done` frame so any client
        # subscribed to /tasks/{task_id}/stream (notably the ChatPanel
        # LiveTaskPopup that auto-attaches when the assistant message
        # carries a `shipped_task_id`) sees the canonical handoff
        # event. chat.py already emits this frame on the chat SSE
        # stream for Mode D→C / ship-shortcut handoffs; mirroring it
        # here covers the HTTP `/tasks/submit` path which never goes
        # through the chat stream — the popup was silently missing
        # for those tasks.
        await _emit(
            task_id, "task_handoff",
            kind="task_handoff",
            project_id=proj.get("project_id"),
            sha=(sha[:7] if sha else ""),
            source="task_worker_done",
        )
        await _emit(task_id, f"Done — {sha[:7]}", kind="done", pct=100)
        db = get_db()
        # Iter 167 — post-task scan: regex-only security + import lint
        # on the files ORA just shipped. Fire-and-forget guard so a slow
        # scan never blocks the "done" emit.
        if db is not None:
            try:
                from services.post_task_scanner import scan_changed_files
                _scan_paths = list(edits.keys())
                _scan_issues = await asyncio.wait_for(
                    scan_changed_files(_scan_paths, edits),
                    timeout=5.0,
                )
                if _scan_issues:
                    await db.cto_tasks.update_one(
                        {"task_id": task_id},
                        {"$set": {"post_scan": {
                            "issues":        _scan_issues,
                            "scanned_at":    time.time(),
                            "files_scanned": len(_scan_paths),
                        }}},
                    )
                    for issue in _scan_issues:
                        await _log(
                            task_id,
                            f"{issue.get('icon','⚠️')} {issue['message']} "
                            f"in {issue['file']}:{issue['line']}",
                            "warn",
                        )
            except Exception as _scan_err:
                logger.debug("post_scan (api path) skipped: %r", _scan_err)
        if db is not None:
            await db.cto_projects.update_one(
                {"project_id": proj["project_id"]},
                {"$inc": {"tasks_done": 1}, "$set": {"last_task": time.time()}},
            )
            # iter 41 — fire-and-forget brain update so ORA remembers what
            # was shipped, what files moved, and any recurring corrections.
            try:
                from services.project_brain import update_brain_after_commit
                asyncio.create_task(update_brain_after_commit(
                    db=db,
                    project_id=proj.get("project_id", ""),
                    task_description=task,
                    files_changed=list(edits.keys()),
                    was_correction_applied=not review_result["pass"],
                    issues_found=review_result.get("issues", []),
                    sha=sha or "",
                ))
            except Exception:
                pass
            # Iter 165 — Brain V2 auto-update. Fire-and-forget so a
            # slow GitHub never blocks task completion. Falls back to
            # full rebuild every FULL_REFRESH_EVERY_N_TASKS tasks.
            try:
                from services.project_brain import update_brain_after_task
                asyncio.create_task(update_brain_after_task(
                    db=db,
                    project_id=proj.get("project_id", ""),
                    user_id=user_id,
                    changed_files=list(edits.keys()),
                    task_id=task_id,
                    github_token=user_token or "",
                    github_owner=proj.get("github_owner", "") or "",
                    github_repo=proj.get("github_repo", "") or "",
                    branch=proj.get("branch", "main") or "main",
                ))
            except Exception as _bv2e:
                logger.warning("brain v2 update skipped: %r", _bv2e)
    except Exception as e:
        logger.exception(f"[cto-task-api {task_id}] failed")
        safe = str(e).replace(user_token or "", "***PAT***")
        await _log(task_id, f"❌ {safe}", "error")
        await _set_status(task_id, status="failed", error=safe,
                          completed_at=time.time())
        await _emit(task_id, f"Failed — {safe[:80]}", kind="fail", pct=100)
        # Iter 48 — background-task crash goes to Sentry (bypasses HTTP
        # middleware so explicit capture needed).
        try:
            import sentry_sdk
            with sentry_sdk.push_scope() as scope:
                scope.set_tag("kind", "cto_task_crash")
                scope.set_tag("task_id", task_id)
                scope.set_tag("project_id", proj.get("project_id", ""))
                scope.set_extra("repo", f"{proj.get('github_owner')}/{proj.get('github_repo')}")
                sentry_sdk.capture_exception(e)
        except Exception:
            pass


async def _run_task_with_git(task_id, proj, task, files, context, user_token, maxx_mode: bool = False):
    import re

    def _scrub(s: str) -> str:
        # Defence-in-depth: clone URLs, stderr, and Python tracebacks can
        # all leak the PAT. Scrub every error string before it lands in
        # Mongo or the user's task feed.
        if not s:
            return s
        return s.replace(user_token or "", "***PAT***") if user_token else s

    ws = WORKSPACE / task_id
    ws.mkdir(parents=True, exist_ok=True)
    repo_path = ws / "repo"
    owner, repo, branch = proj["github_owner"], proj["github_repo"], proj.get("branch", "main")
    clone_url = (f"https://{user_token}@github.com/{owner}/{repo}.git"
                 if user_token else f"https://github.com/{owner}/{repo}.git")

    try:
        # 1) clone
        await _set_status(task_id, status="pulling", started_at=time.time())
        await _log(task_id, f"Cloning {owner}/{repo}@{branch}…")
        r = _sh(["git", "clone", "--depth=1", "--branch", branch, clone_url, str(repo_path)],
                cwd=ws, timeout=120)
        if r.returncode != 0:
            raise RuntimeError(f"git clone failed: {_scrub(r.stderr)[:300]}")
        await _log(task_id, "✅ Cloned", "success")

        # 2) read target files
        await _set_status(task_id, status="reading")
        contents = {}
        for f in (files or [])[:6]:
            fp = repo_path / f
            if fp.is_file():
                contents[f] = fp.read_text(errors="replace")[:10000]
                await _log(task_id, f"📄 read {f}")
        if not contents:
            # auto-pick a few likely files
            for cand in ["main.py", "app.py", "server.py", "index.html",
                         "src/App.jsx", "src/main.jsx", "pages/index.js", "README.md"]:
                fp = repo_path / cand
                if fp.is_file():
                    contents[cand] = fp.read_text(errors="replace")[:10000]
                    if len(contents) >= 4:
                        break

        # 3) ai fix
        await _set_status(task_id, status="fixing")

        # LOGIC FIX — mirror the API path: inject Project Brain, GitHub
        # Issues, and Vanguard security skills here too. Without this, if
        # `git` ever becomes available in production, Iter 41/42/44
        # features silently vanish on every code task.
        brain_ctx = ""
        issues_ctx = ""
        try:
            from services.project_brain import (
                get_brain_v2, format_brain_for_agent,
            )
            _db = get_db()
            if _db is not None:
                _brain = await get_brain_v2(
                    _db, proj.get("project_id", ""), user_id,
                )
                if _brain:
                    brain_ctx = format_brain_for_agent(_brain)
        except Exception:
            brain_ctx = ""
        try:
            from services.github_issues_context import get_relevant_issues_context
            _db = get_db()
            if _db is not None and user_token:
                issues_ctx = await get_relevant_issues_context(
                    db=_db, repo_owner=owner, repo_name=repo,
                    github_pat=user_token, task_description=task,
                )
        except Exception:
            issues_ctx = ""
        if brain_ctx:
            await _log(task_id, "🧠 injected project memory")
        if issues_ctx:
            await _log(task_id, "📋 injected relevant GitHub issues")

        await _emit(task_id, "ORA thinking…", kind="phase_think", pct=30)
        await _log(task_id, "🧠 DeepSeek thinking…")
        files_blob = "\n\n".join(
            f"FILE: {p}\n```\n{c}\n```" for p, c in contents.items()
        )
        extra_context_block = ""
        if brain_ctx:
            extra_context_block += f"\n\n[PROJECT MEMORY]\n{brain_ctx}"
        if issues_ctx:
            extra_context_block += f"\n\n[OPEN ISSUES]\n{issues_ctx}"
        try:
            from services.skill_context_injector import build_skill_context
            sk_ctx = build_skill_context(task)
            if sk_ctx:
                extra_context_block += f"\n\n{sk_ctx}"
                await _log(task_id, "🛡️ injected Vanguard security skills")
        except Exception:
            pass
        user_msg = (
            f"TASK: {task}\n"
            f"{('CONTEXT: ' + context) if context else ''}\n\n"
            f"Tech: {proj.get('tech_stack','auto')}\n\n"
            f"{extra_context_block}\n\n{files_blob}"
        )
        reply = await _retry(
            lambda: call_llm(
                messages=[{"role": "user", "content": user_msg}],
                system=_AI_SYS, max_tokens=3500, temperature=0.0,
            ),
            what="AI codegen", task_id=task_id,
        )
        summary_m = re.search(r"SUMMARY:\s*(.+)", reply)
        summary = (summary_m.group(1).strip() if summary_m else "AI changes")[:300]
        # Iter 212m-33 — tolerant FILE-block parser.
        from services.llm_file_parser import parse_file_blocks
        edits = parse_file_blocks(reply)
        if not edits:
            await _log(task_id, "⚠️ AI returned no file edits", "warning")
            await _set_status(task_id, status="done", result=summary,
                              completed_at=time.time())
            return
        await _log(task_id, f"✏️ {len(edits)} files to update", "success")

        # 4) write
        for path, content in edits.items():
            fp = repo_path / path
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(content)
            await _log(task_id, f"💾 {path}")

        # 5) commit + push
        await _set_status(task_id, status="pushing")
        _sh(["git", "config", "user.email", "cto@auremcto.com"], repo_path)
        _sh(["git", "config", "user.name", "AUREM CTO"], repo_path)
        _sh(["git", "add", "-A"], repo_path)
        cm = _sh(["git", "commit", "-m", f"AUREM CTO: {task[:60]}"], repo_path)
        if "nothing to commit" in cm.stdout:
            await _log(task_id, "ℹ️ no diff to commit", "info")
            await _set_status(task_id, status="done", result=summary,
                              completed_at=time.time())
            return
        push = _sh(["git", "push", "origin", branch], repo_path, timeout=90)
        if push.returncode != 0:
            raise RuntimeError(f"git push failed: {_scrub(push.stderr)[:300]}")
        sha = _sh(["git", "rev-parse", "--short", "HEAD"], repo_path).stdout.strip()
        await _log(task_id, f"🚀 pushed — {sha}", "success")
        await _set_status(task_id, status="done", result=summary,
                          commit_sha=sha,
                          files_changed=list(edits.keys()),
                          edits=_frontend_subset(edits),
                          completed_at=time.time())
        db = get_db()
        # Iter 167 — post-task scan on git-path too (parity with API path).
        if db is not None:
            try:
                from services.post_task_scanner import scan_changed_files
                _scan_paths = list(edits.keys())
                _scan_issues = await asyncio.wait_for(
                    scan_changed_files(_scan_paths, edits),
                    timeout=5.0,
                )
                if _scan_issues:
                    await db.cto_tasks.update_one(
                        {"task_id": task_id},
                        {"$set": {"post_scan": {
                            "issues":        _scan_issues,
                            "scanned_at":    time.time(),
                            "files_scanned": len(_scan_paths),
                        }}},
                    )
                    for issue in _scan_issues:
                        await _log(
                            task_id,
                            f"{issue.get('icon','⚠️')} {issue['message']} "
                            f"in {issue['file']}:{issue['line']}",
                            "warn",
                        )
            except Exception as _scan_err:
                logger.debug("post_scan (git path) skipped: %r", _scan_err)
        if db is not None:
            await db.cto_projects.update_one(
                {"project_id": proj["project_id"]},
                {"$inc": {"tasks_done": 1}, "$set": {"last_task": time.time()}},
            )
            # Brain update on git path too — without this, chat memory
            # only refreshes when the API-path worker is used. API + git
            # workers MUST keep parity, otherwise toggling between them
            # silently loses commit history from the brain.
            try:
                from services.project_brain import update_brain_after_commit
                asyncio.create_task(update_brain_after_commit(
                    db=db,
                    project_id=proj.get("project_id", ""),
                    task_description=task,
                    files_changed=list(edits.keys()),
                    was_correction_applied=False,
                    issues_found=[],
                    sha=sha or "",
                ))
            except Exception:
                pass
            # Iter 165 — Brain V2 auto-update (git path parity).
            try:
                from services.project_brain import update_brain_after_task
                asyncio.create_task(update_brain_after_task(
                    db=db,
                    project_id=proj.get("project_id", ""),
                    user_id=user_id,
                    changed_files=list(edits.keys()),
                    task_id=task_id,
                    github_token=user_token or "",
                    github_owner=proj.get("github_owner", "") or "",
                    github_repo=proj.get("github_repo", "") or "",
                    branch=proj.get("branch", "main") or "main",
                ))
            except Exception as _bv2e:
                logger.warning("brain v2 update (git path) skipped: %r", _bv2e)
    except Exception as e:
        logger.exception(f"[cto-task {task_id}] failed")
        # BUG 1 fix — scrub the PAT from the public error string. The API
        # path already does this; the git path was leaking the token
        # through traceback strings into the task feed AND into Mongo.
        safe = _scrub(str(e))
        await _log(task_id, f"❌ {safe}", "error")
        await _set_status(task_id, status="failed", error=safe[:2000],
                          completed_at=time.time())
        # Sentry capture for git-path worker crashes too.
        try:
            import sentry_sdk
            with sentry_sdk.push_scope() as scope:
                scope.set_tag("kind", "cto_task_crash")
                scope.set_tag("task_id", task_id)
                scope.set_tag("path", "git")
                scope.set_tag("project_id", proj.get("project_id", ""))
                sentry_sdk.capture_exception(e)
        except Exception:
            pass
    finally:
        shutil.rmtree(ws, ignore_errors=True)
