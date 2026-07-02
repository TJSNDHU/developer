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
import os
import time
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, AsyncIterator, Optional

_PID = os.getpid()

logger = logging.getLogger(__name__)

# Phase budgets in seconds (G2). Iter 212m-131 — Re-balanced after deep
# RCA found the verify storm: 5 files × 3 internal retries × ~25s self-
# heal LLM = ~375s which always blew the previous 180s budget, causing
# the phase to be auto-restarted twice for a total of ~9 min of WASTED
# work (same files, same LLM calls, same failure). Two changes:
#   1. Verify budget raised to 360s — covers MAX_SELF_HEALS=2 attempts
#      across up to 6 files realistically (~5×30s + lint overhead).
#   2. Execute budget raised to 420s for the same reason — 8 files at
#      LOOP_EXECUTE_PER_FILE_TIMEOUT_S=60s × ceil(8/3 parallelism) = 180s
#      worst case, plus diagnose-first overhead.
# The auto-restart MAX_PHASE_RESTARTS is also now ONE (not two) so a
# stuck phase doesn't burn 3× the time before _fail() — false hope
# is worse than honest failure.
PHASE_TIMEOUTS_S: dict[str, int] = {
    "plan":      120,
    "execute":   420,
    "verify":    360,
    "scan":      180,
    "ship":      120,
    "self_heal": 180,
}
# Iter 212m-131 — One restart is enough. A phase that times out twice
# in a row was wasting 3×budget worth of time without making progress
# (see bug #2 + #7 in iter 131 RCA — phase coroutines re-run from
# scratch with the same context every restart). Cutting to 1 restart
# bounds the worst case at 2×budget.
MAX_PHASE_RESTARTS = 1
# A session whose Mongo doc hasn't been updated in this long while in
# EXECUTING/VERIFYING is treated as orphaned by resume_stale().
STALE_AFTER_S = 300
# Iter 212m-172 — Awaiting-confirmation / paused-for-user auto-expiry.
# A loop that sits in AWAITING_CONFIRMATION or PAUSED_FOR_USER for more
# than AWAITING_CONFIRM_MAX_S is auto-cancelled, its lock released,
# and the user sees a clean "Loop expired" state on next fetch instead
# of a silent hang.  Envelope-tunable so PROD can widen the window.
AWAITING_CONFIRM_MAX_S = int(
    os.getenv("LOOP_AWAITING_CONFIRM_MAX_S", "600")  # 10 min default
)
# Iter 212m-131 — Self-heal cap. SOURCE OF TRUTH for the verify inner
# loop. The old code had MAX_VERIFY_RETRIES=3 + MAX_SELF_HEALS=2 with
# a fragile "attempt >= MAX_SELF_HEALS + 1" check that worked ONLY
# because the constants happened to align (3 == 2+1). Now: one cap,
# one loop, no coincidence.
MAX_SELF_HEALS = 2
# Iter 212m-131 — Per-self-heal-call timeout. The old `self_heal()`
# in loop_verify.py had NO timeout — a stalled LLM streaming response
# could hang the entire verify phase until the OUTER 360s budget
# tripped. 60s is generous; LLM_HTTP_TIMEOUT_S is already 25s + we
# allow one retry inside the LLM service.
SELF_HEAL_LLM_TIMEOUT_S = 60
# Iter 212m-131 — Engine silence watchdog. If no SSE event has been
# emitted in this long mid-phase, we emit a synthetic `engine_silent`
# event so the frontend's heartbeat dot doesn't lie about progress.
ENGINE_SILENT_WARN_S = 45
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
    # Iter 212m-172 — Auto-expiry state.  Distinct from ABORTED so
    # the UI can render "Loop expired — restart if you still want to
    # run it" instead of "Loop cancelled by user".
    EXPIRED                = "expired"


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


# ─── Iter 212m-172 — Auto-expiry sweeper for user-paused loops ──────

async def sweep_expired_awaiting_confirmations(db) -> int:
    """Auto-expire loops that have been PAUSED waiting for a user
    decision for longer than AWAITING_CONFIRM_MAX_S.

    A user-paused loop that never gets confirmed holds the loop_lock
    for its (project_id, user_id) tuple forever — blocking fresh
    `/loop/start` calls for up to STALE_S.  The founder QA repeatedly
    hit "loop_already_running" with no active pipeline actually doing
    work.

    Design:
      • state IN (awaiting_confirmation, paused_for_user) AND
        updated_at older than the cutoff.
      • Flip to EXPIRED with resume_reason="awaiting_confirmation_timeout".
      • Release the (project_id, user_id) loop_lock so a new /start on
        the same project succeeds on the next request.
      • Return the count for the caller to log.

    Called every 60 s by the lifespan background task.  Also safe to
    call ad-hoc from tests.
    """
    cutoff = _now() - _td(AWAITING_CONFIRM_MAX_S)
    expired = 0
    try:
        cursor = db.loop_sessions.find({
            "state": {"$in": [
                LoopState.AWAITING_CONFIRMATION.value,
                LoopState.PAUSED_FOR_USER.value,
            ]},
            "updated_at": {"$lt": cutoff},
        })
    except Exception as e:
        logger.warning("sweep_expired_awaiting_confirmations: cursor open failed: %r", e)
        return 0
    async for doc in cursor:
        loop_id     = doc.get("loop_id") or ""
        user_id     = doc.get("user_id") or ""
        project_id  = doc.get("project_id") or None
        try:
            await db.loop_sessions.update_one(
                {"loop_id": loop_id},
                {"$set": {
                    "state":         LoopState.EXPIRED.value,
                    "resume_reason": "awaiting_confirmation_timeout",
                    "updated_at":    _now(),
                }},
            )
            # Best-effort lock release.  If loop_safety fails to import
            # (test env), the loop_lock TTL will still clear it after
            # STALE_S — this is just to make it prompt.
            try:
                from services.loop_safety import release_loop_lock
                await release_loop_lock(db, project_id, user_id)
            except Exception as _e:
                logger.debug("release_loop_lock on expired loop failed: %r", _e)
            await _log_error(
                db, loop_id, doc.get("phase", "?"),
                "Awaiting-confirmation timeout — loop auto-expired.",
            )
            # Drop from in-process registry so a follow-up lookup
            # doesn't rehydrate a stale engine.
            _LIVE.pop(loop_id, None)
            expired += 1
        except Exception as e:
            logger.warning(
                "sweep_expired_awaiting_confirmations: update failed for %s: %r",
                loop_id, e,
            )
    if expired:
        logger.info(
            "loop_engine: expired %d awaiting_confirmation session(s)",
            expired,
        )
    return expired


# ─── The engine class ────────────────────────────────────────────────

