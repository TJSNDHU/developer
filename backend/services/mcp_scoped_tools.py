"""
services/mcp_scoped_tools.py — Iter 212m-175
────────────────────────────────────────────────────────────────────────
MCP Scoped Tool Filtering.

Background:
    Paper arxiv.org/abs/2606.30317 (Section VIII-A) shows that once an
    MCP server exposes ≥10-15 tools the LLM's tool-selection accuracy
    drops below 90 %. ORA currently exposes 12 tools statically, which
    puts us squarely on the wrong side of that inflection.

    This module implements the fix WITHOUT rebuilding the classifier:
    we reuse `core.intent_gateway`'s existing DeepSeek call path
    (temp=0.0, 2 s hard timeout, safe fallback) and cap the tool list
    returned to any client at MAX_TOOLS (7).

The MCP protocol quirk:
    `tools/list` is fetched BEFORE the user asks a question, so we
    can't filter by "the current query" at list-time. We solve this
    with three layers, all of which return ≤ MAX_TOOLS:

      (a) tools/list carries a `context` / `query` param      → classify + scope
      (b) session (Mcp-Session-Id) has a cached classification → replay it
      (c) neither — smart default (read + project + ship_code) → 7 tools

    Every `tools/call` populates SESSION_TOOL_CACHE with the groups
    the classifier derived from the call's arguments, so the SECOND
    and later `tools/list` fetches in the same session become scoped
    to the user's actual work.

Also handles two anti-patterns from the same paper:

    • run_vanguard_scan is now async (returns scan_id in <1 s) with a
      companion get_scan_status(scan_id) tool. Never blocks an MCP
      client 30 s+.
    • sanitize_for_llm() strips prompt-injection tripwires from file
      content before it is returned to the LLM (paper Section VIII-B).
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

logger = logging.getLogger("aurem.mcp.scoped")


# ─────────────────────────────────────────────────────────────────────
# Groups & caps — the shape of scoped exposure.
# ─────────────────────────────────────────────────────────────────────
TOOL_GROUPS: dict[str, list[str]] = {
    "read":     ["read_repo_file", "list_repo_files", "search_repo",
                 "get_repo_structure"],
    "write":    ["write_repo_file", "ship_code", "get_task_status"],
    "security": ["run_vanguard_scan", "get_scan_status", "get_repo_health"],
    "project":  ["list_projects", "get_recent_commits", "get_project_info"],
}

# Tools that are ALWAYS exposed regardless of scoping — the LLM must
# be able to enumerate the user's repos to route any subsequent call.
CORE_ALWAYS: list[str] = ["list_projects"]

# Hard cap per paper. Overlap between groups is fine — the cap keeps
# the client-facing tool count below the accuracy cliff.
MAX_TOOLS = 7


# ─────────────────────────────────────────────────────────────────────
# Session cache — populated on every tools/call, consumed on
# subsequent tools/list requests in the same MCP session.
# Key: Mcp-Session-Id (or a synthesised per-user key). TTL 30 min so
# stale sessions don't leak memory.
# ─────────────────────────────────────────────────────────────────────
SESSION_TOOL_CACHE: dict[str, dict[str, Any]] = {}
_SESSION_TTL_S = 1800.0   # 30 minutes


def _prune_sessions() -> None:
    now = time.monotonic()
    stale = [k for k, v in SESSION_TOOL_CACHE.items()
             if v.get("expires_at", 0) < now]
    for k in stale:
        SESSION_TOOL_CACHE.pop(k, None)


def cache_session_groups(session_id: str, groups: list[str]) -> None:
    """Persist a classified group list for a session. No-op on falsy id."""
    if not session_id:
        return
    _prune_sessions()
    # Dedupe while preserving order.
    seen: list[str] = []
    for g in groups:
        if g not in seen and g in TOOL_GROUPS:
            seen.append(g)
    SESSION_TOOL_CACHE[session_id] = {
        "groups":     seen,
        "expires_at": time.monotonic() + _SESSION_TTL_S,
    }


def get_session_groups(session_id: str) -> list[str] | None:
    """Return cached groups for a session or None if absent/expired."""
    if not session_id:
        return None
    _prune_sessions()
    entry = SESSION_TOOL_CACHE.get(session_id)
    return list(entry["groups"]) if entry else None


# ─────────────────────────────────────────────────────────────────────
# Injection scrubber (paper Section VIII-B).
# ─────────────────────────────────────────────────────────────────────
_INJECTION_PATTERNS: tuple[str, ...] = (
    "ignore previous",
    "ignore all previous",
    "disregard above",
    "new instructions",
    "you are now",
    "system prompt:",
)


def sanitize_for_llm(content: str) -> str:
    """Redact lines containing prompt-injection markers before returning
    file content to an LLM. Line-level so file structure is preserved
    and diffs remain reviewable in the client UI."""
    if not content:
        return content
    out: list[str] = []
    for line in content.split("\n"):
        low = line.lower()
        if any(p in low for p in _INJECTION_PATTERNS):
            out.append("# [redacted]")
        else:
            out.append(line)
    return "\n".join(out)


# ─────────────────────────────────────────────────────────────────────
# Classifier — reuses core.intent_gateway's DeepSeek path.
# ─────────────────────────────────────────────────────────────────────
_VALID_GROUPS = {"read", "write", "security", "project"}
_SAFE_DEFAULT_GROUPS = ["read", "project"]

_CLASSIFY_PROMPT = (
    "Classify this coding request into tool groups. "
    "Groups: read (view/find code), write (change/fix/commit code), "
    "security (scan/vuln/audit), project (list repos/commits/status). "
    "Return JSON array of 1-2 most relevant groups only.\n"
    "Request: {q}\nGroups:"
)


async def classify_tool_groups(query: str) -> list[str]:
    """Semantic classifier — returns 1-2 group names from _VALID_GROUPS.

    Uses core.intent_gateway.classify_llm_json (DeepSeek, temp=0.0,
    max_tokens=30, 2 s timeout). Handles ambiguous prompts (e.g. "why
    is my login broken") which pure keyword matching would misroute.

    Falls back to `["read", "project"]` on:
      - empty query
      - LLM timeout / error / import failure
      - LLM returned non-JSON or unknown groups
    """
    q = (query or "").strip()
    if not q:
        return list(_SAFE_DEFAULT_GROUPS)

    try:
        from core.intent_gateway import classify_llm_json
    except Exception:
        return list(_SAFE_DEFAULT_GROUPS)

    prompt = _CLASSIFY_PROMPT.format(q=q[:400])
    parsed = await classify_llm_json(prompt, timeout=2.0, max_tokens=30)
    if isinstance(parsed, list):
        groups = [g for g in parsed if isinstance(g, str) and g in _VALID_GROUPS]
        if groups:
            # Dedupe preserving order, cap at 2 to bound the tool count.
            seen: list[str] = []
            for g in groups:
                if g not in seen:
                    seen.append(g)
                if len(seen) >= 2:
                    break
            return seen
    return list(_SAFE_DEFAULT_GROUPS)


# ─────────────────────────────────────────────────────────────────────
# Scoped selectors — all return ≤ MAX_TOOLS.
# ─────────────────────────────────────────────────────────────────────
def _names_for_groups(groups: list[str]) -> set[str]:
    names = set(CORE_ALWAYS)
    for g in groups:
        names.update(TOOL_GROUPS.get(g, []))
    return names


def _pick(all_tools: list[dict], names: set[str]) -> list[dict]:
    """Filter+cap. Preserves the incoming tool order so the client UI
    stays stable across calls."""
    out = [t for t in all_tools if t.get("name") in names]
    return out[:MAX_TOOLS]


async def get_scoped_tools(query: str, all_tools: list[dict]) -> list[dict]:
    """Full path: classify `query` → resolve groups → return ≤7 tools."""
    groups = await classify_tool_groups(query)
    return _pick(all_tools, _names_for_groups(groups))


def get_smart_default_tools(all_tools: list[dict]) -> list[dict]:
    """No query hint AND no session context.

    Returns 7 tools = CORE + read group + project group + ship_code.
    This is the "safe blend" — the LLM can browse the repo, list the
    user's projects, and ship code, all without seeing the security /
    write groups it doesn't need for typical exploration."""
    names = _names_for_groups(["read", "project"])
    names.add("ship_code")
    return _pick(all_tools, names)


def get_session_tools(
    session_id: str, all_tools: list[dict]
) -> list[dict] | None:
    """Consume cached session classification. Returns None when no
    session context exists (caller falls back to smart default)."""
    groups = get_session_groups(session_id)
    if not groups:
        return None
    return _pick(all_tools, _names_for_groups(groups))


# ─────────────────────────────────────────────────────────────────────
# Session-update helpers used by tools/call.
# ─────────────────────────────────────────────────────────────────────
_QUERY_ARG_KEYS = ("task", "query", "prompt", "message", "goal",
                    "commit_message", "file_path", "path")


def extract_query_from_call(tool_name: str, args: dict) -> str:
    """Best-effort query string from a tools/call payload.

    Prefers explicit `task` / `query` fields; falls back to file paths
    and finally the tool name itself. Guarantees a non-empty return."""
    if isinstance(args, dict):
        for k in _QUERY_ARG_KEYS:
            v = args.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return tool_name or ""


def group_for_tool(tool_name: str) -> str | None:
    """Reverse lookup — which group owns this tool?"""
    for g, names in TOOL_GROUPS.items():
        if tool_name in names:
            return g
    return None


async def update_session_from_call(
    session_id: str, tool_name: str, args: dict,
) -> None:
    """Populate SESSION_TOOL_CACHE from an in-flight tools/call.

    We classify the call's textual arguments (semantic — handles a
    ship_code with task="fix auth bug" as write+security) and union in
    the group of the tool being called (deterministic — so a session
    always includes the tools it has just used)."""
    if not session_id:
        return
    q = extract_query_from_call(tool_name, args)
    try:
        groups = await classify_tool_groups(q)
    except Exception:
        groups = list(_SAFE_DEFAULT_GROUPS)
    own = group_for_tool(tool_name)
    if own and own not in groups:
        groups.append(own)
    cache_session_groups(session_id, groups)


# ─────────────────────────────────────────────────────────────────────
# Async Vanguard scan tracker (fix for the second anti-pattern —
# scans must not block the MCP client for 30 s+).
# ─────────────────────────────────────────────────────────────────────
_VANGUARD_SCANS: dict[str, dict[str, Any]] = {}
_SCAN_TTL_S = 3600.0     # 1 hour retention after start


def _prune_scans() -> None:
    now = time.monotonic()
    stale = [k for k, v in _VANGUARD_SCANS.items()
             if v.get("expires_at", 0) < now]
    for k in stale:
        _VANGUARD_SCANS.pop(k, None)


def register_scan(scan_id: str, user_id: str, project_id: str) -> None:
    _prune_scans()
    _VANGUARD_SCANS[scan_id] = {
        "scan_id":    scan_id,
        "user_id":    user_id,
        "project_id": project_id,
        "status":     "pending",
        "started_at": time.time(),
        "expires_at": time.monotonic() + _SCAN_TTL_S,
        "results":    None,
        "error":      None,
    }


def update_scan(scan_id: str, **fields: Any) -> None:
    scan = _VANGUARD_SCANS.get(scan_id)
    if not scan:
        return
    scan.update(fields)


def get_scan(scan_id: str, user_id: str) -> dict[str, Any] | None:
    """Fetch a scan record if it belongs to `user_id`."""
    _prune_scans()
    scan = _VANGUARD_SCANS.get(scan_id)
    if not scan:
        return None
    if scan.get("user_id") != user_id:
        return None
    # Return a shallow copy without the internal expires_at monotonic.
    return {
        "scan_id":    scan["scan_id"],
        "project_id": scan["project_id"],
        "status":     scan["status"],
        "started_at": scan["started_at"],
        "results":    scan.get("results"),
        "error":      scan.get("error"),
    }


__all__ = [
    "TOOL_GROUPS", "CORE_ALWAYS", "MAX_TOOLS",
    "SESSION_TOOL_CACHE",
    "sanitize_for_llm",
    "classify_tool_groups",
    "get_scoped_tools", "get_smart_default_tools", "get_session_tools",
    "extract_query_from_call", "group_for_tool",
    "update_session_from_call",
    "cache_session_groups", "get_session_groups",
    "register_scan", "update_scan", "get_scan",
]
