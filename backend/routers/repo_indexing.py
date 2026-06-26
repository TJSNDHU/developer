"""
routers/repo_indexing.py — `POST /api/aurem-dev/repos/{repo_id}/index`

Triggers `services.repo_indexing.build_repo_index()` for a project the
authenticated user owns. The route is intentionally narrow:

  - Auth required (Bearer JWT).
  - `repo_id` corresponds to `cto_projects.project_id`.
  - Default is to commit the rendered CODEBASE.md back to GitHub;
    `?commit=false` runs the analysis without pushing (useful for tests
    or for showing the user what would happen).
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from cto_services.auth import current_dev
from services.repo_indexing import build_repo_index

router = APIRouter(prefix="/repos", tags=["Repo Indexing"])


@router.post("/{repo_id:path}/index")
async def index_repo(
    repo_id: str,
    commit: bool = True,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Build (or rebuild) the CODEBASE.md index for the project whose
    `project_id == repo_id`. Returns the full index record on success
    or a 4xx with the `errors` list as `detail` when the project can't
    be found or the analysis bailed."""
    me = await current_dev(authorization)
    result = await build_repo_index(
        user_id=me["user_id"], project_id=repo_id, commit=bool(commit),
    )
    if not result.get("ok"):
        # 404 if the project is unknown; 502 for upstream failures so
        # the frontend can distinguish "fix your project" from "try again".
        errs = result.get("errors") or ["unknown error"]
        joined = "; ".join(errs)
        if "not found" in joined or "not owned" in joined:
            raise HTTPException(404, joined)
        if "no github_owner" in joined:
            raise HTTPException(400, joined)
        raise HTTPException(502, joined)
    return result
