"""
routers/scaffold.py — Iter 212m-231 — Personal Track blank-slate endpoint.

Endpoint set for "no-repo, idea-only" users:
    POST /api/aurem-dev/scaffold/new-project    → create a draft
    GET  /api/aurem-dev/scaffold/{draft_id}     → fetch a draft
    POST /api/aurem-dev/scaffold/{draft_id}/regenerate  → re-run scaffold
    POST /api/aurem-dev/scaffold/{draft_id}/materialize → Phase-2 hook (stub for now)
    DELETE /api/aurem-dev/scaffold/{draft_id}   → abandon a draft

Draft store: `db.scaffold_drafts` with a 48-hour TTL index on
`created_at` so abandoned drafts get GC'd without a cron. Never
touches GitHub in Phase 1 — that's Phase 2 (materialize step).

Draft schema:
    draft_id:         str  (uuid hex, 16 chars)
    user_id:          str
    brief:            str  (user's original idea, ≤ 2000 chars)
    stack_preference: str | None
    stack_detected:   str  (react-fastapi / nextjs-node / vue-express / plain-html)
    files:            [{"path": str, "content": str}, ...]
    status:           "draft" | "materialized" | "abandoned"
    created_at:       float epoch
    updated_at:       float epoch
    materialized_repo: {"owner": str, "name": str, "html_url": str} | None
"""
# arch: allow-http — Draft creation triggers Parliament LLM calls (iter 212m-231)
from __future__ import annotations

import logging
import time
import uuid
from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from cto_services.auth import current_dev
from cto_services.db import get_db
from services.bin_context import build_virtual_bin_context

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/scaffold", tags=["Scaffold — Personal Track"])

# Iter 212m-231 — Component-count cap so a single scaffold pass doesn't
# spiral into a 40-file mega-project the LLM handles poorly. If the
# generated plan proposes more, we truncate to the top-N and mark
# `truncated=True` in the response so the UI can offer a follow-up
# generation round.
_MAX_FILES_PER_DRAFT = 20

# Draft TTL — MongoDB TTL index expires after this many seconds.
_DRAFT_TTL_SECONDS = 48 * 3600

# Default stack when the LLM can't confidently classify.
_DEFAULT_STACK = "react-fastapi"

_ALLOWED_STACKS = ("react-fastapi", "nextjs-node", "vue-express", "plain-html")


# ── Request / response models ────────────────────────────────────
class NewProjectBody(BaseModel):
    brief: str = Field(..., min_length=10, max_length=2000)
    stack_preference: Optional[str] = None


class RegenerateBody(BaseModel):
    brief_refinement: Optional[str] = None
    stack_preference: Optional[str] = None


# ── Draft store helpers ──────────────────────────────────────────
async def _ensure_ttl_index(db) -> None:
    """Create a TTL index on `scaffold_drafts.created_at` (idempotent).
    MongoDB's TTL monitor sweeps expired drafts every 60 s."""
    try:
        await db.scaffold_drafts.create_index(
            "created_at",
            expireAfterSeconds=_DRAFT_TTL_SECONDS,
            name="scaffold_drafts_ttl",
        )
    except Exception as e:      # noqa: BLE001
        logger.warning("[scaffold] TTL index create failed: %r", e)


async def _write_draft(db, draft: dict) -> None:
    await _ensure_ttl_index(db)
    await db.scaffold_drafts.update_one(
        {"draft_id": draft["draft_id"]},
        {"$set": draft},
        upsert=True,
    )


async def _read_draft(db, draft_id: str, user_id: str) -> Optional[dict]:
    return await db.scaffold_drafts.find_one(
        {"draft_id": draft_id, "user_id": user_id},
        {"_id": 0},
    )


