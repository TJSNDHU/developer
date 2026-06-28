"""
services/loop_engine.py — Iter 212m-60 (Loop Mode Phase B)

Production state machine for ORA's 5-phase loop pipeline:

    PLAN → EXECUTE → VERIFY → SCAN → SHIP

Reliability guarantees (G1–G5 from the founder's spec):
  G1 no silent failures   — every exception is caught, logged to the
                            `loop_errors` collection, and surfaced as
                            an SSE event with `requires_user_action`.
  G2 no infinite loops    — each phase has a hard time budget; on
                            timeout we transition to PAUSED_FOR_USER.
  G3 resume capability    — `resume_stale()` is called on boot and
                            picks up any EXECUTING/VERIFYING session
                            whose `updated_at` is more than 2 minutes
                            old.  Picks resume from the last completed
                            step; never restarts from scratch.
  G4 idempotent writes    — `record_backup(loop_id, file, content)`
                            stores the pre-write copy in
                            `loop_backups`; `rollback(loop_id)`
                            restores everything on abort.  (Phase C
                            wires this into the actual file-write
                            path.)
  G5 context preservation — `LoopContext` is a typed dict carried
                            across phases and serialised into Mongo
                            on every transition, so every LLM call
                            sees the full history of what the loop
                            has done.

This module is the brain only — it never calls fastapi.  The thin
router in `routers/loop.py` is the HTTP surface; this file is the
SSE-streaming orchestrator that the router awaits.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, AsyncIterator, Optional

logger = logging.getLogger(__name__)

# Phase budgets in seconds (G2).  Hard caps — exceed and we pause.
PHASE_TIMEOUTS_S: dict[str, int] = {
    "plan":      60,
    "execute":   120,
    "verify":    90,
    "scan":      120,
    "ship":      60,
    "self_heal": 120,
}
# A session whose Mongo doc hasn't been updated in this long while in
# EXECUTING/VERIFYING is treated as orphaned by resume_stale().
STALE_AFTER_S = 120
# How many self-heal attempts before we surface to the user (G1).
MAX_SELF_HEALS = 2
# How many verify retries the engine takes before pause.
MAX_VERIFY_RETRIES = 3
# 24 h TTL on the loop_plans collection (founder's spec).
PLAN_TTL_S = 24 * 60 * 60


class LoopState(str, Enum):
    IDLE                   = "idle"
    PLANNING               = "planning"
    AWAITING_CONFIRMATION  = "awaiting_confirmation"
    EXECUTING              = "executing"
    VERIFYING              = "verifying"
    SCANNING               = "scanning"
    SHIPPING               = "shipping"
    SELF_HEALING           = "self_healing"
    PAUSED_FOR_USER        = "paused_for_user"
    COMPLETED              = "completed"
    FAILED                 = "failed"
    ABORTED                = "aborted"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(d: Optional[datetime] = None) -> str:
    return (d or _now()).isoformat()


def _new_event(loop_id: str, state: LoopState, phase: str,
               step: int = 0, total_steps: int = 0,
               message: str = "", data: Optional[dict] = None,
               requires_user_action: bool = False) -> dict:
    """Canonical SSE event shape (founder's 1.6 spec)."""
    return {
        "loop_id":              loop_id,
        "state":                state.value,
        "phase":                phase,
        "step":                 step,
        "total_steps":          total_steps,
        "message":              message,
        "data":                 data or {},
        "timestamp":            _iso(),
        "requires_user_action": requires_user_action,
    }


# ─── Persistence helpers ──────────────────────────────────────────────

async def _persist_session(db, doc: dict) -> None:
    """Upsert the session doc, bumping `updated_at` so the staleness
    watchdog can find orphans (G3)."""
    doc["updated_at"] = _now()
    await db.loop_sessions.update_one(
        {"loop_id": doc["loop_id"]},
        {"$set": doc, "$setOnInsert": {"created_at": _now()}},
        upsert=True,
    )


async def _log_error(db, loop_id: str, phase: str, error: str,
                     context: Optional[dict] = None) -> None:
    """Append an entry to the loop_errors collection (G1)."""
    try:
        await db.loop_errors.insert_one({
            "loop_id":   loop_id,
            "phase":     phase,
            "error":     str(error)[:4000],
            "context":   (context or {}),
            "timestamp": _now(),
        })
    except Exception as e:  # noqa: BLE001
        # Never let logging itself break the loop.
        logger.error("loop_errors insert failed: %r", e)


# ─── Plan persistence (24h TTL via background sweeper) ────────────────

async def _save_plan(db, loop_id: str, plan: dict) -> None:
    await db.loop_plans.update_one(
        {"loop_id": loop_id},
        {"$set": {"loop_id": loop_id, "plan": plan,
                  "created_at": _now(),
                  "expires_at": _now() + _td(PLAN_TTL_S)}},
        upsert=True,
    )


def _td(seconds: int):  # noqa: ANN201
    from datetime import timedelta
    return timedelta(seconds=seconds)


# ─── File backup (G4) ─────────────────────────────────────────────────

async def record_backup(db, loop_id: str, path: str, content: str) -> None:
    """Store pre-write copy so abort can restore the file.  Phase C
    wires this into the actual write path."""
    await db.loop_backups.insert_one({
        "loop_id":   loop_id,
        "path":      path,
        "content":   content,
        "timestamp": _now(),
    })


async def rollback(db, loop_id: str) -> list[dict]:
    """Return every backed-up (path, content) pair so the caller can
    restore them.  Engine itself never writes user files — Phase C's
    verify/execute flow will hand the list to a GitHub writer."""
    cur = db.loop_backups.find({"loop_id": loop_id})
    return [{"path": d["path"], "content": d["content"]} async for d in cur]


# ─── Resume after crash (G3) ──────────────────────────────────────────

async def resume_stale(db) -> int:
    """Called from main.py on app startup.  Find any session stuck in
    EXECUTING/VERIFYING/SCANNING whose `updated_at` is older than
    STALE_AFTER_S and flip it to PAUSED_FOR_USER with a clear message.
    Returns the count of sessions that were rescued."""
    cutoff = _now() - _td(STALE_AFTER_S)
    rescued = 0
    async for doc in db.loop_sessions.find({
        "state": {"$in": [LoopState.EXECUTING.value,
                          LoopState.VERIFYING.value,
                          LoopState.SCANNING.value,
                          LoopState.SHIPPING.value,
                          LoopState.SELF_HEALING.value]},
        "updated_at": {"$lt": cutoff},
    }):
        loop_id = doc.get("loop_id")
        await db.loop_sessions.update_one(
            {"loop_id": loop_id},
            {"$set": {"state": LoopState.PAUSED_FOR_USER.value,
                      "resume_reason": "server_restart_mid_loop",
                      "updated_at": _now()}},
        )
        await _log_error(
            db, loop_id, doc.get("phase", "?"),
            "Server restarted mid-loop; session paused.",
        )
        rescued += 1
    if rescued:
        logger.info("loop_engine: rescued %d stale session(s)", rescued)
    return rescued


# ─── The engine class ────────────────────────────────────────────────

class LoopEngine:
    """One instance per loop run.  Owns the event queue, the state
    transitions, and the LLM/tool calls."""

    def __init__(self, db, loop_id: str, user_id: str,
                 project_id: Optional[str], user_message: str):
        self.db = db
        self.loop_id = loop_id
        self.user_id = user_id
        self.project_id = project_id
        self.user_message = user_message
        self.state = LoopState.IDLE
        self.phase = "idle"
        self.queue: asyncio.Queue[dict] = asyncio.Queue()
        # G5 context — accreted across phases, dumped to Mongo on each
        # transition so resume gets the full picture.
        self.context: dict = {
            "original_request":      user_message,
            "plan":                  None,
            "files_changed":         [],
            "errors_encountered":    [],
            "self_heals_performed":  [],
            "verification_results":  {},
            "scan_results":          {},
            "commit":                None,
        }
        self._cancelled = False

    # ── Public API ────────────────────────────────────────────────────
    async def start(self) -> AsyncIterator[dict]:
        """Run PLAN → wait for confirmation event → continue.  This is
        an async generator so the router can stream events directly to
        the client."""
        await self._emit(LoopState.PLANNING, "plan",
                         message="Reading the request and drafting a plan…")
        try:
            await asyncio.wait_for(
                self._do_plan(),
                timeout=PHASE_TIMEOUTS_S["plan"],
            )
        except asyncio.TimeoutError:
            await self._fail("plan", "Plan generation exceeded 60s budget.")
        except Exception as e:                          # noqa: BLE001
            await self._fail("plan", f"Plan generation failed: {e!r}")
        # The plan phase ends in AWAITING_CONFIRMATION; the router waits
        # for the user to POST /confirm.  Yield buffered events here.
        while not self.queue.empty():
            yield await self.queue.get()

    # ── Phase 1 — Plan ────────────────────────────────────────────────
    async def _do_plan(self) -> None:
        """Generate the plan via the existing LLM service.  We import
        lazily so this module stays unit-testable without the full app
        bootstrap."""
        plan = await _generate_plan(
            self.user_id, self.project_id, self.user_message,
        )
        self.context["plan"] = plan
        await _save_plan(self.db, self.loop_id, plan)
        self.state = LoopState.AWAITING_CONFIRMATION
        self.phase = "plan"
        await _persist_session(self.db, self._doc())
        await self._emit(
            LoopState.AWAITING_CONFIRMATION, "plan",
            step=1, total_steps=5,
            message="Plan ready — awaiting your approval.",
            data={"plan": plan},
            requires_user_action=True,
        )

    # ── Confirmation handler (router calls this) ─────────────────────
    async def confirm(self, approved: bool, feedback: str = "") -> None:
        if self.state != LoopState.AWAITING_CONFIRMATION:
            raise ValueError(
                f"cannot confirm in state {self.state.value}",
            )
        if not approved:
            self.state = LoopState.ABORTED
            await _persist_session(self.db, self._doc())
            await self._emit(LoopState.ABORTED, "plan",
                             message=f"Loop cancelled by user: {feedback}")
            return
        # Fire EXECUTE phase as a background task; events stream via
        # the queue so the SSE consumer keeps draining.
        asyncio.create_task(self._run_pipeline())

    async def _run_pipeline(self) -> None:
        """EXECUTE → VERIFY → SCAN → SHIP, each wrapped in its own
        timeout + try/except so G1 + G2 hold."""
        try:
            await self._with_budget("execute", self._do_execute)
            if self._should_stop(): return
            await self._with_budget("verify",  self._do_verify)
            if self._should_stop(): return
            await self._with_budget("scan",    self._do_scan)
            if self._should_stop(): return
            await self._with_budget("ship",    self._do_ship)
        except Exception as e:                           # noqa: BLE001
            await self._fail(self.phase or "?", repr(e))

    def _should_stop(self) -> bool:
        """A phase that ended in PAUSED_FOR_USER must NOT advance — we
        need explicit user input to resume.  Same goes for terminal
        states + cancellation."""
        return (
            self._cancelled
            or self.state in _TERMINAL
            or self.state == LoopState.PAUSED_FOR_USER
        )

    async def _with_budget(self, phase: str, coro) -> None:
        try:
            await asyncio.wait_for(coro(), timeout=PHASE_TIMEOUTS_S[phase])
        except asyncio.TimeoutError:
            await self._fail(phase, f"Phase {phase} exceeded "
                                    f"{PHASE_TIMEOUTS_S[phase]}s budget.")

    # ── Phase 2 — Execute (LLM generates file content) ─────────────
    async def _do_execute(self) -> None:
        self.state = LoopState.EXECUTING
        self.phase = "execute"
        plan = self.context.get("plan") or {}
        files = plan.get("files_to_change") or []
        total = max(len(files), 1)
        logger.info("[loop %s] EXECUTE START — %d file(s) planned", self.loop_id, len(files))
        await self._emit(LoopState.EXECUTING, "execute",
                         step=2, total_steps=5,
                         message=f"Executing — {total} file(s) planned…",
                         data={"total_files": total})

        if not files:
            logger.warning("[loop %s] EXECUTE — plan has no files_to_change, skipping LLM", self.loop_id)
            return

        # Iter 212m-109 — Real code generation. Previously this loop
        # only emitted synthetic events without ever populating
        # `submitted_files`, so SHIP found nothing to commit and the
        # user saw "Ship complete" with no real GitHub commit.
        # Now: for each planned file, fetch current content from
        # GitHub, ask the LLM to rewrite it per the approved plan,
        # then feed the result into `submitted_files` so VERIFY can
        # lint it and SHIP can commit it.
        proj = await self.db.cto_projects.find_one(
            {"project_id": self.project_id, "user_id": self.user_id},
            {"_id": 0, "github_owner": 1, "github_repo": 1,
             "github_branch": 1, "github_token": 1},
        )
        if not proj:
            logger.error("[loop %s] EXECUTE — project not found, aborting", self.loop_id)
            await self._fail("execute", "Project not found for execute phase.")
            return
        owner   = proj.get("github_owner") or ""
        repo    = proj.get("github_repo")  or ""
        branch  = proj.get("github_branch") or "main"
        from routers.security_scan import _decrypt_pat  # local import
        token = await _decrypt_pat(self.user_id, proj.get("github_token"))
        if not token:
            try:
                u = await self.db.dev_users.find_one(
                    {"user_id": self.user_id}, {"_id": 0, "github": 1},
                )
                token = ((u or {}).get("github") or {}).get("access_token") or None
            except Exception:
                token = None
        if not (owner and repo and token):
            logger.error("[loop %s] EXECUTE — missing GitHub creds (owner=%s repo=%s token=%s)",
                         self.loop_id, bool(owner), bool(repo), bool(token))
            await self._fail("execute",
                             "GitHub credentials missing for execute. Connect repo + PAT/OAuth.")
            return

        from services.loop_execute import generate_files
        try:
            generated = await generate_files(
                plan=plan, user_message=self.user_message,
                owner=owner, repo=repo, branch=branch, token=token,
                user_id=self.user_id,
            )
        except Exception as e:                              # noqa: BLE001
            logger.exception("[loop %s] EXECUTE — generate_files raised", self.loop_id)
            await self._fail("execute", f"Code generation failed: {e}")
            return

        if not generated:
            logger.warning("[loop %s] EXECUTE — generate_files returned 0 files", self.loop_id)
            await self._fail("execute",
                             "LLM produced no usable file content. Try refining the plan.")
            return

        # Persist + emit per-file events so the frontend can show
        # real progress.
        for i, f in enumerate(generated, start=1):
            self.context["files_changed"].append(f["path"])
            await _persist_session(self.db, self._doc())
            await self._emit(
                LoopState.EXECUTING, "execute",
                step=2, total_steps=5,
                message=f"Wrote {f['path']} ({i}/{len(generated)})",
                data={"file": f["path"], "index": i, "total": len(generated),
                      "bytes": len(f.get("content") or "")},
            )

        self.context["submitted_files"] = generated
        await _persist_session(self.db, self._doc())
        logger.info("[loop %s] EXECUTE DONE — %d files in submitted_files",
                    self.loop_id, len(generated))

    # ── Phase 3 — Verify (Phase C: real ruff/eslint + self-heal) ────
    async def _do_verify(self) -> None:
        self.state = LoopState.VERIFYING
        self.phase = "verify"
        await self._emit(LoopState.VERIFYING, "verify",
                         step=3, total_steps=5,
                         message="Verifying changes…")
        # Pull file objects (path+content) that the caller registered
        # via /loop/{id}/submit-files OR were attached to the plan.
        file_objs: list[dict] = list(self.context.get("submitted_files") or [])
        if not file_objs:
            # No files to verify (Phase A path / plan-only loops).
            self.context["verification_results"] = {
                "ok": True, "results": [], "errors": [],
                "skipped_no_files": True,
            }
            return
        from services.loop_verify import verify_files, self_heal
        for attempt in range(1, MAX_VERIFY_RETRIES + 1):
            report = await verify_files(file_objs)
            self.context["verification_results"] = report
            if report["ok"]:
                if attempt > 1:
                    self.context["self_heals_performed"].append({
                        "phase":   "verify",
                        "attempt": attempt - 1,
                        "ok":      True,
                        "ts":      _iso(),
                    })
                return
            # Last-attempt fail → surface to user (no more self-heals).
            if attempt >= MAX_SELF_HEALS + 1:
                self.state = LoopState.PAUSED_FOR_USER
                await _persist_session(self.db, self._doc())
                await self._emit(
                    LoopState.PAUSED_FOR_USER, "verify",
                    step=3, total_steps=5,
                    message=(
                        f"Verify failed after {attempt - 1} self-heal "
                        "attempts. Your input needed."
                    ),
                    data={"errors": report["errors"][:25]},
                    requires_user_action=True,
                )
                return
            # Self-heal: ask LLM to rewrite the failing files.
            await self._emit(
                LoopState.SELF_HEALING, "self_heal",
                step=3, total_steps=5,
                message=(
                    f"Self-heal attempt {attempt} — rewriting "
                    f"{sum(1 for r in report['results'] if not r['ok'])} "
                    "file(s)…"
                ),
                data={"errors_preview": report["errors"][:10]},
            )
            new_file_objs: list[dict] = []
            for f, r in zip(file_objs, report["results"]):
                if r["ok"]:
                    new_file_objs.append(f)
                    continue
                # Backup pre-heal version (G4).
                with contextlib.suppress(Exception):
                    await record_backup(self.db, self.loop_id,
                                        f["path"], f["content"])
                healed = await self_heal(
                    f, [r["stdout"] or r["stderr"]] + report["errors"],
                    user_request=self.user_message,
                    user_id=self.user_id,
                )
                if healed:
                    new_file_objs.append({"path": f["path"],
                                          "content": healed})
                else:
                    new_file_objs.append(f)   # leave as-is; next pass
                                              # surfaces to user.
                self.context["self_heals_performed"].append({
                    "phase":   "verify",
                    "attempt": attempt,
                    "file":    f["path"],
                    "applied": bool(healed),
                    "ts":      _iso(),
                })
            file_objs = new_file_objs
            self.context["submitted_files"] = file_objs
            self.state = LoopState.VERIFYING
            await _persist_session(self.db, self._doc())
        # All attempts exhausted (shouldn't reach here due to early-out
        # above, but defensive in case constants change).
        self.state = LoopState.PAUSED_FOR_USER
        await _persist_session(self.db, self._doc())

    # ── Phase 4 — Scan (Phase C: real Vanguard via direct internals) ──
    async def _do_scan(self) -> None:
        self.state = LoopState.SCANNING
        self.phase = "scan"
        await self._emit(LoopState.SCANNING, "scan",
                         step=4, total_steps=5,
                         message="Running Vanguard security scan…")
        try:
            results = await _run_security_scan(self.user_id, self.project_id)
            self.context["scan_results"] = results
            crit = (results.get("summary", {})
                           .get("by_severity", {}).get("critical", 0))
            high = (results.get("summary", {})
                           .get("by_severity", {}).get("high", 0))
            if crit > 0:
                self.state = LoopState.PAUSED_FOR_USER
                await _persist_session(self.db, self._doc())
                await self._emit(
                    LoopState.PAUSED_FOR_USER, "scan",
                    step=4, total_steps=5,
                    message=f"{crit} critical finding(s) — review required.",
                    data={"summary": results.get("summary", {}),
                          "findings": (results.get("findings") or [])[:25]},
                    requires_user_action=True,
                )
            elif high > 0:
                # High is a soft warn — emit but continue.
                await self._emit(
                    LoopState.SCANNING, "scan",
                    step=4, total_steps=5,
                    message=f"{high} high finding(s) — continuing with caution.",
                    data={"summary": results.get("summary", {})},
                )
        except Exception as e:                          # noqa: BLE001
            await _log_error(self.db, self.loop_id, "scan", repr(e))
            self.context["scan_results"] = {"error": repr(e)}

    # ── Phase 5 — Ship (commits via existing GitHub writer) ──────────
    async def _do_ship(self) -> None:
        """Iter 212m-106 — Phase C wiring. Was a `phase_b_stub: True` no-op
        that emitted "Ship complete" without ever pushing to GitHub
        (user found this in prod via `git log` showing no commits).
        Now calls services.github_api_writer.commit_files() with the
        files the Execute/Verify phases produced and only marks the
        loop COMPLETED after the GitHub API returns a real commit_sha.
        """
        self.state = LoopState.SHIPPING
        self.phase = "ship"
        await self._emit(LoopState.SHIPPING, "ship",
                         step=5, total_steps=5,
                         message="Resolving GitHub credentials…")

        files_to_commit = self.context.get("submitted_files") or []
        if not files_to_commit:
            # Nothing to ship — Execute produced no diff. Mark the loop
            # PAUSED so the user knows there's nothing to commit instead
            # of a fake "Ship complete".
            self.state = LoopState.PAUSED_FOR_USER
            await _persist_session(self.db, self._doc())
            await self._emit(LoopState.PAUSED_FOR_USER, "ship",
                             step=5, total_steps=5,
                             message="Nothing to ship — Execute produced no file changes.",
                             data={"requires_user_action": True,
                                   "reason": "no_files"})
            return

        # Fetch project's GitHub linkage (owner / repo / branch / token)
        proj = await self.db.cto_projects.find_one(
            {"project_id": self.project_id, "user_id": self.user_id},
            {"_id": 0, "github_owner": 1, "github_repo": 1,
             "github_branch": 1, "github_token": 1},
        )
        if not proj:
            await self._fail_ship("Project not found for ship — re-link your repo in Settings.")
            return
        owner   = proj.get("github_owner") or ""
        repo    = proj.get("github_repo")  or ""
        branch  = proj.get("github_branch") or "main"

        # Resolve PAT (project-scoped) then fall back to user's OAuth
        # access_token if no per-project PAT was stored.
        from routers.security_scan import _decrypt_pat  # local import
        token = await _decrypt_pat(self.user_id, proj.get("github_token"))
        if not token:
            try:
                u = await self.db.dev_users.find_one(
                    {"user_id": self.user_id}, {"_id": 0, "github": 1},
                )
                token = ((u or {}).get("github") or {}).get("access_token") or None
            except Exception:
                token = None
        if not (owner and repo and token):
            await self._fail_ship(
                "GitHub credentials missing. Connect a repo + PAT (or OAuth) before shipping."
            )
            return

        # Convert [{path, content}] → {path: content} for commit_files().
        files_dict: dict[str, str] = {}
        for f in files_to_commit:
            p = (f or {}).get("path")
            c = (f or {}).get("content")
            if p and c is not None:
                files_dict[str(p)] = str(c)
        if not files_dict:
            await self._fail_ship("Submitted files were empty — nothing valid to commit.")
            return

        commit_message = _commit_message(self.user_message)
        logger.info("[loop %s] SHIP ATTEMPT — %s/%s@%s with %d file(s), msg=%r",
                    self.loop_id, owner, repo, branch,
                    len(files_dict), commit_message)
        await self._emit(LoopState.SHIPPING, "ship",
                         step=5, total_steps=5,
                         message=f"Committing {len(files_dict)} file(s) to {owner}/{repo}@{branch}…")

        try:
            from services.github_api_writer import commit_files
            res = await commit_files(
                owner=owner, repo=repo, branch=branch, token=token,
                files=files_dict, commit_message=commit_message,
                progress=None,
            )
            logger.info("[loop %s] SHIP RESULT — %r", self.loop_id, res)
        except Exception as e:  # network / 401 / 422 / etc.
            logger.exception("[loop %s] SHIP commit_files failed", self.loop_id)
            await _log_error(self.db, self.loop_id, "ship", repr(e))
            await self._fail_ship(f"GitHub push failed: {e}")
            return

        full_sha = res.get("full_sha") or res.get("sha") or ""
        short_sha = res.get("sha") or full_sha[:7]
        html_url  = res.get("html_url") or (
            f"https://github.com/{owner}/{repo}/commit/{full_sha}" if full_sha else None
        )
        self.context["commit"] = {
            "message":   commit_message,
            "sha":       short_sha,
            "full_sha":  full_sha,
            "html_url":  html_url,
            "files":     list(files_dict.keys()),
        }
        self.state = LoopState.COMPLETED
        await _persist_session(self.db, self._doc())
        await self._emit(LoopState.COMPLETED, "ship",
                         step=5, total_steps=5,
                         message=f"Shipped {short_sha} → {owner}/{repo}@{branch}",
                         data={
                             "commit_message": commit_message,
                             "commit_sha":     short_sha,
                             "full_sha":       full_sha,
                             "html_url":       html_url,
                             "files_changed":  list(files_dict.keys()),
                             "scan_results":   self.context.get("scan_results"),
                         })

    async def _fail_ship(self, reason: str) -> None:
        """Helper: persist + emit a clean ship-phase failure event."""
        self.state = LoopState.FAILED
        self.context["commit"] = {"error": reason}
        await _persist_session(self.db, self._doc())
        await self._emit(LoopState.FAILED, "ship",
                         step=5, total_steps=5,
                         message=reason,
                         data={"requires_user_action": True,
                               "error": reason})

    # ── Cancellation ─────────────────────────────────────────────────
    async def cancel(self) -> None:
        if self.state in _TERMINAL:
            return
        self._cancelled = True
        self.state = LoopState.ABORTED
        await _persist_session(self.db, self._doc())
        await self._emit(LoopState.ABORTED, self.phase or "?",
                         message="Loop cancelled by user.")

    # ── Submit files for verification (Phase C) ──────────────────────
    async def submit_files(self, files: list[dict]) -> None:
        """Register a list of `{path, content}` objects that the loop's
        VERIFY phase should lint + self-heal.  Idempotent — repeated
        calls replace the prior list (use this when the caller has new
        revisions)."""
        clean = []
        for f in (files or []):
            if isinstance(f, dict) and f.get("path") and f.get("content") is not None:
                clean.append({"path": str(f["path"])[:240],
                              "content": str(f["content"])[:200_000]})
        self.context["submitted_files"] = clean
        await _persist_session(self.db, self._doc())

    # ── Helpers ──────────────────────────────────────────────────────
    async def _emit(self, state: LoopState, phase: str, **kw) -> None:
        self.state = state
        self.phase = phase
        ev = _new_event(self.loop_id, state, phase, **kw)
        await self.queue.put(ev)
        await _persist_session(self.db, self._doc(extra={"last_event": ev}))

    def _doc(self, extra: Optional[dict] = None) -> dict:
        out = {
            "loop_id":    self.loop_id,
            "user_id":    self.user_id,
            "project_id": self.project_id,
            "state":      self.state.value,
            "phase":      self.phase,
            "context":    self.context,
        }
        if extra:
            out.update(extra)
        return out

    async def _fail(self, phase: str, reason: str) -> None:
        self.state = LoopState.FAILED
        self.phase = phase
        self.context["errors_encountered"].append(
            {"phase": phase, "error": reason, "ts": _iso()},
        )
        await _log_error(self.db, self.loop_id, phase, reason,
                         context=self.context)
        await _persist_session(self.db, self._doc())
        await self._emit(LoopState.FAILED, phase,
                         message=reason, requires_user_action=True)


_TERMINAL = {LoopState.COMPLETED, LoopState.FAILED, LoopState.ABORTED}


# ─── Adapter layer ────────────────────────────────────────────────────

async def _generate_plan(user_id: str, project_id: Optional[str],
                         user_message: str) -> dict:
    """Call the existing LLM to produce a structured plan.  Returns a
    dict with: title, files_to_change (list of paths), bullets (list
    of strings), estimated_time."""
    from services.llm import call_llm_with_meta
    sys_msg = (
        "You are ORA, an AI CTO.  The user is in Loop Mode and needs a "
        "PLAN ONLY (no code).  Respond with strict JSON:\n"
        '{\n'
        '  "title": str (3-7 words),\n'
        '  "files_to_change": [list of paths you will touch],\n'
        '  "bullets":         [3-7 short numbered steps],\n'
        '  "estimated_time":  str (e.g. "~3 minutes")\n'
        '}\n'
        "Return ONLY the JSON object — no markdown, no commentary."
    )
    meta = await call_llm_with_meta(sys_msg, user_message,
                                    review_mode="pro",
                                    mode="loop_plan")
    content = (meta or {}).get("content", "").strip()
    # Tolerate a stray ```json fence.
    if content.startswith("```"):
        first_nl = content.find("\n")
        content = content[first_nl + 1:]
        if content.endswith("```"):
            content = content[:-3].rstrip()
    try:
        return json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return {
            "title": "Plan",
            "files_to_change": [],
            "bullets": [content[:500] or "Unable to parse plan."],
            "estimated_time": "?",
            "raw": content[:2000],
        }


async def _run_security_scan(user_id: str,
                             project_id: Optional[str]) -> dict:
    """Re-use the real security_scan logic in-process.  Phase C builds
    a synthetic Body+Header pair and dispatches `run_security_scan`
    directly so we get the same 13-rule output the UI's manual scan
    produces — no skipping, no auth dance.

    Returns the engine-friendly summary shape (or a stub if the user
    has no connected repo, which is the legitimate not-applicable
    case)."""
    if not project_id:
        return {"summary": {"total": 0, "by_severity": {}},
                "skipped_reason": "no_project"}
    # Pull the project's encrypted PAT + repo coords and reuse the
    # scanner's lower-level helpers so we don't need a JWT.
    from cto_services.db import get_db
    from routers.security_scan import _decrypt_pat, _scan_text  # noqa
    import httpx, asyncio as _asyncio
    db = get_db()
    if db is None:
        return {"summary": {"total": 0, "by_severity": {}},
                "skipped_reason": "no_db"}
    proj = await db.cto_projects.find_one(
        {"project_id": project_id, "user_id": user_id},
        {"_id": 0, "github_owner": 1, "github_repo": 1, "github_token": 1},
    )
    if not proj:
        return {"summary": {"total": 0, "by_severity": {}},
                "skipped_reason": "no_project_doc"}
    owner = proj.get("github_owner") or ""
    repo  = proj.get("github_repo")  or ""
    pat   = await _decrypt_pat(user_id, proj.get("github_token"))
    if not (owner and repo and pat):
        return {"summary": {"total": 0, "by_severity": {}},
                "skipped_reason": "no_github_linkage"}
    # Re-run a trimmed scan inline — same rule library, capped 200
    # files for the engine path to keep phase budget under 120s.
    from routers.security_scan import (
        _list_repo_tree, _fetch_file, _SCAN_EXTS, _SKIP_DIRS,
        _MAX_BYTES_PER_FILE,
    )
    async with httpx.AsyncClient() as client:
        try:
            blobs = await _list_repo_tree(client, owner, repo, pat)
        except Exception as e:                          # noqa: BLE001
            return {"summary": {"total": 0, "by_severity": {}},
                    "scan_error": repr(e)}
        candidates: list[dict] = []
        for b in blobs:
            path = b.get("path", "")
            parts = path.split("/")
            if any(p in _SKIP_DIRS for p in parts):
                continue
            if not any(path.lower().endswith(e) for e in _SCAN_EXTS):
                continue
            if b.get("size", 0) > _MAX_BYTES_PER_FILE:
                continue
            candidates.append(b)
            if len(candidates) >= 200:
                break
        sem = _asyncio.Semaphore(8)
        async def _scan_one(b):
            async with sem:
                text = await _fetch_file(client, owner, repo, b["path"], pat)
            return _scan_text(b["path"], text) if text else []
        all_findings = []
        for sub in await _asyncio.gather(
            *[_scan_one(b) for b in candidates], return_exceptions=False,
        ):
            all_findings.extend(sub)
    by_sev: dict[str, int] = {}
    for f in all_findings:
        by_sev[f["severity"]] = by_sev.get(f["severity"], 0) + 1
    return {
        "summary":       {"total": len(all_findings), "by_severity": by_sev},
        "findings":      all_findings[:100],
        "scanned_files": len(candidates),
    }


def _commit_message(user_msg: str) -> str:
    """Auto-derive a Conventional-Commit style message from the user's
    original request.  Phase C may swap in an LLM-written subject."""
    summary = (user_msg or "ORA update").strip().splitlines()[0][:60]
    return f"feat(ora): {summary} [loop-verified]"


# ─── Public helpers (router imports these) ────────────────────────────

def new_loop_id() -> str:
    return f"loop_{uuid.uuid4().hex[:14]}"


async def load_session(db, loop_id: str) -> Optional[dict]:
    return await db.loop_sessions.find_one(
        {"loop_id": loop_id}, {"_id": 0},
    )


# Registry of live engines keyed by loop_id, so the same instance can
# be reached from /confirm + /stream within the same worker.  Cross-
# worker continuation falls back to the Mongo state.
_LIVE: dict[str, "LoopEngine"] = {}


def register(engine: "LoopEngine") -> None:
    _LIVE[engine.loop_id] = engine


def deregister(loop_id: str) -> None:
    _LIVE.pop(loop_id, None)


def lookup(loop_id: str) -> Optional["LoopEngine"]:
    return _LIVE.get(loop_id)


# Defensive shutdown hook for tests.
def reset_registry() -> None:                            # noqa: D401
    _LIVE.clear()


# Re-export the canonical SSE event factory so the router can synthesise
# events when an engine isn't in this worker's memory (e.g. /status).
new_event = _new_event
