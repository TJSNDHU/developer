"""
services/vercel_skills.py — Iter 212m-84 Vercel platform skills for ORA.

Hybrid approach (option C):
  • TODAY: Uses Vercel REST API (api.vercel.com) with the shared
    `VERCEL_API_TOKEN` env var. Works with personal access tokens
    (`vcp_...`). Founder mode — every user shares the same token.
  • TOMORROW: Designed for a clean swap to the strict MCP transport
    (`mcp.vercel.com`) once OAuth 2.1 + PKCE registration lands.
    The skill surface (`VERCEL_TOOLS`) stays IDENTICAL — only the
    underlying `_vercel_api()` helper switches implementation.

Why REST first?
  • `mcp.vercel.com` strictly requires OAuth 2.1 + PKCE consent.
    Bearer tokens are rejected with 401. We confirmed via curl that
    the existing `vcp_...` token works against api.vercel.com but
    NOT against mcp.vercel.com.
  • Building REST tools now ships immediate value; the OAuth/MCP swap
    can happen later without changing skill signatures or the chat
    tool-use loop.

Every skill returns the standard `{"ok": bool, ...}` envelope —
matches dev_skills / web_skills conventions — so the ORA orchestrator
can mix Vercel tools with the existing 30+ first-party tools without
any new dispatch logic.

Audit:
  • Every successful tool call writes a row to MongoDB
    `vercel_tool_audit` (capped collection-style; latest N kept). Used
    by the Settings → Integrations → Vercel card to show recent
    activity, and surfaces unauthorized scope drift.
"""
from __future__ import annotations

import os
import logging
from datetime import datetime, timezone
from typing import Optional

import httpx

from cto_services.db import get_db

logger = logging.getLogger(__name__)

VERCEL_API = "https://api.vercel.com"
DEFAULT_TIMEOUT = 15.0


def _token() -> Optional[str]:
    return (os.environ.get("VERCEL_API_TOKEN") or "").strip() or None


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {_token()}",
        "Content-Type": "application/json",
    }