# ── Scaffold generation (Parliament wrapper) ─────────────────────
async def _generate_file_tree(
    brief: str,
    stack: str,
    user_id: str,
    draft_id: str,
) -> list[dict]:
    """Call Parliament in scaffold mode, return a file tree.

    Iter 212m-231 — Phase 1 implementation uses a heuristic scaffolder
    that produces a minimal but runnable skeleton for the chosen stack.
    Full LLM-driven customisation (interpreting the user's `brief` into
    real routes, models, and UI) is deferred to Phase 2 where we also
    fill in real boilerplate for each template stack.

    The function signature is stable — Phase 2 can swap the
    implementation to a full Parliament call without touching callers.
    """
    ctx = build_virtual_bin_context(user_id, draft_id=draft_id)
    logger.info("[scaffold] draft=%s user=%s stack=%s brief=%r",
                draft_id, user_id, stack, brief[:80])

    # Heuristic file tree — every stack ships a working README + docker
    # compose + minimal frontend/backend entry points. Phase 2 replaces
    # these with fully filled-out boilerplate from
    # backend/templates/stacks/{stack}/.
    files: list[dict] = []

    files.append({
        "path": "README.md",
        "content": (
            f"# Personal Track Project\n\n"
            f"**Idea:** {brief[:400]}\n\n"
            f"**Stack:** {stack}\n\n"
            f"Generated by AUREM CTO. Draft id: `{draft_id}`.\n\n"
            "This is a starter skeleton. Run `docker compose up` after "
            "materialising to the real repo.\n"
        ),
    })

    if stack == "react-fastapi":
        # Iter 212m-232 — Real boilerplate loaded from
        # backend/templates/stacks/react-fastapi/boilerplate/.
        # Each file is a genuine, runnable component (FastAPI CRUD +
        # bcrypt-hashed JWT auth on the backend; React + sonner +
        # lucide-react on the frontend).  A user can `git clone`
        # the materialized repo and `docker compose up` immediately.
        files.extend([
            {"path": "docker-compose.yml",
             "content": _load_template("react-fastapi/docker-compose.yml")},
            {"path": "api/main.py",
             "content": _load_template("react-fastapi/boilerplate/api/main.py")},
            {"path": "api/auth.py",
             "content": _load_template("react-fastapi/boilerplate/api/auth.py")},
            {"path": "api/aurem_db_client.py",
             "content": _load_template("react-fastapi/boilerplate/api/aurem_db_client.py")},
            {"path": "api/requirements.txt",
             "content": _load_template("react-fastapi/boilerplate/api/requirements.txt")},
            {"path": "api/.env.example",
             "content": ("# Iter 212m-233 — Personal Track generated app uses\n"
                         "# AUREM's managed shared MongoDB via a scoped REST\n"
                         "# API — no raw Mongo connection string in your code.\n"
                         "AUREM_API_BASE=https://api.auremcto.com\n"
                         "AUREM_APP_ID=pt_replace_at_materialize_time\n"
                         "AUREM_APP_TOKEN=eyJ_your_app_token\n"
                         "JWT_SECRET=change_me_use_a_long_random_string\n"
                         "FRONTEND_URL=http://localhost:3000\n")},
            {"path": "ui/src/App.jsx",
             "content": _load_template("react-fastapi/boilerplate/ui/src/App.jsx")},
            {"path": "ui/package.json",
             "content": _load_template("react-fastapi/boilerplate/ui/package.json")},
            {"path": "ui/index.html",
             "content": ('<!doctype html><html><head><meta charset="utf-8">'
                         '<title>My App</title></head><body>'
                         '<div id="root"></div>'
                         '<script type="module" src="/src/main.jsx"></script>'
                         '</body></html>\n')},
            {"path": "ui/src/main.jsx",
             "content": ("import React from 'react';\n"
                         "import { createRoot } from 'react-dom/client';\n"
                         "import App from './App.jsx';\n"
                         "createRoot(document.getElementById('root')).render(<App />);\n")},
            {"path": "ui/vite.config.js",
             "content": ("import { defineConfig } from 'vite';\n"
                         "import react from '@vitejs/plugin-react';\n"
                         "export default defineConfig({ plugins: [react()], "
                         "server: { host: '0.0.0.0', port: 3000 } });\n")},
            {"path": ".gitignore",
             "content": ("node_modules/\n__pycache__/\n*.pyc\n.env\n"
                         "dist/\nbuild/\n.venv/\n")},
        ])
    elif stack == "nextjs-node":
        files.extend([
            {"path": "docker-compose.yml", "content": _load_template("nextjs-node/docker-compose.yml")},
            {"path": "app/page.tsx", "content": (
                "export default function Home() {\n"
                "  return <main><h1>Personal Track — Next.js</h1></main>;\n"
                "}\n"
            )},
            {"path": "package.json", "content": (
                '{\n  "name": "personal-track-nextjs",\n  "version": "0.1.0",\n'
                '  "scripts": {"dev": "next dev", "build": "next build", "start": "next start"},\n'
                '  "dependencies": {"next": "^14.0.0", "react": "^18.2.0", "react-dom": "^18.2.0"}\n}\n'
            )},
        ])
    elif stack == "vue-express":
        files.extend([
            {"path": "docker-compose.yml", "content": _load_template("vue-express/docker-compose.yml")},
            {"path": "server/index.js", "content": (
                "const express = require('express');\n"
                "const app = express();\n"
                "app.get('/api/health', (req, res) => res.json({ok: true}));\n"
                "app.listen(3001);\n"
            )},
            {"path": "ui/src/App.vue", "content": (
                "<template><h1>Personal Track — Vue</h1></template>\n"
            )},
        ])
    else:  # plain-html
        files.extend([
            {"path": "docker-compose.yml", "content": _load_template("plain-html/docker-compose.yml")},
            {"path": "index.html", "content": (
                "<!doctype html><html><head><meta charset='utf-8'>"
                f"<title>Personal Track</title></head>"
                f"<body><h1>Your idea, live.</h1>"
                f"<p>{brief[:200]}</p></body></html>\n"
            )},
        ])

    if len(files) > _MAX_FILES_PER_DRAFT:
        logger.warning("[scaffold] truncating file tree from %d → %d",
                       len(files), _MAX_FILES_PER_DRAFT)
        files = files[:_MAX_FILES_PER_DRAFT]

    return files


