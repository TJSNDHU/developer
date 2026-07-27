"""
services/project_brain.py
Persistent per-repo memory: architectural decisions, rejected approaches,
past bugs fixed, tech-stack context, and team preferences.

The compressed `summary` string is what ORA reads before each reply so it
never asks the same question twice and never re-suggests something the
team has already rejected. The full event log is kept in MongoDB for
audit / admin display but never sent to the LLM.

Reads run zero LLM calls (pure projection + string build). Writes are
incremental — only the changed bucket is rewritten, not the whole doc.

Iter 165 — Brain V2: a separate `project_brains_v2` collection that
stores a compact STRUCTURAL map (folders, stack, hot paths). V2
populates via 5 parallel GitHub REST calls on `build_brain_v2` and
auto-updates every task via `update_brain_after_task`. Format helper
`format_brain_for_agent` emits ~200-300 tokens which is injected at
the top of every orchestrator turn — so agents stop blind-exploring
on repeat questions.
"""

from __future__ import annotations
import asyncio
import logging
import os
import time
from collections import Counter
from datetime import datetime, timezone
from typing import Optional
from motor.motor_asyncio import AsyncIOMotorDatabase

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Schema helpers
# ─────────────────────────────────────────────────────────────────────────────

def _default_brain(project_id: str, repo_full_name: str) -> dict:
    return {
        "project_id": project_id,
        "repo_full_name": repo_full_name,
        # Compressed summary — this is what gets injected into ORA system prompt
        "summary": "",
        # Structured memory buckets
        "tech_stack": [],            # ["FastAPI", "MongoDB Motor", "React+Vite"]
        "decisions": [],             # [{title, reason, date}]
        "rejected": [],              # [{idea, why_rejected, date}]
        "recurring_bugs": [],        # [{description, fix_applied, count}]
        "team_preferences": [],      # ["Always use async", "No inline styles"]
        "open_issues": [],           # [{issue_number, title, status}]
        # Raw event log (never sent to LLM — only for export/audit)
        "event_log": [],
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
        "token_count_last": 0,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Read — called every time ORA needs context
# ─────────────────────────────────────────────────────────────────────────────

async def get_brain_context(
    db: AsyncIOMotorDatabase,
    project_id: str,
    repo_full_name: str,
    github_token: str | None = None,
) -> str:
    """
    Returns a compact brain summary string to inject into ORA system prompt.
    Max ~800 tokens. Returns empty string if no brain exists yet.

    If `github_token` is provided, the last 5 GitHub commit messages are
    appended (covers commits made OUTSIDE of AUREM — other contributors,
    direct CLI pushes, force-pushes). Network failures are swallowed.

    Usage in orchestrator.py:
        brain = await get_brain_context(db, project_id, repo_full_name, gh_pat)
        system_prompt += f"\\n\\n[PROJECT MEMORY]\\n{brain}" if brain else ""
    """
    brain = await db["project_brains"].find_one({"project_id": project_id})
    if not brain:
        # First time — create empty brain
        await db["project_brains"].insert_one(_default_brain(project_id, repo_full_name))
        return await _maybe_append_github_commits(db, project_id, "", github_token)

    return await _maybe_append_github_commits(
        db, project_id, _build_context_string(brain), github_token,
    )


async def _maybe_append_github_commits(
    db: AsyncIOMotorDatabase,
    project_id: str,
    existing_context: str,
    github_token: str | None,
) -> str:
    """Best-effort: fetch last 5 GitHub commits and append to context.
    All errors (no token, bad token, rate limit, timeout, missing repo)
    are silently swallowed — brain must work without commit history.
    """
    if not github_token:
        return existing_context
    try:
        proj = await db["cto_projects"].find_one({"project_id": project_id})
        if not proj:
            return existing_context
        owner = proj.get("github_owner")
        repo = proj.get("github_repo")
        if not (owner and repo):
            return existing_context
        import httpx
        async with httpx.AsyncClient(timeout=4) as client:
            resp = await client.get(
                f"https://api.github.com/repos/{owner}/{repo}/commits?per_page=5",
                headers={
                    "Authorization": f"Bearer {github_token}",
                    "Accept": "application/vnd.github.v3+json",
                },
            )
        if resp.status_code != 200:
            return existing_context
        msgs = []
        for c in resp.json()[:5]:
            first_line = c.get("commit", {}).get("message", "").split("\n")[0][:80]
            sha = (c.get("sha") or "")[:7]
            author = c.get("commit", {}).get("author", {}).get("name", "")
            if first_line:
                msgs.append(f"  • [{sha}] {first_line}"
                            + (f" — {author}" if author else ""))
        if not msgs:
            return existing_context
        commits_block = ("Recent GitHub commits on this repo "
                         "(may include work done outside AUREM):\n"
                         + "\n".join(msgs))
        return (existing_context + "\n\n" + commits_block).strip() \
            if existing_context else commits_block
    except Exception:
        return existing_context


def _build_context_string(brain: dict) -> str:
    """Builds the compact context string sent to ORA. Capped tokens."""
    parts = []

    if brain.get("tech_stack"):
        parts.append("Tech stack: " + ", ".join(brain["tech_stack"][:8]))

    # Most recent commits ORA itself shipped — without this section, ORA
    # had no idea what changed last time and kept telling users "I don't
    # know" about features it had literally written 5 minutes earlier.
    # We surface the last 6 commit events (files touched + one-line
    # description) so the next chat turn picks up where the worker left off.
    events = brain.get("event_log") or []
    commits = [e for e in events if e.get("type") == "commit"]
    if commits:
        recent_commits = commits[-6:]
        commit_lines = []
        for ev in recent_commits:
            desc = (ev.get("description") or "").strip().splitlines()[0][:120]
            files = ev.get("files") or []
            file_blob = ", ".join(f"`{f}`" for f in files[:5])
            if len(files) > 5:
                file_blob += f" (+{len(files) - 5} more)"
            line = f"  • {desc}"
            if file_blob:
                line += f"\n      files: {file_blob}"
            if ev.get("correction_applied"):
                line += "\n      (Claude reviewer corrected this)"
            commit_lines.append(line)
        parts.append("Recent commits AUREM has shipped on this repo:\n"
                     + "\n".join(commit_lines))

    if brain.get("decisions"):
        recent = brain["decisions"][-5:]
        parts.append("Past decisions:\n" + "\n".join(
            f"  • {d['title']}: {d['reason']}" for d in recent
        ))

    if brain.get("rejected"):
        recent = brain["rejected"][-4:]
        parts.append("Already rejected (do NOT suggest):\n" + "\n".join(
            f"  • {r['idea']}: {r['why_rejected']}" for r in recent
        ))

    if brain.get("team_preferences"):
        parts.append("Team preferences: " + " | ".join(brain["team_preferences"][:6]))

    if brain.get("recurring_bugs"):
        top = sorted(brain["recurring_bugs"], key=lambda x: x.get("count", 0), reverse=True)[:3]
        parts.append("Known recurring issues:\n" + "\n".join(
            f"  • {b['description']} (seen {b['count']}x, fix: {b['fix_applied']})"
            for b in top
        ))

    return "\n\n".join(parts) if parts else ""


# ─────────────────────────────────────────────────────────────────────────────
# Write — called after commits and key conversations
# ─────────────────────────────────────────────────────────────────────────────

async def update_brain_after_commit(
    db: AsyncIOMotorDatabase,
    project_id: str,
    task_description: str,
    files_changed: list[str],
    was_correction_applied: bool,
    issues_found: list[str],
    sha: str = "",
):
    """
    Called after every successful gh_api_commit().
    Updates brain with what was done and any recurring patterns.

    Iter 328 · #3-a — fail-open silent-failure logging. Every write
    into `project_brains` is now wrapped so a broken write path is
    instrumented (visible at WARNING) instead of silent — matches the
    dead-write-path diagnosis in the master queue. When callsites are
    reattached in step (b), the logs will confirm each write succeeded
    or reveal the exact failure.
    """
    import logging as _log
    _l = _log.getLogger(__name__)
    try:
        now = datetime.now(timezone.utc)
        event = {
            "type": "commit",
            "description": task_description,
            "files": files_changed[:10],      # cap at 10 files
            "correction_applied": was_correction_applied,
            "issues": issues_found[:5],
            "sha": (sha or "")[:40],
            "ts": now,
        }

        update_ops: dict = {
            "$set": {"updated_at": now},
            "$push": {
                "event_log": {
                    "$each": [event],
                    "$slice": -200,           # keep last 200 events only
                }
            }
        }

        # If correction was applied, track as potential recurring bug
        if was_correction_applied and issues_found:
            for issue in issues_found[:2]:
                # Increment count if already exists
                existing = await db["project_brains"].find_one({
                    "project_id": project_id,
                    "recurring_bugs.description": issue,
                })
                if existing:
                    await db["project_brains"].update_one(
                        {"project_id": project_id, "recurring_bugs.description": issue},
                        {"$inc": {"recurring_bugs.$.count": 1}}
                    )
                else:
                    update_ops.setdefault("$push", {})
                    update_ops["$push"]["recurring_bugs"] = {
                        "$each": [{"description": issue, "fix_applied": "auto-corrected by Claude", "count": 1}],
                        "$slice": -20,
                    }

        await db["project_brains"].update_one(
            {"project_id": project_id},
            update_ops,
            upsert=True,
        )
        _l.info(
            "🧠 update_brain_after_commit · project=%s sha=%s "
            "files=%d correction=%s",
            project_id, (sha or "")[:7], len(files_changed),
            was_correction_applied,
        )
    except Exception as e:                                  # noqa: BLE001
        _l.warning(
            "🧠 update_brain_after_commit FAILED (fail-open) · "
            "project=%s err=%r", project_id, e,
        )
        # Never re-raise — must not block a user task.
        return


async def update_brain_from_conversation(
    db: AsyncIOMotorDatabase,
    project_id: str,
    user_message: str,
    ora_reply: str,
    mode: str,
):
    """
    Called after Mode B (advice) conversations.
    Extracts decisions and rejected ideas from the exchange.
    Lightweight — no LLM call, just pattern matching.

    Usage in chat.py SSE handler after streaming completes:
        await update_brain_from_conversation(db, project_id, msg, reply, "B")
    """
    now = datetime.now(timezone.utc)
    lower_msg = user_message.lower()
    lower_reply = ora_reply.lower()

    push_ops = {}

    # Detect tech stack mentions
    stack_keywords = [
        "fastapi", "flask", "django", "react", "vue", "next.js", "mongodb",
        "postgres", "redis", "celery", "docker", "kubernetes", "stripe",
        "typescript", "python", "node", "graphql", "rest", "grpc",
    ]
    found_stack = [k for k in stack_keywords if k in lower_msg or k in lower_reply]
    if found_stack:
        await db["project_brains"].update_one(
            {"project_id": project_id},
            {"$addToSet": {"tech_stack": {"$each": found_stack[:4]}}},
            upsert=True,
        )

    # Detect rejection signals ("don't use", "we decided against", "avoid")
    rejection_signals = ["don't use", "avoid", "we decided against", "not using", "we rejected"]
    if any(s in lower_msg for s in rejection_signals):
        push_ops["rejected"] = {
            "$each": [{"idea": user_message[:120], "why_rejected": "user explicitly rejected", "date": str(now.date())}],
            "$slice": -30,
        }

    # Detect decision signals ("we'll use", "let's go with", "decided to")
    decision_signals = ["we'll use", "let's go with", "decided to", "going with", "we chose"]
    if any(s in lower_msg for s in decision_signals):
        push_ops["decisions"] = {
            "$each": [{"title": user_message[:100], "reason": "from conversation", "date": str(now.date())}],
            "$slice": -30,
        }

    if push_ops:
        await db["project_brains"].update_one(
            {"project_id": project_id},
            {"$set": {"updated_at": now}, "$push": push_ops},
            upsert=True,
        )


async def add_decision(
    db: AsyncIOMotorDatabase,
    project_id: str,
    title: str,
    reason: str,
):
    """Manual decision add — call from admin panel or future UI."""
    await db["project_brains"].update_one(
        {"project_id": project_id},
        {
            "$push": {
                "decisions": {
                    "$each": [{"title": title, "reason": reason, "date": str(datetime.now(timezone.utc).date())}],
                    "$slice": -50,
                }
            },
            "$set": {"updated_at": datetime.now(timezone.utc)},
        },
        upsert=True,
    )


async def add_preference(
    db: AsyncIOMotorDatabase,
    project_id: str,
    preference: str,
):
    """Add a team coding preference. Shown to ORA every time."""
    await db["project_brains"].update_one(
        {"project_id": project_id},
        {"$addToSet": {"team_preferences": preference[:120]}},
        upsert=True,
    )


async def get_brain_full(
    db: AsyncIOMotorDatabase,
    project_id: str,
) -> Optional[dict]:
    """Returns full brain doc — for admin panel display."""
    brain = await db["project_brains"].find_one(
        {"project_id": project_id},
        {"event_log": 0},   # exclude raw log from admin view (too large)
    )
    if brain:
        brain["_id"] = str(brain["_id"])
    return brain


async def delete_decision(
    db: AsyncIOMotorDatabase,
    project_id: str,
    title: str,
) -> int:
    """Remove a decision by its title. Returns number of decisions removed."""
    r = await db["project_brains"].update_one(
        {"project_id": project_id},
        {
            "$pull": {"decisions": {"title": title}},
            "$set": {"updated_at": datetime.now(timezone.utc)},
        },
    )
    return r.modified_count


async def delete_preference(
    db: AsyncIOMotorDatabase,
    project_id: str,
    preference: str,
) -> int:
    """Remove a team preference string. Returns modified_count."""
    r = await db["project_brains"].update_one(
        {"project_id": project_id},
        {
            "$pull": {"team_preferences": preference},
            "$set": {"updated_at": datetime.now(timezone.utc)},
        },
    )
    return r.modified_count


# ═══════════════════════════════════════════════════════════════════════════════
# Brain V2 — Iter 165
#
# A structural map of the repo (folders, stack, entry points, hot paths) that
# lives in a separate `project_brains_v2` collection. Populated via direct
# GitHub REST calls (no LLM, no JWT — just a PAT). Refreshed on first
# connect + every N tasks. Injected as a compact string into every
# orchestrator turn so agents stop blind-exploring.
# ═══════════════════════════════════════════════════════════════════════════════

BRAIN_VERSION = 2
FULL_REFRESH_EVERY_N_TASKS = int(
    os.getenv("BRAIN_V2_FULL_REFRESH_EVERY_N_TASKS", "10")
)


# ── GitHub REST helpers (no JWT, just PAT) ───────────────────────────────────

async def _gh_list_files(
    token: str, owner: str, repo: str, path: str = "", branch: str = "main",
) -> list[str]:
    """List entries (names) at `path` in `owner/repo` via the contents API.
    Returns [] on any failure — Brain V2 must NEVER raise."""
    if not (token and owner and repo):
        return []
    import httpx
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path.lstrip('/')}"
    try:
        async with httpx.AsyncClient(timeout=8.0) as cx:
            r = await cx.get(
                url,
                params={"ref": branch} if branch else None,
                headers={
                    "Authorization": f"token {token}",
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "aurem-brain-v2",
                },
            )
            if r.status_code != 200:
                return []
            data = r.json()
            if not isinstance(data, list):
                return []
            return [
                (e.get("name") or "")
                for e in data
                if isinstance(e, dict) and e.get("name")
            ][:60]
    except Exception:
        return []