async def _audit(ctx: dict, tool: str, args: dict, status: str,
                 summary: str = "") -> None:
    """Best-effort audit log — silent on failure (db may be unavailable
    in tests)."""
    db = get_db()
    if db is None:
        return
    try:
        await db.vercel_tool_audit.insert_one({
            "user_id":    ctx.get("user_id") or "",
            "tool":       tool,
            "args":       {k: v for k, v in (args or {}).items()
                           if k not in {"value", "secret"}},  # never log secret values
            "status":     status,
            "summary":    summary[:240],
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as e:                                  # noqa: BLE001
        logger.debug("vercel audit insert failed: %r", e)


async def _vercel_get(path: str, params: Optional[dict] = None) -> dict:
    """Thin GET wrapper — returns `{"ok": True, "data": json}` or
    `{"ok": False, "error": ...}`. Keeps the skill bodies short."""
    if not _token():
        return {"ok": False, "error": "VERCEL_API_TOKEN not configured"}
    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as c:
            r = await c.get(f"{VERCEL_API}{path}", headers=_headers(),
                            params=params or {})
        if r.status_code == 401 or r.status_code == 403:
            return {"ok": False, "error": "Vercel auth failed — token invalid or lacks scope"}
        if r.status_code == 404:
            return {"ok": False, "error": "Resource not found on Vercel"}
        if r.status_code >= 500:
            return {"ok": False, "error": f"Vercel upstream error ({r.status_code})"}
        if r.status_code >= 400:
            try:
                return {"ok": False,
                        "error": f"Vercel error: {r.json().get('error', {}).get('message', r.text[:200])}"}
            except Exception:
                return {"ok": False, "error": f"Vercel error {r.status_code}"}
        return {"ok": True, "data": r.json()}
    except httpx.TimeoutException:
        return {"ok": False, "error": "Vercel request timed out"}
    except Exception as e:                                  # noqa: BLE001
        return {"ok": False, "error": f"Vercel call failed: {e!s}"}


async def _vercel_post(path: str, body: dict) -> dict:
    if not _token():
        return {"ok": False, "error": "VERCEL_API_TOKEN not configured"}
    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as c:
            r = await c.post(f"{VERCEL_API}{path}", headers=_headers(),
                             json=body)
        if r.status_code >= 400:
            try:
                msg = r.json().get('error', {}).get('message', r.text[:200])
            except Exception:
                msg = r.text[:200]
            return {"ok": False, "error": f"Vercel error: {msg}"}
        try:
            return {"ok": True, "data": r.json()}
        except Exception:
            return {"ok": True, "data": {"status": r.status_code}}
    except Exception as e:                                  # noqa: BLE001
        return {"ok": False, "error": f"Vercel call failed: {e!s}"}


async def _vercel_delete(path: str) -> dict:
    if not _token():
        return {"ok": False, "error": "VERCEL_API_TOKEN not configured"}
    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as c:
            r = await c.delete(f"{VERCEL_API}{path}", headers=_headers())
        if r.status_code >= 400:
            try:
                msg = r.json().get('error', {}).get('message', r.text[:200])
            except Exception:
                msg = r.text[:200]
            return {"ok": False, "error": f"Vercel error: {msg}"}
        return {"ok": True, "data": {"deleted": True, "status": r.status_code}}
    except Exception as e:                                  # noqa: BLE001
        return {"ok": False, "error": f"Vercel call failed: {e!s}"}


# ── SKILL 1: vercel_list_projects ─────────────────────────────────────

async def vercel_list_projects(ctx: dict, args: dict) -> dict:
    """List Vercel projects in the connected account."""
    limit = max(1, min(int(args.get("limit") or 10), 25))
    res = await _vercel_get("/v9/projects", params={"limit": limit})
    if not res["ok"]:
        await _audit(ctx, "vercel_list_projects", args, "failed", res["error"])
        return {"ok": False, "error": res["error"]}
    projects = res["data"].get("projects", []) or []
    out = [{
        "id":         p.get("id"),
        "name":       p.get("name"),
        "framework":  p.get("framework"),
        "updated_at": p.get("updatedAt"),
        "live_url":   (p.get("targets") or {}).get("production", {}).get("alias", [None])[0] if p.get("targets") else None,
    } for p in projects]
    await _audit(ctx, "vercel_list_projects", args, "ok",
                 f"{len(out)} projects")
    return {"ok": True, "count": len(out), "projects": out}


# ── SKILL 2: vercel_list_deployments ─────────────────────────────────

async def vercel_list_deployments(ctx: dict, args: dict) -> dict:
    """Recent deployments — optionally filter by project name/id."""
    project = (args.get("project") or "").strip()
    limit   = max(1, min(int(args.get("limit") or 10), 25))
    params: dict = {"limit": limit}
    if project:
        params["projectId" if project.startswith("prj_") else "app"] = project
    res = await _vercel_get("/v6/deployments", params=params)
    if not res["ok"]:
        await _audit(ctx, "vercel_list_deployments", args, "failed", res["error"])
        return {"ok": False, "error": res["error"]}
    deps = res["data"].get("deployments", []) or []
    out = [{
        "uid":          d.get("uid"),
        "name":         d.get("name"),
        "url":          d.get("url"),
        "state":        d.get("state"),       # READY | BUILDING | ERROR | QUEUED
        "ready":        d.get("ready"),
        "created_at":   d.get("created"),
        "creator":      (d.get("creator") or {}).get("username"),
        "target":       d.get("target"),      # 'production' | None (preview)
    } for d in deps]
    await _audit(ctx, "vercel_list_deployments", args, "ok",
                 f"{len(out)} deployments")
    return {"ok": True, "count": len(out), "deployments": out}


# ── SKILL 3: vercel_get_deployment_logs ──────────────────────────────

async def vercel_get_deployment_logs(ctx: dict, args: dict) -> dict:
    """Fetch build events for a deployment uid. Returns last ~60 events."""
    uid = (args.get("deployment_id") or "").strip()
    if not uid:
        return {"ok": False, "error": "deployment_id is required"}
    res = await _vercel_get(f"/v3/deployments/{uid}/events",
                            params={"direction": "backward", "limit": 60})
    if not res["ok"]:
        await _audit(ctx, "vercel_get_deployment_logs", args, "failed", res["error"])
        return {"ok": False, "error": res["error"]}
    raw = res["data"] if isinstance(res["data"], list) else res["data"].get("events", [])
    events = []
    for ev in raw[:60]:
        text = ev.get("payload", {}).get("text") or ev.get("text") or ""
        events.append({
            "type": ev.get("type"),
            "date": ev.get("date") or ev.get("created"),
            "text": (text or "")[:500],
        })
    await _audit(ctx, "vercel_get_deployment_logs", args, "ok",
                 f"{len(events)} events for {uid[:12]}…")
    return {"ok": True, "deployment_id": uid, "events": events}


# ── SKILL 4: vercel_get_project_details ──────────────────────────────

async def vercel_get_project_details(ctx: dict, args: dict) -> dict:
    """Full project info — framework, build settings, environment, git
    integration, latest production deployment."""
    name = (args.get("project") or "").strip()
    if not name:
        return {"ok": False, "error": "project name or id is required"}
    res = await _vercel_get(f"/v9/projects/{name}")
    if not res["ok"]:
        await _audit(ctx, "vercel_get_project_details", args, "failed", res["error"])
        return {"ok": False, "error": res["error"]}
    p = res["data"]
    summary = {
        "id":              p.get("id"),
        "name":            p.get("name"),
        "framework":       p.get("framework"),
        "node_version":    p.get("nodeVersion"),
        "build_command":   p.get("buildCommand"),
        "install_command": p.get("installCommand"),
        "output_dir":      p.get("outputDirectory"),
        "root_dir":        p.get("rootDirectory"),
        "git": {
            "type":    (p.get("link") or {}).get("type"),
            "repo":    (p.get("link") or {}).get("repo"),
            "branch":  (p.get("link") or {}).get("productionBranch"),
        } if p.get("link") else None,
        "production_alias": (p.get("alias") or [{}])[0].get("domain")
                            if p.get("alias") else None,
        "updated_at":      p.get("updatedAt"),
    }
    await _audit(ctx, "vercel_get_project_details", args, "ok", name)
    return {"ok": True, "project": summary}


# ── SKILL 5: vercel_list_env_vars ────────────────────────────────────

async def vercel_list_env_vars(ctx: dict, args: dict) -> dict:
    """List env var KEYS for a project (values NEVER returned — even to
    ORA — for security; if user wants the value, they must look at the
    Vercel dashboard directly)."""
    name = (args.get("project") or "").strip()
    if not name:
        return {"ok": False, "error": "project name or id is required"}
    res = await _vercel_get(f"/v9/projects/{name}/env",
                            params={"decrypt": "false"})
    if not res["ok"]:
        await _audit(ctx, "vercel_list_env_vars", args, "failed", res["error"])
        return {"ok": False, "error": res["error"]}
    envs = res["data"].get("envs", []) or []
    out = [{
        "key":     e.get("key"),
        "target":  e.get("target"),     # ['production','preview','development']
        "type":    e.get("type"),       # 'plain'|'secret'|'encrypted'
        "comment": (e.get("comment") or "")[:80],
        "updated_at": e.get("updatedAt"),
    } for e in envs]
    await _audit(ctx, "vercel_list_env_vars", args, "ok",
                 f"{len(out)} env keys for {name}")
    return {"ok": True, "project": name, "count": len(out), "env_vars": out}


# ── SKILL 6: vercel_list_domains ─────────────────────────────────────

async def vercel_list_domains(ctx: dict, args: dict) -> dict:
    """All custom domains across the connected account."""
    res = await _vercel_get("/v5/domains", params={"limit": 25})
    if not res["ok"]:
        await _audit(ctx, "vercel_list_domains", args, "failed", res["error"])
        return {"ok": False, "error": res["error"]}
    doms = res["data"].get("domains", []) or []
    out = [{
        "name":          d.get("name"),
        "verified":      d.get("verified"),
        "expires_at":    d.get("expiresAt"),
        "created_at":    d.get("createdAt"),
        "service_type":  d.get("serviceType"),
    } for d in doms]
    await _audit(ctx, "vercel_list_domains", args, "ok", f"{len(out)} domains")
    return {"ok": True, "count": len(out), "domains": out}


# ── SKILL 7: vercel_trigger_deploy_hook ──────────────────────────────

async def vercel_trigger_deploy_hook(ctx: dict, args: dict) -> dict:
    """Trigger a new deployment via a Vercel Deploy Hook URL.

    NOTE: This uses the deploy-hook pattern (no token consumption,
    safer scope). If the user doesn't paste a URL the founder default
    (`VERCEL_DEPLOY_HOOK_URL` env var) is used so ORA can redeploy
    the connected production project out-of-the-box. Hooks are not
    listable through the REST API.
    """
    url = (args.get("hook_url") or os.environ.get("VERCEL_DEPLOY_HOOK_URL") or "").strip()
    if not url.startswith("https://api.vercel.com/v1/integrations/deploy/"):
        return {"ok": False, "error": "hook_url must be a vercel.com deploy hook URL"}
    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as c:
            r = await c.post(url)
        if r.status_code >= 400:
            await _audit(ctx, "vercel_trigger_deploy_hook", args,
                         "failed", f"HTTP {r.status_code}")
            return {"ok": False, "error": f"Deploy hook returned HTTP {r.status_code}"}
        data = {}
        try:
            data = r.json()
        except Exception:
            pass
        await _audit(ctx, "vercel_trigger_deploy_hook", args,
                     "ok", data.get("job", {}).get("id", ""))
        return {"ok": True, "job": data.get("job"), "raw": data}
    except Exception as e:                                  # noqa: BLE001
        return {"ok": False, "error": f"Hook call failed: {e!s}"}


async def vercel_account_info(ctx: dict, args: dict) -> dict:
    """Who am I — verifies the connected Vercel account."""
    res = await _vercel_get("/v2/user")
    if not res["ok"]:
        await _audit(ctx, "vercel_account_info", args, "failed", res["error"])
        return {"ok": False, "error": res["error"]}
    u = res["data"].get("user") or res["data"] or {}
    out = {
        "id":          u.get("id"),
        "username":    u.get("username"),
        "email":       u.get("email"),
        "name":        u.get("name"),
        "plan":        (u.get("billing") or {}).get("plan"),
        "team_id":     u.get("defaultTeamId"),
        "created_at":  u.get("createdAt"),
    }
    await _audit(ctx, "vercel_account_info", args, "ok", out.get("email", ""))
    return {"ok": True, "account": out}


# ── SKILL 9: vercel_create_project (write, admin) ────────────────────

async def vercel_create_project(ctx: dict, args: dict) -> dict:
    """Create a new Vercel project, optionally linking a Git repo."""
    name = (args.get("name") or "").strip()
    if not name:
        return {"ok": False, "error": "name is required"}
    body: dict = {"name": name}
    if args.get("framework"):
        body["framework"] = args["framework"]
    if args.get("repo"):  # e.g. "owner/repo" on GitHub
        body["gitRepository"] = {
            "repo":  args["repo"],
            "type":  (args.get("git_provider") or "github").lower(),
        }
    if args.get("root_directory"):
        body["rootDirectory"] = args["root_directory"]
    if args.get("install_command"):
        body["installCommand"] = args["install_command"]
    res = await _vercel_post("/v11/projects", body)
    if not res["ok"]:
        await _audit(ctx, "vercel_create_project", args, "failed", res["error"])
        return {"ok": False, "error": res["error"]}
    p = res["data"]
    await _audit(ctx, "vercel_create_project", args, "ok", p.get("id", ""))
    return {"ok": True, "project_id": p.get("id"), "name": p.get("name"),
            "framework": p.get("framework"), "created_at": p.get("createdAt")}


# ── SKILL 10: vercel_pause_project (write, admin) ────────────────────

async def vercel_pause_project(ctx: dict, args: dict) -> dict:
    """Pause a Vercel project — production deployments stop serving
    traffic (return 503 DEPLOYMENT_PAUSED). Useful for halting spend on
    a runaway project or temporarily disabling a service."""
    pid = (args.get("project_id") or "").strip()
    if not pid:
        return {"ok": False, "error": "project_id is required"}
    team_q = ""
    if args.get("team_id"):
        team_q = f"?teamId={args['team_id']}"
    res = await _vercel_post(f"/v1/projects/{pid}/pause{team_q}", {})
    if not res["ok"]:
        await _audit(ctx, "vercel_pause_project", args, "failed", res["error"])
        return {"ok": False, "error": res["error"]}
    await _audit(ctx, "vercel_pause_project", args, "ok", pid)
    return {"ok": True, "paused": True, "project_id": pid}


# ── SKILL 11: vercel_resume_project (write, admin) ───────────────────

async def vercel_resume_project(ctx: dict, args: dict) -> dict:
    """Resume a previously paused Vercel project — production deployments
    serve traffic again. Counterpart to vercel_pause_project."""
    pid = (args.get("project_id") or "").strip()
    if not pid:
        return {"ok": False, "error": "project_id is required"}
    team_q = ""
    if args.get("team_id"):
        team_q = f"?teamId={args['team_id']}"
    res = await _vercel_post(f"/v1/projects/{pid}/unpause{team_q}", {})
    if not res["ok"]:
        await _audit(ctx, "vercel_resume_project", args, "failed", res["error"])
        return {"ok": False, "error": res["error"]}
    await _audit(ctx, "vercel_resume_project", args, "ok", pid)
    return {"ok": True, "paused": False, "project_id": pid}


# ── SKILL 12: vercel_add_domain (write, admin) ───────────────────────

async def vercel_add_domain(ctx: dict, args: dict) -> dict:
    """Add a custom domain to a Vercel project. After this call, the user
    must update their DNS records to point to Vercel; an SSL cert is
    provisioned automatically once DNS is verified."""
    pid    = (args.get("project_id") or "").strip()
    domain = (args.get("domain") or "").strip()
    if not pid or not domain:
        return {"ok": False, "error": "project_id and domain are required"}
    res = await _vercel_post(f"/v10/projects/{pid}/domains",
                             {"name": domain})
    if not res["ok"]:
        await _audit(ctx, "vercel_add_domain", args, "failed", res["error"])
        return {"ok": False, "error": res["error"]}
    d = res["data"]
    await _audit(ctx, "vercel_add_domain", args, "ok", domain)
    return {"ok": True, "domain": d.get("name") or domain,
            "verified": d.get("verified", False),
            "project_id": pid,
            "next_step": "Update DNS records — run vercel_list_domains to "
                         "see required A/CNAME entries"}


# ── SKILL 13: vercel_delete_project (write, admin — DESTRUCTIVE) ─────

async def vercel_delete_project(ctx: dict, args: dict) -> dict:
    """⚠ DESTRUCTIVE — permanently delete a Vercel project, including
    deployments, env vars, and (non-transferred) domains. Requires
    explicit `confirm: true` flag to prevent accidental loss."""
    pid = (args.get("project_id") or "").strip()
    if not pid:
        return {"ok": False, "error": "project_id is required"}
    if args.get("confirm") is not True:
        return {"ok": False,
                "error": "Refusing destructive op — pass {confirm: true} "
                         "to delete project " + pid}
    res = await _vercel_delete(f"/v9/projects/{pid}")
    if not res["ok"]:
        await _audit(ctx, "vercel_delete_project", args, "failed", res["error"])
        return {"ok": False, "error": res["error"]}
    await _audit(ctx, "vercel_delete_project", args, "ok", pid)
    return {"ok": True, "deleted": True, "project_id": pid}


# ─────────────────────────────────────────────────────────────────────
# Tool catalogue — exposed to ORA orchestrator
# ─────────────────────────────────────────────────────────────────────

VERCEL_TOOL_SPECS = [
    {
        "name": "vercel_account_info",
        "description": (
            "Show who's connected on Vercel — email, username, plan, default team. "
            "USE when the user asks 'whose vercel is connected' or before a "
            "destructive operation to confirm the right account."
        ),
        "args_spec": {},
    },
    {
        "name": "vercel_list_projects",
        "description": (
            "List Vercel projects in the connected account (most recently updated "
            "first). Returns id, name, framework, last update, production URL. "
            "USE when the user asks 'show my vercel projects' or before "
            "deploying / inspecting one."
        ),
        "args_spec": {
            "limit": "optional int — default 10, cap 25",
        },
    },
    {
        "name": "vercel_get_project_details",
        "description": (
            "Full info for one Vercel project — framework, build/install commands, "
            "node version, root dir, output dir, linked git repo & branch, "
            "production alias. USE before debugging a build or before recommending "
            "a vercel.json change."
        ),
        "args_spec": {
            "project": "string — Vercel project name OR id (prj_…)",
        },
    },
    {
        "name": "vercel_list_deployments",
        "description": (
            "Recent deployments across the account (or filtered to one project). "
            "Each row has uid, url, state (READY/BUILDING/ERROR/QUEUED), target "
            "(production|preview), creator, timestamp. USE to find a failing build "
            "before pulling its logs, or to verify a deploy went out."
        ),
        "args_spec": {
            "project": "optional string — project name OR id (prj_…) to filter",
            "limit":   "optional int — default 10, cap 25",
        },
    },
    {
        "name": "vercel_get_deployment_logs",
        "description": (
            "Build/runtime events for a single deployment. Returns up to 60 most "
            "recent events with type, timestamp, and 500-char text snippet. USE "
            "after vercel_list_deployments surfaces a deployment in state=ERROR, "
            "to diagnose the failure cause."
        ),
        "args_spec": {
            "deployment_id": "string — deployment uid from vercel_list_deployments",
        },
    },
    {
        "name": "vercel_list_env_vars",
        "description": (
            "List environment variable KEYS (no values, ever — security rule) for "
            "a Vercel project. Returns key, target environments (production/preview/"
            "development), type (plain/secret/encrypted). USE to verify required "
            "env vars exist before a deploy, or to discover what's configured."
        ),
        "args_spec": {
            "project": "string — Vercel project name OR id",
        },
    },
    {
        "name": "vercel_list_domains",
        "description": (
            "All custom domains in the connected Vercel account — name, verified, "
            "expiry, creation. USE when the user asks about their domains or "
            "wonders why a domain isn't working."
        ),
        "args_spec": {},
    },
    {
        "name": "vercel_trigger_deploy_hook",
        "description": (
            "Trigger a new deployment via a Vercel Deploy Hook URL. If no "
            "hook_url is passed, falls back to the founder-configured default "
            "(`VERCEL_DEPLOY_HOOK_URL` env var) so 'deploy now' just works. "
            "USE when the user says 'redeploy', 'ship to vercel', or after "
            "pushing a fix when CI isn't set up."
        ),
        "args_spec": {
            "hook_url": "optional string — vercel.com deploy hook URL; "
                        "omitted = use the founder default",
        },
    },
    {
        "name": "vercel_create_project",
        "description": (
            "Create a new Vercel project — optionally link a GitHub repo "
            "(`repo: 'owner/repo'`) and pick a framework. USE when the user "
            "says 'create a vercel project for X' or to scaffold deployment "
            "for a fresh repo. ADMIN-ONLY."
        ),
        "args_spec": {
            "name":            "string — project name (3-100 chars, kebab-case)",
            "framework":       "optional string — 'nextjs'|'vite'|'remix'|… (auto-detect if omitted)",
            "repo":            "optional string — 'owner/repo' on the git provider",
            "git_provider":    "optional 'github'|'gitlab'|'bitbucket' (default github)",
            "root_directory":  "optional string — monorepo subdir",
            "install_command": "optional string — override install command",
        },
    },
    {
        "name": "vercel_pause_project",
        "description": (
            "Pause a Vercel project — production deployments return "
            "503 DEPLOYMENT_PAUSED. Useful to halt runaway spend or "
            "temporarily disable a service without deleting it. ADMIN-ONLY."
        ),
        "args_spec": {
            "project_id": "string — Vercel project id (prj_…)",
            "team_id":    "optional string — team id if multi-team scope",
        },
    },
    {
        "name": "vercel_resume_project",
        "description": (
            "Resume a previously paused Vercel project — production "
            "deployments serve traffic again. Counterpart to "
            "vercel_pause_project. ADMIN-ONLY."
        ),
        "args_spec": {
            "project_id": "string — Vercel project id (prj_…)",
            "team_id":    "optional string — team id if multi-team scope",
        },
    },
    {
        "name": "vercel_add_domain",
        "description": (
            "Attach a custom domain to a Vercel project. SSL is "
            "auto-provisioned once DNS verifies. After calling, prompt "
            "the user to update their A/CNAME records — vercel_list_domains "
            "shows the required entries. ADMIN-ONLY."
        ),
        "args_spec": {
            "project_id": "string — Vercel project id (prj_…)",
            "domain":     "string — fully-qualified domain (e.g. 'app.aurem.dev')",
        },
    },
    {
        "name": "vercel_delete_project",
        "description": (
            "⚠ DESTRUCTIVE — permanently delete a Vercel project, including "
            "all deployments, env vars, and non-transferred domains. REQUIRES "
            "explicit `confirm: true` flag — without it the call refuses. "
            "ADMIN-ONLY. Use only after confirming intent with the user."
        ),
        "args_spec": {
            "project_id": "string — Vercel project id (prj_…)",
            "confirm":    "bool — MUST be true to authorise deletion",
        },
    },
]


VERCEL_TOOLS = {
    "vercel_account_info":         vercel_account_info,
    "vercel_list_projects":        vercel_list_projects,
    "vercel_get_project_details":  vercel_get_project_details,
    "vercel_list_deployments":     vercel_list_deployments,
    "vercel_get_deployment_logs":  vercel_get_deployment_logs,
    "vercel_list_env_vars":        vercel_list_env_vars,
    "vercel_list_domains":         vercel_list_domains,
    "vercel_trigger_deploy_hook":  vercel_trigger_deploy_hook,
    "vercel_create_project":       vercel_create_project,
    "vercel_pause_project":        vercel_pause_project,
    "vercel_resume_project":       vercel_resume_project,
    "vercel_add_domain":           vercel_add_domain,
    "vercel_delete_project":       vercel_delete_project,
}
