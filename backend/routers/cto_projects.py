"""
routers/cto_projects.py — AUREM CTO multi-project system.
Connect existing client GitHub repos, run AI tasks (git pull → fix → push).
Mounted under /api/aurem-dev/cto/* to avoid clashing with /projects/* (new-project flow).
"""
from __future__ import annotations
import asyncio
import logging
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException
from pydantic import BaseModel

from cto_services.auth import current_dev
from cto_services.db import get_db, require_db
from services.llm import call_llm
from services.usage import assert_has_budget, get_usage
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


# ── Endpoints ────────────────────────────────────────────────────────────
@router.post("/projects/add")
async def add_project(body: AddProject, authorization: str = Header(None)) -> dict:
    me = await current_dev(authorization)
    db = require_db()
    owner, repo = _parse_repo(body.github_url)
    proj_id = f"p_{uuid.uuid4().hex[:10]}"
    doc = {
        "project_id": proj_id, "user_id": me["user_id"],
        "name": body.name, "github_url": body.github_url,
        "github_owner": owner, "github_repo": repo,
        "github_token": body.github_token,
        "branch": body.branch, "tech_stack": body.tech_stack or "auto",
        "preview_url": (body.preview_url or "").strip() or None,
        "status": "connected", "tasks_done": 0,
        "created_at": time.time(),
    }
    await db.cto_projects.insert_one(doc)
    return {"ok": True, "project_id": proj_id, "owner": owner, "repo": repo}


@router.get("/projects/list")
async def list_projects(authorization: str = Header(None)) -> dict:
    me = await current_dev(authorization)
    db = require_db()
    projs = await db.cto_projects.find(
        {"user_id": me["user_id"]},
        {"_id": 0, "github_token": 0},
    ).sort("created_at", -1).to_list(50)
    return {"ok": True, "projects": projs}


@router.delete("/projects/{project_id}")
async def remove_project(project_id: str, authorization: str = Header(None)) -> dict:
    me = await current_dev(authorization)
    db = require_db()
    r = await db.cto_projects.delete_one({"project_id": project_id, "user_id": me["user_id"]})
    return {"ok": True, "deleted": r.deleted_count}


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


@router.post("/tasks/submit")
async def submit_task(
    body: TaskBody,
    bg: BackgroundTasks,
    authorization: str = Header(None),
) -> dict:
    me = await current_dev(authorization)
    # THING 1 — hard-stop token enforcement. Raises HTTP 402 if the user has
    # spent their plan_limit + any admin-granted bonus. The AI is NEVER
    # called and no row is written to `cto_tasks`.
    await assert_has_budget(me["user_id"])
    db = require_db()
    proj = await db.cto_projects.find_one(
        {"project_id": body.project_id, "user_id": me["user_id"]}
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
        "created_at": time.time(),
    })
    user_token = proj.get("github_token") or await _user_gh_token(me["user_id"])
    bg.add_task(_run_task, task_id, proj, body.task, body.files, body.context, user_token)
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
        {"project_id": t["project_id"], "user_id": me["user_id"]}
    )
    if not proj:
        raise HTTPException(404, "Parent project not found")

    user_token = proj.get("github_token") or await _user_gh_token(me["user_id"])
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


@router.get("/tasks/project/{project_id}")
async def project_tasks(project_id: str, authorization: str = Header(None)) -> dict:
    me = await current_dev(authorization)
    db = require_db()
    tasks = await db.cto_tasks.find(
        {"project_id": project_id, "user_id": me["user_id"]}, {"_id": 0}
    ).sort("created_at", -1).limit(20).to_list(20)
    return {"ok": True, "tasks": tasks}


# ── Background worker ────────────────────────────────────────────────────
async def _log(task_id: str, step: str, status: str = "info"):
    db = get_db()
    if db is None:
        return
    await db.cto_tasks.update_one(
        {"task_id": task_id},
        {"$push": {"steps": {"step": step, "status": status, "ts": time.time()}}},
    )


async def _set_status(task_id: str, **fields):
    db = get_db()
    if db is not None:
        await db.cto_tasks.update_one({"task_id": task_id}, {"$set": fields})


def _sh(cmd: list, cwd: Path, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout)


_AI_SYS = (
    "You are AUREM CTO. You can create new files AND modify existing ones "
    "to complete the user's task. Be precise.\n"
    "Reply ONLY in this format:\n"
    "SUMMARY: <one line describing what changed>\n"
    "FILE: <relative/path/from/repo/root>\n"
    "```\n<complete final file content>\n```\n"
    "FILE: <another/path>\n```\n...\n```\n\n"
    "Rules:\n"
    "  - To CREATE a new file: emit a FILE block with a path that doesn't "
    "yet exist — parent directories are auto-created.\n"
    "  - To EDIT a file: emit its FILE block with the COMPLETE final "
    "contents (not a diff, not a patch).\n"
    "  - To DELETE a file: skip it (rollback is available, deletes need a "
    "separate workflow).\n"
    "  - No prose anywhere outside the SUMMARY line and FILE blocks."
)