async def _gh_read_small(
    token: str, owner: str, repo: str, path: str, branch: str = "main",
    max_chars: int = 2000,
) -> str:
    """Fetch raw file content up to `max_chars`. Returns "" on any failure."""
    if not (token and owner and repo and path):
        return ""
    import base64
    import httpx
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path.lstrip('/')}"
    try:
        async with httpx.AsyncClient(timeout=8.0) as cx:
            r = await cx.get(
                url,
                params={"ref": branch} if branch else None,
                headers={
                    "Authorization": f"token {token}",
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "aurem-brain-v2",
                },
            )
            if r.status_code != 200:
                return ""
            data = r.json()
            content_b64 = (data or {}).get("content") or ""
            if not content_b64:
                return ""
            raw = base64.b64decode(content_b64).decode("utf-8", errors="ignore")
            return raw[:max_chars]
    except Exception:
        return ""


# ── Core V2 functions ────────────────────────────────────────────────────────

async def get_brain_v2(
    db: Optional[AsyncIOMotorDatabase],
    project_id: str,
    user_id: str,
) -> dict:
    """Return the V2 brain doc for this project, or `{}` if not built yet."""
    if db is None or not project_id or not user_id:
        return {}
    try:
        doc = await db["project_brains_v2"].find_one(
            {"project_id": project_id, "user_id": user_id},
            {"_id": 0},
        )
    except Exception:
        return {}
    return doc or {}


