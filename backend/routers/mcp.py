"""
MCP (Model Context Protocol) server endpoint for AUREM CTO.

Spec: 2025-03-26 (Streamable HTTP transport). JSON-RPC 2.0 over a
single endpoint. Reference:
  https://modelcontextprotocol.io/specification/2025-03-26/basic/transports
  https://modelcontextprotocol.io/specification/2025-03-26/server/tools

Endpoints:
  GET  /mcp   — convenience manifest (server info + capabilities + tool list).
                Strict MCP clients use GET only when they want an SSE
                event stream; we additionally return the manifest as JSON
                when the Accept header is `application/json` (or absent)
                so a plain `curl https://…/mcp` returns something useful.
  POST /mcp   — JSON-RPC 2.0 request handler. Methods:
                  • initialize          → server info exchange
                  • tools/list          → enumerate tools
                  • tools/call          → invoke a tool

Tools (4):
  1. list_projects        — user ke projects
  2. ship_code            — task bhejo ORA ko (Mode C enqueue)
  3. get_task_status      — task status check
  4. get_recent_commits   — recent commits for a project's repo

Auth: existing JWT — `Authorization: Bearer <token>` (same as the rest
of /api/aurem-dev). The router rejects with JSON-RPC error -32001
(custom: "Unauthorized") when the bearer is missing/invalid.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse

from cto_services.auth import current_dev
from cto_services.db import get_db

logger = logging.getLogger("aurem.mcp")

router = APIRouter(prefix="/mcp", tags=["mcp"])


# Protocol constants ───────────────────────────────────────────────────
MCP_PROTOCOL_VERSION = "2025-03-26"
MCP_SERVER_NAME      = "aurem-cto"
MCP_SERVER_VERSION   = "1.0.0"

# JSON-RPC 2.0 error codes (spec)
_RPC_PARSE_ERROR      = -32700
_RPC_INVALID_REQUEST  = -32600
_RPC_METHOD_NOT_FOUND = -32601
_RPC_INVALID_PARAMS   = -32602
_RPC_INTERNAL_ERROR   = -32603
# Custom (server-defined) error codes — MCP allows -32000 to -32099.
_RPC_UNAUTHORIZED     = -32001
_RPC_TOOL_FAILED      = -32002


# ── Tool schemas ──────────────────────────────────────────────────────
TOOLS: list[dict[str, Any]] = [
    {
        "name": "list_projects",
        "description": (
            "List all AUREM CTO projects owned by the authenticated user. "
            "Returns project_id, name, GitHub owner/repo, branch, task "
            "count, and last-task timestamp."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Max projects to return (1–100).",
                    "minimum": 1,
                    "maximum": 100,
                    "default": 50,
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "ship_code",
        "description": (
            "Queue a code-change task on the AUREM CTO worker (Mode C). "
            "ORA will read the connected GitHub repo, plan the edit, "
            "implement, run security scans, and commit directly to the "
            "project's branch. Returns the task_id for polling."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {
                    "type": "string",
                    "description": (
                        "Target project_id. If omitted, the user's most "
                        "recently used project is used."
                    ),
                },
                "task": {
                    "type": "string",
                    "description": (
                        "Concrete file-edit task brief. Example: "
                        "'Add /api/health route to backend/routers/health.py "
                        "returning {ok: true}'."
                    ),
                    "minLength": 10,
                    "maxLength": 4000,
                },
                "maxx_mode": {
                    "type": "boolean",
                    "description": "Enable extended-reasoning mode (slower, deeper).",
                    "default": False,
                },
            },
            "required": ["task"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_task_status",
        "description": (
            "Fetch the live status of a previously-queued ship task. "
            "Returns status (queued|reading|thinking|writing|committing"
            "|done|failed), commit SHA when shipped, error text when "
            "failed, and the list of step events."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "The task_id returned by ship_code.",
                    "minLength": 4,
                },
            },
            "required": ["task_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_recent_commits",
        "description": (
            "Fetch the most recent commits from the project's connected "
            "GitHub repo at the pinned branch. Returns commit SHA, "
            "author, message, and timestamp."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {
                    "type": "string",
                    "description": "Target project_id.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max commits to return (1–50).",
                    "minimum": 1,
                    "maximum": 50,
                    "default": 10,
                },
            },
            "required": ["project_id"],
            "additionalProperties": False,
        },
    },
]


# ── Auth helper ───────────────────────────────────────────────────────
async def _auth_or_error(authorization: Optional[str]) -> tuple[Optional[dict], Optional[dict]]:
    """Returns (user_dict, None) on success or (None, error_payload) on failure."""
    if not authorization:
        return None, {"code": _RPC_UNAUTHORIZED, "message": "Missing Authorization header"}
    try:
        me = await current_dev(authorization)
        return me, None
    except Exception as e:
        return None, {"code": _RPC_UNAUTHORIZED, "message": f"Unauthorized: {e}"}


# ── Tool implementations ──────────────────────────────────────────────
async def _tool_list_projects(user_id: str, args: dict) -> dict:
    db = get_db()
    if db is None:
        raise RuntimeError("Database unavailable")
    limit = int(args.get("limit") or 50)
    limit = max(1, min(100, limit))
    cursor = db.cto_projects.find(
        {"user_id": user_id},
        {"_id": 0, "github_token": 0},   # never expose encrypted PAT
    ).sort([("last_task", -1), ("created_at", -1)]).limit(limit)
    projects = []
    async for p in cursor:
        projects.append({
            "project_id":    p.get("project_id"),
            "name":          p.get("name"),
            "github_owner":  p.get("github_owner"),
            "github_repo":   p.get("github_repo"),
            "branch":        p.get("branch") or "main",
            "tasks_count":   int(p.get("tasks_count") or 0),
            "last_task":     p.get("last_task"),
            "created_at":    p.get("created_at"),
        })
    return {"projects": projects, "count": len(projects)}


async def _tool_ship_code(user_id: str, args: dict) -> dict:
    task_text = (args.get("task") or "").strip()
    if len(task_text) < 10:
        raise ValueError("`task` must be ≥ 10 characters")
    project_id = args.get("project_id")
    maxx_mode  = bool(args.get("maxx_mode") or False)
    # Lazy import: avoids a circular import at module load time
    # (cto_projects imports from a few services that themselves pull in
    # main app state).
    from routers.cto_projects import _enqueue_cto_task
    res = await _enqueue_cto_task(
        user_id=user_id,
        project_id=project_id,
        task_text=task_text,
        bg=None,
        maxx_mode=maxx_mode,
    )
    if not res.get("ok"):
        # Surface the actual reason (no_project, no_pat, etc.) so the
        # MCP client can show something useful.
        raise RuntimeError(f"Could not queue task: {res.get('reason', 'unknown')}")
    return {
        "task_id":    res["task_id"],
        "project_id": res.get("project_id"),
        "status":     "queued",
    }


async def _tool_get_task_status(user_id: str, args: dict) -> dict:
    task_id = (args.get("task_id") or "").strip()
    if not task_id:
        raise ValueError("`task_id` is required")
    db = get_db()
    if db is None:
        raise RuntimeError("Database unavailable")
    t = await db.cto_tasks.find_one(
        {"task_id": task_id, "user_id": user_id},
        {"_id": 0},
    )
    if not t:
        raise RuntimeError(f"Task not found: {task_id}")
    return {
        "task_id":     t.get("task_id"),
        "project_id":  t.get("project_id"),
        "status":      t.get("status"),
        "commit_sha":  t.get("commit_sha"),
        "error":       t.get("error"),
        "task":        t.get("task"),
        "created_at":  t.get("created_at"),
        # Trim steps to the tail so the response stays small. The full
        # tape is available via the existing /cto/tasks/{id} HTTP route.
        "steps":       (t.get("steps") or [])[-20:],
    }


async def _tool_get_recent_commits(user_id: str, args: dict) -> dict:
    project_id = (args.get("project_id") or "").strip()
    if not project_id:
        raise ValueError("`project_id` is required")
    limit = int(args.get("limit") or 10)
    limit = max(1, min(50, limit))
    db = get_db()
    if db is None:
        raise RuntimeError("Database unavailable")
    proj = await db.cto_projects.find_one(
        {"project_id": project_id, "user_id": user_id}
    )
    if not proj:
        raise RuntimeError(f"Project not found: {project_id}")
    # Decrypt PAT through the same helper the rest of the router uses
    # so encrypted-token storage stays consistent.
    from routers.cto_projects import _decrypt_pat, _user_gh_token
    gh_token = await _decrypt_pat(user_id, proj.get("github_token")) \
        or await _user_gh_token(user_id)
    owner  = proj.get("github_owner")
    repo   = proj.get("github_repo")
    branch = proj.get("branch") or "main"
    if not (owner and repo and gh_token):
        raise RuntimeError("GitHub not connected to this project")
    headers = {
        "Accept":               "application/vnd.github+json",
        "Authorization":        f"Bearer {gh_token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    url = (
        f"https://api.github.com/repos/{owner}/{repo}/commits"
        f"?sha={branch}&per_page={limit}"
    )
    async with httpx.AsyncClient(timeout=15.0) as c:
        r = await c.get(url, headers=headers)
    if r.status_code == 404:
        raise RuntimeError(f"Branch {branch} not found on GitHub")
    if r.status_code == 401:
        raise RuntimeError("GitHub PAT invalid or expired")
    r.raise_for_status()
    items = []
    for c in (r.json() or [])[:limit]:
        commit = c.get("commit") or {}
        author = commit.get("author") or {}
        items.append({
            "sha":     c.get("sha"),
            "message": (commit.get("message") or "").splitlines()[0][:240],
            "author":  author.get("name"),
            "email":   author.get("email"),
            "date":    author.get("date"),
            "url":     c.get("html_url"),
        })
    return {
        "owner": owner, "repo": repo, "branch": branch,
        "commits": items, "count": len(items),
    }


_TOOL_DISPATCH = {
    "list_projects":      _tool_list_projects,
    "ship_code":          _tool_ship_code,
    "get_task_status":    _tool_get_task_status,
    "get_recent_commits": _tool_get_recent_commits,
}


# ── JSON-RPC helpers ──────────────────────────────────────────────────
def _rpc_ok(req_id, result) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _rpc_err(req_id, code: int, message: str, data: Any = None) -> dict:
    err = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": err}


def _server_manifest() -> dict:
    """Cached server-info payload returned by both GET /mcp and the
    `initialize` RPC. Keep these in lockstep so MCP clients see a
    coherent picture regardless of how they introspected the server."""
    return {
        "protocolVersion": MCP_PROTOCOL_VERSION,
        "serverInfo": {
            "name":    MCP_SERVER_NAME,
            "version": MCP_SERVER_VERSION,
        },
        "capabilities": {
            # We expose tools; no resources / prompts / sampling support
            # yet — they can be added later without a breaking change.
            "tools": {"listChanged": False},
        },
        "instructions": (
            "AUREM CTO MCP server. Authenticate with a JWT bearer token "
            "issued by /api/aurem-dev/auth/login. Use `tools/list` to "
            "enumerate available tools and `tools/call` to invoke them."
        ),
    }


# ── HTTP handlers ─────────────────────────────────────────────────────
@router.get("")
async def mcp_manifest(request: Request) -> JSONResponse:
    """GET /mcp — convenience manifest.

    A strict MCP client would only GET this endpoint to open an SSE
    stream. But `curl https://…/mcp` is the most common smoke-test and
    we want it to return something useful instead of 405, so we serve
    the manifest plus the tool catalogue as JSON.
    """
    payload = _server_manifest()
    payload["tools"] = TOOLS
    payload["transport"] = "streamable-http"
    payload["endpoint"] = str(request.url)
    return JSONResponse(payload)


@router.post("")
async def mcp_rpc(
    request: Request,
    authorization: Optional[str] = Header(None),
) -> JSONResponse:
    """POST /mcp — JSON-RPC 2.0 dispatch.

    Supported methods:
        initialize, tools/list, tools/call
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            _rpc_err(None, _RPC_PARSE_ERROR, "Invalid JSON"),
            status_code=400,
        )

    # Allow batch requests per JSON-RPC 2.0 — process sequentially
    # because tool calls may have side effects (ship_code enqueues a
    # task) and order matters more than throughput here.
    if isinstance(body, list):
        if not body:
            return JSONResponse(
                _rpc_err(None, _RPC_INVALID_REQUEST, "Empty batch"),
                status_code=400,
            )
        out = [await _handle_one(req, authorization) for req in body]
        return JSONResponse(out)
    if not isinstance(body, dict):
        return JSONResponse(
            _rpc_err(None, _RPC_INVALID_REQUEST, "Body must be an object or array"),
            status_code=400,
        )
    return JSONResponse(await _handle_one(body, authorization))


