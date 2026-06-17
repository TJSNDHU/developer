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
MCP_SERVER_NAME      = "ORA by Aurem CTO"
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
            "List all ORA by Aurem CTO projects owned by the authenticated user. "
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
            "Queue a code-change task on the ORA by Aurem CTO worker (Mode C). "
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
# Iter 174 — API-key auth mode.
#
# External MCP clients (Claude Desktop, Cursor, Cline …) don't have an
# easy way to obtain a JWT from our login flow — they need a stable
# bearer they can paste once. We support two formats interchangeably:
#
#   1. `Bearer <jwt>`               — existing browser-issued token
#   2. `Bearer sk-aurem-<urlsafe>`  — long-lived API key issued by
#                                     POST /mcp/keys, stored in Mongo
#                                     with `active: true`.
#
# The API-key path looks the key up in `db.api_keys`, refuses when
# inactive/missing, and returns the same `{"user_id": …}` shape the
# JWT path returns. All MCP tool calls thereafter behave identically
# regardless of which auth mode the client used.
API_KEY_PREFIX = "sk-aurem-"


async def _resolve_user(authorization: Optional[str]) -> dict:
    """Accept JWT or `sk-aurem-…` API key.

    Returns the user dict ({"user_id": …}). Raises a ValueError with
    a human-readable reason on any auth failure — callers map that to
    the appropriate JSON-RPC error code.
    """
    if not authorization:
        raise ValueError("Missing Authorization header")
    token = authorization.replace("Bearer ", "", 1).strip()
    if not token:
        raise ValueError("Empty bearer token")

    if token.startswith(API_KEY_PREFIX):
        db = get_db()
        if db is None:
            raise ValueError("Database unavailable")
        rec = await db.api_keys.find_one({"key": token, "active": True})
        if not rec or not rec.get("user_id"):
            raise ValueError("API key invalid or revoked")
        # Best-effort last-used touch; ignore errors so a slow write
        # never blocks the tool call.
        try:
            await db.api_keys.update_one(
                {"key": token}, {"$set": {"last_used_at": time.time()}}
            )
        except Exception:
            pass
        return {"user_id": rec["user_id"]}

    # Fall through to the existing JWT path (current_dev parses the
    # `Authorization: Bearer …` header itself).
    return await current_dev(authorization)


async def _auth_or_error(authorization: Optional[str]) -> tuple[Optional[dict], Optional[dict]]:
    """Returns (user_dict, None) on success or (None, error_payload) on failure."""
    try:
        me = await _resolve_user(authorization)
        return me, None
    except ValueError as e:
        return None, {"code": _RPC_UNAUTHORIZED, "message": str(e)}
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
            "ORA by Aurem CTO — AI engineer that reads "
            "your repo and ships code to GitHub. No IDE needed."
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



# ── Iter 174 — well-known discovery + API-key generation ──────────────
# Both routes are intentionally mounted UNDER the same router as the
# main `/mcp` endpoint so they inherit the `/api/aurem-dev` prefix
# applied in main.py:
#
#     GET  /api/aurem-dev/mcp/.well-known/mcp   — discovery
#     POST /api/aurem-dev/mcp/keys              — issue API key
#
# An additional root-level alias for the discovery endpoint
# (`/.well-known/mcp` with no `/api` prefix) is mounted in main.py
# because the well-known URL standard expects discovery documents at
# the domain root.

def _public_mcp_endpoint() -> str:
    """The canonical public URL clients should hit. Configurable via
    AUREM_PUBLIC_BASE_URL so the same code serves auremcto.com in
    production and the *.preview.emergentagent.com URL in dev."""
    import os
    base = (os.getenv("AUREM_PUBLIC_BASE_URL") or "https://auremcto.com").rstrip("/")
    return f"{base}/api/aurem-dev/mcp"


def _discovery_payload() -> dict:
    return {
        "mcp_endpoint":     _public_mcp_endpoint(),
        "protocol_version": MCP_PROTOCOL_VERSION,
        "server_name":      MCP_SERVER_NAME,
        "auth": {
            "type":        "bearer",
            "description": (
                "JWT token from auremcto.com login, OR a long-lived "
                "API key starting with `sk-aurem-` issued via "
                "POST /api/aurem-dev/mcp/keys."
            ),
            "formats": ["jwt", "api_key"],
            "api_key_prefix": API_KEY_PREFIX,
        },
    }