async def build_brain_v2(
    db: AsyncIOMotorDatabase,
    project_id: str,
    user_id: str,
    github_token: str,
    github_owner: str,
    github_repo: str,
    branch: str = "main",
) -> dict:
    """Full structural scan — 5 parallel `_gh_list_files` calls + 2 small
    `_gh_read_small` reads for stack detection. Persists to
    `project_brains_v2` and returns the saved doc.

    Cheap: only directory listings + 2 small file reads — no LLM, no full
    repo walk."""
    if db is None or not (github_token and github_owner and github_repo):
        return {}

    # 5 parallel directory listings (all cheap)
    listings = await asyncio.gather(
        _gh_list_files(github_token, github_owner, github_repo, "", branch),
        _gh_list_files(github_token, github_owner, github_repo, "backend", branch),
        _gh_list_files(github_token, github_owner, github_repo, "frontend/src", branch),
        _gh_list_files(github_token, github_owner, github_repo, "backend/routers", branch),
        _gh_list_files(github_token, github_owner, github_repo, "backend/services", branch),
        return_exceptions=True,
    )

    def _safe(r):
        return r if isinstance(r, list) else []

    root_files     = _safe(listings[0])
    backend_files  = _safe(listings[1])
    frontend_files = _safe(listings[2])
    routers_files  = _safe(listings[3])
    services_files = _safe(listings[4])

    def _has(files: list[str], keyword: str) -> bool:
        kl = keyword.lower()
        return any(kl in (f or "").lower() for f in files)

    # ── structure
    structure: dict = {}
    if _has(backend_files, "main.py"):
        structure["backend_root"] = "backend/"
    if _has(frontend_files, "App.jsx") or _has(frontend_files, "App.tsx"):
        structure["frontend_root"] = "frontend/src/"
    if _has(frontend_files, "components"):
        structure["components"] = "frontend/src/components/"
    if _has(frontend_files, "pages"):
        structure["pages"] = "frontend/src/pages/"
    if _has(frontend_files, "hooks"):
        structure["hooks"] = "frontend/src/hooks/"
    if routers_files:
        structure["routers"] = "backend/routers/"
    if services_files:
        structure["services"] = "backend/services/"
    if _has(backend_files, "tests"):
        structure["tests"] = "backend/tests/"

    # ── stack (2 small reads, parallel)
    stack: dict = {}
    pkg_path = "package.json" if _has(root_files, "package.json") else (
        "frontend/package.json"
        if _has(_safe(await _gh_list_files(github_token, github_owner, github_repo, "frontend", branch)), "package.json")
        else ""
    )
    req_path = ""
    if _has(root_files, "requirements.txt"):
        req_path = "requirements.txt"
    elif _has(backend_files, "requirements.txt"):
        req_path = "backend/requirements.txt"

    pkg_text, req_text = await asyncio.gather(
        _gh_read_small(github_token, github_owner, github_repo, pkg_path, branch) if pkg_path else _noop(),
        _gh_read_small(github_token, github_owner, github_repo, req_path, branch) if req_path else _noop(),
    )

    pkg_l = (pkg_text or "").lower()
    req_l = (req_text or "").lower()
    languages: list[str] = []
    if "react" in pkg_l:
        stack["frontend"] = "React"
        languages.append("JavaScript")
    if "vite" in pkg_l and stack.get("frontend"):
        stack["frontend"] = f"{stack['frontend']} + Vite"
    if "next" in pkg_l:
        stack["frontend"] = "Next.js"
        if "JavaScript" not in languages:
            languages.append("JavaScript")
    if "typescript" in pkg_l:
        languages.append("TypeScript")
    if "fastapi" in req_l:
        stack["backend"] = "FastAPI"
        languages.append("Python")
    elif "django" in req_l:
        stack["backend"] = "Django"
        languages.append("Python")
    elif "flask" in req_l:
        stack["backend"] = "Flask"
        languages.append("Python")
    if _has(root_files, "go.mod"):
        stack["backend"] = "Go"
        languages.append("Go")
    if _has(root_files, "Cargo.toml"):
        stack["backend"] = "Rust"
        languages.append("Rust")
    if "motor" in req_l or "pymongo" in req_l:
        stack["db"] = "MongoDB"
    elif "psycopg" in req_l or "asyncpg" in req_l:
        stack["db"] = "PostgreSQL"
    if "jwt" in req_l or "pyjwt" in req_l:
        stack["auth"] = "JWT"
    if languages:
        stack["languages"] = sorted(set(languages))

    # ── entry points
    entry_points: dict = {}
    if _has(backend_files, "main.py"):
        entry_points["backend_main"] = "backend/main.py"
    elif _has(root_files, "main.py"):
        entry_points["backend_main"] = "main.py"
    if _has(frontend_files, "App.jsx"):
        entry_points["frontend_main"] = "frontend/src/App.jsx"
    elif _has(frontend_files, "App.tsx"):
        entry_points["frontend_main"] = "frontend/src/App.tsx"
    if _has(backend_files, ".env"):
        entry_points["env_file"] = "backend/.env"

    # ── sensitive paths
    sensitive: list[str] = []
    for f in backend_files:
        fl = (f or "").lower()
        if (".env" in fl) or ("secret" in fl) or ("vault" in fl):
            sensitive.append(f"backend/{f}")

    # ── hot paths — seed with conventional CTO-app candidates that
    # actually exist in this repo. update_brain_after_task() refines
    # this from real commit frequency over time.
    seed_candidates = [
        "backend/routers/chat.py",
        "frontend/src/components/ChatPanel.jsx",
        "backend/services/orchestrator.py",
        "backend/main.py",
        "frontend/src/App.jsx",
    ]
    hot_paths: list[str] = []
    for cand in seed_candidates:
        parts = cand.split("/")
        if parts[0] == "backend" and len(parts) >= 3 and parts[1] in ("routers", "services"):
            target = routers_files if parts[1] == "routers" else services_files
            if parts[2] in target:
                hot_paths.append(cand)
        elif cand == "backend/main.py" and _has(backend_files, "main.py"):
            hot_paths.append(cand)
        elif cand == "frontend/src/App.jsx" and _has(frontend_files, "App.jsx"):
            hot_paths.append(cand)
        elif cand.startswith("frontend/src/components/") and _has(frontend_files, "components"):
            # Could exist — keep as a hint; updates will prune if wrong
            hot_paths.append(cand)

    now = time.time()
    brain = {
        "project_id": project_id,
        "user_id":    user_id,
        "version":    BRAIN_VERSION,
        "task_count": 0,
        "last_scan":  now,
        "next_full_refresh_at": FULL_REFRESH_EVERY_N_TASKS,
        "structure":    structure,
        "stack":        stack,
        "entry_points": entry_points,
        "patterns": {
            "api_style":        "REST + SSE streaming" if structure.get("routers") else "",
            "component_style":  "functional + hooks" if structure.get("components") else "",
            "naming_backend":   "snake_case" if structure.get("backend_root") else "",
            "naming_frontend":  "camelCase + PascalCase components" if structure.get("frontend_root") else "",
        },
        "sensitive_paths": sensitive,
        "hot_paths":      hot_paths,
        "recent_changes": [],
        "branch":         branch,
    }

    # Preserve existing task_count if the doc already exists (refresh path).
    existing = await get_brain_v2(db, project_id, user_id)
    if existing:
        brain["task_count"] = existing.get("task_count", 0)
        brain["next_full_refresh_at"] = (
            brain["task_count"] + FULL_REFRESH_EVERY_N_TASKS
        )
        # Carry over learned hot_paths if more accurate than seed
        learned_hot = existing.get("hot_paths") or []
        if learned_hot:
            brain["hot_paths"] = learned_hot
        brain["recent_changes"] = (existing.get("recent_changes") or [])[:10]

    try:
        await db["project_brains_v2"].update_one(
            {"project_id": project_id, "user_id": user_id},
            {"$set": brain},
            upsert=True,
        )
    except Exception as e:
        logger.warning("brain_v2 save failed for %s: %r", project_id, e)
    return brain


