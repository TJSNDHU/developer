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
# Iter 308 — MUST be strictly greater than any phase's own timeout,
# otherwise the reaper can kill a legitimately-progressing phase before
# its own `_with_budget` timeout fires. The largest phase budget is
# `execute` at 420 s; +60 s safety margin.
STALE_AFTER_S = max(PHASE_TIMEOUTS_S.values()) + 60
# Iter 308 v2 — Hard startup invariant so a future engineer bumping
# any PHASE_TIMEOUTS_S entry without adjusting STALE_AFTER_S gets a
# LOUD import-time failure instead of a silent regression. This
# `assert` runs when `loop_engine` is first imported by main.py,
# guaranteeing prod can never boot with a broken invariant.
assert STALE_AFTER_S > max(PHASE_TIMEOUTS_S.values()), (
    f"STALE_AFTER_S ({STALE_AFTER_S}s) must exceed the largest "
    f"phase budget ({max(PHASE_TIMEOUTS_S.values())}s) — otherwise "
    "the resume_stale reaper can kill a legitimately-progressing "
    "phase (iter 308 root cause of the 2.5-hr stuck-execute bug)."
)
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
# Iter 278 — heartbeat cadence during slow single-file LLM generation.
# Named constant (not a magic literal) so future tuning is greppable
# and CI-invariants can lock the value against silent regressions.
# 6s pairs with the frontend LoopLiveFeed's 10s gap threshold — at
# most one keepalive shows before real progress or the gap fallback.
HEARTBEAT_INTERVAL_S = 6.0


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
    """Iter 308 v2 — Called every 60 s by main.py's background sweeper
    (previously ONLY at startup, which is what allowed the 2.5 hr
    stuck-execute prod bug to fester). Find any session stuck in
    EXECUTING/VERIFYING/SCANNING whose `updated_at` is older than
    STALE_AFTER_S and flip it to PAUSED_FOR_USER with a clear message.

    Iter 308 v3 — MUST ALSO PERSIST `last_event` with the rescue signal,
    otherwise cross-worker SSE clients polling Mongo's `last_event`
    field never see the rescue (they keep seeing the stale
    "EXECUTE START" for the rest of eternity, which is exactly what
    the founder's screenshot showed for 2.5 hours). Every rescue now
    writes a real event dict into `last_event` with:
      * state       = paused_for_user
      * phase       = the frozen phase (execute / verify / scan / ship)
      * message     = human-readable "server restarted mid-loop"
      * data.rescued = true (frontend flag)
      * requires_user_action = true (unlocks the retry CTA)

    Returns the count of sessions that were rescued.
    """
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
        frozen_phase = doc.get("phase") or "?"
        rescue_event = _new_event(
            loop_id,
            LoopState.PAUSED_FOR_USER,
            frozen_phase,
            step=0,
            total_steps=5,
            message=(f"Server restarted mid-{frozen_phase}; "
                     "session paused — retry when ready."),
            requires_user_action=True,
            data={"sub_step": "rescued_stale",
                  "rescued":  True,
                  "resume_reason": "server_restart_mid_loop"},
        )
        await db.loop_sessions.update_one(
            {"loop_id": loop_id},
            {"$set": {"state": LoopState.PAUSED_FOR_USER.value,
                      "resume_reason": "server_restart_mid_loop",
                      "updated_at":  _now(),
                      "last_event":  rescue_event}},
        )
        await _log_error(
            db, loop_id, frozen_phase,
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
        # ── Iter 328 · Deploy 2 — in-memory pre-execution content cache.
        # Populated during EXECUTE from the `current` file body fetched
        # for the parliament prompt. Used by _do_ship to compute the
        # per-file line-diff summary that ShipPendingCard renders
        # BEFORE the founder approves the ship. NOT persisted to Mongo
        # (context row bloat + PAT-adjacent data hygiene) — falls back
        # to `original_bytes_by_path` byte-delta after cross-worker
        # rehydration.
        self._orig_content_cache: dict[str, str] = {}

    # ── Public API ────────────────────────────────────────────────────
    async def start(self) -> AsyncIterator[dict]:
        """Run PLAN → wait for confirmation event → continue.  This is
        an async generator so the router can stream events directly to
        the client."""
        await self._emit(LoopState.PLANNING, "plan",
                         message="Reading the request and drafting a plan…")
        try:
            # Iter 309 · Pre-Phase-1 (bug_verify_312 fix) — the plan
            # phase is loop-originated too, so its LLM call must
            # write a `loop.plan` row to `ora_chat_usage`.  Wrap in
            # the same contextvars scope `_with_budget` uses for
            # execute/verify/scan/ship — no other semantics change.
            from services.loop_token_ledger import loop_call_context
            async with loop_call_context(
                loop_id=self.loop_id, phase_tag="plan",
                user_id=self.user_id,
            ):
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
                    from services.pat_vault import decrypt_pat as _decrypt_pat  # iter 212m-225 boundary fix
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

        # ── Iter 322 — Plan-phase latency profile persistence ──────
        # `_generate_plan` attaches `_profile` with per-segment
        # wall-clock (graph refresh vs repo map read vs LLM call).
        # Persist to loop_run_log so the speed-diagnostic dashboard
        # can identify where the 21s went in loop_678eea28436c4e-
        # class incidents. Stripped from `plan` before shipping to
        # the frontend so the approval card stays clean.
        try:
            _prof = None
            if isinstance(plan, dict):
                _prof = plan.pop("_profile", None)
            if _prof:
                await self.db.loop_run_log.insert_one({
                    "loop_id":     self.loop_id,
                    "user_id":     self.user_id,
                    "project_id":  self.project_id,
                    "kind":        "plan_latency_profile",
                    "profile":     _prof,
                    "ts":          _iso(),
                })
        except Exception as e:                          # noqa: BLE001
            logger.debug(
                "[loop %s] plan latency profile persistence "
                "skipped (non-fatal): %r", self.loop_id, e,
            )

        # ── Iter 289 — Plan-phase path grounding diagnostic ─────────
        # The plan prompt says "Do NOT invent paths" but nothing
        # ENFORCES it. If loop_1f8/loop_bff class of failures had the
        # planner emitting paths that do not exist in the connected
        # repo, we currently discover it only when Parliament produces
        # 0 files (or worse, silently creates orphan files). Compute
        # the intersection of the plan's files_to_change against the
        # repo map and attach `ungrounded_paths` + `known_paths` to
        # the plan so the approval UI + audit log carry the evidence.
        # This is diagnostic-only in this iter — it does NOT block —
        # so a legitimate "create new file" plan still works. But it
        # ends the "did the LLM invent paths?" ambiguity for good.
        try:
            if self.project_id and isinstance(plan, dict):
                plan_paths = [str(p).strip() for p in
                              (plan.get("files_to_change") or [])
                              if str(p).strip()]
                if plan_paths:
                    from services.repo_map import build_repo_map as _brm
                    _rm = await _brm(self.db, self.project_id, self.user_id)
                    map_text = (_rm or {}).get("map_text") or ""
                    known: set[str] = set()
                    for line in map_text.splitlines():
                        line = line.strip()
                        if not line or line.startswith("#"):
                            continue
                        # Each map line begins with "<path> [<layer>] · ..."
                        head = line.split(" ", 1)[0]
                        if head:
                            known.add(head)
                    ungrounded = [p for p in plan_paths if p not in known]
                    plan["ungrounded_paths"] = ungrounded
                    plan["known_paths_count"] = len(known)
                    if ungrounded:
                        logger.warning(
                            "[loop %s] PLAN grounding — %d/%d paths not in "
                            "repo map (may be new files or hallucinations): %s",
                            self.loop_id, len(ungrounded), len(plan_paths),
                            ungrounded[:8],
                        )
                        try:
                            await self.db.loop_run_log.insert_one({
                                "loop_id":    self.loop_id,
                                "user_id":    self.user_id,
                                "project_id": self.project_id,
                                "kind":       "plan_ungrounded_paths",
                                "plan_paths":       plan_paths,
                                "ungrounded_paths": ungrounded,
                                "known_paths_count": len(known),
                                "ts":         _iso(),
                            })
                        except Exception as e:                # noqa: BLE001
                            logger.debug(
                                "[loop %s] plan-grounding audit write "
                                "failed (non-fatal): %r",
                                self.loop_id, e,
                            )
        except Exception as e:                                # noqa: BLE001
            # Never block Plan on a grounding hiccup — this is
            # strictly additive. Log and continue.
            logger.debug(
                "[loop %s] plan-grounding check skipped: %r",
                self.loop_id, e,
            )

        self.context["plan"] = plan
        await _save_plan(self.db, self.loop_id, plan)

        # ── Iter 272 Feature 1.1 — freeze the task spec BEFORE the
        # user's confirmation. The frozen snapshot is what the
        # independent verifier (Iter 272 Feature 1.3) will judge the
        # final diff against. Fixing agents must not see or mutate
        # this row for the rest of the run.
        try:
            from services import loop_task_specs as _lts
            await _lts.freeze(
                self.db,
                loop_id=self.loop_id,
                task_id=getattr(self, "task_id", None),
                user_id=self.user_id,
                project_id=self.project_id,
                user_message=self.user_message,
                plan=plan,
            )
        except Exception as e:                                # noqa: BLE001
            # Never block the pipeline on a spec-freeze failure — but
            # DO surface it in the audit log so it's visible.
            logger.warning("[loop %s] task spec freeze failed: %r",
                           self.loop_id, e)
            try:
                from services import loop_audit_log as _lal
                await _lal.log(
                    self.db, loop_id=self.loop_id, phase="plan",
                    kind="task_spec_freeze",
                    verdict=_lal.VERDICT_WARN,
                    detail={"error": repr(e)},
                )
            except Exception:
                pass

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
            from routers.trust_level import get_user_trust_level  # arch: allow-router-import — router owns the canonical trust ladder
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
            # Iter 272 Feature 1.5 — no silent check skipping. Every
            # pipeline-level exception, even one that was fully caught
            # here and translated into a graceful _fail() event, MUST
            # leave a row in loop_run_log so drift jobs can see it.
            try:
                from services import loop_audit_log as _lal
                await _lal.log(
                    self.db, loop_id=self.loop_id,
                    phase=self.phase or "?",
                    kind=_lal.KIND_SILENT_CATCH,
                    verdict=_lal.VERDICT_FAIL,
                    detail={"exception": repr(e)[:400]},
                )
            except Exception:
                pass
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

        Iter 308 v2 — Now ALSO runs a background heartbeat task for
        the entire duration of the phase. Every HEARTBEAT_INTERVAL_S
        seconds while the phase is in flight, we _emit() an
        EXECUTING/VERIFYING/SCANNING/SHIPPING event with
        `data.sub_step="heartbeat"` and `data.elapsed_s` populated.
        This keeps `last_event` in Mongo fresh, so cross-worker SSE
        clients polling last_event never see a stale event for the
        entire phase duration. Root cause of the user's 2.5 hr
        "stuck on execute — no live details" report: without this
        heartbeat, `generate_files`' internal asyncio.gather()
        blocked silently for up to 60 s × ceil(N/3) with zero
        emission, so watchers saw a frozen UI.

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
        # Iter 308 — canonical phase → LoopState so the heartbeat
        # event carries the correct enum value for the frontend
        # LoopStepBar (matches PHASE_TO_STEP).
        _PHASE_STATE = {
            "plan":      LoopState.PLANNING,
            "execute":   LoopState.EXECUTING,
            "verify":    LoopState.VERIFYING,
            "scan":      LoopState.SCANNING,
            "ship":      LoopState.SHIPPING,
            "self_heal": LoopState.SELF_HEALING,
        }
        for attempt in range(MAX_PHASE_RESTARTS + 1):
            # Iter 308 — Generic heartbeat wrapper. Fires alongside
            # every phase; visible to SSE clients + LoopLiveFeed.
            _hb_done = asyncio.Event()
            _hb_t0   = time.time()

            async def _heartbeat_loop():
                i = 0
                while not _hb_done.is_set():
                    try:
                        await asyncio.wait_for(_hb_done.wait(),
                                               HEARTBEAT_INTERVAL_S)
                        return
                    except asyncio.TimeoutError:
                        pass
                    i += 1
                    elapsed = int(time.time() - _hb_t0)
                    # Iter 308 — only heartbeat while the phase is
                    # actually in flight; a phase that legitimately
                    # transitioned to PAUSED_FOR_USER inside its
                    # coroutine (e.g. scope drift, test-file lock)
                    # must NOT be visually flipped back to a running
                    # state by our heartbeat.
                    if self.state != _PHASE_STATE.get(phase):
                        continue
                    try:
                        # Iter 308 v2 — explicit human-readable
                        # gerund per phase so the "still N..."
                        # message never renders as "executeing".
                        _phase_gerund = {
                            "plan":      "planning",
                            "execute":   "executing",
                            "verify":    "verifying",
                            "scan":      "scanning",
                            "ship":      "shipping",
                            "self_heal": "self-healing",
                        }.get(phase, phase)
                        await self._emit(
                            _PHASE_STATE.get(phase, self.state),
                            phase,
                            step={"plan":1,"execute":2,"verify":3,
                                  "scan":4,"ship":5}.get(phase, 0),
                            total_steps=5,
                            message=(f"Still {_phase_gerund} — {elapsed}s elapsed…"),
                            data={"sub_step":  "heartbeat",
                                  "keepalive": True,
                                  "phase":     phase,
                                  "elapsed_s": elapsed,
                                  "hb_tick":   i},
                        )
                    except Exception as _hbe:                    # noqa: BLE001
                        logger.debug(
                            "[loop %s] heartbeat emit failed "
                            "(phase=%s): %r", self.loop_id, phase, _hbe,
                        )

            _hb_task = asyncio.create_task(_heartbeat_loop())
            try:
                try:
                    # Iter 309 · Pre-Phase-1 — Loop-token accounting.
                    # Wrap the phase coroutine in a contextvar scope so
                    # every downstream LLM call (Council A, Parliament,
                    # verify_agent, healer) writes a `loop.<phase>` row
                    # into `ora_chat_usage`.  Zero API-surface change
                    # for non-loop LLM callers.
                    from services.loop_token_ledger import loop_call_context
                    async with loop_call_context(
                        loop_id=self.loop_id,
                        phase_tag=phase,
                        user_id=self.user_id,
                    ):
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
            finally:
                # Iter 308 — ALWAYS stop the heartbeat before leaving
                # this iteration (return, raise, retry, or fall-through).
                # Without this the heartbeat task leaks into the next
                # phase and continues emitting stale "still executing"
                # events after we've moved on to verify.
                _hb_done.set()
                if not _hb_task.done():
                    try:
                        await asyncio.wait_for(_hb_task, timeout=0.5)
                    except (asyncio.TimeoutError, asyncio.CancelledError,
                            Exception):
                        _hb_task.cancel()

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

        # Iter 331 · Bug 1 fix — the engine never emitted a step="plan"
        # narration, so LoopStepBar's stepTones.plan stayed unset and
        # raw-phase hydration paths rendered PLAN gray during EXECUTE.
        # Emitted HERE (after state=EXECUTING) — emitting inside
        # confirm() would carry state=awaiting_confirmation and flip
        # ChatPanel back to plan_pending (PlanApprovalCard regression).
        await self._narrate("plan", "success",
                            "Plan approved — execution started.")

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
            from services.pat_vault import decrypt_pat as _decrypt_pat  # iter 212m-225 boundary fix  # local import
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

            # ── Iter 288 (j007) — Scope-drift block ─────────────────
            # Compare the paths Execute is about to touch against the
            # WORM-frozen file list captured at plan-approval time. If
            # anything new has appeared (file_selector expansion, a
            # mutated context.plan, a rehydrated engine that re-planned
            # implicitly), we pause the loop with a scope_drift event
            # instead of silently generating extra files. The user
            # then either re-approves or aborts. Previously the
            # verifier only caught this at ship time — after a full
            # generation had already burned tokens (see loop_1f8
            # postmortem, this iter's PRD notes).
            try:
                from services import loop_task_specs as _lts
                spec = await _lts.get(self.db, self.loop_id)
                frozen = (spec or {}).get("frozen_files_to_change") or []
                frozen_set = {str(p).strip() for p in frozen if str(p).strip()}
                if frozen_set:
                    current_set = {str(p).strip() for p in paths if str(p).strip()}
                    extras = sorted(current_set - frozen_set)
                    if extras:
                        logger.warning(
                            "[loop %s] SCOPE DRIFT — plan froze %d files, "
                            "Execute wants %d (extras: %s)",
                            self.loop_id, len(frozen_set),
                            len(current_set), extras[:10],
                        )
                        # Persist to loop_events for audit (j007
                        # traceability row references this collection).
                        try:
                            await self.db.loop_events.insert_one({
                                "loop_id":    self.loop_id,
                                "user_id":    self.user_id,
                                "project_id": self.project_id,
                                "kind":       "scope_drift",
                                "frozen":     sorted(frozen_set),
                                "extras":     extras,
                                "ts":         _iso(),
                            })
                        except Exception as e:                # noqa: BLE001
                            logger.debug(
                                "[loop %s] scope_drift audit write "
                                "failed (non-fatal): %r",
                                self.loop_id, e,
                            )
                        # Pause the loop and hand control back to the
                        # user via a paused_for_user frame — same
                        # pattern the test-file-lock gate uses.
                        self.state = LoopState.PAUSED_FOR_USER
                        await _persist_session(self.db, self._doc())
                        await self._emit(
                            LoopState.PAUSED_FOR_USER, "execute",
                            step=2, total_steps=5,
                            message=(
                                f"Scope drift — plan approved {len(frozen_set)} "
                                f"file(s); agent now wants to touch "
                                f"{len(current_set)}. Approve the expanded "
                                f"scope or abort."
                            ),
                            data={
                                "kind":        "scope_drift",
                                "frozen":      sorted(frozen_set),
                                "extras":      extras,
                                "planned_now": sorted(current_set),
                            },
                            requires_user_action=True,
                        )
                        return
            except Exception as e:                            # noqa: BLE001
                # Never block Execute on a spec-lookup hiccup; log and
                # continue with the existing (weaker) behaviour so the
                # feature is strictly additive.
                logger.warning(
                    "[loop %s] scope-drift check skipped: %r",
                    self.loop_id, e,
                )

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
                        # Iter 276 — emit a REAL per-file event before
                        # entering the Parliament call so the frontend
                        # shows granular sub-step activity instead of a
                        # static "Executing — N file(s) planned" line
                        # for the entire per-file window (which can be
                        # 30-300s per file at PER_FILE_TIMEOUT_S).
                        await self._emit(
                            LoopState.EXECUTING, "execute",
                            step=2, total_steps=5,
                            message=f"Generating {path}…",
                            data={"file": path, "sub_step": "generating"},
                        )
                        # Iter 309 · Narration — pending "file-open".
                        # correlation_id = file path so the paired
                        # "file-write-complete" (or timeout/error)
                        # narration resolves the same line.
                        await self._narrate(
                            step="execute", tone="pending",
                            text=f"Writing {path}",
                            correlation_id=f"execute:{path}",
                        )
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
                        # ── Iter 318 · Bug 1b — capture repo bytes ──
                        # _do_ship + _do_verify use this to enforce the
                        # size-delta rule without a second GitHub fetch.
                        try:
                            obp = self.context.setdefault(
                                "original_bytes_by_path", {},
                            )
                            obp[path] = len(current or "")
                            # ── Iter 328 · Deploy 2 — cache pre-exec
                            # content so _do_ship can build the per-file
                            # line-diff for ShipPendingCard. In-memory
                            # only (never persisted to Mongo).
                            self._orig_content_cache[path] = current or ""
                        except Exception:
                            pass
                        task_text = (
                            f"USER REQUEST:\n{self.user_message}\n\n"
                            f"APPROVED PLAN:\n{plan_title}\n{plan_bullets}\n\n"
                            f"FILE PATH: {path}\n\n"
                            f"--- CURRENT CONTENT ({len(current)} bytes) ---\n"
                            f"{current}\n"
                            f"--- END CURRENT CONTENT ---\n\n"
                            "Return the complete new content for this file. "
                            "No fences. No commentary. Just the file content.\n\n"
                            # ── Iter 318 · Bug 1a — placeholder/elision ban ──
                            # Live incident: LLM emitted "
                            # "`[Rest of existing README content remains "
                            # "unchanged...]` as the file body. The ban must "
                            # "be repeated in the task text (system prompt is "
                            # "not always honoured by every council member)."
                            "STRICT: No elision. No placeholders. Never emit "
                            "'[Rest of ... unchanged]', '... unchanged', "
                            "'<!-- snip -->', '// ... unchanged', '# ...', "
                            "or any marker meaning 'skipped for brevity'. "
                            "If a line is not being modified, INCLUDE THE "
                            "LINE VERBATIM. Output MUST be the complete "
                            "final file body — not a diff, not a summary."
                        )
                        try:
                            # Iter 309 · Batch-2 Item 5 — the per-file
                            # heartbeat (iter 278) has been REMOVED
                            # to eliminate duplicate/near-simultaneous
                            # heartbeat pairs during EXECUTE.  The
                            # generic phase-level heartbeat in
                            # `_with_budget` (iter 308, cadence
                            # HEARTBEAT_INTERVAL_S) is now the SOLE
                            # emitter of `sub_step: "heartbeat"`
                            # frames — this cleanup was demanded
                            # after the founder observed two
                            # heartbeat loops racing per file.
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
                            # Iter 276 — surface the timeout so the user
                            # sees WHY nothing is landing, not just a
                            # silent gap.
                            await self._emit(
                                LoopState.EXECUTING, "execute",
                                step=2, total_steps=5,
                                message=(f"Timed out waiting on {path} "
                                          f"(>{PER_FILE_TIMEOUT_S}s) — skipping"),
                                data={"file": path, "sub_step": "timeout"},
                            )
                            # Iter 309 · Narration — DANGER resolves
                            # the pending "Writing {path}" line red.
                            await self._narrate(
                                step="execute", tone="danger",
                                text=f"Timed out on {path}",
                                correlation_id=f"execute:{path}",
                            )
                            return None
                        except Exception as e:                # noqa: BLE001
                            logger.exception(
                                "[parliament] file %s raised: %r", path, e,
                            )
                            await self._emit(
                                LoopState.EXECUTING, "execute",
                                step=2, total_steps=5,
                                message=f"Error generating {path}: "
                                         f"{type(e).__name__}",
                                data={"file": path, "sub_step": "error"},
                            )
                            # Iter 309 · Narration — DANGER pair.
                            await self._narrate(
                                step="execute", tone="danger",
                                text=f"Error on {path}",
                                correlation_id=f"execute:{path}",
                            )
                            return None
                        if result.get("status") == "success" and result.get("output"):
                            # ── Iter 318 · Bug 1a — post-emission ban ──
                            # Even after the strict prompt, an LLM may
                            # still emit placeholders. Grep the content
                            # before letting it land in submitted_files;
                            # a marker hit means REGENERATE (return None
                            # so the file is skipped and surfaced in
                            # per_file_diag as skipped_or_error).
                            try:
                                from services.loop_integrity_guard import (
                                    find_elision_markers,
                                )
                                _hits = find_elision_markers(
                                    result.get("output") or "",
                                )
                            except Exception:
                                _hits = []
                            if _hits:
                                logger.error(
                                    "[loop %s] EXECUTE — %s emitted "
                                    "elision marker %s (%s...) — REJECTED "
                                    "at post-emission guard; file will "
                                    "not enter submitted_files.",
                                    self.loop_id, path,
                                    _hits[0]["pattern"],
                                    _hits[0]["match"][:60],
                                )
                                try:
                                    await self.db.loop_run_log.insert_one({
                                        "loop_id":     self.loop_id,
                                        "user_id":     self.user_id,
                                        "project_id": self.project_id,
                                        "kind":        "executor_elision_rejected",
                                        "path":        path,
                                        "pattern":     _hits[0]["pattern"],
                                        "marker_text": _hits[0]["match"],
                                        "ts":          _iso(),
                                    })
                                except Exception:
                                    pass
                                await self._emit(
                                    LoopState.EXECUTING, "execute",
                                    step=2, total_steps=5,
                                    message=(
                                        f"Rejected placeholder output "
                                        f"for {path} — regenerate needed"
                                    ),
                                    data={
                                        "file":     path,
                                        "sub_step": "elision_rejected",
                                        "pattern":  _hits[0]["pattern"],
                                    },
                                )
                                return None
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
            # Iter 288 (j007 diagnostic) — the previous message
            # ("LLM produced no usable file content") gave the user
            # zero signal about why every per-file attempt returned
            # None. We now capture the per-file result tuples (status
            # + reasoning tail + output-length + finish_reason when
            # the provider surfaces one) and log them to loop_run_log
            # so the NEXT occurrence is diagnosable from the DB
            # instead of the ephemeral console. This is exactly what
            # loop_1f8 / loop_bff needed — the raw evidence didn't
            # persist because we never wrote it.
            per_file_diag: list[dict] = []
            for p, res in zip(paths, _results):
                if res is None:
                    per_file_diag.append({
                        "path":     p,
                        "outcome":  "skipped_or_error",
                    })
                else:
                    per_file_diag.append({
                        "path":     res.get("path"),
                        "outcome":  "success",
                        "bytes":    len((res.get("content") or "")),
                    })
            logger.warning(
                "[loop %s] EXECUTE — generate_files returned 0 files. "
                "Per-file diag: %s", self.loop_id, per_file_diag,
            )
            try:
                await self.db.loop_run_log.insert_one({
                    "loop_id":     self.loop_id,
                    "user_id":     self.user_id,
                    "project_id": self.project_id,
                    "kind":        "execute_empty_output",
                    "planned_paths": paths,
                    "per_file":     per_file_diag,
                    "ts":           _iso(),
                })
            except Exception as e:                            # noqa: BLE001
                logger.debug(
                    "[loop %s] execute_empty_output audit write "
                    "failed (non-fatal): %r", self.loop_id, e,
                )
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
            # Iter 309 · Narration — SUCCESS resolves the pending
            # "Writing {path}" line green (same correlation_id).
            await self._narrate(
                step="execute", tone="success",
                text=f"Wrote {f['path']}",
                correlation_id=f"execute:{f['path']}",
                extra={"index": i, "total": len(generated)},
            )

        self.context["submitted_files"] = generated
        await _persist_session(self.db, self._doc())
        logger.info("[loop %s] EXECUTE DONE — %d files in submitted_files",
                    self.loop_id, len(generated))

    # ── Phase 3 — Verify (Phase C: real ruff/eslint + self-heal) ────
    def _apply_integrity_guard_to_report(
        self, report: dict, file_objs: list[dict],
    ) -> None:
        """Iter 318 · Bug 2 (hardened after bug_testing_agent RCA).

        In-place mutation of a verify_files() report so that:
          1. Every row is re-scanned for elision markers + size-delta
             regardless of linter status (skip / pass / fail).
          2. A guard hit downgrades the row to ok=false, attaches
             `integrity_guard` metadata, and appends a distinct
             `integrity_guard:<rule>` line to `report["errors"]`.
          3. `report["ok"]` is recomputed from the mutated results.

        Why this had to be extracted from `_do_verify`:
        the self-heal loop calls `verify_files()` again on a subset
        and merges those rows BACK into `report["results"]`. Without
        this helper being re-invoked after that merge, a fresh row
        with linter='skip' + ok:true silently overwrote the earlier
        downgrade, restoring the exact `.md skip == pass` incident
        the guard was meant to prevent (bug_testing_agent, Iter 318).
        """
        try:
            from services.loop_integrity_guard import check_file_integrity
            _orig_bytes = self.context.get("original_bytes_by_path") or {}
            _content_by_path = {
                f["path"]: (f.get("content") or "") for f in file_objs
            }
            for _row in report.get("results", []):
                _path = _row.get("path")
                _v = check_file_integrity(
                    path=_path,
                    submitted_content=_content_by_path.get(_path, ""),
                    repo_bytes=int(_orig_bytes.get(_path) or 0),
                    original_request=self.user_message or "",
                    action="edit",
                )
                if not _v:
                    continue
                _row["ok"] = False
                _row["integrity_guard"] = _v
                _reason = _v.get("rule_fired") or "integrity_guard"
                _row["stderr"] = (_row.get("stderr") or "") + (
                    f"\nintegrity_guard: {_reason} "
                    f"(path={_v.get('offending_path')})"
                )
                # Dedupe error lines so repeated sweeps don't grow
                # the error list unboundedly.
                _err_line = f"{_path}: integrity_guard:{_reason}"
                _errors = report.setdefault("errors", [])
                if _err_line not in _errors:
                    _errors.append(_err_line)
            report["ok"] = all(
                r.get("ok") for r in report.get("results", [])
            )
        except Exception as _ig_err:                          # noqa: BLE001
            logger.exception(
                "[loop %s] VERIFY integrity guard crashed: %r",
                self.loop_id, _ig_err,
            )
            report["ok"] = False
            _errors = report.setdefault("errors", [])
            _err_line = f"integrity_guard_error: {type(_ig_err).__name__}"
            if _err_line not in _errors:
                _errors.append(_err_line)

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

        # Iter 309 · Narration — test-run-start pending.
        # correlation_id = "verify:pass_1" pairs with the first
        # verify_files() report.
        await self._narrate(
            step="verify", tone="pending",
            text="Running lint and type checks",
            correlation_id="verify:pass_1",
        )

        # Initial verify pass.
        report = await verify_files(file_objs)

        # ═══════════════════════════════════════════════════════════
        # Iter 318 · Bug 2 — Skip-linter ≠ pass  (INITIAL sweep)
        # Re-applied after every subset reverify inside the self-heal
        # loop below — see `_apply_integrity_guard_to_report`.
        # ═══════════════════════════════════════════════════════════
        self._apply_integrity_guard_to_report(report, file_objs)

        self.context["verification_results"] = report
        if report["ok"]:
            # Iter 309 · Narration — SUCCESS resolves pending line green.
            await self._narrate(
                step="verify", tone="success",
                text="All checks passed",
                correlation_id="verify:pass_1",
            )
            # Iter 272 Feature 1.5 — every Vanguard verdict is
            # audit-logged. A pass is just as important to record
            # as a fail (a drift job may notice the pass-rate
            # trending upward suspiciously).
            try:
                from services import loop_audit_log as _lal
                await _lal.log(
                    self.db, loop_id=self.loop_id, phase="verify",
                    kind=_lal.KIND_VANGUARD, verdict=_lal.VERDICT_PASS,
                    detail={"files": len(file_objs),
                             "self_heal_attempts": 0},
                )
            except Exception:
                pass
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
            # Iter 309 · Narration — WARNING pending. Resolved by the
            # per-round done narration emitted right after the heal
            # inner loop finishes (or by a final danger if the outer
            # loop exhausts all attempts).
            await self._narrate(
                step="verify", tone="warning",
                text=(f"Self-heal attempt {heal_attempt}/{MAX_SELF_HEALS} — "
                      f"rewriting {len(failing_indices)} file(s)"),
                correlation_id=f"verify:heal_{heal_attempt}",
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
            # ── Iter 318 · Bug 2 (post-heal re-sweep) ─────────────
            # bug_testing_agent RCA: `verify_files` on a subset
            # doesn't know about the integrity guard. Without this
            # re-sweep, a `.md → linter: skip` row could flip back
            # to ok:true while the healer either did nothing or
            # escalated, silently reopening the exact incident the
            # guard was built to close. Pre-ship still catches it,
            # but the verify contract must NOT report ok:true for
            # a body that still carries an elision marker.
            self._apply_integrity_guard_to_report(report, file_objs)
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
                # Iter 309 · Narration — SUCCESS resolves the pending
                # heal-attempt line green.
                await self._narrate(
                    step="verify", tone="success",
                    text=f"Self-heal {heal_attempt} fixed all files",
                    correlation_id=f"verify:heal_{heal_attempt}",
                )
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
        # Iter 309 · Narration — DANGER as the FINAL narration for the
        # verify step. The last-event tone is what drives the ECG strip
        # to flatline red for this step (founder Part 1.6 rule).
        await self._narrate(
            step="verify", tone="danger",
            text=f"Verify failed after {MAX_SELF_HEALS} attempts",
            correlation_id="verify:final",
        )

    # ── Phase 4 — Scan (Phase C: real Vanguard via direct internals) ──
    async def _do_scan(self) -> None:
        self.state = LoopState.SCANNING
        self.phase = "scan"
        await self._emit(LoopState.SCANNING, "scan",
                         step=4, total_steps=5,
                         message="Running Vanguard security scan…")
        # Iter 309 · Narration — pending scan-start.
        # correlation_id = "scan:vanguard" pairs with scan-result below.
        await self._narrate(
            step="scan", tone="pending",
            text="Running Vanguard security scan",
            correlation_id="scan:vanguard",
        )
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
                # Iter 309 · Narration — DANGER resolves scan pending
                # red. This is the LAST narration for the scan step
                # before phase transition → ECG flatlines red.
                await self._narrate(
                    step="scan", tone="danger",
                    text=f"{crit} critical finding{'s' if crit != 1 else ''}",
                    correlation_id="scan:vanguard",
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
                # Iter 309 · Narration — WARNING resolves scan pending
                # amber (not red — high alone doesn't block ship).
                # NOTE: this resolves the pending line but scan step
                # still transitions successfully to ship, so the ECG
                # flatlines green — warning tone here just colors this
                # ONE line amber inside the feed history.
                await self._narrate(
                    step="scan", tone="warning",
                    text=f"{high} high finding{'s' if high != 1 else ''}, continuing",
                    correlation_id="scan:vanguard",
                )
            else:
                # Iter 309 · Narration — SUCCESS when scan is clean.
                # Previously silent — this closed the "did the scan
                # even run?" honesty gap that Part 1 audit flagged.
                await self._narrate(
                    step="scan", tone="success",
                    text="Scan clean, no findings",
                    correlation_id="scan:vanguard",
                )

            # ── Iter 212m-190 (Directive Session 2 · Part B) — Full Scan ──
            # Extend the existing Vanguard-only scan with Bug Hunt +
            # HTTP-headers + Docker-CIS on the SAME diff files, gated
            # by change-size so single-file typo fixes stay fast.
            # If Ship was already blocked above (paused_for_user on a
            # Vanguard critical) we skip Full Scan — the user is
            # already reviewing, running more scanners would just
            # add noise.
            if self.state != LoopState.PAUSED_FOR_USER and submitted_files:
                await self._run_full_scan_pass(submitted_files)
        except Exception as e:                          # noqa: BLE001
            # ── Iter 319 · Bug 3 — FAIL-CLOSED on any scan exception ──
            # Live incident: _scan_text NameError crashed the scan
            # but the previous handler wrote scan_results={'error':
            # repr(e)} and RETURNED, letting the state machine
            # proceed to Ship as if the scan had passed. That is
            # the exact wrong default for a security scanner. Now:
            # any exception halts the loop at FAILED with a distinct
            # kind='scan_exception' marker.
            logger.exception(
                "[loop %s] SCAN CRASHED — fail-closed: %r",
                self.loop_id, e,
            )
            await _log_error(self.db, self.loop_id, "scan", repr(e))
            self.context["scan_results"] = {
                "error":       repr(e),
                "fail_closed": True,
            }
            self.context["errors_encountered"].append({
                "phase": "scan",
                "error": f"scan_exception: {type(e).__name__}: {e!r}",
                "ts":    _iso(),
            })
            # Narrate the failure so the ECG ends red on this step.
            try:
                await self._narrate(
                    step="scan", tone="danger",
                    text=f"Scan crashed: {type(e).__name__}",
                    correlation_id="scan:vanguard",
                )
            except Exception:
                pass
            self.state = LoopState.FAILED
            self.phase = "scan"
            await _persist_session(self.db, self._doc())
            try:
                from services.loop_safety import (
                    record_loop_failure, release_loop_lock,
                )
                proj_key = self.project_id or "_no_project"
                await record_loop_failure(
                    self.db, proj_key, self.user_id,
                    "scan", f"scan_exception: {type(e).__name__}",
                )
                await release_loop_lock(
                    self.db, proj_key, self.user_id, self.loop_id,
                )
            except Exception as _safe_err:               # noqa: BLE001
                logger.debug(
                    "[loop %s] scan fail-closed safety hooks: %r",
                    self.loop_id, _safe_err,
                )
            await self._emit(
                LoopState.FAILED, "scan",
                step=4, total_steps=5,
                message=(
                    f"Scan crashed ({type(e).__name__}) — ship "
                    f"blocked. This is fail-closed by design: a "
                    f"broken security scan MUST NOT let the loop "
                    f"reach the ship gate."
                ),
                data={
                    "kind":       "scan_exception",
                    "error_type": type(e).__name__,
                    "error_repr": repr(e)[:400],
                },
                requires_user_action=True,
            )
            return

    async def _run_full_scan_pass(self, submitted_files: list[dict]) -> None:
        """Run the Full-Scan orchestrator on the just-generated files,
        block Ship on self-generated critical/high, and self-heal up to
        MAX_SCAN_HEALS times before surfacing to the user.

        Split out from `_do_scan` so the retry logic is testable in
        isolation and the parent method stays scannable at a glance.
        """
        from services.full_scan_orchestrator import (
            run_full_scan, should_run_full_scan, files_to_text_cache,
            group_findings_for_self_heal,
        )
        from services import loop_full_scan as _lfs

        should_run, reason = should_run_full_scan(submitted_files)
        if not should_run:
            await self._emit(
                LoopState.SCANNING, "scan", step=4, total_steps=5,
                message=f"Full Scan skipped — {reason}",
                data={"full_scan": {"ran": False, "reason": reason}},
            )
            return

        await self._emit(
            LoopState.SCANNING, "scan", step=4, total_steps=5,
            message=f"Full Scan running — {reason}",
        )
        scoped_paths = {f.get("path") or "" for f in submitted_files
                        if f.get("path")}
        result = run_full_scan(files_to_text_cache(submitted_files))
        _lfs.record_scan_health(result)

        # Persist critical/high findings to the backlog collection so
        # Session 3's notification strip has real data to draw from.
        with contextlib.suppress(Exception):
            await _lfs.persist_findings_to_backlog(
                self.db,
                user_id=self.user_id, project_id=self.project_id,
                findings=result.get("findings") or [],
            )

        offending = group_findings_for_self_heal(
            result.get("findings") or [], scoped_paths=scoped_paths,
        )
        self.context["full_scan_results"] = {
            "summary":         result.get("summary"),
            "scanner_status":  result.get("scanner_status"),
            "degraded":        result.get("degraded"),
            "critical_count":  result.get("critical_count"),
            "high_count":      result.get("high_count"),
            "elapsed_seconds": result.get("elapsed_seconds"),
            "self_generated_findings": sum(len(v) for v in offending.values()),
        }

        if not offending:
            await self._emit(
                LoopState.SCANNING, "scan", step=4, total_steps=5,
                message=(
                    f"Full Scan clean — {result['summary']['total']} "
                    f"finding(s) total, 0 in self-generated code "
                    f"({result['elapsed_seconds']}s)."
                ),
                data={"full_scan": {
                    "ran": True,
                    "summary":  result.get("summary"),
                    "degraded": result.get("degraded"),
                    "scanner_status": result.get("scanner_status"),
                }},
            )
            return

        # Ship-block path: run up to MAX_SCAN_HEALS self-heal rounds.
        # The healer used here is the same Parliament-backed healer
        # already used for lint failures — reusing it means one code
        # path, one prompt shape, one behaviour under retry.
        remaining_files = dict(offending)  # {path: [findings]}
        attempt = 0
        while remaining_files and attempt < _lfs.MAX_SCAN_HEALS:
            attempt += 1
            await self._emit(
                LoopState.SCANNING, "scan", step=4, total_steps=5,
                message=_lfs.format_retry_message(attempt, remaining_files),
                data={"full_scan_retry": attempt,
                      "max_retries": _lfs.MAX_SCAN_HEALS,
                      "affected_files": list(remaining_files.keys())},
            )
            healed_any = await self._heal_full_scan_findings(
                remaining_files, submitted_files,
            )
            if not healed_any:
                # Healer returned nothing usable — no point in more retries.
                break
            # Re-scan JUST the changed files after healing so we don't
            # linearly grow scan cost on each retry.
            result = run_full_scan(files_to_text_cache(submitted_files))
            _lfs.record_scan_health(result)
            remaining_files = group_findings_for_self_heal(
                result.get("findings") or [], scoped_paths=scoped_paths,
            )
            self.context["full_scan_results"]["retry_attempts"] = attempt
            self.context["full_scan_results"]["remaining_after_retry"] = (
                sum(len(v) for v in remaining_files.values())
            )

        if remaining_files:
            self.state = LoopState.PAUSED_FOR_USER
            await _persist_session(self.db, self._doc())
            await self._emit(
                LoopState.PAUSED_FOR_USER, "scan", step=4, total_steps=5,
                message=_lfs.format_ship_block_reason(remaining_files),
                data={"full_scan_blocked": True,
                      "attempts_used":     attempt,
                      "affected_files":    list(remaining_files.keys()),
                      "findings":          [f
                                            for hits in remaining_files.values()
                                            for f in hits][:25]},
                requires_user_action=True,
            )
            return

        # Cleared after 1+ retries → carry on to Ship.
        await self._emit(
            LoopState.SCANNING, "scan", step=4, total_steps=5,
            message=(f"Full Scan cleared after {attempt} self-heal "
                     f"attempt(s) — proceeding to Ship."),
            data={"full_scan": {
                "ran": True,
                "self_healed": True,
                "attempts": attempt,
                "summary":  result.get("summary"),
                "degraded": result.get("degraded"),
                "scanner_status": result.get("scanner_status"),
            }},
        )

    async def _heal_full_scan_findings(
        self,
        offending: dict[str, list[dict]],
        submitted_files: list[dict],
    ) -> bool:
        """Rewrite each offending file via the Parliament healer,
        mutating `submitted_files` in place so the next scan pass sees
        the healed content. Returns True iff at least one file was
        actually changed.

        Iter 212m-229 — Triage layer inserted BEFORE the healer.
        The founder's ask: "kya tumhare (main agent) jitna capable
        hai fix karna" — is our auto-fix as smart as a human?  It
        wasn't — every finding was assumed real and every file got
        a full LLM rewrite. Now:
          • FALSE_POSITIVE findings never reach the healer (they
            get logged to `scanner_feedback` for rule tuning).
          • ARCHITECTURALLY_SAFE findings get a per-line marker
            edit — no LLM roundtrip.
          • DUPLICATES across scanners are merged.
          • DEFERRED items go to backlog, not to a wasteful rewrite.
          • Only REAL_BUG findings hit Parliament.heal.
        """
        from services.fix_triage import apply_triage_before_heal
        healed_any = False

        # Flatten offending {file: [hits...]} into a single findings
        # list for the triage engine.
        flat_findings: list[dict] = []
        file_contents: dict[str, str] = {}
        for path, hits in offending.items():
            file_contents[path] = next(
                (f.get("content", "") for f in submitted_files
                 if f.get("path") == path),
                "",
            )
            for h in hits:
                flat_findings.append({**h, "file": h.get("file") or path})

        # Callback to POST FPs into the scanner_feedback collection
        # so the rules can be improved offline.
        async def _log_fps(fps: list[dict]) -> None:
            if not fps or self.db is None:
                return
            try:
                await self.db.scanner_feedback.insert_many([
                    {
                        "loop_id":    self.loop_id,
                        "user_id":    self.user_id,
                        "finding":    fp,
                        "detected_at": time.time(),
                        "source":     "loop_engine._heal_full_scan",
                    } for fp in fps
                ])
            except Exception as e:                      # noqa: BLE001
                logger.warning("[fix-triage] scanner_feedback write failed: %r", e)

        def _log_fps_sync(fps: list[dict]) -> None:
            # Run the async log in the loop's executor.
            asyncio.create_task(_log_fps(fps))

        # Run triage — real_bugs is a strict subset of the input.
        real_bugs, report = apply_triage_before_heal(
            flat_findings, file_contents, feedback_callback=_log_fps_sync,
        )
        _tr_summary = report.summary()
        logger.info(
            "[loop-heal] triage → real=%d fp=%d arch_safe=%d dup=%d deferred=%d",
            _tr_summary["real_bugs"], _tr_summary["false_positives"],
            _tr_summary["architecturally_safe"], _tr_summary["duplicates"],
            _tr_summary["deferred"],
        )

        # Apply ARCH_SAFE markers directly — no LLM cost.
        for tf in report.architecturally_safe:
            f = tf.finding
            target = next((x for x in submitted_files
                           if x.get("path") == f.get("file")), None)
            if not target or not tf.suggested_marker:
                continue
            content = target.get("content") or ""
            lines_ = content.split("\n")
            idx = f.get("line", 1) - 1
            if 0 <= idx < len(lines_) and tf.suggested_marker not in lines_[idx]:
                lines_[idx] = f"{lines_[idx]}  {tf.suggested_marker}"
                target["content"] = "\n".join(lines_)
                healed_any = True
                logger.info("[loop-heal] arch-safe marker applied: %s:%d",
                            f.get("file"), f.get("line"))

        # Rebuild the per-file offending map from ONLY real bugs.
        real_by_file: dict[str, list[dict]] = {}
        for f in real_bugs:
            real_by_file.setdefault(f["file"], []).append(f)

        # Now heal only what's actually a real bug.
        for path, hits in real_by_file.items():
            target = next((f for f in submitted_files
                           if f.get("path") == path), None)
            if target is None:
                continue
            error_lines = [
                f"L{h.get('line')} [{h.get('severity', '').upper()}] "
                f"{h.get('rule_id')}: {h.get('message') or ''}"
                for h in hits[:10]
            ]
            heal_task = (
                f"Original user request:\n{self.user_message}\n\n"
                f"File path: {path}\n\n"
                f"The Full Scan flagged the following critical/high "
                f"security findings in the code you just wrote. Rewrite "
                f"the file so ALL of these are resolved WITHOUT "
                f"changing the intended functionality. Do not "
                f"introduce new vulnerabilities.\n\n"
                f"--- FULL SCAN FINDINGS ---\n"
                + "\n".join(error_lines) +
                "\n--- END FINDINGS ---"
            )
            try:
                from core.parliament import Parliament as _P
                _parl = _P(db=self.db)
                heal_result = await _parl.heal(
                    task=heal_task,
                    file_path=path,
                    current_content=target.get("content") or "",
                    last_error="\n".join(error_lines),
                    user_id=self.user_id,
                )
                new_content = (heal_result or {}).get("content") or ""
                if new_content and new_content != target.get("content"):
                    with contextlib.suppress(Exception):
                        await record_backup(
                            self.db, self.loop_id, path,
                            target.get("content") or "",
                        )
                    target["content"] = new_content
                    healed_any = True
            except Exception as e:                      # noqa: BLE001
                logger.warning(
                    "[full-scan-heal] Parliament healer failed on %s: %r",
                    path, e,
                )
        return healed_any

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
            from services.pat_vault import decrypt_pat as _decrypt_pat  # iter 212m-225 boundary fix  # local import
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

        # ═══════════════════════════════════════════════════════════
        # Iter 318 · Bug 1b — PRE-SHIP INTEGRITY GUARD
        # Sweep every file body for (1) elision markers and (2)
        # >70 % size shrink (unless the founder's original prompt
        # explicitly asked for a delete/wipe). A hit halts the ship
        # at `FAILED` with kind='integrity_guard_rejected' so the
        # founder sees WHY the ship was blocked — not the generic
        # "verify failed". Live incident loop_678eea28436c4e would
        # have committed placeholder text with no user-visible
        # rejection reason without this gate.
        # ═══════════════════════════════════════════════════════════
        try:
            from services.loop_integrity_guard import check_file_integrity
            _orig_bytes = self.context.get("original_bytes_by_path") or {}
            _violations: list[dict] = []
            for _p, _c in files_dict.items():
                _v = check_file_integrity(
                    path=_p,
                    submitted_content=_c or "",
                    repo_bytes=int(_orig_bytes.get(_p) or 0),
                    original_request=self.user_message or "",
                    action="edit",
                )
                if _v:
                    _violations.append(_v)
            if _violations:
                _first = _violations[0]
                logger.error(
                    "[loop %s] SHIP BLOCKED — integrity guard: %d "
                    "violation(s). First: %s",
                    self.loop_id, len(_violations), _first,
                )
                self.context["integrity_guard"] = {
                    "violations": _violations,
                    "blocked_at": _iso(),
                }
                self.context.pop("ship_pending", None)
                try:
                    from services import loop_audit_log as _lal
                    await _lal.log(
                        self.db, loop_id=self.loop_id, phase="ship",
                        kind=_lal.KIND_SHIP_GATE,
                        verdict=_lal.VERDICT_FAIL,
                        detail={
                            "blocker":    "integrity_guard_rejected",
                            "rule_fired": _first.get("rule_fired"),
                            "path":       _first.get("offending_path"),
                            "count":      len(_violations),
                        },
                    )
                except Exception:
                    pass
                self.state = LoopState.FAILED
                self.phase = "ship"
                self.context["errors_encountered"].append({
                    "phase": "ship",
                    "error": (
                        f"integrity_guard_rejected: "
                        f"{_first.get('rule_fired')} in "
                        f"{_first.get('offending_path')}"
                    ),
                    "ts":    _iso(),
                })
                await _log_error(
                    self.db, self.loop_id, "ship",
                    f"integrity_guard_rejected: {_first}",
                    context=self.context,
                )
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
                    LoopState.FAILED, "ship",
                    step=5, total_steps=5,
                    message=(
                        f"Ship blocked: {_first.get('rule_fired')} in "
                        f"{_first.get('offending_path')} — refusing to "
                        f"commit potentially destructive content."
                    ),
                    data={
                        "kind":       "integrity_guard_rejected",
                        "violations": _violations,
                        "first":      _first,
                    },
                    requires_user_action=True,
                )
                return
        except Exception as _ig_err:                          # noqa: BLE001
            # A guard-implementation crash MUST NOT silently allow
            # a ship — fail closed with a distinct error.
            logger.exception(
                "[loop %s] SHIP integrity guard crashed: %r",
                self.loop_id, _ig_err,
            )
            await self._fail_ship(
                f"integrity_guard_error: {type(_ig_err).__name__}"
            )
            return

        # ═══════════════════════════════════════════════════════════
        # Iter 272 — HELD-OUT VERIFICATION GATE
        # Runs AFTER Vanguard has already passed but BEFORE the manual
        # ship confirmation / L3 auto-ship. Two independent gates:
        #   (a) Feature 1.2 — diff classifier: any test/fixture file
        #       touched → force human review, EVEN for L3.
        #   (b) Feature 1.3 — independent verifier: fresh-context LLM
        #       judges the diff against the FROZEN task spec.
        # A "no" from either gate blocks the ship. Both write to
        # loop_run_log so nothing can be silently swallowed.
        # ═══════════════════════════════════════════════════════════
        gate_files = [{"path": p, "content": c}
                       for p, c in files_dict.items()]
        try:
            from services import loop_diff_classifier as _ldc
            classified = _ldc.classify(gate_files)
        except Exception as e:                                # noqa: BLE001
            logger.warning("[loop %s] diff classify failed: %r",
                           self.loop_id, e)
            classified = {"source": [], "tests": [],
                           "test_touched": False, "test_lines": []}
        try:
            from services import loop_audit_log as _lal
            await _lal.log(
                self.db, loop_id=self.loop_id, phase="ship",
                kind=_lal.KIND_TEST_TOUCH,
                verdict=(_lal.VERDICT_FAIL if classified["test_touched"]
                          else _lal.VERDICT_PASS),
                detail={
                    "tests_touched": classified["tests"],
                    "test_lines":    classified["test_lines"],
                    "source_files":  classified["source"][:20],
                },
            )
        except Exception:
            pass

        # Independent verifier (Feature 1.3). Fail-CLOSED on
        # verdict="no"; skips (no_llm / no_spec) do NOT block but
        # are logged as WARN.
        try:
            from services import loop_independent_verifier as _liv
            verifier_result = await _liv.verify(
                self.db, loop_id=self.loop_id, files=gate_files,
            )
        except Exception as e:                                # noqa: BLE001
            logger.warning("[loop %s] independent verifier crash: %r",
                           self.loop_id, e)
            verifier_result = {"verdict": "skipped_no_llm",
                                "reason": f"crash:{type(e).__name__}",
                                "verifier_model": ""}

        try:
            from services import loop_audit_log as _lal
            verdict = verifier_result.get("verdict", "no")
            await _lal.log(
                self.db, loop_id=self.loop_id, phase="ship",
                kind=_lal.KIND_INDEPENDENT,
                verdict=(_lal.VERDICT_PASS if verdict == "yes"
                          else (_lal.VERDICT_FAIL if verdict == "no"
                                else _lal.VERDICT_WARN)),
                detail={
                    "verifier_model": verifier_result.get("verifier_model"),
                    "reason":         verifier_result.get("reason"),
                    "latency_s":      verifier_result.get("latency_s"),
                },
            )
        except Exception:
            pass

        # Decide the gate outcome.
        requires_human_review = classified["test_touched"]
        verifier_rejected     = verifier_result.get("verdict") == "no"

        if verifier_rejected:
            # Hard-fail path: verifier said no. Do NOT ship.
            self.state = LoopState.PAUSED_FOR_USER
            self.context["independent_verifier"] = {
                "verdict": "no",
                "reason":  verifier_result.get("reason", ""),
                "model":   verifier_result.get("verifier_model", ""),
            }
            try:
                from services import loop_audit_log as _lal
                await _lal.log(
                    self.db, loop_id=self.loop_id, phase="ship",
                    kind=_lal.KIND_SHIP_GATE, verdict=_lal.VERDICT_FAIL,
                    detail={"blocker": "independent_verifier_rejected",
                             "reason":   verifier_result.get("reason", "")},
                )
            except Exception:
                pass
            await _persist_session(self.db, self._doc())
            await self._emit(
                LoopState.PAUSED_FOR_USER, "ship",
                step=5, total_steps=5,
                message=("Independent verifier rejected the diff: "
                         f"{verifier_result.get('reason','')}. "
                         "Ship blocked — review the diff or re-run."),
                data={
                    "kind":              "verifier_rejected",
                    "requires_review":   True,
                    "verifier_verdict":  "no",
                    "verifier_reason":   verifier_result.get("reason", ""),
                    "verifier_model":    verifier_result.get(
                                             "verifier_model", ""),
                },
                requires_user_action=True,
            )
            return

        if requires_human_review:
            # Force manual review even for L3 — the fixing agent
            # touched a test file, which is exactly what this gate
            # exists to prevent from silent-shipping.
            self.state = LoopState.PAUSED_FOR_USER
            self.context["requires_human_review"] = True
            self.context["ship_pending"] = {
                "owner": owner, "repo": repo, "branch": branch,
                "token": token, "files": files_dict,
                "commit_message": commit_message,
            }
            try:
                from services import loop_audit_log as _lal
                await _lal.log(
                    self.db, loop_id=self.loop_id, phase="ship",
                    kind=_lal.KIND_HUMAN_REVIEW_HOLD,
                    verdict=_lal.VERDICT_FAIL,
                    detail={"reason":         "test_files_modified",
                             "tests_touched":  classified["tests"],
                             "trust_level":    self.context.get(
                                                   "trust_level")},
                )
            except Exception:
                pass
            await _persist_session(self.db, self._doc())
            await self._emit(
                LoopState.PAUSED_FOR_USER, "ship",
                step=5, total_steps=5,
                message=("Test/fixture files were modified — "
                         "human review required regardless of trust level. "
                         "Approve manually to ship."),
                data={
                    "kind":            "human_review_required",
                    "reason":          "test_files_modified",
                    "tests_touched":   classified["tests"],
                    "test_lines":      classified["test_lines"],
                    "owner":           owner,
                    "repo":            repo,
                    "branch":          branch,
                    "file_count":      len(files_dict),
                    "commit_message":  commit_message,
                    "requires_human_review": True,
                },
                requires_user_action=True,
            )
            return
        # ═══════════════════════════════════════════════════════════
        # END Iter 272 gate. Verifier said yes and no test files
        # were touched — normal ship path continues below.
        # ═══════════════════════════════════════════════════════════

        # Iter 212m-111 — PAUSE for manual ship confirmation. The actual
        # `commit_files()` call now lives in `confirm_ship()` which is
        # triggered by POST /loop/{loop_id}/confirm-ship.
        # Iter 212m-117 — L3 trust-level users SKIP the manual gate
        # (auto-ship). L1 never reaches here. L2 is the safe default.
        # ── Iter 328 · Deploy 2 — build the per-file line-diff + surface
        # the Iter 318 integrity-guard verdict so ShipPendingCard can
        # show WHAT is about to ship + WHETHER it passed the safety
        # gate. Fail-open: any diff-compute error just drops the pill
        # (blind-ship remains the pre-Iter-328 default UX, we don't
        # regress it).
        _files_diff: list[dict] = []
        try:
            from services.loop_ship_diff import compute_files_diff
            _files_diff = compute_files_diff(
                orig_contents=self._orig_content_cache,
                new_contents=files_dict,
                orig_bytes_by_path=self.context.get(
                    "original_bytes_by_path") or {},
            )
        except Exception as e:                              # noqa: BLE001
            logger.debug(
                "[loop %s] files_diff compute skipped: %r",
                self.loop_id, e,
            )
            _files_diff = []
        # Integrity verdict — we only reach here if Rule 1/2/3 passed
        # (the guard above sets state=FAILED on any violation and
        # returns), so verdict is "clean". If the guard was somehow
        # bypassed (feature flag OFF, etc.), fall back to "unknown".
        _guard_ran = bool(self.context.get("original_bytes_by_path"))
        _integrity_verdict = "clean" if _guard_ran else "unknown"

        self.context["ship_pending"] = {
            "owner":             owner,
            "repo":              repo,
            "branch":            branch,
            "token":             token,
            "files":             files_dict,
            "commit_message":    commit_message,
            "files_diff":        _files_diff,
            "integrity_verdict": _integrity_verdict,
        }
        if self.context.get("trust_level") == "L3":
            logger.info("[loop %s] L3 auto-ship — skipping manual gate",
                        self.loop_id)
            await self.confirm_ship(True)
            return
        self.state = LoopState.PAUSED_FOR_USER
        await _persist_session(self.db, self._doc())
        logger.info("[loop %s] SHIP PAUSED for manual confirmation — "
                    "%s/%s@%s with %d file(s) · integrity=%s · diff_rows=%d",
                    self.loop_id, owner, repo, branch, len(files_dict),
                    _integrity_verdict, len(_files_diff))
        await self._emit(
            LoopState.PAUSED_FOR_USER, "ship",
            step=5, total_steps=5,
            message=f"Ready to ship {len(files_dict)} file(s) to "
                    f"{owner}/{repo}@{branch}. Click 'Ship to GitHub' to commit.",
            data={
                "kind":              "awaiting_ship",
                "reason":            "awaiting_ship_confirmation",
                "owner":             owner,
                "repo":              repo,
                "branch":            branch,
                "files":             list(files_dict.keys()),
                "file_count":        len(files_dict),
                "commit_message":    commit_message,
                "files_diff":        _files_diff,
                "integrity_verdict": _integrity_verdict,
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

        # Iter 212m-177 — P0-1 idempotency: atomically claim the ship in
        # Mongo before pushing. Split-brain can leave TWO workers each
        # holding a PAUSED_FOR_USER engine for the same loop; only the
        # winner of this find_one_and_update may push — every other
        # caller becomes a no-op that surfaces the existing commit.
        claim = await self.db.loop_sessions.find_one_and_update(
            {
                "loop_id": self.loop_id,
                "context.ship_claimed_at": {"$exists": False},
                "context.commit.sha":      {"$exists": False},
            },
            {"$set": {"context.ship_claimed_at": _now(), "state": "shipping"}},
        )
        if claim is None:
            doc = await self.db.loop_sessions.find_one(
                {"loop_id": self.loop_id}, {"_id": 0, "context.commit": 1, "state": 1})
            existing = ((doc or {}).get("context") or {}).get("commit") or {}
            logger.warning(
                "[loop %s] SHIP already claimed/committed — idempotent no-op "
                "(existing sha=%s)", self.loop_id, existing.get("sha"))
            if existing:
                self.context["commit"] = existing
                self.context.pop("ship_pending", None)
                self.state = LoopState.COMPLETED
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
        # Iter 309 · Narration — pending commit-start.
        # correlation_id = "ship:commit_1" pairs with commit-complete
        # (or ship-fail) narration below.
        await self._narrate(
            step="ship", tone="pending",
            text=f"Committing {len(files_dict)} file(s) to {owner}/{repo}",
            correlation_id="ship:commit_1",
        )
        try:
            from services.github_api_writer import commit_files
            from services.git_identity import (
                resolve_git_identity, build_commit_message,
            )
            # Iter 212m-218 — real developer identity + Conventional
            # Commits + Co-authored-by trailer. The commit_message
            # arriving here may be either the raw user task or an
            # already-formatted `feat: …` string; `build_commit_message`
            # normalises both and adds the `[via ORA]` marker + the
            # co-author trailer.
            author_name, author_email = await resolve_git_identity(
                self.db, self.user_id,
            )
            final_commit_msg = build_commit_message(
                user_message=commit_message,
                summary=commit_message,
            )
            res = await commit_files(
                owner=owner, repo=repo, branch=branch, token=token,
                files=files_dict, commit_message=final_commit_msg,
                author_name=author_name, author_email=author_email,
                progress=None,
            )
            # Keep the persisted message consistent with what actually
            # landed on GitHub — so the UI doesn't show one string and
            # the git log another.
            commit_message = final_commit_msg
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

        # ── Iter 272 Feature 2.1 — record the shipped commit so
        # future runs can detect repeat_touch on the same file paths.
        try:
            from services import loop_outcomes as _lo
            await _lo.record_shipped_commit(
                self.db,
                loop_id=self.loop_id,
                task_id=getattr(self, "task_id", None),
                user_id=self.user_id,
                project_id=self.project_id,
                commit_sha=full_sha or short_sha,
                file_paths=list(files_dict.keys()),
                owner=owner, repo=repo, branch=branch,
            )
        except Exception as e:                                # noqa: BLE001
            logger.warning("[loop %s] outcome record failed: %r",
                           self.loop_id, e)
            try:
                from services import loop_audit_log as _lal
                await _lal.log(
                    self.db, loop_id=self.loop_id, phase="ship",
                    kind="outcome_record",
                    verdict=_lal.VERDICT_WARN,
                    detail={"error": repr(e)},
                )
            except Exception:
                pass

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
        # ── Iter 328 · #3-b · Brain V2 writeback ─────────────────────
        # Fire-and-forget writes to project_brains after every real
        # commit. Callsites 1 + 3 from ora_learning_callsite_proposal:
        #   • update_brain_after_commit — event_log push + recurring
        #     bugs increment
        #   • update_brain_after_task   — full Brain V2 refresh
        # Both wrapped in per-callsite WARNING log so a silent failure
        # (import err / wrong signature / missing db field) is visible
        # in backend.err.log, not swallowed. NEVER blocks the ship
        # path or user-facing state.
        try:
            from services.project_brain import (
                update_brain_after_commit, update_brain_after_task,
            )
            asyncio.create_task(update_brain_after_commit(
                db=self.db,
                project_id=self.project_id or "",
                task_description=(self.context.get("original_prompt") or "")[:200],
                files_changed=list(files_dict.keys()),
                was_correction_applied=bool(self.context.get("integrity_guard")),
                issues_found=[],
                sha=full_sha,
            ))
            asyncio.create_task(update_brain_after_task(
                db=self.db,
                project_id=self.project_id or "",
                user_id=self.user_id,
                changed_files=list(files_dict.keys()),
                task_id=self.loop_id,
                github_token=token, github_owner=owner,
                github_repo=repo, branch=branch,
            ))
        except Exception as e:                              # noqa: BLE001
            logger.warning(
                "brain-write callsite failed: %r "
                "(loop=%s project=%s)",
                e, self.loop_id, self.project_id,
            )
        # Iter 212m-115 — release the concurrent-loop lock on success.
        try:
            from services.loop_safety import release_loop_lock
            await release_loop_lock(
                self.db, self.project_id or "_no_project",
                self.user_id, self.loop_id,
            )
        except Exception as e:                              # noqa: BLE001
            logger.debug("release_loop_lock on COMPLETED failed: %r", e)
        # ── Iter 323 · Bug A backend — narrate BEFORE terminal emit ──
        # Live incident (commit 7bb304d): the ship-success narration
        # was emitted AFTER the state=COMPLETED terminal frame. SSE
        # streams typically close on the terminal frame, so the
        # trailing narration was lost — stepTones.ship stayed
        # "pending" and the LoopStepBar SHIP node stayed orange
        # forever. Reorder: narrate green FIRST, then emit terminal.
        await self._narrate(
            step="ship", tone="success",
            text=f"Shipped {short_sha}",
            correlation_id="ship:commit_1",
            extra={"commit_sha": short_sha, "html_url": html_url},
        )
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
        # Iter 309 · Narration — DANGER resolves the ship pending line
        # red. Last narration for ship step → ECG flatlines red.
        await self._narrate(
            step="ship", tone="danger",
            text=f"Ship failed: {reason[:60]}",
            correlation_id="ship:commit_1",
        )

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
        # Iter 309 · Batch-2 Item 6 (bug_verify_315 fix) — record to
        # the SSE replay buffer at the PRODUCER, not the consumer.
        # If we only record when a stream is consuming, then events
        # emitted while the browser is disconnected are lost — the
        # exact failure mode the buffer exists to prevent.
        try:
            from services.sse_replay_buffer import record as _sse_record
            _sse_record(self.loop_id, ev)
        except Exception:
            pass
        await self.queue.put(ev)
        await _persist_session(self.db, self._doc(extra={"last_event": ev}))
        # ── Iter 315 · Fix 1 — persist phase-transition to loop_events ──
        # Diagnostic aggregator (`services/loop_speed_diagnostic.py::
        # _phase_durations_from_events`) reads `db.loop_events` grouped
        # by (loop_id, phase, ts) to compute per-phase wall-clock. Before
        # Iter 315 only audit kinds (scope_drift, task_spec_freeze,
        # plan_ungrounded_paths) wrote to that collection, so every
        # regular loop reported n:0 for every phase in the speed report.
        # Fix: fire a `state_transition` row per emit. `kind` marker
        # keeps this family distinct from the audit kinds. Fire-and-
        # forget — a Mongo failure must NEVER break the live SSE loop.
        try:
            await self.db.loop_events.insert_one({
                "loop_id":    self.loop_id,
                "user_id":    self.user_id,
                "project_id": self.project_id,
                "kind":       "state_transition",
                "state":      state.value if hasattr(state, "value") else str(state),
                "phase":      phase,
                "ts":         ev.get("timestamp") or _iso(),
                "seq":        ev.get("seq"),
            })
        except Exception as _e:                        # noqa: BLE001
            # Log-only. Diagnostic honesty is worth less than a
            # working loop.
            logger.debug(
                "[loop %s] loop_events state_transition write failed "
                "(non-fatal): %r", self.loop_id, _e,
            )

    # ── Iter 309 · Live Narration (Part 1) ─────────────────────────────
    #
    # `_narrate()` emits a PARALLEL narration event on top of any
    # existing state-transition emits. Frontend filters events by
    # `data.type == "narration"` to render the live-feed line list +
    # the ECG strip per step.
    #
    # DESIGN INVARIANTS (do not break):
    #   1. Zero new async loops / timers. Every narration is emitted
    #      synchronously from an existing code path — the Item 5
    #      heartbeat-count contract test (exactly one
    #      `async def _heartbeat_loop`) must stay green.
    #   2. Backward compat: the narration event is a regular SSE frame
    #      via `_emit()`. Consumers that don't know about narration
    #      (older frontends, tests) just render it as another event.
    #   3. `ts_epoch` is added inside `data` (numeric server time) so
    #      the frontend timer maths don't have to parse the top-level
    #      ISO `timestamp` string every 100ms tick. On SSE reconnect
    #      the frontend recomputes elapsed from `ts_epoch`, never from
    #      client `Date.now()` at receipt.
    #   4. Text rule (founder spec): ≤ 10 words, factual, present-tense
    #      active voice. No filler ("please wait", "hang tight"). The
    #      helper does NOT enforce this — callers must obey.
    #   5. Failure signal: the LAST narration event for a step before
    #      a phase transition must carry `tone="danger"` so the ECG
    #      strip flatlines red. No separate "step failed" event.
    async def _narrate(
        self,
        step: str,                   # plan | execute | verify | scan | ship
        tone: str,                   # pending | success | warning | danger
        text: str,                   # ≤ 10 words, present tense
        correlation_id: str = "",    # pairs pending↔done events
        extra: Optional[dict] = None,
    ) -> None:
        import time as _time
        data: dict = {
            "type":           "narration",
            "tone":           tone,
            "narration_step": step,
            "narration_text": text,
            "correlation_id": correlation_id or "",
            "ts_epoch":       _time.time(),
        }
        if extra:
            data.update(extra)
        # Emit under the CURRENT phase/state so the SSE frame's outer
        # phase field stays coherent with what the engine is actually
        # doing. Narration never changes state; it only observes.
        await self._emit(self.state, self.phase, step=0, total_steps=5,
                         message=text, data=data)

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
    right files.

    Iter 322 — Latency profiling. Live incident loop_678eea28436c4e
    took 21.6s to plan a one-line README edit. Record per-segment
    timings (graph refresh vs repo map read vs LLM call) so the
    speed-diagnostic dashboard can identify where the wall-clock
    actually goes. Returned as `plan['_profile']` — callers stash
    it into loop_run_log under kind='plan_latency_profile'.
    """
    import time as _time
    from services.llm import call_llm_with_meta

    _t0 = _time.monotonic()
    _profile: dict = {
        "graph_refresh_s":  0.0,
        "repo_map_read_s":  0.0,
        "llm_call_s":       0.0,
        "json_parse_s":     0.0,
        "total_s":          0.0,
        "graph_refreshed":  False,
        "repo_map_present": False,
    }

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
                        from services.pat_vault import decrypt_pat as _decrypt_pat  # iter 212m-225 boundary fix
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
                            _t_gr0 = _time.monotonic()
                            await build_graph(
                                db=db, project_id=project_id, user_id=user_id,
                                gh_token=tok,
                                gh_owner=proj["github_owner"],
                                gh_repo=proj["github_repo"],
                                branch=proj.get("github_branch") or "main",
                            )
                            _profile["graph_refresh_s"] = round(
                                _time.monotonic() - _t_gr0, 3,
                            )
                            _profile["graph_refreshed"] = True
            except Exception as e:                          # noqa: BLE001
                logger.debug("[plan] silent graph refresh skipped: %r", e)
        _t_rm0 = _time.monotonic()
        rm = await build_repo_map(db, project_id, user_id)
        _profile["repo_map_read_s"] = round(
            _time.monotonic() - _t_rm0, 3,
        )
        if rm.get("has_map"):
            repo_map_block = (
                "\n\n--- COMPACT REPO MAP ({n} files, {c} chars) ---\n"
                "Each line: <path> [<layer>] · symbols: ... · imports: ... · // <desc>\n"
                "Use this to pick exact files to change. Do NOT invent paths.\n\n"
                "{m}\n"
                "--- END REPO MAP ---\n"
            ).format(n=rm["file_count"], c=rm["char_count"], m=rm["map_text"])
            _profile["repo_map_present"] = True
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
    _profile["llm_call_s"] = round(
        _time.monotonic() - _t0 - _profile["graph_refresh_s"]
        - _profile["repo_map_read_s"], 3,
    )
    content = (meta or {}).get("content", "").strip()
    # Tolerate a stray ```json fence.
    if content.startswith("```"):
        first_nl = content.find("\n")
        content = content[first_nl + 1:]
        if content.endswith("```"):
            content = content[:-3].rstrip()
    _t_jp0 = _time.monotonic()
    try:
        plan = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        plan = {
            "title": "Plan",
            "files_to_change": [],
            "bullets": [content[:500] or "Unable to parse plan."],
            "estimated_time": "?",
            "raw": content[:2000],
        }
    _profile["json_parse_s"] = round(
        _time.monotonic() - _t_jp0, 3,
    )
    _profile["total_s"] = round(_time.monotonic() - _t0, 3)
    # Attach the profile so the caller (`_do_plan`) can persist it.
    if isinstance(plan, dict):
        plan["_profile"] = _profile
    logger.info(
        "[plan] LATENCY PROFILE — total=%.2fs · graph=%.2fs · "
        "repo_map=%.2fs · llm=%.2fs · json=%.2fs · "
        "refreshed=%s · map_present=%s",
        _profile["total_s"], _profile["graph_refresh_s"],
        _profile["repo_map_read_s"], _profile["llm_call_s"],
        _profile["json_parse_s"], _profile["graph_refreshed"],
        _profile["repo_map_present"],
    )
    return plan


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
    from services.pat_vault import decrypt_pat as _decrypt_pat  # iter 212m-225 boundary fix
    # Iter 319 · Bug 3 — restore the missing `_scan_text` import.
    # This function references `_scan_text(...)` at the scan_one
    # inner call site; the import block that once carried it was
    # truncated, producing NameError in every scan on every loop.
    from routers.security_scan import _scan_text
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
    # arch: allow-router-import — scanner internals live in security_scan router until phase-4 refactor
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

    from services.pat_vault import decrypt_pat as _decrypt_pat  # iter 212m-225 boundary fix
    # Iter 319 · Bug 3 — restore the missing `_scan_text` import
    # for the diff-only scan path. Same defect as `_run_security_scan`.
    from routers.security_scan import _scan_text
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