def _load_template(rel_path: str) -> str:
    """Load a template file from backend/templates/stacks/. Fails
    silently to an empty string — Phase 2 replaces these with real
    boilerplate loading."""
    import os
    try:
        base = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "templates", "stacks",
        )
        with open(os.path.join(base, rel_path), "r") as f:
            return f.read()
    except Exception:
        return ""


def _detect_stack(brief: str, preference: Optional[str]) -> str:
    """Rule-based stack picker. Preference wins if valid; otherwise
    infer from keywords in the brief."""
    if preference and preference in _ALLOWED_STACKS:
        return preference
    low = (brief or "").lower()
    if any(k in low for k in ("next", "nextjs", "next.js", "server-side render")):
        return "nextjs-node"
    if any(k in low for k in ("vue", "vuejs", "nuxt")):
        return "vue-express"
    if any(k in low for k in ("landing page", "one-page", "static", "brochure")):
        return "plain-html"
    return _DEFAULT_STACK


# ── Endpoints ────────────────────────────────────────────────────
@router.post("/new-project")
async def create_new_project(
    body: NewProjectBody,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Create a Personal Track draft.

    Phase 1 scope: generate a file tree in memory + persist to
    `db.scaffold_drafts`. NO GitHub repo is created — that's Phase 2's
    materialize step.
    """
    user = await current_dev(authorization)
    db = get_db()
    if db is None:
        raise HTTPException(503, "Database not connected")

    stack = _detect_stack(body.brief, body.stack_preference)
    draft_id = uuid.uuid4().hex[:16]
    now = time.time()

    files = await _generate_file_tree(
        body.brief, stack, user["user_id"], draft_id,
    )

    draft = {
        "draft_id":         draft_id,
        "user_id":          user["user_id"],
        "brief":            body.brief,
        "stack_preference": body.stack_preference,
        "stack_detected":   stack,
        "files":            files,
        "status":           "draft",
        "created_at":       now,
        "updated_at":       now,
        "materialized_repo": None,
    }
    await _write_draft(db, draft)

    logger.info("[scaffold] new draft created: %s user=%s stack=%s files=%d",
                draft_id, user["user_id"], stack, len(files))

    return {
        "draft_id":        draft_id,
        "stack_detected":  stack,
        "files":           files,
        "truncated":       len(files) >= _MAX_FILES_PER_DRAFT,
        "expires_at":      now + _DRAFT_TTL_SECONDS,
        "next_step":       "review + POST /scaffold/{draft_id}/materialize",
    }


@router.get("/{draft_id}")
async def get_draft(
    draft_id: str,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Fetch a draft for review. Returns 404 if the draft doesn't exist
    OR belongs to another user (never leak existence)."""
    user = await current_dev(authorization)
    db = get_db()
    if db is None:
        raise HTTPException(503, "Database not connected")

    draft = await _read_draft(db, draft_id, user["user_id"])
    if not draft:
        raise HTTPException(404, "Draft not found or expired")
    return draft


@router.post("/{draft_id}/regenerate")
async def regenerate_draft(
    draft_id: str,
    body: RegenerateBody,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Regenerate the file tree with an optional brief refinement.
    Overwrites the existing draft's files. Keeps the same draft_id."""
    user = await current_dev(authorization)
    db = get_db()
    if db is None:
        raise HTTPException(503, "Database not connected")

    draft = await _read_draft(db, draft_id, user["user_id"])
    if not draft:
        raise HTTPException(404, "Draft not found or expired")

    new_brief = (body.brief_refinement or draft["brief"]).strip()[:2000]
    new_stack = _detect_stack(new_brief, body.stack_preference or draft.get("stack_preference"))
    new_files = await _generate_file_tree(
        new_brief, new_stack, user["user_id"], draft_id,
    )
    now = time.time()

    await db.scaffold_drafts.update_one(
        {"draft_id": draft_id, "user_id": user["user_id"]},
        {"$set": {
            "brief":          new_brief,
            "stack_detected": new_stack,
            "files":          new_files,
            "updated_at":     now,
        }},
    )
    return {
        "draft_id":       draft_id,
        "stack_detected": new_stack,
        "files":          new_files,
        "truncated":      len(new_files) >= _MAX_FILES_PER_DRAFT,
    }


@router.post("/{draft_id}/materialize")
async def materialize_draft(
    draft_id: str,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Phase-2 entry point — create the real AUREM-owned GitHub repo,
    push the draft's files, register as a Personal Track project.

    Flow:
      1. Load the draft (404 on wrong user / expired).
      2. Guard: refuse to materialize a draft that's already been
         materialized (idempotent — returns the existing repo URL).
      3. `create_org_repo` on AUREM's GitHub org with a slug derived
         from the brief.  Collision retry: on 422 we append `-N`.
      4. `push_files_bulk` — pushes every file in the draft to the new
         repo on the default branch.
      5. On any partial-push failure, call `delete_org_repo` to unwind
         so we never leave orphan half-empty repos in the org.
      6. Register the project in `cto_projects` tagged
         `personal_track=True` so downstream deploy/billing logic
         knows this is a Personal Track project.
      7. Mark the draft `status="materialized"` + attach the repo info.
    """
    from services.github_org_client import (
        is_configured as _org_configured,
        create_org_repo, push_files_bulk, delete_org_repo,
        sanitize_repo_name,
    )
    user = await current_dev(authorization)
    db = get_db()
    if db is None:
        raise HTTPException(503, "Database not connected")
    draft = await _read_draft(db, draft_id, user["user_id"])
    if not draft:
        raise HTTPException(404, "Draft not found or expired")

    # Idempotent — if already materialized, just return the existing repo.
    if draft.get("status") == "materialized" and draft.get("materialized_repo"):
        return {
            "ok":           True,
            "already_done": True,
            "draft_id":     draft_id,
            "repo":         draft["materialized_repo"],
            "project_id":   draft.get("project_id"),
        }

    if not _org_configured():
        raise HTTPException(
            status_code=503,
            detail={
                "reason":       "aurem_org_not_configured",
                "message":      "AUREM GitHub Org token missing. Founder must "
                                "set AUREM_ORG_NAME and AUREM_ORG_GITHUB_APP_TOKEN "
                                "in backend/.env and restart the backend.",
                "docs":         "See backend/services/github_org_client.py",
            },
        )

    files = draft.get("files") or []
    if not files:
        raise HTTPException(400, "Draft has no files to materialize")

    # Repo name: use user's own slug + short id so collisions are rare.
    # Draft-id suffix guarantees uniqueness inside the org.
    user_slug = sanitize_repo_name(
        (user.get("email") or user.get("user_id") or "user").split("@")[0]
    )[:30]
    base_slug = sanitize_repo_name(draft["brief"][:40])[:40] or "app"
    repo_name = f"{user_slug}-{base_slug}-{draft_id[:8]}"[:90]

    # ── Step 3: create the repo ────────────────────────────────
    created = await create_org_repo(
        name=repo_name,
        description=f"AUREM Personal Track — {draft['brief'][:200]}",
        private=True,
    )
    if not created.get("ok"):
        # 422 collision — try one suffix; anything else is a hard fail.
        if created.get("reason") == "github_422":
            import uuid as _uuid
            repo_name = f"{repo_name[:80]}-{_uuid.uuid4().hex[:6]}"
            created = await create_org_repo(
                name=repo_name,
                description=f"AUREM Personal Track — {draft['brief'][:200]}",
                private=True,
            )
        if not created.get("ok"):
            raise HTTPException(
                status_code=502,
                detail={"reason": "org_repo_create_failed", "github": created},
            )

    real_repo_name = created["name"]

    # ── Step 4: push all files ────────────────────────────────
    push = await push_files_bulk(
        repo_name=real_repo_name,
        files=files,
        commit_message=f"[AUREM CTO] initial scaffold for draft {draft_id}",
        branch=created.get("default_branch") or "main",
    )
    if not push.get("ok"):
        # ── Step 5: unwind on partial failure ──────────────────
        logger.warning("[materialize] partial push failure — unwinding repo %s",
                       real_repo_name)
        await delete_org_repo(real_repo_name)
        raise HTTPException(
            status_code=502,
            detail={
                "reason":     "file_push_failed",
                "pushed":     push.get("pushed"),
                "failed":     push.get("failed"),
                "results":    push.get("results"),
                "note":       "Repo was created and then deleted so no orphans remain.",
            },
        )

    # ── Step 6: register as a Personal Track project ───────────
    now = time.time()
    from uuid import uuid4 as _uuid4
    project_id = f"pt_{_uuid4().hex[:16]}"
    project_doc = {
        "project_id":       project_id,
        "user_id":          user["user_id"],
        "name":             draft["brief"][:80],
        "github_owner":     created["full_name"].split("/", 1)[0],
        "github_repo":      real_repo_name,
        "branch":           created.get("default_branch") or "main",
        "github_token":     "",   # personal-track repos use org token, not per-user PAT
        "stack":            draft.get("stack_detected"),
        "personal_track":   True,
        "materialized_from_draft": draft_id,
        "created_at":       now,
        "updated_at":       now,
    }
    await db.cto_projects.insert_one(project_doc)

    # ── Step 7: mark the draft materialized ────────────────────
    materialized_repo = {
        "owner":     created["full_name"].split("/", 1)[0],
        "name":      real_repo_name,
        "full_name": created["full_name"],
        "html_url":  created["html_url"],
        "clone_url": created["clone_url"],
    }
    await db.scaffold_drafts.update_one(
        {"draft_id": draft_id, "user_id": user["user_id"]},
        {"$set": {
            "status":            "materialized",
            "materialized_repo": materialized_repo,
            "project_id":        project_id,
            "materialized_at":   now,
            "updated_at":        now,
        }},
    )

    logger.info("[materialize] SUCCESS: draft=%s repo=%s project_id=%s files=%d",
                draft_id, real_repo_name, project_id, push.get("pushed"))

    # ── Step 8: Phase-3 auto-deploy (best-effort, non-blocking) ──
    # If VERCEL_PLATFORM_TEAM_ID + AUREM_VERCEL_PLATFORM_TOKEN are set,
    # kick off a Vercel deploy right after materialization. Failures
    # here don't roll back the materialize — the repo is still valid,
    # user can retry deploy separately.
    deploy_result: dict = {"attempted": False}
    try:
        from services.vercel_platform_deploy import (
            is_available as _v_ok, deploy_personal_track,
        )
        if _v_ok():
            framework = "vite" if draft.get("stack_detected") == "react-fastapi" else None
            deploy_result = await deploy_personal_track(
                user_id=user["user_id"],
                project_id=project_id,
                github_full_name=created["full_name"],
                framework=framework,
                display_name=draft.get("brief", "")[:32],
            )
            deploy_result["attempted"] = True
            if deploy_result.get("ok"):
                await db.cto_projects.update_one(
                    {"project_id": project_id},
                    {"$set": {
                        "vercel_project_id": deploy_result.get("vercel_project_id"),
                        "live_url":          deploy_result.get("live_url"),
                    }},
                )
        else:
            deploy_result["skipped_reason"] = "vercel_platform_not_configured"
    except Exception as e:                                    # noqa: BLE001
        logger.warning("[materialize] deploy step failed: %r", e)
        deploy_result["error"] = str(e)[:200]

    return {
        "ok":           True,
        "draft_id":     draft_id,
        "project_id":   project_id,
        "repo":         materialized_repo,
        "files_pushed": push.get("pushed"),
        "deploy":       deploy_result,
        "next_step":    ("Your app is being deployed — check /projects for the live URL"
                         if deploy_result.get("ok")
                         else "Repo created. Deploy will retry once Vercel is configured."),
    }


@router.delete("/{draft_id}")
async def abandon_draft(
    draft_id: str,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Explicitly abandon a draft (frees the TTL slot early)."""
    user = await current_dev(authorization)
    db = get_db()
    if db is None:
        raise HTTPException(503, "Database not connected")
    res = await db.scaffold_drafts.delete_one(
        {"draft_id": draft_id, "user_id": user["user_id"]},
    )
    return {"ok": True, "deleted": res.deleted_count > 0}