@router.get("/.well-known/mcp")
async def mcp_discovery() -> dict:
    """MCP discovery endpoint — spec compliant (well-known URL pattern).

    Returned at:
      • /api/aurem-dev/mcp/.well-known/mcp   (via this router)
      • /.well-known/mcp                     (root alias in main.py)
    Both serve the same payload so clients can introspect the server
    without knowing the AUREM-specific URL prefix.
    """
    return _discovery_payload()


# Exported so main.py can mount the same handler at the domain-root
# `/.well-known/mcp` path without re-implementing the logic.
async def mcp_discovery_root(_request):
    return JSONResponse(_discovery_payload())


@router.post("/keys")
async def create_mcp_key(
    authorization: Optional[str] = Header(None),
) -> dict:
    """Issue an API key for external MCP clients (Claude Desktop,
    Cursor, etc.). The key is `sk-aurem-{urlsafe-secret}` and
    inherits the calling user's permissions.

    The user is identified via JWT (this endpoint deliberately does
    NOT accept API keys — only a real logged-in browser session can
    mint new keys, otherwise a stolen key could mint more keys).
    """
    import secrets
    from fastapi import HTTPException
    if not authorization:
        raise HTTPException(401, "Authorization header required")
    raw = authorization.replace("Bearer ", "", 1).strip()
    if raw.startswith(API_KEY_PREFIX):
        raise HTTPException(
            403,
            "API keys cannot mint new API keys — sign in to the dashboard first.",
        )
    user = await current_dev(authorization)
    db = get_db()
    if db is None:
        raise HTTPException(503, "Database unavailable")

    key = f"{API_KEY_PREFIX}{secrets.token_urlsafe(32)}"
    await db.api_keys.insert_one({
        "key":        key,
        "user_id":    user["user_id"],
        "created_at": time.time(),
        "active":     True,
        "label":      "MCP API Key",
    })
    return {
        "ok":    True,
        "key":   key,
        "usage": "Use as Bearer token in MCP clients",
        "note":  "Store this immediately — it is shown only once.",
    }


@router.get("/keys")
async def list_mcp_keys(
    authorization: Optional[str] = Header(None),
) -> dict:
    """List the calling user's API keys with their last-used metadata.
    Returns the key prefix only (masked tail) so the dashboard can
    show "sk-aurem-…3a7b" without leaking the secret."""
    from fastapi import HTTPException
    if not authorization:
        raise HTTPException(401, "Authorization header required")
    user = await current_dev(authorization)
    db = get_db()
    if db is None:
        raise HTTPException(503, "Database unavailable")
    out = []
    cursor = db.api_keys.find(
        {"user_id": user["user_id"]},
        {"_id": 0, "key": 1, "label": 1, "created_at": 1,
         "last_used_at": 1, "active": 1},
    )
    async for k in cursor:
        full = k.get("key") or ""
        masked = f"{full[:13]}…{full[-4:]}" if len(full) > 20 else "…"
        out.append({
            "key_masked":   masked,
            "label":        k.get("label"),
            "created_at":   k.get("created_at"),
            "last_used_at": k.get("last_used_at"),
            "active":       bool(k.get("active")),
        })
    return {"keys": out, "count": len(out)}


@router.delete("/keys/{key_tail}")
async def revoke_mcp_key(
    key_tail: str,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Revoke an API key by its 4-char tail (shown in the masked
    listing). Idempotent — returns ok=true even if nothing matched.
    Scoped to the calling user's keys so users can never revoke
    other users' keys."""
    from fastapi import HTTPException
    if not authorization:
        raise HTTPException(401, "Authorization header required")
    user = await current_dev(authorization)
    db = get_db()
    if db is None:
        raise HTTPException(503, "Database unavailable")
    if not key_tail or len(key_tail) < 4:
        raise HTTPException(400, "key_tail must be ≥ 4 chars")
    res = await db.api_keys.update_many(
        {"user_id": user["user_id"],
         "key": {"$regex": f"{key_tail[-12:]}$"},   # cheap tail match
         "active": True},
        {"$set": {"active": False, "revoked_at": time.time()}},
    )
    return {"ok": True, "revoked": res.modified_count}