async def _run_task(task_id, proj, task, files, context, user_token):
    """Dispatcher — uses git-subprocess path when git is installed, falls
    back to the pure GitHub-API path when it isn't (Iter 21)."""
    if _GIT_AVAILABLE:
        return await _run_task_with_git(
            task_id, proj, task, files, context, user_token
        )
    return await _run_task_via_api(
        task_id, proj, task, files, context, user_token
    )


async def _run_task_via_api(task_id, proj, task, files, context, user_token):
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

    try:
        await _set_status(task_id, status="pulling", started_at=time.time())
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

        # 2) AI codegen (same prompt format as the git path)
        await _set_status(task_id, status="fixing")
        await _log(task_id, "🧠 DeepSeek thinking…")
        files_blob = "\n\n".join(
            f"FILE: {p}\n```\n{c}\n```" for p, c in contents.items()
        )
        user_msg = (
            f"TASK: {task}\n"
            f"{('CONTEXT: ' + context) if context else ''}\n\n"
            f"Tech: {proj.get('tech_stack','auto')}\n\n{files_blob}"
        )
        reply = await call_llm(
            messages=[{"role": "user", "content": user_msg}],
            system=_AI_SYS, max_tokens=3500, temperature=0.0,
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
        edits: dict[str, str] = {}
        for m in re.finditer(r"FILE:\s*(\S+)\s*\n```[^\n]*\n(.*?)```", reply, re.DOTALL):
            edits[m.group(1).strip()] = m.group(2)
        if not edits:
            await _log(task_id, "⚠️ AI returned no file edits", "warning")
            await _set_status(task_id, status="done", result=summary,
                              completed_at=time.time())
            return
        await _log(task_id, f"✏️ {len(edits)} files to update", "success")

        # 3) Commit + push as one atomic API call
        await _set_status(task_id, status="pushing")

        async def _prog(step: str, status: str = "info"):
            await _log(task_id, step, status)

        result = await gh_api_commit(
            owner=owner, repo=repo, branch=branch, token=user_token,
            files=edits,
            commit_message=f"AUREM CTO: {task[:60]}",
            progress=_prog,
        )
        sha = result["sha"]
        await _set_status(task_id, status="done", result=summary,
                          commit_sha=sha, completed_at=time.time())
        db = get_db()
        if db is not None:
            await db.cto_projects.update_one(
                {"project_id": proj["project_id"]},
                {"$inc": {"tasks_done": 1}, "$set": {"last_task": time.time()}},
            )
    except Exception as e:
        logger.exception(f"[cto-task-api {task_id}] failed")
        safe = str(e).replace(user_token or "", "***PAT***")
        await _log(task_id, f"❌ {safe}", "error")
        await _set_status(task_id, status="failed", error=safe,
                          completed_at=time.time())


async def _run_task_with_git(task_id, proj, task, files, context, user_token):
    import re
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
            raise RuntimeError(f"git clone failed: {r.stderr[:300]}")
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
        await _log(task_id, "🧠 DeepSeek thinking…")
        files_blob = "\n\n".join(
            f"FILE: {p}\n```\n{c}\n```" for p, c in contents.items()
        )
        user_msg = (
            f"TASK: {task}\n"
            f"{('CONTEXT: ' + context) if context else ''}\n\n"
            f"Tech: {proj.get('tech_stack','auto')}\n\n{files_blob}"
        )
        reply = await call_llm(
            messages=[{"role": "user", "content": user_msg}],
            system=_AI_SYS, max_tokens=3500, temperature=0.0,
        )
        summary_m = re.search(r"SUMMARY:\s*(.+)", reply)
        summary = (summary_m.group(1).strip() if summary_m else "AI changes")[:300]
        edits = {}
        for m in re.finditer(r"FILE:\s*(\S+)\s*\n```[^\n]*\n(.*?)```", reply, re.DOTALL):
            edits[m.group(1).strip()] = m.group(2)
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
            raise RuntimeError(f"git push failed: {push.stderr[:300]}")
        sha = _sh(["git", "rev-parse", "--short", "HEAD"], repo_path).stdout.strip()
        await _log(task_id, f"🚀 pushed — {sha}", "success")
        await _set_status(task_id, status="done", result=summary,
                          commit_sha=sha, completed_at=time.time())
        db = get_db()
        if db is not None:
            await db.cto_projects.update_one(
                {"project_id": proj["project_id"]},
                {"$inc": {"tasks_done": 1}, "$set": {"last_task": time.time()}},
            )
    except Exception as e:
        logger.exception(f"[cto-task {task_id}] failed")
        await _log(task_id, f"❌ {e}", "error")
        await _set_status(task_id, status="failed", error=str(e),
                          completed_at=time.time())
    finally:
        shutil.rmtree(ws, ignore_errors=True)
