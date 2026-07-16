"""
services/vercel_platform_deploy.py — Iter 212m-233 — Phase 3

Platform-owned Vercel deployment for Personal Track projects. Sits on
top of the existing `services.vercel_skills` (which is founder-token
based) and adds two things:

  1. Per-user project namespacing: `aurem-{user_slug}-{project_slug}`
     so multiple Personal Track users can coexist on ONE Vercel Pro
     team without name collisions.

  2. Auto-link to the AUREM-org GitHub repo created in Phase 2 —
     `gitRepository: { repo: "aurem-apps/<name>", type: "github" }`
     so pushes to `main` trigger a Vercel build automatically. No
     manual "connect Vercel" step for the user.

Configuration
=============
Reuses `VERCEL_API_TOKEN` from `services.vercel_skills._token()`.
Optionally reads `AUREM_VERCEL_TEAM_ID` from env — set this to keep
Personal Track projects under a dedicated Vercel team (recommended
for billing separation from AUREM's own infra).

Fallback DNS
============
Every deployment currently lands on Vercel's default
`{project-name}.vercel.app`. Wildcard `*.aurem.app` DNS is a
separate backlog item (per founder note in the Phase 3 plan);
`_public_url()` centralises the URL builder so we can swap it later.

Public API
==========
    is_available() -> bool
    deploy_personal_track(user_id, project_id, github_full_name, framework, name) -> dict
    get_deploy_status(vercel_project_id) -> dict
    check_spend_alert(vercel_project_id) -> dict     # simple bandwidth guardrail
"""
# arch: allow-http — Vercel API calls are this module's purpose (iter 212m-233)
from __future__ import annotations

import logging
import os
import re
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_VERCEL_API = "https://api.vercel.com"
_TIMEOUT = 20.0

# Bandwidth threshold above which we alert + suspend. Default matches
# roughly what a viral Personal Track app on Hobby-like traffic would
# hit in a month. Configurable via env for team-specific tuning.
_DEFAULT_BANDWIDTH_ALERT_GB = 10.0
_DEFAULT_BANDWIDTH_KILL_GB  = 50.0


def _token() -> str:
    """Iter 212m-233 — Dedicated Personal-Track platform token.
    NEVER reuse the founder's shared VERCEL_API_TOKEN for
    Personal Track deploys (bills/rate-limits get mixed with
    AUREM's own infra). Requires AUREM_VERCEL_PLATFORM_TOKEN.
    """
    return (os.environ.get("AUREM_VERCEL_PLATFORM_TOKEN") or "").strip()


def _team_id() -> str:
    """Vercel Pro team id under which every Personal Track project lands.
    Required — no fallback to founder's personal account allowed."""
    return (os.environ.get("VERCEL_PLATFORM_TEAM_ID") or "").strip()


def is_available() -> bool:
    """Both the dedicated token AND the team id must be present.
    Router uses this to emit a 503 with setup instructions instead of
    silently falling back to a wrong Vercel identity."""
    return bool(_token() and _team_id())


def _headers() -> dict:
    return {"Authorization": f"Bearer {_token()}",
            "Content-Type":  "application/json"}


def _team_param() -> str:
    """Append `?teamId=...` when configured — required for team-scoped
    project creates so they land under the AUREM Pro team, not the
    founder's personal Hobby account."""
    tid = _team_id()
    return f"?teamId={tid}" if tid else ""


_SLUG_RX = re.compile(r"[^a-z0-9-]+")


def _slugify(raw: str) -> str:
    """Vercel project names: lowercase alphanumeric + `-`, ≤52 chars."""
    s = _SLUG_RX.sub("-", (raw or "").lower()).strip("-")
    return (s or "app")[:52]


def _project_name(user_id: str, project_slug: str) -> str:
    """Namespace: `aurem-{user_id_short}-{project_slug}`.

    Users don't see this — it's for admin/audit clarity in Vercel's UI.
    Trimmed so we always fit Vercel's 52-char cap even with long project
    names.
    """
    us = _slugify(user_id)[:12]
    ps = _slugify(project_slug)[:32]
    return f"aurem-{us}-{ps}"[:52]


