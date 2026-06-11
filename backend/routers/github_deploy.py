"""
routers/github_deploy.py — Iter 123

HTTP surface around `services.github_deploy_service`. Lets customers
(and the founder via admin) connect a GitHub repo, push AUREM-generated
fixes as PRs, install the auto-deploy workflow, and receive deploy
result reports from their CI back into our deployments collection.

Routes
======
  POST /github-deploy/connect            (auth: user)   — store token
  GET  /github-deploy/status             (auth: user)   — connection status
  POST /github-deploy/push-fix           (auth: user)   — open PR with a fix
  GET  /github-deploy/pr-status          (auth: user)   — PR merge state
  POST /github-deploy/install-workflow   (auth: user)   — install .github/workflows
  POST /github-deploy/report             (auth: api_key) — customer CI → us

The `report` endpoint authenticates via the `api_key` field in the body,
NOT via a JWT — because it's called from the customer's GitHub Actions
runner, not a logged-in browser. The service layer does the lookup.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from services import github_deploy_service as gh
from cto_services.auth import current_dev

router = APIRouter(prefix="/github-deploy", tags=["github-deploy"])
logger = logging.getLogger(__name__)


def _tenant_id_of(user: dict) -> str:
    # Standardize on user_id as the tenant identifier across the app.
    return user.get("user_id") or user.get("email") or ""


# ── Request models ────────────────────────────────────────────────────

class ConnectBody(BaseModel):
    token: str = Field(..., min_length=10, max_length=512)
    repo:  Optional[str] = Field(None, pattern=r"^[\w.\-]+/[\w.\-]+$")


class PushFixBody(BaseModel):
    repo:            str = Field(..., pattern=r"^[\w.\-]+/[\w.\-]+$")
    fix_title:       str = Field(..., min_length=3, max_length=200)
    fix_description: str = Field(..., min_length=3, max_length=8000)
    file_path:       str = Field(..., min_length=1, max_length=500)
    file_content:    str = Field(..., min_length=1)
    base_branch:     str = Field("main", min_length=1, max_length=120)


class InstallWorkflowBody(BaseModel):
    repo:        str = Field(..., pattern=r"^[\w.\-]+/[\w.\-]+$")
    base_branch: str = Field("main", min_length=1, max_length=120)


class ReportBody(BaseModel):
    api_key:     str = Field(..., min_length=8, max_length=200)
    commit:      str = Field(..., min_length=7, max_length=64)
    status:      str = Field(..., pattern=r"^(success|failure|cancelled)$")
    repo:        str = Field(..., pattern=r"^[\w.\-]+/[\w.\-]+$")
    deployed_at: Optional[str] = None


# ── Endpoints ─────────────────────────────────────────────────────────

@router.post("/connect")
async def connect(body: ConnectBody, authorization: Optional[str] = Header(None)):
    user = await current_dev(authorization)
    tid = _tenant_id_of(user)
    if not tid:
        raise HTTPException(400, "no tenant id on user")
    result = await gh.connect_github(tid, body.token, body.repo)
    if not result.get("connected"):
        raise HTTPException(400, result.get("error", "connect failed"))
    return result


@router.get("/status")
async def status(authorization: Optional[str] = Header(None)):
    user = await current_dev(authorization)
    return await gh.get_connection_status(_tenant_id_of(user))


@router.post("/push-fix")
async def push_fix(body: PushFixBody, authorization: Optional[str] = Header(None)):
    user = await current_dev(authorization)
    tid = _tenant_id_of(user)
    result = await gh.push_fix(
        tenant_id=tid,
        repo=body.repo,
        fix_title=body.fix_title,
        fix_description=body.fix_description,
        file_path=body.file_path,
        file_content=body.file_content,
        base_branch=body.base_branch,
    )
    if not result.get("success"):
        raise HTTPException(400, result.get("error", "push failed"))
    return result


@router.get("/pr-status")
async def pr_status(
    pr_number: Optional[int] = None,
    repo: Optional[str] = None,
    authorization: Optional[str] = Header(None),
):
    user = await current_dev(authorization)
    res = await gh.get_pr_status(_tenant_id_of(user), pr_number=pr_number, repo=repo)
    if res.get("error"):
        raise HTTPException(404 if "No PR" in res["error"] else 400, res["error"])
    return res


@router.post("/install-workflow")
async def install_workflow(
    body: InstallWorkflowBody,
    authorization: Optional[str] = Header(None),
):
    user = await current_dev(authorization)
    res = await gh.ship_auto_deploy_workflow(
        tenant_id=_tenant_id_of(user),
        repo=body.repo,
        base_branch=body.base_branch,
    )
    if not (res.get("success") or res.get("already_installed")):
        raise HTTPException(400, res.get("error", "install failed"))
    return res


@router.post("/report")
async def deploy_report(body: ReportBody):
    """Public-ish: authenticated by api_key in body, not JWT.
    Called from the customer's GitHub Actions runner."""
    res = await gh.record_customer_deploy_report(
        api_key=body.api_key,
        commit=body.commit,
        status=body.status,
        repo=body.repo,
        deployed_at=body.deployed_at,
    )
    if not res.get("ok"):
        # Soft-record returns ok=False with soft_recorded=True; that's
        # not a client error — we still want to acknowledge so the
        # action doesn't retry.
        if res.get("soft_recorded"):
            return res
        raise HTTPException(400, res.get("error", "report rejected"))
    return res