class LoopEngine:
    """One instance per loop run.  Owns the event queue, the state
    transitions, and the LLM/tool calls."""

    def __init__(self, db, loop_id: str, user_id: str,
                 project_id: Optional[str], user_message: str,
                 bin_ctx=None):
        self.db = db
        self.loop_id = loop_id
        self.user_id = user_id
        self.project_id = project_id
        self.user_message = user_message
        # Iter 212m-169 — BINContext is the single source of truth for
        # this loop's user + project + PAT + is_founder flag.  Built
        # ONCE at the router entry point (see routers/loop.py) and
        # never re-fetched from the DB inside the pipeline.  Older
        # code paths that still call self.user_id / self.project_id
        # are kept for backward compat but any NEW code MUST read
        # from self.bin_ctx.
        self.bin_ctx = bin_ctx
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
        # Iter 212m-131 — Hold a strong reference to the pipeline task
        # so the asyncio GC can't reap it mid-flight. The old code
        # called `asyncio.create_task(self._run_pipeline())` in
        # `confirm()` and dropped the result — Python 3.11's docs
        # explicitly warn that "the event loop only keeps weak refs
        # to tasks" and a long-running task without an owning
        # reference can disappear during GC. We now own it on `self`
        # AND `cancel()` actually cancels it instead of just setting
        # a flag the inner phases check between LLM calls.
        self._pipeline_task: Optional[asyncio.Task] = None
        # Last event timestamp — used by the silence watchdog to
        # detect a phase coroutine that is technically "running" but
        # not emitting any progress.
        self._last_event_at: float = time.time()

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
        bootstrap.

        Iter 212m-115 safety #1 — PAT pre-flight. We validate the
        user's GitHub token at the START of the loop (before spending
        LLM tokens) so an expired/revoked PAT fails-fast in <2 s
        instead of crashing at SHIP after Plan + Execute + Verify +
        Scan have completed. Only runs when project_id is set."""
        if self.project_id:
            try:
                proj = await self.db.cto_projects.find_one(
                    {"project_id": self.project_id, "user_id": self.user_id},
                    {"_id": 0, "github_owner": 1, "github_repo": 1,
                     "github_branch": 1, "github_token": 1},
                )
                if proj and proj.get("github_owner") and proj.get("github_repo"):
                    from routers.security_scan import _decrypt_pat
                    token = await _decrypt_pat(self.user_id, proj.get("github_token"))
                    if not token:
                        u = await self.db.dev_users.find_one(
                            {"user_id": self.user_id}, {"_id": 0, "github": 1},
                        )
                        token = ((u or {}).get("github") or {}).get("access_token") or None
                    if token:
                        from services.loop_safety import validate_github_token
                        ok, err = await validate_github_token(
                            proj["github_owner"], proj["github_repo"], token,
                        )
                        if not ok:
                            await self._fail(
                                "plan",
                                f"GitHub PAT preflight failed: {err}. "
                                f"Reconnect your repo before running the loop.",
                            )
                            return
            except Exception as e:                          # noqa: BLE001
                # Preflight is best-effort. Don't block the loop on
                # an unexpected error in the preflight code itself.
                logger.warning("[loop %s] PAT preflight skipped: %r",
                               self.loop_id, e)

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
        # Iter 212m-117 — Trust-level branching.
        # L1: Plan-only — skip Execute/Verify/Scan/Ship and mark COMPLETED.
        # L2: Standard pipeline with manual Ship gate (default).
        # L3: Pipeline with auto-ship (skips the manual confirmation pause).
        try:
            from routers.trust_level import get_user_trust_level
            level = await get_user_trust_level(self.db, self.user_id)
        except Exception:
            level = "L2"
        self.context["trust_level"] = level
        if level == "L1":
            self.state = LoopState.COMPLETED
            self.phase = "plan"
            await _persist_session(self.db, self._doc())
            try:
                from services.loop_safety import release_loop_lock
                await release_loop_lock(
                    self.db, self.project_id or "_no_project",
                    self.user_id, self.loop_id,
                )
            except Exception:
                pass
            await self._emit(
                LoopState.COMPLETED, "plan",
                step=1, total_steps=5,
                message="L1 report-only mode — plan ready, no code changes will be written. "
                        "Upgrade to L2 in Settings to execute the plan.",
                data={"trust_level": "L1",
                      "plan": self.context.get("plan")},
            )
            return
        # L2 + L3 — fire the EXECUTE phase as a background task.
        # Iter 212m-131 — bug #1 fix: HOLD the task reference on
        # `self` so the asyncio GC can't reap it. The old code
        # dropped the create_task() return value, which Python's
        # docs explicitly warn against ("the event loop only keeps
        # weak refs to tasks"). Add a done-callback to also clear
        # the ref on completion so we don't leak a Task per loop.
        task = asyncio.create_task(self._run_pipeline())
        self._pipeline_task = task

        def _on_pipeline_done(t: asyncio.Task) -> None:
            self._pipeline_task = None
            # Surface a truly unhandled exception as a FAILED event —
            # _run_pipeline wraps its own logic in try/except, but a
            # bug in the wrapper itself or an exception raised during
            # _fail() would otherwise be invisible.
            if not t.cancelled() and t.exception():
                exc = t.exception()
                logger.error(
                    "[loop %s] pipeline task crashed unhandled: %r",
                    self.loop_id, exc,
                )
                # Best-effort emit — we're not in an async context here.
                try:
                    self.queue.put_nowait(_new_event(
                        self.loop_id, LoopState.FAILED, self.phase or "?",
                        message=f"Pipeline crashed: {exc!r}",
                        requires_user_action=True,
                    ))
                except Exception:
                    pass
        task.add_done_callback(_on_pipeline_done)

    async def _run_pipeline(self) -> None:
        """EXECUTE → VERIFY → SCAN → SHIP, each wrapped in its own
        timeout + try/except so G1 + G2 hold.

        Iter 212m-131 — explicit CancelledError handling. When
        `cancel()` runs `task.cancel()`, this will be raised into
        whichever phase coroutine is currently awaiting. We let it
        propagate cleanly (no _fail() — cancel != failure) so the
        loop's terminal state reflects the user's intent (ABORTED,
        not FAILED). The user already saw the ABORTED event from
        cancel() itself."""
        try:
            await self._with_budget("execute", self._do_execute)
            if self._should_stop(): return
            await self._with_budget("verify",  self._do_verify)
            if self._should_stop(): return
            await self._with_budget("scan",    self._do_scan)
            if self._should_stop(): return
            await self._with_budget("ship",    self._do_ship)
        except asyncio.CancelledError:
            # Bubble up so the Task transitions to cancelled state.
            # cancel() has already persisted ABORTED + emitted the
            # event, so nothing more to do here.
            logger.info("[loop %s] pipeline task cancelled cleanly", self.loop_id)
            raise
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
        """Iter 212m-131 — Auto-restart on phase timeout, hardened.

        Changes from iter 212m-112:
          • MAX_PHASE_RESTARTS reduced 2 → 1 (see module docstring —
            phase coroutines aren't idempotent across restarts).
          • CancelledError propagates cleanly (not retried).
          • State is set to the phase's RUNNING state BEFORE we emit
            the SELF_HEALING auto-restart event (was: after, which
            briefly leaked the wrong state).
          • Phase-specific context keys are RESET on restart so the
            second attempt sees a clean slate instead of a partially
            self-healed mess that confused the next iteration.
        """
        budget = PHASE_TIMEOUTS_S[phase]
        last_err: Optional[str] = None
        for attempt in range(MAX_PHASE_RESTARTS + 1):
            try:
                await asyncio.wait_for(coro(), timeout=budget)
                if attempt > 0:
                    logger.info(
                        "[loop %s] phase=%s recovered on attempt %d/%d",
                        self.loop_id, phase, attempt + 1,
                        MAX_PHASE_RESTARTS + 1,
                    )
                return
            except asyncio.CancelledError:
                # User cancel must NOT trigger an auto-restart.
                raise
            except asyncio.TimeoutError:
                last_err = (f"Phase {phase} exceeded {budget}s budget "
                            f"(attempt {attempt + 1}/{MAX_PHASE_RESTARTS + 1})")
                logger.warning("[loop %s] %s", self.loop_id, last_err)
                if attempt < MAX_PHASE_RESTARTS:
                    backoff = 2 ** (attempt + 1)
                    # Bug #10 fix — set state BEFORE emitting the event
                    # so SSE consumers don't observe a half-truth.
                    self.state = LoopState.SELF_HEALING
                    self.phase = "self_heal"
                    # Bug #7 fix — clear phase-specific scratch keys so
                    # the next attempt isn't confused by stale partial
                    # data from the timed-out one. Plan stays (it's
                    # locked-in user-approved content).
                    if phase == "execute":
                        self.context["submitted_files"] = []
                        self.context["files_changed"]   = []
                    elif phase == "verify":
                        self.context["verification_results"] = {}
                    elif phase == "scan":
                        self.context["scan_results"] = {}
                    await self._emit(
                        LoopState.SELF_HEALING, "self_heal",
                        message=(
                            f"Phase {phase} timed out — auto-restarting "
                            f"(attempt {attempt + 2}/{MAX_PHASE_RESTARTS + 1}) "
                            f"in {backoff}s…"
                        ),
                        data={"phase": phase, "attempt": attempt + 1,
                              "max":   MAX_PHASE_RESTARTS + 1,
                              "kind":  "phase_auto_restart"},
                    )
                    await asyncio.sleep(backoff)
                    # Reset state to the phase's running state so the
                    # next attempt looks like a fresh run to the
                    # frontend's LoopStepBar.
                    self.state = {
                        "plan":    LoopState.PLANNING,
                        "execute": LoopState.EXECUTING,
                        "verify":  LoopState.VERIFYING,
                        "scan":    LoopState.SCANNING,
                        "ship":    LoopState.SHIPPING,
                    }.get(phase, self.state)
                    continue
                # Exhausted — surface to user.
                await self._fail(phase, last_err or
                                 f"Phase {phase} exceeded {budget}s budget.")
                return

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
            logger.warning("[loop %s] EXECUTE — plan has no files_to_change, failing",
                           self.loop_id)
            # Iter 212m-131 — bug #6 fix: previously this returned
            # silently, letting Verify → Scan → Ship all progress with
            # no files; user saw "Ship complete" without any commit.
            # Now we _fail() so the user sees a real reason.
            await self._fail(
                "execute",
                "Plan has no files_to_change — refine the plan and retry.",
            )
            return

        # Iter 212m-109 — Real code generation. Previously this loop
        # only emitted synthetic events without ever populating
        # `submitted_files`, so SHIP found nothing to commit and the
        # user saw "Ship complete" with no real GitHub commit.
        # Now: for each planned file, fetch current content from
        # GitHub, ask the LLM to rewrite it per the approved plan,
        # then feed the result into `submitted_files` so VERIFY can
        # lint it and SHIP can commit it.
        # Iter 212m-169 — Use BINContext directly.  No DB re-fetch, no
        # re-decrypt: everything was validated at loop start.
        if self.bin_ctx is not None:
            owner  = self.bin_ctx.repo_owner
            repo   = self.bin_ctx.repo_name
            branch = self.bin_ctx.branch
            token  = self.bin_ctx.pat
        else:
            # Legacy fallback for loop sessions started before bin_ctx
            # was mandatory.  Kept behind a warning so we notice if
            # anything still hits it.
            logger.warning(
                "[loop %s] EXECUTE — bin_ctx is None, falling back to DB fetch",
                self.loop_id,
            )
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

        # Iter 212m-116 — Apply Sweep-pattern file selector: trim the
        # planner's `files_to_change` to the TOP-N most relevant by
        # keyword score against the user's task description. The
        # planner sometimes over-eagerly lists 10+ files for a simple
        # change; this cuts those down to the 5-8 that actually
        # matter, slashing Execute LLM token spend.
        try:
            from services.file_selector import select_relevant_files
            sel = await select_relevant_files(
                db=self.db,
                project_id=self.project_id,
                user_id=self.user_id,
                task_description=self.user_message,
                planner_files=plan.get("files_to_change") or [],
                top_n=10,
            )
            if sel.get("has_graph") and sel.get("candidates"):
                old_count = len(plan.get("files_to_change") or [])
                plan = {**plan, "files_to_change": sel["candidates"]}
                logger.info(
                    "[loop %s] EXECUTE — file_selector trimmed "
                    "%d → %d candidates (skipped %d)",
                    self.loop_id, old_count, len(sel["candidates"]),
                    len(sel.get("skipped") or []),
                )
        except Exception as e:                              # noqa: BLE001
            logger.debug("[loop %s] file_selector skipped: %r",
                         self.loop_id, e)

        try:
            # Iter 212m-150 — Parliament wired into the per-file LLM
            # generation step.  generate_files() still handles parallel
            # fetch + localization but each file's actual code-generation
            # LLM call is now routed through Council A (3 members @ temps
            # 0.1 / 0.2 / 0.3, CEO pick @ temp 0.0). 3-file parallel cap
            # and per-file timeout are preserved by running the parliament
            # task inside the existing semaphore + asyncio.wait_for envelope.
            from core.parliament import Parliament
            _parliament = Parliament(db=self.db)

            paths: list[str] = list((plan or {}).get("files_to_change") or [])
            if not paths:
                logger.warning(
                    "[loop %s] EXECUTE — plan has no files_to_change",
                    self.loop_id,
                )
                generated = []
            else:
                from services.loop_execute import (
                    MAX_PARALLEL_GENS, PER_FILE_TIMEOUT_S,
                )
                from services.github_api_writer import fetch_file
                import httpx as _httpx
                sem = asyncio.Semaphore(MAX_PARALLEL_GENS)
                plan_bullets = "\n".join(
                    f"- {b}" for b in (plan.get("bullets") or [])[:12]
                )
                plan_title = plan.get("title", "")

                async def _gen_via_parliament(client, path):
                    async with sem:
                        try:
                            current = await fetch_file(
                                client, owner, repo, path, branch, token,
                            ) or ""
                        except Exception as e:                # noqa: BLE001
                            logger.warning(
                                "[parliament] fetch_file failed for %s: "
                                "%r (treating as new file)", path, e,
                            )
                            current = ""
                        task_text = (
                            f"USER REQUEST:\n{self.user_message}\n\n"
                            f"APPROVED PLAN:\n{plan_title}\n{plan_bullets}\n\n"
                            f"FILE PATH: {path}\n\n"
                            f"--- CURRENT CONTENT ({len(current)} bytes) ---\n"
                            f"{current}\n"
                            f"--- END CURRENT CONTENT ---\n\n"
                            "Return the complete new content for this file. "
                            "No fences. No commentary. Just the file content."
                        )
                        try:
                            result = await asyncio.wait_for(
                                _parliament.run(
                                    task=task_text,
                                    context={
                                        # Iter 212m-160 — `council` hardcode
                                        # removed. `task_type="code_fix"`
                                        # still routes to Council A via
                                        # TaskRouter, so behaviour is
                                        # unchanged for this code-gen
                                        # path. The change unblocks
                                        # Council B/C for future callers
                                        # that pass different task_types.
                                        "file_path":       path,
                                        "task_type":       "code_fix",
                                        "loop_session_id": self.loop_id,
                                        "user_id":         self.user_id,
                                    },
                                ),
                                timeout=PER_FILE_TIMEOUT_S,
                            )
                        except asyncio.TimeoutError:
                            logger.warning(
                                "[parliament] file %s timed out (>%ds) — skipping",
                                path, PER_FILE_TIMEOUT_S,
                            )
                            return None
                        except Exception as e:                # noqa: BLE001
                            logger.exception(
                                "[parliament] file %s raised: %r", path, e,
                            )
                            return None
                        if result.get("status") == "success" and result.get("output"):
                            return {"path": path, "content": result["output"]}
                        # manual_review or fail → skip this file; verify
                        # phase will surface anything else that breaks.
                        logger.warning(
                            "[parliament] file %s status=%s — skipping "
                            "(reason: %s)",
                            path, result.get("status"),
                            result.get("reasoning", "")[:200],
                        )
                        return None

                async with _httpx.AsyncClient(timeout=20.0) as _client:
                    _tasks = [_gen_via_parliament(_client, p) for p in paths]
                    _results = await asyncio.gather(*_tasks, return_exceptions=False)
                generated = [r for r in _results if r]
                logger.info(
                    "[parliament] EXECUTE generated %d/%d files",
                    len(generated), len(paths),
                )
        except Exception as e:                              # noqa: BLE001
            logger.exception("[loop %s] EXECUTE — parliament raised", self.loop_id)
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
        """Iter 212m-131 — Rewritten to kill the verify-storm bug.

        Root cause being fixed (bugs #2 + #3 + #4 from RCA):
          • Old code looped `MAX_VERIFY_RETRIES = 3` times, where each
            iteration ran `verify_files()` against ALL files + called
            `self_heal()` per failing file.  5 files × 3 attempts ×
            ~25 s self-heal LLM = ~375 s; the verify phase budget was
            180 s, so the phase always timed out → `_with_budget`
            auto-restarted from scratch, repeating the same work for
            ~9 minutes before _fail().  Pure storm.
          • The two constants (`MAX_VERIFY_RETRIES=3`,
            `MAX_SELF_HEALS=2`) had a coincidental equality
            (`attempt >= MAX_SELF_HEALS + 1` == `attempt >= 3` ==
            `MAX_VERIFY_RETRIES`) that broke immediately if either was
            tuned.  Now we have a single source of truth.
          • `self_heal()` had NO timeout — a stalled LLM streaming
            response hung the whole phase.

        New design:
          • ONE pass + up to MAX_SELF_HEALS healing rounds.
          • Each `self_heal()` call is wrapped in
            asyncio.wait_for(SELF_HEAL_LLM_TIMEOUT_S=60).
          • Only re-verify files that were healed (not the ones
            already passing).  Cuts repeat work by ~70% on mixed
            pass/fail batches.
          • Cancellation is propagated cleanly (raise, don't catch).
        """
        self.state = LoopState.VERIFYING
        self.phase = "verify"
        await self._emit(LoopState.VERIFYING, "verify",
                         step=3, total_steps=5,
                         message="Verifying changes…")

        file_objs: list[dict] = list(self.context.get("submitted_files") or [])
        if not file_objs:
            # No files to verify (Phase A path / plan-only loops).
            self.context["verification_results"] = {
                "ok": True, "results": [], "errors": [],
                "skipped_no_files": True,
            }
            return

        from services.loop_verify import verify_files, self_heal

        # Initial verify pass.
        report = await verify_files(file_objs)
        self.context["verification_results"] = report
        if report["ok"]:
            return  # Everything passed first try.

        # Up to MAX_SELF_HEALS healing rounds.  Each round only
        # touches the files that failed in the PREVIOUS report —
        # passing files stay locked in so we don't re-lint them.
        for heal_attempt in range(1, MAX_SELF_HEALS + 1):
            if self._cancelled:
                return
            failing_indices = [
                i for i, r in enumerate(report["results"]) if not r["ok"]
            ]
            if not failing_indices:
                break  # All healed.

            await self._emit(
                LoopState.SELF_HEALING, "self_heal",
                step=3, total_steps=5,
                message=(
                    f"Self-heal attempt {heal_attempt}/{MAX_SELF_HEALS} — "
                    f"rewriting {len(failing_indices)} file(s)…"
                ),
                data={"errors_preview": report["errors"][:10],
                      "failing_count":  len(failing_indices)},
            )

            # Self-heal each failing file with a HARD timeout per call
            # (bug #4 fix) so one stalled LLM stream can't hang the
            # whole phase.  Healed content replaces the bad version
            # in `file_objs` in-place.
            for idx in failing_indices:
                if self._cancelled:
                    return
                f = file_objs[idx]
                r = report["results"][idx]
                # Backup pre-heal version (G4).
                with contextlib.suppress(Exception):
                    await record_backup(self.db, self.loop_id,
                                        f["path"], f["content"])
                try:
                    # Iter 212m-150 — Parliament healer replaces the
                    # single-LLM-call self_heal.  The healer.heal()
                    # method runs under the same SELF_HEAL_LLM_TIMEOUT_S
                    # budget, threads prior failed attempts into the
                    # prompt so it can't repeat the same fix, and
                    # bumps temperature slightly per round.
                    from core.parliament import Parliament as _P
                    _parl = _P(db=self.db)
                    heal_task = (
                        f"Original user request:\n{self.user_message}\n\n"
                        f"File path: {f['path']}\n\n"
                        f"--- LINT ERRORS ---\n"
                        + "\n".join(
                            ([r.get("stdout") or r.get("stderr") or ""]
                             + report["errors"])[:25]
                        )
                        + "\n--- END ERRORS ---"
                    )
                    last_err = (r.get("stdout") or r.get("stderr") or "") + "\n" + "\n".join(report["errors"][:8])
                    heal_result = await asyncio.wait_for(
                        _parl.healer.heal(
                            task=heal_task,
                            all_attempts=[{
                                "output": f["content"],
                                "score":  0.0,
                                "error":  last_err,
                            }],
                            round_num=heal_attempt,
                            max_rounds=MAX_SELF_HEALS,
                        ),
                        timeout=SELF_HEAL_LLM_TIMEOUT_S,
                    )
                    if heal_result.get("status") == "retry":
                        healed = heal_result.get("output")
                    else:
                        # status ∈ {"escalate", "circuit_open"} →
                        # existing behaviour (None → mark file failed).
                        # `circuit_open` means the upstream LLM is sick;
                        # the legacy heal path could also fail similarly,
                        # so we let the outer phase budget handle it.
                        healed = None
                except asyncio.TimeoutError:
                    logger.warning(
                        "[loop %s] parliament healer timed out for %s "
                        "(%ds budget)",
                        self.loop_id, f["path"], SELF_HEAL_LLM_TIMEOUT_S,
                    )
                    healed = None
                except asyncio.CancelledError:
                    raise
                except Exception as e:                       # noqa: BLE001
                    logger.warning(
                        "[loop %s] parliament healer raised for %s: %r",
                        self.loop_id, f["path"], e,
                    )
                    healed = None

                if healed:
                    file_objs[idx] = {"path": f["path"], "content": healed}
                self.context["self_heals_performed"].append({
                    "phase":   "verify",
                    "attempt": heal_attempt,
                    "file":    f["path"],
                    "applied": bool(healed),
                    "ts":      _iso(),
                })

            # Re-verify ONLY the files we just healed (not the ones
            # that passed earlier) — they were already verified, no
            # point spending another ruff/eslint subprocess on them.
            self.context["submitted_files"] = file_objs
            healed_subset = [file_objs[i] for i in failing_indices]
            subset_report = await verify_files(healed_subset)
            # Merge the subset's verdict back into the full report so
            # context["verification_results"] keeps a complete picture.
            results = list(report["results"])
            for sub_i, full_i in enumerate(failing_indices):
                results[full_i] = subset_report["results"][sub_i]
            errors = [
                e for i, e in enumerate(report["errors"])
                if not any(e.startswith(f["path"] + ":")
                           for f in (file_objs[i] for i in failing_indices))
            ]
            errors.extend(subset_report["errors"])
            report = {
                "ok":      all(r["ok"] for r in results),
                "results": results,
                "errors":  errors,
            }
            self.context["verification_results"] = report
            self.state = LoopState.VERIFYING
            await _persist_session(self.db, self._doc())
            if report["ok"]:
                self.context["self_heals_performed"].append({
                    "phase":   "verify",
                    "attempt": heal_attempt,
                    "ok":      True,
                    "ts":      _iso(),
                })
                return

        # MAX_SELF_HEALS exhausted with files still failing — pause
        # for user input (G1 — no silent failures).
        self.state = LoopState.PAUSED_FOR_USER
        await _persist_session(self.db, self._doc())
        await self._emit(
            LoopState.PAUSED_FOR_USER, "verify",
            step=3, total_steps=5,
            message=(
                f"Verify failed after {MAX_SELF_HEALS} self-heal "
                "attempts. Your input needed."
            ),
            data={"errors": report["errors"][:25],
                  "failed_files": [
                      r["path"] for r in report["results"] if not r["ok"]
                  ]},
            requires_user_action=True,
        )

    # ── Phase 4 — Scan (Phase C: real Vanguard via direct internals) ──
    async def _do_scan(self) -> None:
        self.state = LoopState.SCANNING
        self.phase = "scan"
        await self._emit(LoopState.SCANNING, "scan",
                         step=4, total_steps=5,
                         message="Running Vanguard security scan…")
        try:
            # Iter 212m-132 — Diff-only scan. The old path called
            # `_run_security_scan` which scanned the ENTIRE repo
            # (up to 200 files), so pre-existing vulns in untouched
            # files / lines blocked every Loop commit. Now we ONLY
            # scan the files this loop actually changed, AND only
            # flag findings on lines the patch added/modified.
            submitted_files = list(self.context.get("submitted_files") or [])
            if submitted_files:
                results = await _run_diff_security_scan(
                    self.db, self.user_id, self.project_id, submitted_files,
                )
            else:
                # No submitted files (Phase A / plan-only loops) →
                # fall through to the legacy full-repo scan because
                # there's no diff to compare against.
                results = await _run_security_scan(self.user_id,
                                                   self.project_id)
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
                    message=f"{crit} critical finding(s) introduced by this "
                            f"patch — review required.",
                    data={"summary": results.get("summary", {}),
                          "findings": (results.get("findings") or [])[:25],
                          "diff_mode": results.get("diff_mode", False)},
                    requires_user_action=True,
                )
            elif high > 0:
                # High is a soft warn — emit but continue.
                await self._emit(
                    LoopState.SCANNING, "scan",
                    step=4, total_steps=5,
                    message=f"{high} high finding(s) introduced — continuing with caution.",
                    data={"summary": results.get("summary", {}),
                          "diff_mode": results.get("diff_mode", False)},
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

        Iter 212m-111 — Manual Ship gate (founder spec: "always before
        ship codes in repo our system must show button and ship to
        github hard save only mannual no auto ship"). The phase now
        PREPARES the commit (validates files, resolves credentials,
        builds the commit message) and then PAUSES the loop with a
        `paused_for_user` event carrying `data.kind="awaiting_ship"`.
        The frontend renders a big "Ship to GitHub" button; the actual
        `commit_files()` push happens only after the user POSTs to
        `/loop/{loop_id}/confirm-ship`, which calls `confirm_ship()`.
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

        # Iter 212m-169 — Use BINContext directly.  No DB re-fetch,
        # no re-decrypt.  bin_ctx was validated at loop start.
        if self.bin_ctx is not None:
            owner  = self.bin_ctx.repo_owner
            repo   = self.bin_ctx.repo_name
            branch = self.bin_ctx.branch
            token  = self.bin_ctx.pat
        else:
            # Legacy fallback (should never trigger post Iter 212m-169).
            logger.warning(
                "[loop %s] SHIP — bin_ctx is None, falling back to DB fetch",
                self.loop_id,
            )
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

        # Iter 212m-111 — PAUSE for manual ship confirmation. The actual
        # `commit_files()` call now lives in `confirm_ship()` which is
        # triggered by POST /loop/{loop_id}/confirm-ship.
        # Iter 212m-117 — L3 trust-level users SKIP the manual gate
        # (auto-ship). L1 never reaches here. L2 is the safe default.
        self.context["ship_pending"] = {
            "owner":          owner,
            "repo":           repo,
            "branch":         branch,
            "token":          token,
            "files":          files_dict,
            "commit_message": commit_message,
        }
        if self.context.get("trust_level") == "L3":
            logger.info("[loop %s] L3 auto-ship — skipping manual gate",
                        self.loop_id)
            await self.confirm_ship(True)
            return
        self.state = LoopState.PAUSED_FOR_USER
        await _persist_session(self.db, self._doc())
        logger.info("[loop %s] SHIP PAUSED for manual confirmation — "
                    "%s/%s@%s with %d file(s)",
                    self.loop_id, owner, repo, branch, len(files_dict))
        await self._emit(
            LoopState.PAUSED_FOR_USER, "ship",
            step=5, total_steps=5,
            message=f"Ready to ship {len(files_dict)} file(s) to "
                    f"{owner}/{repo}@{branch}. Click 'Ship to GitHub' to commit.",
            data={
                "kind":           "awaiting_ship",
                "reason":         "awaiting_ship_confirmation",
                "owner":          owner,
                "repo":           repo,
                "branch":         branch,
                "files":          list(files_dict.keys()),
                "file_count":     len(files_dict),
                "commit_message": commit_message,
            },
            requires_user_action=True,
        )

    async def confirm_ship(self, approved: bool) -> None:
        """Iter 212m-111 — User clicked the manual "Ship to GitHub"
        button (approved=True) or "Cancel" (approved=False) on the
        awaiting_ship_confirmation pause card."""
        if self.state != LoopState.PAUSED_FOR_USER or self.phase != "ship":
            raise ValueError(
                f"cannot confirm ship in state {self.state.value}/{self.phase}"
            )
        pending = self.context.get("ship_pending") or {}
        if not pending:
            await self._fail_ship("Ship state lost — re-run the loop.")
            return
        if not approved:
            # User cancelled the ship — abort the loop cleanly.
            self.state = LoopState.ABORTED
            self.context.pop("ship_pending", None)
            await _persist_session(self.db, self._doc())
            # Iter 212m-115 — release lock on cancel.
            try:
                from services.loop_safety import release_loop_lock
                await release_loop_lock(
                    self.db, self.project_id or "_no_project",
                    self.user_id, self.loop_id,
                )
            except Exception:
                pass
            await self._emit(
                LoopState.ABORTED, "ship",
                step=5, total_steps=5,
                message="Ship cancelled by user — no commit pushed.",
            )
            return

        # Resume — push the commit for real.
        self.state = LoopState.SHIPPING
        self.phase = "ship"
        owner   = pending["owner"]
        repo    = pending["repo"]
        branch  = pending["branch"]
        token   = pending["token"]
        files_dict      = pending["files"]
        commit_message  = pending["commit_message"]
        logger.info("[loop %s] SHIP CONFIRMED — pushing %s/%s@%s with %d file(s)",
                    self.loop_id, owner, repo, branch, len(files_dict))
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
        # Clear the pending payload (contains the GitHub token).
        self.context.pop("ship_pending", None)
        self.state = LoopState.COMPLETED
        await _persist_session(self.db, self._doc())
        # Iter 212m-115 — release the concurrent-loop lock on success.
        try:
            from services.loop_safety import release_loop_lock
            await release_loop_lock(
                self.db, self.project_id or "_no_project",
                self.user_id, self.loop_id,
            )
        except Exception as e:                              # noqa: BLE001
            logger.debug("release_loop_lock on COMPLETED failed: %r", e)
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
        """User clicked Cancel.  Iter 212m-131 — bug #8 fix:
        previously this only set `self._cancelled = True` and emitted
        an ABORTED event, but the in-flight pipeline task continued
        running (including any blocking LLM HTTP calls) because the
        old `_should_stop()` check only fired between PHASES.  Now
        we actually cancel the asyncio task, which propagates
        `CancelledError` into the await chain and stops the LLM call
        within ~1s."""
        if self.state in _TERMINAL:
            return
        self._cancelled = True
        # Cancel the background pipeline task so the LLM call /
        # subprocess inside the current phase aborts immediately
        # instead of waiting for the next inter-phase check.
        task = self._pipeline_task
        if task is not None and not task.done():
            task.cancel()
            # Don't await it here — that would block the HTTP handler
            # that called us.  The done-callback we set in confirm()
            # will clean up self._pipeline_task.
        self.state = LoopState.ABORTED
        await _persist_session(self.db, self._doc())
        # Iter 212m-131 — release the concurrent-loop lock so the
        # user can immediately try again without waiting for the
        # 5-min cooldown.
        try:
            from services.loop_safety import release_loop_lock
            await release_loop_lock(
                self.db, self.project_id or "_no_project",
                self.user_id, self.loop_id,
            )
        except Exception as e:                              # noqa: BLE001
            logger.debug("release_loop_lock on cancel failed: %r", e)
        await self._emit(LoopState.ABORTED, self.phase or "?",
                         message="Loop cancelled by user.")

    # ── Submit files for verification (Phase C) ──────────────────────
    async def submit_files(self, files: list[dict]) -> None:
        """Register a list of `{path, content}` objects that the loop's
        VERIFY phase should lint + self-heal.  Idempotent — repeated
        calls replace the prior list (use this when the caller has new
        revisions).

        Iter 212m-131 — bug #9 fix: refuse mid-Execute writes.
        Previously a caller could overwrite `submitted_files` while
        the Execute phase was actively populating it from the LLM,
        causing the verify phase to see a mixed state.  Now we
        reject writes once the engine has moved past AWAITING_
        CONFIRMATION — at that point the engine itself owns the
        submitted_files list.
        """
        if self.state not in (LoopState.IDLE,
                              LoopState.AWAITING_CONFIRMATION):
            raise ValueError(
                f"submit_files refused — engine is {self.state.value}; "
                "the loop is already running and owns the file list."
            )
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
        # Iter 212m-115 safety #4 — feed the circuit breaker + release
        # the concurrent-loop lock so the user can retry (after the
        # cooldown if they've hit FAIL_THRESHOLD).
        try:
            from services.loop_safety import (
                record_loop_failure, release_loop_lock,
            )
            proj_key = self.project_id or "_no_project"
            await record_loop_failure(
                self.db, proj_key, self.user_id, phase, reason,
            )
            await release_loop_lock(
                self.db, proj_key, self.user_id, self.loop_id,
            )
        except Exception as e:                              # noqa: BLE001
            logger.debug("safety hooks on _fail failed: %r", e)
        await self._emit(LoopState.FAILED, phase,
                         message=reason, requires_user_action=True)


_TERMINAL = {LoopState.COMPLETED, LoopState.FAILED, LoopState.ABORTED}


# ─── Adapter layer ────────────────────────────────────────────────────

async def _generate_plan(user_id: str, project_id: Optional[str],
                         user_message: str) -> dict:
    """Call the existing LLM to produce a structured plan.  Returns a
    dict with: title, files_to_change (list of paths), bullets (list
    of strings), estimated_time.

    Iter 212m-116 — Inject a COMPACT repo map (file paths + symbols +
    imports per file, no raw content) into the planner system prompt
    when a graph exists. Reduces planner token cost ~60% on
    repo-aware projects without losing the signal needed to pick the
    right files."""
    from services.llm import call_llm_with_meta

    # Inject the compact repo map if available (cheap — single DB read).
    # Iter 212m-117 — Auto-refresh stale graphs (>30 min old) before
    # building the map so the planner always sees the current state of
    # the user's repo. Incremental build (iter 113) means an unchanged
    # repo costs ZERO new LLM tokens to refresh — just a regex pass +
    # blob_sha diff. Best-effort: a refresh failure must NOT block plan.
    repo_map_block = ""
    try:
        from cto_services.db import get_db
        from services.repo_map import build_repo_map
        db = get_db()
        # Stale-check the graph; if >30 min old, rebuild silently.
        if db is not None and project_id:
            try:
                from services.graph_builder import build_graph
                import time as _time
                prior = await db.project_graphs.find_one(
                    {"project_id": project_id, "user_id": user_id},
                    {"_id": 0, "built_at": 1},
                )
                age = _time.time() - (
                    (prior or {}).get("built_at") or 0
                ) if prior else float("inf")
                if (not prior) or age > 30 * 60:
                    proj = await db.cto_projects.find_one(
                        {"project_id": project_id, "user_id": user_id},
                        {"_id": 0, "github_owner": 1, "github_repo": 1,
                         "github_branch": 1, "github_token": 1},
                    )
                    if proj and proj.get("github_owner") and proj.get("github_repo"):
                        from routers.security_scan import _decrypt_pat
                        tok = await _decrypt_pat(user_id, proj.get("github_token"))
                        if not tok:
                            u = await db.dev_users.find_one(
                                {"user_id": user_id}, {"_id": 0, "github": 1},
                            )
                            tok = ((u or {}).get("github") or {}).get("access_token")
                        if tok:
                            logger.info(
                                "[plan] graph stale (age=%.0fs) — rebuilding silently",
                                age if age != float("inf") else -1,
                            )
                            await build_graph(
                                db=db, project_id=project_id, user_id=user_id,
                                gh_token=tok,
                                gh_owner=proj["github_owner"],
                                gh_repo=proj["github_repo"],
                                branch=proj.get("github_branch") or "main",
                            )
            except Exception as e:                          # noqa: BLE001
                logger.debug("[plan] silent graph refresh skipped: %r", e)
        rm = await build_repo_map(db, project_id, user_id)
        if rm.get("has_map"):
            repo_map_block = (
                "\n\n--- COMPACT REPO MAP ({n} files, {c} chars) ---\n"
                "Each line: <path> [<layer>] · symbols: ... · imports: ... · // <desc>\n"
                "Use this to pick exact files to change. Do NOT invent paths.\n\n"
                "{m}\n"
                "--- END REPO MAP ---\n"
            ).format(n=rm["file_count"], c=rm["char_count"], m=rm["map_text"])
            logger.info(
                "[plan] injected repo map: %d files, %d chars",
                rm["file_count"], rm["char_count"],
            )
    except Exception as e:                              # noqa: BLE001
        logger.debug("[plan] repo map injection skipped: %r", e)

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
        + repo_map_block
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


# Iter 212m-132 — Diff-only Loop scan helper.
async def _run_diff_security_scan(
    db, user_id: str, project_id: Optional[str],
    submitted_files: list[dict],
) -> dict:
    """Scan ONLY the files this loop just touched, and only flag
    findings whose line was added or modified by the patch.

    Returns a shape compatible with `_do_scan`'s consumers:
        {
          "summary":   {"total": N, "by_severity": {...}},
          "findings":  [...],
          "diff_mode": True,
          "scanned_files": M,
          "preexisting_skipped": K,
        }

    Why this exists (founder report — iter 212m-132):
      The full-repo scan in `_run_security_scan` was flagging
      pre-existing vulns in files the Loop never touched, blocking
      every commit at the SCAN phase even when the patch itself
      was clean.  We now restrict the scan to the patch surface AND
      use `vanguard_verify_agent.changed_lines_for_file` to drop
      findings outside the changed-line set.
    """
    if not submitted_files:
        return {"summary": {"total": 0, "by_severity": {}},
                "findings": [], "diff_mode": True,
                "skipped_reason": "no_submitted_files"}
    if not project_id:
        return {"summary": {"total": 0, "by_severity": {}},
                "findings": [], "diff_mode": True,
                "skipped_reason": "no_project"}
    if db is None:
        return {"summary": {"total": 0, "by_severity": {}},
                "findings": [], "diff_mode": True,
                "skipped_reason": "no_db"}

    proj = await db.cto_projects.find_one(
        {"project_id": project_id, "user_id": user_id},
        {"_id": 0, "github_owner": 1, "github_repo": 1,
         "github_branch": 1, "github_token": 1},
    )
    if not proj:
        return {"summary": {"total": 0, "by_severity": {}},
                "findings": [], "diff_mode": True,
                "skipped_reason": "no_project_doc"}

    from routers.security_scan import _decrypt_pat, _scan_text
    from services.github_api_writer import fetch_file as gh_fetch
    from services.vanguard_verify_agent import (
        changed_lines_for_file, filter_findings_to_changed_lines,
    )
    import httpx

    owner  = proj.get("github_owner") or ""
    repo   = proj.get("github_repo")  or ""
    branch = proj.get("github_branch") or "main"
    pat    = await _decrypt_pat(user_id, proj.get("github_token"))
    if not (owner and repo and pat):
        return {"summary": {"total": 0, "by_severity": {}},
                "findings": [], "diff_mode": True,
                "skipped_reason": "no_github_linkage"}

    # Fetch the BASE content for each changed file (the head SHA on
    # the loop branch).  If a file doesn't exist on the base (new
    # file), `base` stays empty → every line counts as "changed".
    line_map: dict[str, set[int]] = {}
    findings: list[dict] = []
    skipped_preexisting = 0
    async with httpx.AsyncClient(timeout=30.0) as client:
        for f in submitted_files:
            path = f.get("path") or ""
            new_content = f.get("content") or ""
            if not path or not new_content:
                continue
            try:
                base_content = await gh_fetch(
                    client, owner, repo, path, branch, pat,
                )
            except Exception as e:                       # noqa: BLE001
                logger.debug("diff scan base fetch failed %s: %r", path, e)
                base_content = None
            base = base_content or ""
            # Compute changed-line set.
            line_map[path] = changed_lines_for_file(base, new_content)
            # Run the regex scan on the NEW content.
            raw = _scan_text(path, new_content)
            findings.extend(raw)

    # Drop pre-existing findings.
    kept, dropped = filter_findings_to_changed_lines(findings, line_map)
    skipped_preexisting = len(dropped)

    # Build severity histogram on kept findings only.
    sev: dict[str, int] = {
        "critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0,
    }
    for fnd in kept:
        s = (fnd.get("severity") or "").lower()
        if s in sev:
            sev[s] += 1

    return {
        "summary":              {"total": len(kept), "by_severity": sev},
        "findings":             kept,
        "diff_mode":            True,
        "scanned_files":        len(submitted_files),
        "preexisting_skipped":  skipped_preexisting,
    }


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


# ─── Iter 212m-144 — cross-worker engine rehydration ─────────────────
#
# `_LIVE` is per-process in-memory. With multiple uvicorn workers in
# PROD, `start()` can land on worker A (engine created in A's _LIVE)
# while `confirm()` lands on worker B (lookup returns None → 404
# "Loop not found or already finished"). The Mongo `loop_sessions`
# collection always has the latest persisted state — we just need
# to reconstruct an engine instance from that doc when the local
# worker has no record.
#
# Rehydration is safe ONLY for engines that are PAUSED (waiting on a
# user action like AWAITING_CONFIRMATION / PAUSED_FOR_USER). If a
# loop is mid-execution on worker A and worker B tries to rehydrate,
# we'd end up with two engines racing against each other — explicit
# guard below.
async def lookup_or_rehydrate(
    db, loop_id: str,
) -> Optional["LoopEngine"]:
    """Local lookup first; fall back to rebuilding the engine from
    the Mongo session doc when the loop is in a PAUSED state.

    Returns None if no session exists OR the persisted state is
    mid-execution (caller gets a clean 404 instead of a stale split-
    brain rehydration).
    """
    eng = _LIVE.get(loop_id)
    if eng is not None:
        # Iter 212m-176 — split-brain guard. A local engine that is
        # sitting IDLE (awaiting user input) may be a stale copy: the
        # pipeline may have advanced on another worker (confirm landed
        # there via rehydration). If Mongo disagrees with the idle
        # local copy, evict it and fall through to a fresh rehydrate —
        # otherwise confirm/confirm-ship silently no-op against the
        # stale state (observed in PROD: ship 200 but nothing pushed).
        _IDLE = {LoopState.AWAITING_CONFIRMATION, LoopState.PAUSED_FOR_USER}
        if db is not None and eng.state in _IDLE:
            _doc = await load_session(db, loop_id)
            _dstate = (_doc or {}).get("state")
            _dphase = (_doc or {}).get("phase") or "plan"
            if _doc and (_dstate != eng.state.value or _dphase != eng.phase):
                logger.warning(
                    "[loop %s] STALE local engine (local=%s/%s mongo=%s/%s) "
                    "— evicting and rehydrating", loop_id,
                    eng.state.value, eng.phase, _dstate, _dphase,
                )
                _LIVE.pop(loop_id, None)
                eng = None
        if eng is not None:
            return eng
    if db is None:
        return None
    doc = await load_session(db, loop_id)
    if not doc:
        return None
    persisted_state = doc.get("state")
    # Only rehydrate PAUSED loops — these are safe because no
    # pipeline task is running anywhere right now (the engine on
    # worker A finished its current phase, persisted, and exited
    # _run_pipeline waiting for a queue event we will never put).
    _PAUSED_STATES = {
        LoopState.AWAITING_CONFIRMATION.value,
        LoopState.PAUSED_FOR_USER.value,
    }
    if persisted_state not in _PAUSED_STATES:
        logger.warning(
            "[loop %s] refused rehydration in state %s "
            "(not paused); caller will get 404",
            loop_id, persisted_state,
        )
        return None
    # Iter 212m-169 — Rebuild BINContext during rehydrate so the
    # resumed loop keeps its request-scoped user+project+PAT
    # invariant.  If the PAT no longer decrypts (revoked, HKDF key
    # rotated), the loop still rehydrates but bin_ctx stays None —
    # the fallback DB path in EXECUTE/SHIP will surface a clean
    # "GitHub credentials missing" instead of crashing.
    rehydrated_bin_ctx = None
    try:
        from services.ora_context import build_ora_context
        rehydrated_bin_ctx = await build_ora_context(
            user_id=doc.get("user_id") or "",
            project_id=doc.get("project_id"),
            db=db,
            is_founder=False,   # rehydrated sessions default non-founder
        )
    except Exception as _bce:                            # noqa: BLE001
        logger.warning(
            "[loop %s] REHYDRATE — bin_ctx rebuild failed: %r "
            "(engine will DB-fallback per phase)",
            loop_id, _bce,
        )

    eng = LoopEngine(
        db=db, loop_id=loop_id,
        user_id=doc.get("user_id") or "",
        project_id=doc.get("project_id"),
        user_message=(doc.get("context") or {}).get(
            "original_request", "",
        ),
        bin_ctx=rehydrated_bin_ctx,
    )
    # Restore state machine + accreted context.
    try:
        eng.state = LoopState(persisted_state)
    except ValueError:
        eng.state = LoopState.AWAITING_CONFIRMATION
    eng.phase = doc.get("phase") or "plan"
    if doc.get("context"):
        eng.context = doc["context"]
    register(eng)
    logger.info(
        "[loop %s] REHYDRATED from Mongo (state=%s phase=%s) "
        "in worker pid=%s",
        loop_id, eng.state.value, eng.phase, _PID,
    )
    return eng


# Defensive shutdown hook for tests.
def reset_registry() -> None:                            # noqa: D401
    _LIVE.clear()


# Re-export the canonical SSE event factory so the router can synthesise
# events when an engine isn't in this worker's memory (e.g. /status).
new_event = _new_event
