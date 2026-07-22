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

import asyncio
import hashlib
import json
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

# ── Iter 274 — GC-safe background task registry ─────────────────
# Python's asyncio only holds WEAK references to tasks created via
# create_task(). Without a strong ref, a low-priority background
# task can be garbage-collected mid-run. Stdlib docs explicitly
# call this out. Every T1.5 design-review task is registered here
# and self-removes on completion.
_BG_TASKS: set[asyncio.Task] = set()


def _spawn_bg(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _BG_TASKS.add(task)
    task.add_done_callback(_BG_TASKS.discard)
    return task


def _compute_files_hash(files: list[dict]) -> str:
    """Content-address of a file tree. Used by the T1.5 background
    review to guard against stale-verdict overwrites if the user
    hits /regenerate before the review completes."""
    payload = [{"p": (f or {}).get("path") or "",
                "c": (f or {}).get("content") or ""} for f in (files or [])]
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]

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


class TransferRepoBody(BaseModel):
    new_owner: str        # GitHub username or org name the user controls
    confirm:   bool = False


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


# ── Iter 274 — T1.5 design-review background runner ────────────
async def _run_design_review_bg(draft_id: str, user_id: str,
                                  brief: str, files: list[dict],
                                  files_hash_at_start: str) -> None:
    """Runs on the event loop, NOT the request worker. Writes the
    review verdict back to the draft ONLY IF the draft still holds
    the same `files_hash` — otherwise the user has already hit
    /regenerate and a fresher review is on its way; this stale
    verdict is discarded (matched_count=0 no-op)."""
    try:
        db = get_db()
        if db is None:
            logger.warning("[design_review] db unavailable for draft=%s",
                            draft_id)
            return
        from services.scaffold_design_review import verify_scaffold
        review = await verify_scaffold(
            db, draft_id=draft_id, brief=brief, files=files,
        )
        # Predicate-guarded write — protects against T3 race where
        # /regenerate has swapped `files` (and thus `files_hash`)
        # while this review was in flight.
        payload = {
            "verdict":         review["verdict"],
            "reason":          review.get("reason") or "",
            "user_message":    review.get("user_message") or "",
            "verifier_model":  review["verifier_model"],
            "latency_s":       review.get("latency_s"),
            "checked_at":      review["created_at"],
        }
        result = await db.scaffold_drafts.update_one(
            {"draft_id": draft_id, "user_id": user_id,
             "files_hash": files_hash_at_start},
            {"$set": {"design_review": payload}},
        )
        if result.matched_count == 0:
            logger.info(
                "[design_review] STALE — draft=%s hash changed during "
                "review; verdict=%r discarded (regenerate raced)",
                draft_id, review["verdict"])
        else:
            logger.info(
                "[design_review] draft=%s verdict=%r reason=%r",
                draft_id, review["verdict"], review.get("reason"))
    except Exception as e:                                # noqa: BLE001
        logger.warning("[design_review] bg task crashed for draft=%s: %r",
                        draft_id, e)


