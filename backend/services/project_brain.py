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
"""

from __future__ import annotations
import asyncio
from datetime import datetime, timezone
from typing import Optional
from motor.motor_asyncio import AsyncIOMotorDatabase


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
):
    """
    Called after every successful gh_api_commit().
    Updates brain with what was done and any recurring patterns.

    Usage in cto_projects.py after gh_api_commit():
        await update_brain_after_commit(
            db=db,
            project_id=str(task.project_id),
            task_description=task.description,
            files_changed=list(final_code.keys()),
            was_correction_applied=not review["pass"],
            issues_found=review.get("issues", []),
        )
    """
    now = datetime.now(timezone.utc)
    event = {
        "type": "commit",
        "description": task_description,
        "files": files_changed[:10],      # cap at 10 files
        "correction_applied": was_correction_applied,
        "issues": issues_found[:5],
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

