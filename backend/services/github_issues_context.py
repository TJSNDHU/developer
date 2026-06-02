"""
services/github_issues_context.py
====================================
AUREM GitHub Issues Context — ORA reads tickets before every code task.

When user says "fix the auth bug" — ORA already knows which issue it is,
what the error message was, who reported it, what branch it's on.

No copy-paste from user needed. ORA reads the repo's open issues,
finds the most relevant ones for the current task, and injects them
into the system context.

TOKEN OPTIMIZATION:
  • Only fetches open issues (not closed — saves API calls)
  • Injects at most 3 most-relevant issues per task (~300 tokens)
  • Relevance = simple keyword overlap between task description + issue title/body
  • Caches issues in MongoDB for 15 minutes to avoid repeated GitHub API calls
  • Uses same GitHub PAT that github_api_writer.py already uses

Wire-in:
  orchestrator.py OR cto_projects.py — call get_relevant_issues_context()
  and append to system prompt before DeepSeek call.
"""

from __future__ import annotations
import re
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx
from motor.motor_asyncio import AsyncIOMotorDatabase


CACHE_TTL_MINUTES = 15
GITHUB_API = "https://api.github.com"


# ─────────────────────────────────────────────────────────────────────────────
# Fetch + cache
# ─────────────────────────────────────────────────────────────────────────────

async def _fetch_open_issues(
    repo_owner: str,
    repo_name: str,
    github_pat: str,
    max_issues: int = 30,
) -> list[dict]:
    """
    Fetches open issues from GitHub API.
    Returns simplified list: [{number, title, body_short, labels, url}]
    """
    url = f"{GITHUB_API}/repos/{repo_owner}/{repo_name}/issues"
    headers = {
        "Authorization": f"Bearer {github_pat}",
        "Accept": "application/vnd.github.v3+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    params = {"state": "open", "per_page": max_issues, "sort": "updated"}

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(url, headers=headers, params=params)
        resp.raise_for_status()
        raw = resp.json()

    issues = []
    for issue in raw:
        # Skip pull requests (GitHub API returns PRs in issues endpoint)
        if "pull_request" in issue:
            continue
        issues.append({
            "number": issue["number"],
            "title": issue["title"],
            "body_short": (issue.get("body") or "")[:300].strip(),
            "labels": [l["name"] for l in issue.get("labels", [])],
            "url": issue["html_url"],
            "created_at": issue["created_at"],
        })

    return issues


async def get_issues_cached(
    db: AsyncIOMotorDatabase,
    repo_owner: str,
    repo_name: str,
    github_pat: str,
) -> list[dict]:
    """
    Returns issues from cache if fresh, otherwise fetches from GitHub.
    Cache TTL: 15 minutes.
    """
    cache_key = f"{repo_owner}/{repo_name}"
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=CACHE_TTL_MINUTES)

    cached = await db["issues_cache"].find_one({"repo": cache_key, "fetched_at": {"$gt": cutoff}})
    if cached:
        return cached["issues"]

    try:
        issues = await _fetch_open_issues(repo_owner, repo_name, github_pat)
    except Exception as e:
        print(f"[Issues Context] Failed to fetch issues: {e}")
        return []

    await db["issues_cache"].update_one(
        {"repo": cache_key},
        {"$set": {"issues": issues, "fetched_at": datetime.now(timezone.utc)}},
        upsert=True,
    )
    return issues


# ─────────────────────────────────────────────────────────────────────────────
# Relevance matching
# ─────────────────────────────────────────────────────────────────────────────

def _relevance_score(task_description: str, issue: dict) -> float:
    """
    Simple keyword overlap score. No LLM needed — fast and free.
    Returns 0.0–1.0.
    """
    task_words = set(re.findall(r'\b\w{3,}\b', task_description.lower()))
    issue_text = f"{issue['title']} {issue['body_short']} {' '.join(issue['labels'])}".lower()
    issue_words = set(re.findall(r'\b\w{3,}\b', issue_text))

    if not task_words or not issue_words:
        return 0.0

    overlap = task_words & issue_words
    return len(overlap) / max(len(task_words), 10)


def find_relevant_issues(task_description: str, issues: list[dict], top_n: int = 3) -> list[dict]:
    """Returns top N most relevant issues for the task."""
    scored = [(issue, _relevance_score(task_description, issue)) for issue in issues]
    scored.sort(key=lambda x: x[1], reverse=True)
    # Only return if score > 0 (has some relevance)
    return [issue for issue, score in scored[:top_n] if score > 0]


# ─────────────────────────────────────────────────────────────────────────────
# Context string builder
# ─────────────────────────────────────────────────────────────────────────────

def build_issues_context(relevant_issues: list[dict]) -> str:
    """
    Builds a compact context string to inject into ORA system prompt.
    ~100-300 tokens depending on issue detail.
    """
    if not relevant_issues:
        return ""

    lines = ["Open GitHub issues related to this task:"]
    for issue in relevant_issues:
        label_str = f" [{', '.join(issue['labels'])}]" if issue["labels"] else ""
        lines.append(f"  #{issue['number']}{label_str}: {issue['title']}")
        if issue["body_short"]:
            body_preview = issue["body_short"][:150].replace("\n", " ")
            lines.append(f"    → {body_preview}")

    return "\n".join(lines)


async def get_relevant_issues_context(
    db: AsyncIOMotorDatabase,
    repo_owner: str,
    repo_name: str,
    github_pat: str,
    task_description: str,
) -> str:
    """
    Main entry point. Returns context string ready to inject into system prompt.
    Returns empty string on any error — never blocks main flow.

    Usage in orchestrator.py or cto_projects.py:
        issues_ctx = await get_relevant_issues_context(
            db=db,
            repo_owner=task.repo_owner,
            repo_name=task.repo_name,
            github_pat=decrypted_pat,
            task_description=task.description,
        )
        if issues_ctx:
            system_prompt += f"\\n\\n{issues_ctx}"
    """
    try:
        all_issues = await get_issues_cached(db, repo_owner, repo_name, github_pat)
        relevant   = find_relevant_issues(task_description, all_issues)
        return build_issues_context(relevant)
    except Exception as e:
        print(f"[Issues Context] Error: {e}")
        return ""


# ─────────────────────────────────────────────────────────────────────────────
# Admin helper — list all open issues for a repo
# ─────────────────────────────────────────────────────────────────────────────

async def list_all_open_issues(
    db: AsyncIOMotorDatabase,
    repo_owner: str,
    repo_name: str,
    github_pat: str,
) -> list[dict]:
    """Returns all cached open issues. Used by admin panel."""
    return await get_issues_cached(db, repo_owner, repo_name, github_pat)