def _public_url(project_name: str) -> str:
    """Where the deployed app will be reachable. Currently the raw
    Vercel default; a future migration to `*.aurem.app` custom
    domain lives here."""
    return f"https://{project_name}.vercel.app"


# ── Public API ──────────────────────────────────────────────────
async def deploy_personal_track(
    user_id:           str,
    project_id:        str,
    github_full_name:  str,          # "aurem-apps/user-app-abc"
    framework:         Optional[str] = None,
    display_name:      Optional[str] = None,
) -> dict:
    """Create a Vercel project under AUREM's team, linked to the
    AUREM-org GitHub repo. Vercel's default deployment webhook will
    build+ship the initial `main` commit automatically — no explicit
    deploy trigger required.

    Returns:
        { ok, vercel_project_id, name, live_url, dashboard_url, framework }
        or { ok: False, reason, detail }.
    """
    if not is_available():
        return {"ok": False, "reason": "vercel_platform_not_configured",
                "detail": ("Set AUREM_VERCEL_PLATFORM_TOKEN and "
                           "VERCEL_PLATFORM_TEAM_ID in backend/.env then "
                           "restart the backend.")}

    slug = _slugify(display_name or project_id)
    name = _project_name(user_id, slug)

    payload = {
        "name":  name,
        "gitRepository": {"repo": github_full_name, "type": "github"},
    }
    if framework:
        payload["framework"] = framework

    async with httpx.AsyncClient(timeout=_TIMEOUT) as cli:
        r = await cli.post(
            f"{_VERCEL_API}/v11/projects{_team_param()}",
            headers=_headers(), json=payload,
        )
    if r.status_code not in (200, 201):
        # Iter 212m-233 — Plain-language error for non-tech users.
        # Never surface the raw Vercel JSON — extract known codes
        # and produce a sentence a vibe-coder can understand.
        return {
            "ok": False,
            "reason": f"vercel_{r.status_code}",
            "user_message": _friendly_error(r.status_code, r.text),
            "detail": r.text[:400],
            "attempted_name": name,
        }
    p = r.json()
    logger.info("[vercel-deploy] created project name=%s id=%s repo=%s",
                p.get("name"), p.get("id"), github_full_name)
    return {
        "ok":                True,
        "vercel_project_id": p.get("id"),
        "name":              p.get("name"),
        "framework":         p.get("framework"),
        "live_url":          _public_url(p.get("name")),
        "dashboard_url":     f"https://vercel.com/{_team_id() or 'dashboard'}/{p.get('name')}",
    }


async def get_deploy_status(vercel_project_id: str) -> dict:
    """Return the latest deployment's state. Used to poll
    'still building' → 'ready' after materialization."""
    if not is_available():
        return {"ok": False, "reason": "vercel_not_configured"}
    async with httpx.AsyncClient(timeout=_TIMEOUT) as cli:
        r = await cli.get(
            f"{_VERCEL_API}/v6/deployments{_team_param()}"
            f"{'&' if _team_id() else '?'}projectId={vercel_project_id}&limit=1",
            headers=_headers(),
        )
    if r.status_code != 200:
        return {"ok": False, "reason": f"vercel_{r.status_code}",
                "detail": r.text[:200]}
    depls = r.json().get("deployments") or []
    if not depls:
        return {"ok": True, "state": "no_deploys_yet"}
    d = depls[0]
    return {
        "ok":       True,
        "state":    d.get("state") or d.get("readyState") or "unknown",
        "url":      d.get("url"),
        "created":  d.get("created"),
        "meta":     d.get("meta", {}),
    }