async def _noop() -> str:
    """Placeholder coroutine for asyncio.gather when a path is empty."""
    return ""


async def update_brain_after_task(
    db: Optional[AsyncIOMotorDatabase],
    project_id: str,
    user_id: str,
    changed_files: list[str],
    task_id: str,
    github_token: str = "",
    github_owner: str = "",
    github_repo:  str = "",
    branch: str = "main",
) -> dict:
    """Called fire-and-forget after every completed task:
      1. increment task_count
      2. push to recent_changes (cap at 10)
      3. recompute hot_paths from frequency
      4. if task_count >= next_full_refresh_at → call build_brain_v2()

    Never raises — brain failure must never bubble into the task path."""
    if db is None or not project_id or not user_id:
        return {}

    brain = await get_brain_v2(db, project_id, user_id)
    if not brain:
        # First task ever — build fresh if we have GitHub creds
        if github_token and github_owner and github_repo:
            return await build_brain_v2(
                db, project_id, user_id,
                github_token, github_owner, github_repo, branch,
            )
        return {}

    now = time.time()
    new_task_count = int(brain.get("task_count", 0)) + 1
    next_refresh   = int(brain.get("next_full_refresh_at", FULL_REFRESH_EVERY_N_TASKS))

    recent = list(brain.get("recent_changes") or [])
    for f in (changed_files or []):
        if not f:
            continue
        recent.insert(0, {"file": f, "task": task_id, "ts": now})
    recent = recent[:10]

    # Recompute hot_paths from recent_changes frequency
    freq = Counter(e.get("file") for e in recent if e.get("file"))
    if freq:
        hot = [path for path, _ in freq.most_common(5)]
        brain["hot_paths"] = hot

    # Full refresh trigger
    if new_task_count >= next_refresh and github_token and github_owner and github_repo:
        refreshed = await build_brain_v2(
            db, project_id, user_id,
            github_token, github_owner, github_repo, branch,
        )
        # build_brain_v2 carries over task_count; bump it again so the
        # refresh task itself is counted.
        try:
            await db["project_brains_v2"].update_one(
                {"project_id": project_id, "user_id": user_id},
                {"$set": {
                    "task_count":            new_task_count,
                    "next_full_refresh_at":  new_task_count + FULL_REFRESH_EVERY_N_TASKS,
                    "recent_changes":        recent,
                }},
            )
            refreshed["task_count"] = new_task_count
            refreshed["next_full_refresh_at"] = new_task_count + FULL_REFRESH_EVERY_N_TASKS
            refreshed["recent_changes"] = recent
        except Exception as e:
            logger.warning("brain_v2 refresh-count bump failed: %r", e)
        return refreshed

    # Incremental update
    try:
        await db["project_brains_v2"].update_one(
            {"project_id": project_id, "user_id": user_id},
            {"$set": {
                "task_count":     new_task_count,
                "recent_changes": recent,
                "hot_paths":      brain.get("hot_paths", []),
                "last_task_at":   now,
            }},
        )
    except Exception as e:
        logger.warning("brain_v2 incremental update failed: %r", e)
    brain["task_count"]     = new_task_count
    brain["recent_changes"] = recent
    return brain