async def _handle_one(req: dict, authorization: Optional[str]) -> dict:
    req_id = req.get("id")
    if req.get("jsonrpc") != "2.0":
        return _rpc_err(req_id, _RPC_INVALID_REQUEST, "jsonrpc must be '2.0'")
    method = req.get("method")
    params = req.get("params") or {}
    if not isinstance(method, str):
        return _rpc_err(req_id, _RPC_INVALID_REQUEST, "Missing method")

    # initialize is the one method clients are allowed to call WITHOUT
    # being authenticated yet (so they can probe the server). Tool
    # calls and tools/list require auth.
    if method == "initialize":
        return _rpc_ok(req_id, _server_manifest())

    me, err = await _auth_or_error(authorization)
    if err:
        return _rpc_err(req_id, err["code"], err["message"])

    if method == "tools/list":
        return _rpc_ok(req_id, {"tools": TOOLS})

    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        if not isinstance(name, str) or name not in _TOOL_DISPATCH:
            return _rpc_err(
                req_id, _RPC_METHOD_NOT_FOUND,
                f"Unknown tool: {name!r}",
            )
        if not isinstance(args, dict):
            return _rpc_err(
                req_id, _RPC_INVALID_PARAMS,
                "`arguments` must be an object",
            )
        try:
            t0 = time.monotonic()
            result = await _TOOL_DISPATCH[name](me["user_id"], args)
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            return _rpc_ok(req_id, {
                # MCP `tools/call` result shape: a `content` array of
                # blocks. We return both a JSON text block (for clients
                # that prefer parsing) and a structured `data` field
                # (richer typed payload). Spec allows additional fields.
                "content": [
                    {
                        "type": "text",
                        "text": _json_safe_dumps(result),
                    },
                ],
                "isError": False,
                "data":      result,
                "elapsedMs": elapsed_ms,
            })
        except ValueError as e:
            return _rpc_err(req_id, _RPC_INVALID_PARAMS, str(e))
        except Exception as e:
            logger.exception(f"[mcp] tool {name!r} failed: {e}")
            return _rpc_err(
                req_id, _RPC_TOOL_FAILED,
                f"Tool execution failed: {type(e).__name__}: {e}",
            )

    return _rpc_err(req_id, _RPC_METHOD_NOT_FOUND, f"Unknown method: {method!r}")


def _json_safe_dumps(obj: Any) -> str:
    """Serialise tool results to JSON — falls back to str() on any
    odd types (datetime, ObjectId, etc.) instead of throwing."""
    import json
    return json.dumps(obj, default=str, ensure_ascii=False)