async def check_spend_alert(vercel_project_id: str) -> dict:
    """Iter 212m-233 — Real bandwidth guardrail (MUST-HAVE per Phase 3
    plan). Polls Vercel's usage API for the current month and
    classifies the project into: `ok` (< alert threshold),
    `alert` (crossed alert but not kill), or `kill` (crossed kill
    threshold — caller should pause the project immediately).

    Thresholds are configurable via env:
        VERCEL_BANDWIDTH_ALERT_GB   (default 10.0)
        VERCEL_BANDWIDTH_KILL_GB    (default 50.0)
    """
    if not is_available():
        return {"ok": False, "reason": "vercel_platform_not_configured"}

    alert_gb = float(os.environ.get("VERCEL_BANDWIDTH_ALERT_GB", _DEFAULT_BANDWIDTH_ALERT_GB))
    kill_gb  = float(os.environ.get("VERCEL_BANDWIDTH_KILL_GB",  _DEFAULT_BANDWIDTH_KILL_GB))

    # Vercel usage endpoint returns aggregated bandwidth for the project
    # in the current billing period.
    async with httpx.AsyncClient(timeout=_TIMEOUT) as cli:
        r = await cli.get(
            f"{_VERCEL_API}/v1/usage{_team_param()}"
            f"{'&' if _team_id() else '?'}projectId={vercel_project_id}",
            headers=_headers(),
        )
    if r.status_code != 200:
        return {"ok": True, "state": "usage_unavailable",
                "detail": r.text[:200], "http": r.status_code}

    body = r.json() or {}
    # Different plan tiers surface bandwidth under different keys —
    # try the two known shapes. Bytes → GB.
    bytes_used = 0
    for k in ("bandwidth", "totalBytes", "outbound", "edgeRequests"):
        v = body.get(k)
        if isinstance(v, (int, float)):
            bytes_used = int(v); break
    gb_used = bytes_used / (1024 ** 3) if bytes_used else 0.0

    state = "ok"
    if gb_used >= kill_gb:
        state = "kill"
    elif gb_used >= alert_gb:
        state = "alert"
    return {
        "ok":         True,
        "state":      state,
        "bandwidth_gb": round(gb_used, 3),
        "alert_gb":   alert_gb,
        "kill_gb":    kill_gb,
        "project_id": vercel_project_id,
    }


async def pause_project(vercel_project_id: str) -> dict:
    """Suspend a runaway project. Vercel exposes this via a PATCH on
    the project setting `paused=true`. Used by the scheduled spend
    guardrail once a project crosses the KILL threshold."""
    if not is_available():
        return {"ok": False, "reason": "vercel_platform_not_configured"}
    async with httpx.AsyncClient(timeout=_TIMEOUT) as cli:
        r = await cli.patch(
            f"{_VERCEL_API}/v9/projects/{vercel_project_id}{_team_param()}",
            headers=_headers(), json={"paused": True},
        )
    if r.status_code in (200, 201):
        logger.warning("[vercel-spend-guard] paused project %s", vercel_project_id)
        return {"ok": True, "paused": True, "project_id": vercel_project_id}
    return {"ok": False, "reason": f"vercel_{r.status_code}",
            "detail": r.text[:200]}


def _friendly_error(status_code: int, raw_body: str) -> str:
    """Translate Vercel API errors into plain language for non-tech users.
    Iter 212m-233 — Phase 3 requirement: never surface raw build logs."""
    if status_code == 409 or "already exists" in (raw_body or "").lower():
        return ("A project with this name already exists on our platform. "
                "We'll try a different name automatically.")
    if status_code == 403:
        return "AUREM's deployment slot on Vercel is temporarily restricted. Please try again shortly."
    if status_code == 402:
        return "AUREM's Vercel plan has hit its monthly limit. Our team has been notified."
    if status_code in (401, 400):
        return "Deployment couldn't be started. Our team is investigating."
    if status_code >= 500:
        return "Vercel is currently having issues. Deployment will be retried automatically."
    return "Something unexpected happened while starting your deployment. We'll retry shortly."


__all__ = [
    "is_available",
    "deploy_personal_track",
    "get_deploy_status",
    "check_spend_alert",
    "pause_project",
]