def format_brain_for_agent(brain: dict) -> str:
    """Render the brain as a compact ~200-300 token string for system-prompt
    injection. Returns "" if the brain is empty."""
    if not brain:
        return ""
    lines: list[str] = ["[PROJECT BRAIN V2]"]

    s = brain.get("structure") or {}
    if s:
        lines.append("Structure: " + " | ".join(f"{k}={v}" for k, v in s.items()))

    st = brain.get("stack") or {}
    if st:
        bits = []
        for k, v in st.items():
            if isinstance(v, list):
                v = ",".join(v[:4])
            bits.append(f"{k}={v}")
        lines.append("Stack: " + " | ".join(bits))

    ep = brain.get("entry_points") or {}
    if ep:
        lines.append("Entry points: " + " | ".join(str(v) for v in ep.values() if v))

    hot = brain.get("hot_paths") or []
    if hot:
        lines.append("Hot files: " + ", ".join(hot[:3]))

    recent = brain.get("recent_changes") or []
    if recent:
        last = recent[0]
        if last.get("file"):
            lines.append("Last changed: " + str(last["file"]))

    sensitive = brain.get("sensitive_paths") or []
    if sensitive:
        lines.append("Sensitive: " + ", ".join(sensitive[:2]))

    tc = brain.get("task_count", 0)
    lines.append(f"Tasks done: {tc}")
    return "\n".join(lines)


