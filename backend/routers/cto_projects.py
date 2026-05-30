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

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/cto", tags=["AUREM CTO Projects"])

WORKSPACE = Path(os.getenv("WORKSPACE_PATH", "/tmp/aurem-dev-projects"))
WORKSPACE.mkdir(parents=True, exist_ok=True)


# ── Models ───────────────────────────────────────────────────────────────
class AddProject(BaseModel):
    name: str
    github_url: str
    github_token: Optional[str] = None  # PAT; fall back to user's OAuth token
    branch: str = "main"
    tech_stack: Optional[str] = None


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


@router.post("/tasks/submit")
async def submit_task(
    body: TaskBody,
    bg: BackgroundTasks,
    authorization: str = Header(None),
) -> dict:
    me = await current_dev(authorization)
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
    "You are AUREM CTO. Modify existing code files. Be precise.\n"
    "Reply ONLY in this format:\n"
    "SUMMARY: <one line>\n"
    "FILE: <relative/path>\n"
    "```\n<complete updated file content>\n```\n"
    "FILE: <another>\n```\n...\n```\n"
    "No prose. Return full file content, not diffs."
)


async def _run_task(task_id, proj, task, files, context, user_token):
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