# ── Scaffold generation (Parliament wrapper) ─────────────────────
async def _generate_file_tree(
    brief: str,
    stack: str,
    user_id: str,
    draft_id: str,
) -> list[dict]:
    """Call Parliament in scaffold mode, return a file tree.

    Iter 212m-236 (Tier 2) — Parliament LLM is now the primary
    generator. It reads the user's brief + the stack's boilerplate
    skeleton and emits a customised file tree via a strict JSON
    contract. If ANY step of that call fails (network, quota,
    malformed JSON, empty response) we transparently fall back to
    the heuristic scaffolder below — the user still gets a runnable
    draft, just not customised to their brief.
    """
    ctx = build_virtual_bin_context(user_id, draft_id=draft_id)
    logger.info("[scaffold] draft=%s user=%s stack=%s brief=%r",
                draft_id, user_id, stack, brief[:80])

    # Iter 212m-236 — try Parliament first. Returns None on any
    # failure, which triggers the heuristic path below.
    try:
        from services.scaffold_llm import generate_scaffold_via_parliament
        llm_files = await generate_scaffold_via_parliament(
            brief=brief, stack=stack, user_id=user_id, draft_id=draft_id,
        )
    except Exception as e:                                # noqa: BLE001
        # Never let the LLM path crash the endpoint.
        logger.warning("[scaffold] LLM path errored: %r — fallback", e)
        llm_files = None

    if llm_files:
        # Always guarantee a README exists — some LLM outputs skip it.
        has_readme = any(
            (f.get("path", "").lower() == "readme.md") for f in llm_files
        )
        if not has_readme:
            llm_files.insert(0, {
                "path": "README.md",
                "content": (
                    f"# Personal Track Project\n\n"
                    f"**Idea:** {brief[:400]}\n\n"
                    f"**Stack:** {stack}\n\n"
                    f"Generated by AUREM CTO. Draft id: `{draft_id}`.\n"
                ),
            })
        return llm_files[:_MAX_FILES_PER_DRAFT]

    # ── Fallback: heuristic scaffolder (Iter 212m-231/232 behaviour) ──
    logger.info("[scaffold] using heuristic fallback for draft=%s", draft_id)
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
        # Iter 212m-236/238 — Real Next.js boilerplate with httpOnly-cookie
        # JWT auth, refresh tokens, rate-limiting, and enumeration-safe
        # password reset. `docker compose up` after materialize gives
        # a full production-shaped auth flow out of the box.
        files.extend([
            {"path": "docker-compose.yml",
             "content": _load_template("nextjs-node/docker-compose.yml")},
            {"path": "package.json",
             "content": _load_template("nextjs-node/boilerplate/package.json")},
            {"path": "lib/aurem-db.js",
             "content": _load_template("nextjs-node/boilerplate/lib/aurem-db.js")},
            {"path": "lib/auth.js",
             "content": _load_template("nextjs-node/boilerplate/lib/auth.js")},
            {"path": "app/page.jsx",
             "content": _load_template("nextjs-node/boilerplate/app/page.jsx")},
            {"path": "app/api/auth/signup/route.js",
             "content": _load_template("nextjs-node/boilerplate/app/api/auth/signup/route.js")},
            {"path": "app/api/auth/login/route.js",
             "content": _load_template("nextjs-node/boilerplate/app/api/auth/login/route.js")},
            {"path": "app/api/auth/refresh/route.js",
             "content": _load_template("nextjs-node/boilerplate/app/api/auth/refresh/route.js")},
            {"path": "app/api/auth/logout/route.js",
             "content": _load_template("nextjs-node/boilerplate/app/api/auth/logout/route.js")},
            {"path": "app/api/auth/me/route.js",
             "content": _load_template("nextjs-node/boilerplate/app/api/auth/me/route.js")},
            {"path": "app/api/auth/password-reset-request/route.js",
             "content": _load_template("nextjs-node/boilerplate/app/api/auth/password-reset-request/route.js")},
            {"path": "app/api/auth/password-reset-confirm/route.js",
             "content": _load_template("nextjs-node/boilerplate/app/api/auth/password-reset-confirm/route.js")},
            {"path": ".env.example",
             "content": ("AUREM_API_BASE=https://api.auremcto.com\n"
                         "AUREM_APP_ID=pt_replace_at_materialize_time\n"
                         "AUREM_APP_TOKEN=eyJ_your_app_token\n"
                         "JWT_SECRET=change_me_use_a_long_random_string\n"
                         "FRONTEND_URL=http://localhost:3000\n"
                         "APP_ENV=development\n")},
            {"path": ".gitignore",
             "content": "node_modules/\n.next/\n.env\n.env.local\n"},
        ])
    elif stack == "vue-express":
        # Iter 212m-236 — Real Vue + Express boilerplate. Server exposes
        # /api/auth/signup, /api/auth/login, /api/auth/logout, /api/auth/me
        # with bcrypt hashing + JWT httpOnly cookies. UI is Vue 3 + Vite.
        files.extend([
            {"path": "docker-compose.yml",
             "content": _load_template("vue-express/docker-compose.yml")},
            {"path": "server/package.json",
             "content": _load_template("vue-express/boilerplate/server/package.json")},
            {"path": "server/aurem-db.js",
             "content": _load_template("vue-express/boilerplate/server/aurem-db.js")},
            {"path": "server/index.js",
             "content": _load_template("vue-express/boilerplate/server/index.js")},
            {"path": "ui/package.json",
             "content": _load_template("vue-express/boilerplate/ui/package.json")},
            {"path": "ui/vite.config.js",
             "content": _load_template("vue-express/boilerplate/ui/vite.config.js")},
            {"path": "ui/src/main.js",
             "content": _load_template("vue-express/boilerplate/ui/src/main.js")},
            {"path": "ui/src/App.vue",
             "content": _load_template("vue-express/boilerplate/ui/src/App.vue")},
            {"path": "ui/index.html",
             "content": ('<!doctype html><html><head><meta charset="utf-8">'
                         '<title>My App</title></head><body>'
                         '<div id="app"></div>'
                         '<script type="module" src="/src/main.js"></script>'
                         '</body></html>\n')},
            {"path": ".env.example",
             "content": ("AUREM_API_BASE=https://api.auremcto.com\n"
                         "AUREM_APP_ID=pt_replace_at_materialize_time\n"
                         "AUREM_APP_TOKEN=eyJ_your_app_token\n"
                         "JWT_SECRET=change_me_use_a_long_random_string\n"
                         "FRONTEND_URL=http://localhost:3000\n")},
            {"path": ".gitignore",
             "content": "node_modules/\ndist/\n.env\n"},
        ])
    else:  # plain-html
        # Iter 212m-236 — Real static-HTML boilerplate. Uses AUREM's
        # managed-db-auth REST proxy directly from the browser — no
        # backend to run.
        files.extend([
            {"path": "docker-compose.yml",
             "content": _load_template("plain-html/docker-compose.yml")},
            {"path": "index.html",
             "content": _load_template("plain-html/boilerplate/index.html")},
            {"path": "main.js",
             "content": _load_template("plain-html/boilerplate/main.js")},
            {"path": "style.css",
             "content": _load_template("plain-html/boilerplate/style.css")},
            {"path": ".env.example",
             "content": ("AUREM_API_BASE=https://api.auremcto.com\n"
                         "AUREM_APP_ID=pt_replace_at_materialize_time\n")},
            {"path": ".gitignore",
             "content": ".env\n.DS_Store\n"},
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

    # Tier 4 gate — daily scaffold-drafts cap (per plan). Founder bypass.
    from services.personal_track_quotas import enforce_daily_rate_or_429
    quota = await enforce_daily_rate_or_429(db, user, "scaffold_drafts_per_day")

    stack = _detect_stack(body.brief, body.stack_preference)
    draft_id = uuid.uuid4().hex[:16]
    now = time.time()

    files = await _generate_file_tree(
        body.brief, stack, user["user_id"], draft_id,
    )
    files_hash = _compute_files_hash(files)

    draft = {
        "draft_id":         draft_id,
        "user_id":          user["user_id"],
        "brief":            body.brief,
        "stack_preference": body.stack_preference,
        "stack_detected":   stack,
        "files":            files,
        "files_hash":       files_hash,
        "status":           "draft",
        "created_at":       now,
        "updated_at":       now,
        "materialized_repo": None,
        "design_review":    None,     # populated by T1.5 bg task
    }
    await _write_draft(db, draft)

    # ── Iter 274 T1.5 — design review runs in background so the
    # T1→T2 preview promise (~60-90s in E2B) is not lengthened.
    # Client re-fetches the draft when opening the preview panel;
    # by then this ~3-8s review has usually landed.
    _spawn_bg(_run_design_review_bg(
        draft_id, user["user_id"], body.brief, files, files_hash,
    ))

    logger.info("[scaffold] new draft created: %s user=%s stack=%s files=%d",
                draft_id, user["user_id"], stack, len(files))

    return {
        "draft_id":        draft_id,
        "stack_detected":  stack,
        "files":           files,
        "truncated":       len(files) >= _MAX_FILES_PER_DRAFT,
        "expires_at":      now + _DRAFT_TTL_SECONDS,
        "quota":           quota,
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

    # Tier 4 — regenerate counts against the same daily cap.
    from services.personal_track_quotas import enforce_daily_rate_or_429
    await enforce_daily_rate_or_429(db, user, "scaffold_drafts_per_day")

    draft = await _read_draft(db, draft_id, user["user_id"])
    if not draft:
        raise HTTPException(404, "Draft not found or expired")

    new_brief = (body.brief_refinement or draft["brief"]).strip()[:2000]
    new_stack = _detect_stack(new_brief, body.stack_preference or draft.get("stack_preference"))
    new_files = await _generate_file_tree(
        new_brief, new_stack, user["user_id"], draft_id,
    )
    new_hash = _compute_files_hash(new_files)
    now = time.time()

    await db.scaffold_drafts.update_one(
        {"draft_id": draft_id, "user_id": user["user_id"]},
        {"$set": {
            "brief":          new_brief,
            "stack_detected": new_stack,
            "files":          new_files,
            "files_hash":     new_hash,
            "design_review":  None,     # invalidate previous verdict
            "updated_at":     now,
        }},
    )
    # Iter 274 — spawn a fresh T1.5 review on the new files. Any
    # earlier in-flight review from before /regenerate will attempt
    # to write with its OLD files_hash predicate → matched_count=0
    # → dropped as stale.
    _spawn_bg(_run_design_review_bg(
        draft_id, user["user_id"], new_brief, new_files, new_hash,
    ))
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

    files = draft.get("files") or []
    if not files:
        raise HTTPException(400, "Draft has no files to materialize")

    # ── Iter 274 T4 QA GATE — held-out design review ─────────────
    # Runs a FRESH scaffold_design_review.verify_scaffold() call on
    # the (possibly-iterated) final files. Runs BEFORE the security
    # gate + BEFORE any AUREM-owned resource is created.
    # Failure modes:
    #   verdict="no"        → 422 hard-block with plain-English
    #                          user_message (founder override still works
    #                          via the existing override_active field —
    #                          same override mechanism as security gate,
    #                          nothing new to configure).
    #   verdict="skipped_no_llm" → 503 fail-CLOSED, retryable=True.
    #                          Personal Track is destructive (real repo,
    #                          real Vercel deploy), so we do NOT ship
    #                          scaffolds on unverified network hiccups.
    #                          User just clicks materialize again.
    override_active = bool(draft.get("override_active"))
    if not override_active:
        from services.scaffold_design_review import verify_scaffold as _verify
        qa = await _verify(
            db, draft_id=draft_id, brief=draft.get("brief") or "",
            files=files,
        )
        if qa["verdict"] == "skipped_no_llm":
            raise HTTPException(
                status_code=503,
                detail={
                    "reason":       "qa_reviewer_unavailable",
                    "user_message": qa.get("user_message") or (
                        "Our quality reviewer is temporarily "
                        "unavailable. Please try again in a minute."),
                    "retryable":    True,
                    "raw_error":    qa.get("reason"),
                },
            )
        if qa["verdict"] == "no":
            await db.scaffold_drafts.update_one(
                {"draft_id": draft_id, "user_id": user["user_id"]},
                {"$set": {
                    "status":          "blocked_by_qa",
                    "qa_block_reason": qa.get("reason"),
                    "qa_user_message": qa.get("user_message"),
                    "qa_blocked_at":   time.time(),
                }},
            )
            logger.warning(
                "[materialize] BLOCKED by QA gate: draft=%s user=%s "
                "reason=%r", draft_id, user["user_id"], qa.get("reason"))
            raise HTTPException(
                status_code=422,
                detail={
                    "reason":         "qa_review_failed",
                    "user_message":   qa.get("user_message") or (
                        "Your app doesn't fully match your "
                        "description yet. Try clicking regenerate."),
                    "technical_reason": qa.get("reason"),
                    "override_hint":  ("Founders can bypass via POST "
                                       "/scaffold/{draft_id}/founder-override "
                                       "(audit-logged)."),
                },
            )
        # verdict == "yes" → fall through to security gate.
    else:
        logger.info("[materialize] QA gate skipped — override_active for "
                    "draft=%s user=%s", draft_id, user["user_id"])

    # ── Step 2.5 — SECURITY GATE (Iter 212m-237, Lovable-hardened) ──
    # Runs BEFORE any AUREM-owned resource is created (repo, project,
    # deploy) AND before the org-config 503 check — so a scan block
    # is visible to the user even if some downstream integration
    # isn't configured. Retroactive by construction — every
    # materialize call re-runs it, so purane drafts jab redeploy
    # ho toh they must also pass.
    from services.scaffold_security_gate import (
        scan_files as _scan_files,
        friendly_user_message as _friendly_scan_msg,
    )
    scan = await _scan_files(files)
    if not scan["ok"] and not override_active:
        # Mark the draft as blocked so the frontend can surface the
        # right state and the founder can inspect from admin.
        await db.scaffold_drafts.update_one(
            {"draft_id": draft_id, "user_id": user["user_id"]},
            {"$set": {
                "status":             "blocked_by_scan",
                "scan_summary":       scan["summary"],
                "scan_blocked_at":    time.time(),
                "scan_findings_snapshot": scan["findings"][:50],
            }},
        )
        logger.warning(
            "[materialize] BLOCKED by security gate: draft=%s user=%s summary=%s",
            draft_id, user["user_id"], scan["summary"],
        )
        raise HTTPException(
            status_code=422,
            detail={
                "reason":         "security_scan_failed",
                "user_message":   _friendly_scan_msg(scan["summary"]),
                "summary":        scan["summary"],
                "override_hint":  ("Founders can bypass via POST "
                                   "/scaffold/{draft_id}/founder-override "
                                   "(audit-logged)."),
            },
        )
    if override_active and not scan["ok"]:
        logger.warning(
            "[materialize] OVERRIDE-BYPASS: draft=%s user=%s reason=%r summary=%s",
            draft_id, user["user_id"], draft.get("override_reason"), scan["summary"],
        )
    # Medium findings are logged but do not block — user's approved
    # threshold policy.
    if scan["summary"].get("medium", 0) > 0:
        logger.info("[materialize] draft=%s advisory medium findings: %d",
                    draft_id, scan["summary"]["medium"])

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


# ── Iter 212m-239 — Live preview (Sandpack-first, E2B for react-fastapi) ──
@router.post("/{draft_id}/preview")
async def create_live_preview(
    draft_id: str,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Spin up an E2B preview for react-fastapi drafts.

    JS-based stacks (nextjs-node, vue-express, plain-html) preview
    in-browser via Sandpack — they don't hit this endpoint at all.
    react-fastapi (Python) needs a real interpreter → E2B.

    Idempotent per (draft_id): returns the existing live sandbox
    if one exists and hasn't expired.  Records the sandbox in
    `db.preview_sandboxes` so `sweep_expired_previews()` can
    clean it up on TTL expiry.
    """
    user = await current_dev(authorization)
    db = get_db()
    if db is None:
        raise HTTPException(503, "Database not connected")

    draft = await _read_draft(db, draft_id, user["user_id"])
    if not draft:
        raise HTTPException(404, "Draft not found or expired")
    stack = draft.get("stack_detected")
    if stack != "react-fastapi":
        raise HTTPException(
            400,
            {"reason": "wrong_stack",
             "detail": f"E2B preview is only for react-fastapi. "
                       f"{stack} stacks preview client-side via Sandpack."},
        )

    from services import preview_sandbox as ps
    if not ps.is_configured():
        raise HTTPException(
            status_code=503,
            detail={
                "reason":  "e2b_not_configured",
                "message": ("Live preview requires E2B_API_KEY in "
                            "backend/.env. Get one free at https://e2b.dev."),
            },
        )

    # Reuse an existing live sandbox if we have one.
    existing = await db[ps.PREVIEW_COLLECTION].find_one(
        {"draft_id": draft_id, "user_id": user["user_id"], "killed": {"$ne": True}},
    )
    now = time.time()
    if existing and (existing.get("expires_at") or 0) > now + 60:
        return {
            "ok":         True,
            "reused":     True,
            "sandbox_id": existing["sandbox_id"],
            "url":        existing.get("url"),
            "expires_at": existing.get("expires_at"),
        }

    created = await ps.create_preview_sandbox(draft_id, draft.get("files") or [])
    if not created.get("ok"):
        raise HTTPException(502, detail=created)
    await db[ps.PREVIEW_COLLECTION].update_one(
        {"draft_id": draft_id, "user_id": user["user_id"]},
        {"$set": {
            "draft_id":   draft_id,
            "user_id":    user["user_id"],
            "sandbox_id": created["sandbox_id"],
            "url":        created["url"],
            "expires_at": created["expires_at"],
            "created_at": now,
            "killed":     False,
        }},
        upsert=True,
    )
    return {"ok": True, "reused": False, **{
        k: created[k] for k in ("sandbox_id", "url", "expires_at")
    }}


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


# ── Iter 212m-240 (Tier 3) — Transfer AUREM-org repo to user's account ──
@router.post("/{project_id}/transfer-repo")
async def transfer_repo(
    project_id: str,
    body:       TransferRepoBody,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Transfer a materialized Personal Track repo from AUREM's org to
    the user's own GitHub account.

    Requirements:
      - Caller owns the project.
      - Caller is on a tier with `transfer_ownership=True` (Starter+).
      - `body.confirm=True` — transfer is a one-way action.

    GitHub transfers require the receiving account to accept the invite,
    so we return `transfer_pending=True` and flip
    `cto_projects.repo_transferred=True` as an audit marker.
    """
    user = await current_dev(authorization)
    db = get_db()
    if db is None:
        raise HTTPException(503, "Database not connected")

    from services.personal_track_quotas import enforce_feature_or_402
    await enforce_feature_or_402(db, user, "transfer_ownership")

    proj = await db.cto_projects.find_one({
        "project_id":     project_id,
        "user_id":        user["user_id"],
        "personal_track": True,
    })
    if not proj:
        raise HTTPException(404, "Project not found or not owned by caller")
    if proj.get("repo_transferred"):
        return {"ok": True, "already_done": True,
                "transferred_to": proj.get("repo_transferred_to")}

    if not body.confirm:
        raise HTTPException(
            400,
            {"reason": "confirmation_required",
             "user_message": "Repo transfer is irreversible. Set confirm=true to proceed."},
        )

    from services.github_org_client import (
        is_configured as _org_configured,
        transfer_repo_to_user,
    )
    if not _org_configured():
        raise HTTPException(
            503,
            {"reason": "aurem_org_not_configured",
             "message": "Founder must configure AUREM_ORG_NAME and AUREM_ORG_GITHUB_APP_TOKEN."},
        )

    repo_name = proj.get("github_repo")
    if not repo_name:
        raise HTTPException(400, "Project has no repo to transfer")

    result = await transfer_repo_to_user(repo_name, body.new_owner)
    if not result.get("ok"):
        raise HTTPException(502, detail=result)

    now = time.time()
    await db.cto_projects.update_one(
        {"project_id": project_id, "user_id": user["user_id"]},
        {"$set": {
            "repo_transferred":     True,
            "repo_transferred_to":  body.new_owner.strip(),
            "repo_transferred_at":  now,
            "updated_at":           now,
        }},
    )
    logger.warning(
        "[scaffold] REPO TRANSFERRED: project=%s repo=%s → %s (user=%s)",
        project_id, repo_name, body.new_owner, user["user_id"],
    )
    return {
        "ok":               True,
        "transfer_pending": True,
        "transferred_to":   body.new_owner.strip(),
        "user_message":     result.get("user_message"),
    }


# ── Iter 212m-240 (Tier 4) — Daily quota status ──────────────────
@router.get("/quota/status")
async def quota_status(
    authorization: Optional[str] = Header(None),
) -> dict:
    """Return the caller's current daily scaffold-drafts quota — used
    by the Build UI to show "You have N drafts left today" nudges.
    """
    user = await current_dev(authorization)
    db = get_db()
    if db is None:
        raise HTTPException(503, "Database not connected")

    from services.personal_track_quotas import (
        get_user_tier, get_numeric_limit, is_founder,
        _COUNTER_COLLECTION, _today_utc_date,
    )
    if is_founder(user):
        return {"tier": "founder", "used": 0, "limit": None,
                "remaining": None, "unlimited": True}
    tier  = await get_user_tier(db, user["user_id"])
    limit = get_numeric_limit(tier, "scaffold_drafts_per_day")
    if limit is None:
        return {"tier": tier, "used": 0, "limit": None,
                "remaining": None, "unlimited": True}
    row = await db[_COUNTER_COLLECTION].find_one(
        {"user_id": user["user_id"], "feature": "scaffold_drafts_per_day",
         "date_key": _today_utc_date()},
        {"count": 1, "_id": 0},
    ) or {}
    used = int(row.get("count") or 0)
    return {"tier":      tier,
            "used":      used,
            "limit":     limit,
            "remaining": max(0, limit - used),
            "unlimited": False}



# ── Iter 212m-237 — Founder-only security-gate override ──────────
class FounderOverrideBody(BaseModel):
    reason: str = Field(..., min_length=8, max_length=500)


@router.post("/{draft_id}/founder-override")
async def founder_override(
    draft_id: str,
    body:     FounderOverrideBody,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Force-approve a draft that was blocked by the security gate.

    Founder-only (checks `is_founder=True`).  Never mutates the
    draft's files — only writes an override record that
    `materialize_draft` will check before re-running the gate.
    Every override is audit-logged to `db.scaffold_scan_overrides`
    so we have a paper trail of every bypass, forever.

    Requires a `reason` string (min 8 chars) so we never have
    unexplained bypasses in the audit log.
    """
    user = await current_dev(authorization)
    if not (user.get("is_founder") or user.get("is_admin")):
        raise HTTPException(403, "Founder / admin only.")
    db = get_db()
    if db is None:
        raise HTTPException(503, "Database not connected")

    draft = await _read_draft(db, draft_id, user["user_id"])
    if not draft:
        raise HTTPException(404, "Draft not found or expired")
    if draft.get("status") != "blocked_by_scan":
        raise HTTPException(
            400,
            {"reason": "not_blocked",
             "detail": "This draft was not blocked by the security gate."},
        )

    now = time.time()
    # Audit log (append-only) — indexed by draft_id + user_id.
    await db.scaffold_scan_overrides.insert_one({
        "draft_id":            draft_id,
        "draft_user_id":       user["user_id"],
        "overridden_by":       user["user_id"],
        "overridden_by_email": user.get("email"),
        "reason":              body.reason.strip()[:500],
        "findings_snapshot":   draft.get("scan_findings_snapshot") or [],
        "summary_snapshot":    draft.get("scan_summary") or {},
        "created_at":          now,
    })
    # Flip the draft back to `draft` status so materialize can pick it
    # up.  materialize_draft will still re-run the scan gate on the
    # next call — the override is checked in that flow (below).
    await db.scaffold_drafts.update_one(
        {"draft_id": draft_id, "user_id": user["user_id"]},
        {"$set": {
            "status":              "draft",
            "override_active":     True,
            "override_at":         now,
            "override_reason":     body.reason.strip()[:500],
        }, "$unset": {
            "scan_summary":       "",
            "scan_blocked_at":    "",
        }},
    )
    logger.warning(
        "[scaffold] FOUNDER OVERRIDE: draft=%s reason=%r by=%s",
        draft_id, body.reason[:80], user["user_id"],
    )
    return {"ok": True, "draft_id": draft_id, "override_active": True,
            "next_step": "POST /scaffold/{draft_id}/materialize now proceeds."}


# ── Iter 212m-237 — LLM health diagnostic (founder-only) ─────────
@router.get("/admin/llm-health")
async def llm_health(
    authorization: Optional[str] = Header(None),
) -> dict:
    """Canary probe that fires a tiny scaffold prompt through the
    Parliament LLM path so the founder can see immediately post-deploy
    whether real customised generation is firing or whether the
    endpoint is silently falling back to the heuristic.

    Returns:
        {
            ok:            bool,
            llm_reachable: bool,       # True iff the LLM returned a parseable payload
            file_count:    int,        # how many files the canary produced
            model_used:    str | None,
            fallback:      bool,       # True iff generate_scaffold_via_parliament returned None
            elapsed_ms:    int,
        }
    """
    user = await current_dev(authorization)
    if not (user.get("is_founder") or user.get("is_admin")):
        raise HTTPException(403, "Founder / admin only.")
    from services.scaffold_llm import generate_scaffold_via_parliament
    import time as _t
    t0 = _t.time()
    try:
        files = await generate_scaffold_via_parliament(
            brief="A very tiny hello-world app with a signup form.",
            stack="react-fastapi",
            user_id=user["user_id"],
            draft_id="canary_llm_health",
        )
    except Exception as e:                                 # noqa: BLE001
        return {
            "ok":            False,
            "llm_reachable": False,
            "file_count":    0,
            "model_used":    None,
            "fallback":      True,
            "error":         type(e).__name__,
            "elapsed_ms":    int((_t.time() - t0) * 1000),
        }
    elapsed = int((_t.time() - t0) * 1000)
    if not files:
        return {
            "ok":            True,      # Fallback still constitutes a working system
            "llm_reachable": False,
            "file_count":    0,
            "model_used":    None,
            "fallback":      True,
            "elapsed_ms":    elapsed,
        }
    return {
        "ok":            True,
        "llm_reachable": True,
        "file_count":    len(files),
        "model_used":    "parliament",   # actual model attribution is in the accounting log
        "fallback":      False,
        "elapsed_ms":    elapsed,
    }



# ── Iter 212m-240 — Personal Track admin panel data endpoints ────
@router.get("/admin/blocked-drafts")
async def list_blocked_drafts(
    authorization: Optional[str] = Header(None),
) -> dict:
    """List every draft the security gate blocked. Founder uses this
    to spot systemic false-positives and drives the override CTA."""
    user = await current_dev(authorization)
    if not (user.get("is_founder") or user.get("is_admin")):
        raise HTTPException(403, "Founder / admin only.")
    db = get_db()
    if db is None:
        raise HTTPException(503, "DB not connected")
    cursor = db.scaffold_drafts.find(
        {"status": "blocked_by_scan"},
        {"draft_id": 1, "user_id": 1, "brief": 1, "stack_detected": 1,
         "scan_summary": 1, "scan_blocked_at": 1, "override_active": 1,
         "_id": 0},
    ).sort("scan_blocked_at", -1).limit(100)
    rows = []
    async for r in cursor:
        r["brief"] = (r.get("brief") or "")[:200]
        rows.append(r)
    return {"ok": True, "count": len(rows), "rows": rows}


@router.get("/admin/personal-projects")
async def list_personal_projects(
    limit: int = 50,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Consolidated list of materialized Personal Track projects with
    live-URL, deploy state, and Supabase-tier status."""
    user = await current_dev(authorization)
    if not (user.get("is_founder") or user.get("is_admin")):
        raise HTTPException(403, "Founder / admin only.")
    db = get_db()
    if db is None:
        raise HTTPException(503, "DB not connected")
    cursor = db.cto_projects.find(
        {"personal_track": True},
        {"_id": 0, "project_id": 1, "user_id": 1, "name": 1,
         "github_owner": 1, "github_repo": 1, "stack": 1, "live_url": 1,
         "storage_tier": 1, "supabase_ref": 1,
         "repo_transferred": 1, "repo_transferred_to": 1,
         "created_at": 1, "vercel_project_id": 1},
    ).sort("created_at", -1).limit(max(1, min(int(limit), 200)))
    rows = [r async for r in cursor]
    return {"ok": True, "count": len(rows), "rows": rows}


@router.get("/admin/draft-summary")
async def draft_summary(
    authorization: Optional[str] = Header(None),
) -> dict:
    """Counts of drafts by status (draft, materialized, blocked_by_scan)
    for the admin dashboard headline widget."""
    user = await current_dev(authorization)
    if not (user.get("is_founder") or user.get("is_admin")):
        raise HTTPException(403, "Founder / admin only.")
    db = get_db()
    if db is None:
        raise HTTPException(503, "DB not connected")
    pipeline = [
        {"$group": {"_id": "$status", "n": {"$sum": 1}}},
    ]
    counts: dict[str, int] = {}
    async for row in db.scaffold_drafts.aggregate(pipeline):
        counts[row["_id"] or "unknown"] = row["n"]
    projects = await db.cto_projects.count_documents({"personal_track": True})
    return {
        "ok":                   True,
        "drafts_by_status":     counts,
        "personal_projects":    projects,
    }


@router.post("/admin/smoke-test")
async def run_infra_smoke_test(
    cleanup: bool = True,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Founder-only: run the full Personal Track infra pipeline with a
    throwaway smoke project. Per-step pass/fail so a bad token points
    at exactly one step. See services/personal_track_smoke.py."""
    user = await current_dev(authorization)
    if not (user.get("is_founder") or user.get("is_admin")):
        raise HTTPException(403, "Founder / admin only.")
    db = get_db()
    if db is None:
        raise HTTPException(503, "DB not connected")
    from services.personal_track_smoke import run_smoke
    result = await run_smoke(db, user, cleanup=cleanup)
    await db.smoke_test_runs.insert_one(
        {**result, "user_id": user["user_id"]}
    )
    result.pop("_id", None)
    return result


@router.get("/admin/revenue-snapshot")
async def revenue_snapshot(
    authorization: Optional[str] = Header(None),
) -> dict:
    """MRR snapshot from dev_users tiers — first signal that billing
    gates work (a bug treating paid users as free shows up here)."""
    user = await current_dev(authorization)
    if not (user.get("is_founder") or user.get("is_admin")):
        raise HTTPException(403, "Founder / admin only.")
    db = get_db()
    if db is None:
        raise HTTPException(503, "DB not connected")
    from services.subscription_tiers import plan_price
    pipeline = [{"$group": {"_id": "$tier", "n": {"$sum": 1}}}]
    by_tier: dict[str, int] = {}
    async for row in db.dev_users.aggregate(pipeline):
        by_tier[row["_id"] or "free"] = row["n"]
    mrr = 0
    paid_users = 0
    breakdown = {}
    for tier, n in by_tier.items():
        try:
            price = plan_price(tier)
        except Exception:
            price = 0
        breakdown[tier] = {"users": n, "price_monthly": price, "mrr": price * n}
        mrr += price * n
        if price > 0:
            paid_users += n
    stripe_linked = await db.dev_users.count_documents(
        {"stripe_customer_id": {"$exists": True, "$nin": [None, ""]}}
    )
    return {
        "ok":             True,
        "mrr_usd":        mrr,
        "paid_users":     paid_users,
        "total_users":    sum(by_tier.values()),
        "stripe_linked":  stripe_linked,
        "by_tier":        breakdown,
    }
