"""
core/tool_router.py — Iter 212m-152 (Prompt Mode gap fix #1)

Keyword-based tool namespace reduction.  Replaces the "all 39 tools
on every call" anti-pattern with a per-task slice.

Research finding driving this fix: LLM accuracy drops sharply once
the available-tool catalog exceeds ~20 entries (Anthropic + OpenAI
tool-routing papers, 2024-2025).  Our catalog hit 39 tools and was
costing ~19.5 k tokens per request in tool-schema overhead alone,
of which only 4-6 schemas were ever relevant to a given task.

This module is purely heuristic — runs in microseconds.  Wired only
from `services/orchestrator.py::chat_with_tools` (Prompt Mode).
Loop Mode, ORA, Codebase Health are untouched.
"""
from __future__ import annotations

# Tool groups — each lists tool *names* that match the entries
# registered by local_tools.py / web_skills.py / dev_skills.py /
# vercel_skills.py.  An unknown name here just becomes a no-op (the
# orchestrator's intersection step ignores anything that isn't in
# the real catalog).
TOOL_GROUPS: dict[str, list[str]] = {
    "code": [
        "read_repo_file", "read_repo_files", "write_repo_file",
        "patch_repo_file", "get_repo_structure", "list_repo_files",
        "search_repo", "semantic_search_repo", "execute_bash",
        "validate_syntax", "find_usages", "get_dependencies",
        "detect_framework", "get_commit_diff",
    ],
    "query": [
        "read_repo_file", "get_repo_structure", "list_repo_files",
        "search_repo", "semantic_search_repo", "get_repo_info",
        "get_commit_history", "list_issues", "get_pr_comments",
        "find_package_docs", "get_env_vars",
    ],
    "web": [
        "web_search", "fetch_url", "web_search_and_summarize",
        "firecrawl_scrape", "firecrawl_crawl_site",
    ],
    "deploy": [
        "vercel_list_projects", "vercel_get_project",
        "vercel_trigger_deploy_hook", "vercel_list_deployments",
        "vercel_get_deployment", "vercel_get_deployment_build_logs",
        "vercel_get_runtime_errors", "vercel_get_runtime_logs",
    ],
    "debug": [
        "read_repo_file", "search_repo", "semantic_search_repo",
        "execute_bash", "validate_syntax", "e2b_run_code",
        "get_commit_diff", "find_usages", "list_issues",
    ],
    "casual": [],   # no tools — direct LLM reply path
}

# Keyword → group mapping.  Order matters slightly: shorter signals
# come first so we tokenise on the more specific phrase only when it
# appears.  Score = count of matches; ties resolve in `pick_group`.
TASK_SIGNALS: dict[str, list[str]] = {
    "code": [
        "fix", "bug", "implement", "refactor", "write", "create",
        "add", "update", "change", "edit", "patch", "build",
        "function", "class", "component", "feature", "error",
        "failing", "rewrite", "vulnerability",
    ],
    "query": [
        "show", "list", "what", "how many", "status", "check",
        "find", "get", "which", "describe", "explain", "analyze",
        "review", "summarize", "history", "issues", "pr",
    ],
    "web": [
        "search", "google", "look up", "find online", "latest",
        "news", "documentation", "docs", "website", "url", "fetch",
    ],
    "deploy": [
        "deploy", "vercel", "production", "staging", "preview",
        "build", "deployment", "release", "publish", "ship",
    ],
    "debug": [
        "debug", "test", "run", "execute", "console", "error",
        "crash", "failing test", "f12", "stack trace", "exception",
    ],
}


def pick_group(message: str, tier: str) -> str:
    """Return the single best-matching group name for `message`.

    `tier` is the Intent Gateway tier (`casual` / `query` / `agentic`).
    Tie-break rules:
      • all-zero score on `agentic` tier → "code" (safer default)
      • all-zero score on `query`   tier → "query"
      • all-zero score on `casual`  tier → "casual"
    """
    if tier == "casual":
        return "casual"
    message_lower = (message or "").lower()
    scores = {g: 0 for g in TOOL_GROUPS if g != "casual"}
    for group, signals in TASK_SIGNALS.items():
        for signal in signals:
            if signal in message_lower:
                scores[group] += 1
    primary, best_score = max(scores.items(), key=lambda kv: kv[1])
    if best_score == 0:
        return "code" if tier == "agentic" else "query"
    return primary


def get_tools_for_task(message: str, tier: str) -> list[str]:
    """Return the list of tool *names* the LLM should see for this task.

    `tier` comes from the Intent Gateway (`casual` / `query` /
    `agentic`).  Casual → empty list (no tool catalog injected).
    Otherwise the primary task group's tool set, plus the deploy
    group when the message also carries deploy signals alongside
    code signals (shipping is often paired with code edits).
    """
    if tier == "casual":
        return []
    message_lower = (message or "").lower()
    scores = {g: 0 for g in TOOL_GROUPS if g != "casual"}
    for group, signals in TASK_SIGNALS.items():
        for signal in signals:
            if signal in message_lower:
                scores[group] += 1
    primary, best_score = max(scores.items(), key=lambda kv: kv[1])
    if best_score == 0:
        primary = "code" if tier == "agentic" else "query"
    tools = list(TOOL_GROUPS[primary])
    # Secondary group: deploy tools are useful alongside code tasks
    # (e.g. "fix the bug and redeploy"). Always add them when both
    # signal groups fire — never the other way (don't pollute a pure
    # deploy task with code tools).
    if primary == "code" and scores.get("deploy", 0) > 0:
        for t in TOOL_GROUPS["deploy"]:
            if t not in tools:
                tools.append(t)
    return tools


__all__ = [
    "TOOL_GROUPS", "TASK_SIGNALS",
    "pick_group", "get_tools_for_task",
]
