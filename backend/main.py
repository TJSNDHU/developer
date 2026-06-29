"""
AUREM Dev — Developer AI Platform
Clean FastAPI entry point — wired to all routers from aurem_cto
"""
import os
import time
import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv()  # MUST run before importing routers/services that read env at module load

# Routers
from routers.deploy import router as deploy_router
from routers.vault import router as vault_router
from routers.stacks import router as stacks_router
from routers.domain import router as domain_router
from routers.github_bot import router as github_router
from routers.harden import router as harden_router
from routers.trust import router as trust_router
from routers.chat_commits import router as chat_commits_router
from routers.engagement import router as engagement_router
from routers.unlock import router as unlock_router
from routers.projects import router as projects_router
from routers.auth import router as auth_router
from routers.mfa import router as mfa_router  # Iter 212m-20 — admin 2FA
from routers.chat import router as chat_router
from routers.github_oauth import router as github_oauth_router
from routers.cto_projects import router as cto_projects_router
from routers.automations import router as automations_router
from routers.upload import router as upload_router
from routers.admin import router as admin_router
from routers.support import router as support_router
from routers.payments import router as payments_router
from routers.mcp import router as mcp_router, mcp_discovery_root
from routers.vercel import router as vercel_router  # Iter 212m-84 — Vercel platform tools for ORA
from routers.oauth import router as oauth_router
from routers.usage import router as usage_router
from routers.lint_preview import router as lint_preview_router
from routers.shipwall import router as shipwall_router
from routers.wrapped import router as wrapped_router
from routers.hosted_deploy import router as hosted_deploy_router
from routers.github_deploy import router as github_deploy_router   # iter 123
from routers.thinking_hints import router as thinking_hints_router  # iter 158
from routers.repo_indexing import router as repo_indexing_router    # Iter 212m-30 PR-2
from routers.founder_offer import router as founder_offer_router    # Iter 212m-30 PR-2
from routers.onboarding import router as onboarding_router          # Iter 212m-32 nudge emails
from routers.admin_vanguard import router as admin_vanguard_router  # Iter 212m-42 vanguard admin toggle
from routers.security_scan import router as security_scan_router    # Iter 212m-55 1-click vuln scanner
from routers.vanguard_ci import router as vanguard_ci_router        # Iter 212m-120 Phase 1: trufflehog CI ingest
from routers.fix_pipeline import router as fix_pipeline_router      # Iter 212m-121 bulk + SSE fix pipeline
from routers.repo_status import router as repo_status_router        # Iter 212m-125 live repo connection ping
from routers.codebase_health import router as codebase_health_router # Iter 212m-72 5-category health scanner
from routers.loop          import router as loop_router             # Iter 212m-60 Loop Mode engine
from routers.diagram       import router as diagram_router          # Iter 212m-61 /diagram
from routers.feature_window import router as feature_window_router  # Iter 212m-64 system map
from services.codebase_indexer import router as codebase_router
from services.daily_digest import schedule_daily_digest



async def _schedule_daily_evals() -> None:
    """Iter 124g — once-per-day persona quality eval at EVAL_HOUR_UTC.
    Writes one row into ora_eval_runs; the admin tile + 30-day trend
    chart light up automatically. Swallows all failures so a transient
    LLM outage never crashes the boot loop.

    Iter 124i — hard 12-minute wall cap on the whole battery + the
    runner respects EVAL_PROMPT_TIMEOUT_S (default 30s) per prompt.
    These two guards together make the cron incapable of pegging the
    pod (was OOM-killed / liveness-probe-killed at iter 124g first fire)."""
    import asyncio as _aio
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td
    target = int(os.environ.get("EVAL_HOUR_UTC", "3"))
    cron_budget = float(os.environ.get("EVAL_TOTAL_BUDGET_S", "720"))  # 12 min
    while True:
        now = _dt.now(_tz.utc)
        nxt = now.replace(hour=target, minute=0, second=0, microsecond=0)
        if nxt <= now:
            nxt = nxt + _td(days=1)
        await _aio.sleep((nxt - now).total_seconds())
        try:
            from evals.runner import run as _run_evals
            r = await _aio.wait_for(_run_evals(quick=False), timeout=cron_budget)
            s = r.get("summary") or {}
            logger.info(
                f"🧪 daily eval: ok={r.get('ok')} "
                f"passed={s.get('passed')}/{s.get('total')} "
                f"hard_fails={s.get('hard_fails')}"
            )
        except _aio.TimeoutError:
            logger.warning(
                f"daily eval cron exceeded total budget ({cron_budget}s) — aborted"
            )
            await _aio.sleep(3600)
        except Exception as e:
            logger.warning(f"daily eval cron failed: {e!r}")
            await _aio.sleep(3600)   # avoid tight loop on persistent failure


load_dotenv()

# Iter 45 + 48 — Sentry (full-coverage error monitoring). Opt-in via SENTRY_DSN.
# In dev/preview without DSN it stays inert — zero perf cost.
#
# Iter 48 — bumped to full coverage per the friendly-reply-no-real-action
# class of bugs: every unhandled exception, FastAPI request span, slow
# requests (> 5s), MongoDB calls, and background task crashes are now
# captured. Set SENTRY_DSN in production env to activate.
def _sentry_filter(event, hint):
    """Drop noisy events before sending to Sentry.
    - 4xx HTTPExceptions are not bugs.
    - Connection-reset on SSE streams are client-side.
    """
    exc_info = hint.get("exc_info") if hint else None
    if exc_info:
        exc_type = exc_info[0].__name__ if exc_info[0] else ""
        exc_msg  = str(exc_info[1] or "")
        # Skip 4xx HTTPException (we already return clean 4xx JSON)
        if exc_type == "HTTPException":
            status = getattr(exc_info[1], "status_code", 500)
            if 400 <= status < 500:
                return None
        if "Connection lost" in exc_msg or "ClientDisconnect" in exc_msg:
            return None
    return event


_SENTRY_DSN = os.getenv("SENTRY_DSN", "").strip()
SENTRY_ACTIVE = False
if _SENTRY_DSN:
    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.starlette import StarletteIntegration
        from sentry_sdk.integrations.asyncio import AsyncioIntegration
        from sentry_sdk.integrations.pymongo import PyMongoIntegration

        sentry_sdk.init(
            dsn=_SENTRY_DSN,
            environment=os.getenv("SENTRY_ENV", "production"),
            release=os.getenv("SENTRY_RELEASE", "aurem-dev@1.0.0"),
            # Performance — 10% sampling by default keeps quota tight
            # but every error is still captured at 100%.
            traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.1")),
            profiles_sample_rate=float(os.getenv("SENTRY_PROFILES_SAMPLE_RATE", "0.0")),
            send_default_pii=False,
            attach_stacktrace=True,
            # Iter 128 — disable Sentry's auto-discovery of integrations.
            # With it ON (default), sentry_sdk.init() probes EVERY known
            # integration target (django, flask, celery, aws_lambda,
            # clickhouse, cohere, google.genai, openai, huggingface_hub,
            # falcon, gql, …) and IMPORTS each one whose package is
            # installed. In our case that pulled ~3 s of cold imports
            # (google.genai alone is 285 ms, openai 185 ms, etc.) onto
            # the critical path BEFORE uvicorn could bind port 8001 —
            # nginx upstream saw connection refused and the K8s
            # readiness probe killed the pod. We don't use any of
            # those frameworks; only the four below are real.
            auto_enabling_integrations=False,
            integrations=[
                FastApiIntegration(transaction_style="endpoint"),
                StarletteIntegration(transaction_style="endpoint"),
                AsyncioIntegration(),
                PyMongoIntegration(),
            ],
            # Drop low-value events client-side
            ignore_errors=[
                # Auth failures aren't bugs — they're user errors.
                "HTTPException",
                # Rate-limit 429s are expected
                "RateLimitExceeded",
            ],
            before_send=_sentry_filter,
        )
        SENTRY_ACTIVE = True
        logging.getLogger(__name__).info(
            "Sentry active — env=%s, traces=%.0f%%",
            os.getenv("SENTRY_ENV", "production"),
            float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.1")) * 100,
        )
    except Exception as _se:
        logging.getLogger(__name__).warning("Sentry init failed: %r", _se)

# Iter 45 — slowapi rate limiting (per-IP). Hand-rolled to avoid
# decorator/dep-injection collision.
from services.rate_limiter import check_rate_limit, client_ip_from_request

# Services
from cto_services.db import set_db
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

MONGO_URL = os.getenv("MONGO_URL", "")
DB_NAME   = os.getenv("DB_NAME", "aurem_dev")
JWT_SECRET = os.getenv("JWT_SECRET")
if not JWT_SECRET:
    raise RuntimeError("JWT_SECRET must be set")

START_TIME = time.time()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("AUREM Dev starting...")
    # Iter 127 — cold-start fix. uvicorn doesn't bind port 8001 until the
    # lifespan.startup phase completes, so any awaited work here blocks
    # the entire HTTP listener. The previous flow ran a synchronous
    # `mongo.admin.command("ping")` (~15-17 s of TLS + SRV lookup on
    # cold Atlas connections) followed by ~3-4 more awaits (index
    # ensure, collection bootstrap, deploy event log). Net effect:
    # nginx upstream got 111/ECONNREFUSED for ~19 s every deploy and
    # the pod was killed by the liveness probe → CrashLoopBackOff.
    #
    # The fix: create the Motor client (cheap, non-blocking — it
    # connects lazily on first query) and offload ALL the
    # nice-to-have boot work into background tasks. Lifespan now
    # returns in <50 ms; uvicorn binds the port immediately; the
    # background tasks finish on their own time and log their result.
    try:
        # Iter 212m-70 — production-grade connection pool. Without
        # these, Motor silently caps at maxPoolSize=100 and a traffic
        # spike (e.g. a signup wave) starves connections. The values
        # below are sized for our current Atlas M10 tier:
        #   • maxPoolSize=50   — 50 concurrent sockets per worker (we
        #                        run 2 uvicorn workers in prod, so up
        #                        to 100 total — safe under M10's 1500
        #                        connection ceiling)
        #   • minPoolSize=5    — keep 5 warm so the first request
        #                        after idle doesn't pay TCP+TLS setup
        #   • maxIdleTimeMS    — recycle sockets after 30 s of idle
        #                        so Atlas's 10-minute idle-kill never
        #                        surprises us mid-request
        #   • connectTimeoutMS — fail fast on partition; 10 s is the
        #                        Atlas-recommended default
        #   • retryWrites=True — default in Atlas, made explicit so a
        #                        single transient WriteConflict
        #                        auto-retries without surfacing to
        #                        the caller
        app.state.mongo = AsyncIOMotorClient(
            MONGO_URL,
            maxPoolSize=50,
            minPoolSize=5,
            maxIdleTimeMS=30_000,
            serverSelectionTimeoutMS=5_000,
            connectTimeoutMS=10_000,
            retryWrites=True,
        )
        app.state.db = app.state.mongo[DB_NAME]
        set_db(app.state.db)
        logger.info("✅ MongoDB client created (lazy connect)")
    except Exception as e:
        logger.warning(f"⚠️  MongoDB client init failed: {e}")
        app.state.mongo = None
        app.state.db    = None

    import asyncio as _asyncio

    # Iter 25 — daily digest scheduler (runs forever, fires at DIGEST_HOUR_UTC)
    app.state.digest_task = _asyncio.create_task(schedule_daily_digest())

    # Iter 212m-60 — Loop Mode G3 resume.  Sweep any session stuck in
    # an executing phase from a prior worker crash and flip it to
    # PAUSED_FOR_USER with a clear reason so the frontend can prompt.
    async def _resume_stale_loops():
        try:
            if app.state.db is None:
                return
            from services.loop_engine import resume_stale
            n = await resume_stale(app.state.db)
            if n:
                logger.info("loop_engine: rescued %d stale session(s)", n)
        except Exception as e:                          # noqa: BLE001
            logger.warning("loop_engine resume_stale failed: %r", e)
    _asyncio.create_task(_resume_stale_loops())

    # Iter 212m-115 safety — Create the unique index for the
    # concurrent-loop lock collection so we can never have two active
    # loops on the same {project_id, user_id}.
    async def _ensure_loop_safety_indexes():
        try:
            if app.state.db is None:
                return
            from services.loop_safety import ensure_loop_lock_index
            await ensure_loop_lock_index(app.state.db)
            # Index on loop_failures for the circuit-breaker window query.
            await app.state.db.loop_failures.create_index(
                [("project_id", 1), ("user_id", 1), ("occurred_at", -1)],
                name="ix_loop_fail_window",
            )
        except Exception as e:                            # noqa: BLE001
            logger.warning("loop_safety indexes failed: %r", e)
    _asyncio.create_task(_ensure_loop_safety_indexes())

    # Iter 212m-128 — Orphan-detection for fix jobs.  Any `fix_jobs`
    # row still flagged `status:"running"` was killed by the
    # previous pod's restart and has no live asyncio task — flip to
    # `"orphaned"` so the UI can offer Restart instead of letting
    # the user stare at a hung "Fix in progress" forever.
    async def _orphan_running_fix_jobs():
        try:
            if app.state.db is None:
                return
            from services.fix_job_manager import mark_running_orphaned
            n = await mark_running_orphaned(app.state.db)
            if n:
                logger.info("fix_job_manager: orphaned %d running job(s) "
                            "from prior pod", n)
            # Also ensure the index used by /list (user_id + status +
            # started_at) so the lookup stays fast as the collection
            # grows.
            await app.state.db.fix_jobs.create_index(
                [("user_id", 1), ("status", 1), ("started_at", -1)],
                name="ix_fix_jobs_user_status_started",
            )
            await app.state.db.fix_jobs.create_index(
                [("job_id", 1)], unique=True, name="ux_fix_jobs_job_id",
            )
        except Exception as e:                            # noqa: BLE001
            logger.warning("fix_jobs orphan sweep failed: %r", e)
    _asyncio.create_task(_orphan_running_fix_jobs())

    # Iter 212m-129 — ORA fix-learning indexes.  Idempotent index
    # creation for the two new analytics collections the scan + fix
    # pipelines now write to (`ora_fix_learning`, `ora_scan_learning`).
    async def _ensure_ora_learning_indexes():
        try:
            if app.state.db is None:
                return
            from services.ora_fix_learning import ensure_indexes
            await ensure_indexes(app.state.db)
            logger.info("📚 ora_fix_learning + ora_scan_learning "
                        "indexes ensured")
        except Exception as e:                            # noqa: BLE001
            logger.warning("ora_fix_learning indexes failed: %r", e)
    _asyncio.create_task(_ensure_ora_learning_indexes())
    # Iter 212m-32 — hourly onboarding nudge cron. Sends the
    # "connect a repo" email to users 24h after signup (and again at
    # 72h if still no repo). Idempotent via the `onboarding_emails`
    # audit log; safe to run every hour. Opt-in flag so dev pods
    # without RESEND_API_KEY don't spam each other's inboxes.
    if os.environ.get("ENABLE_ONBOARDING_NUDGE", "1").lower() in ("1", "true", "yes"):
        try:
            from services.onboarding_email import nudge_cron
            app.state.nudge_task = _asyncio.create_task(nudge_cron(3600))
            logger.info("📧 onboarding nudge cron enabled (hourly)")
        except Exception as e:
            app.state.nudge_task = None
            logger.warning("nudge_cron not started: %r", e)
    else:
        app.state.nudge_task = None
    # Iter 153 — nightly MongoDB backup (03:00 UTC, keeps 7 days).
    # Guarded behind ENABLE_DB_BACKUP=1 so dev environments without
    # mongodump installed don't spam warnings.
    if os.environ.get("ENABLE_DB_BACKUP", "1").lower() in ("1", "true", "yes"):
        try:
            from services.db_backup import backup_cron
            app.state.backup_task = _asyncio.create_task(backup_cron())
            logger.info("🗄️ nightly DB backup cron enabled (03:00 UTC, 7-day retention)")
        except Exception as e:
            app.state.backup_task = None
            logger.warning("db backup cron not started: %r", e)
    else:
        app.state.backup_task = None
    # Iter 124g — daily persona-quality eval at EVAL_HOUR_UTC.
    # OPT-IN via ENABLE_EVAL_CRON=1 env var. When the cron fired at 03:00
    # UTC on prod (Iter 124g initial deploy), 22 sequential LLM calls
    # combined with the project-scoping Mongo round-trips ran the pod for
    # ~45 minutes and tripped either the K8s liveness probe or the memory
    # limit, sending the pod into a CrashLoopBackOff. Disabled by default
    # until we move the heavy battery into a separate cron-only sidecar
    # or chunk the prompts across several wakes.
    if os.environ.get("ENABLE_EVAL_CRON", "").lower() in ("1", "true", "yes"):
        app.state.eval_task = _asyncio.create_task(_schedule_daily_evals())
        logger.info("🧪 daily persona-eval cron enabled (ENABLE_EVAL_CRON=1)")
    else:
        app.state.eval_task = None
        logger.info("🧪 daily persona-eval cron disabled (set ENABLE_EVAL_CRON=1 to enable)")

    # Iter 127 — background bootstrap. None of this blocks the HTTP
    # listener. Each step logs its own success/failure so operators can
    # still audit boot health from the log stream.
    async def _bg_bootstrap():
        # Verify Mongo connectivity (1 ping). Logged but not fatal.
        if app.state.mongo is not None:
            try:
                await app.state.mongo.admin.command("ping")
                logger.info("✅ MongoDB ping OK")
            except Exception as _e:
                logger.warning(f"⚠️  MongoDB ping failed (will retry on demand): {_e}")
        # Iter 40 — ORA council logs indexes (idempotent, safe to re-run)
        try:
            from services.ora_council_logger import ensure_indexes as _ora_idx
            await _ora_idx()
        except Exception as _e:
            logger.warning(f"ora council index ensure failed: {_e}")
        # Iter 116 — Idempotent collection bootstrap.
        try:
            from scripts.init_prod_collections import init_prod_collections
            result = await init_prod_collections(app.state.db)
            if result.get("created"):
                logger.info("📦 collections created on boot: %s", result["created"])
            if result.get("errors"):
                logger.warning("init_prod_collections errors: %s", result["errors"])
        except Exception as _e:
            logger.warning(f"init_prod_collections failed: {_e}")
        # Iter 158 — seed default thinking_hints (idempotent, never
        # overwrites admin edits, only inserts missing hint_ids).
        try:
            from services.thinking_hints import ensure_default_hints
            inserted = await ensure_default_hints(app.state.db)
            if inserted:
                logger.info("💡 thinking_hints seeded: %d entries", inserted)
        except Exception as _e:
            logger.warning(f"thinking_hints seed failed: {_e}")
        # Iter 165 — warm_start_jobs TTL index. Auto-delete completed
        # warm-start jobs after 1 hour so the collection never grows
        # unbounded. Idempotent; safe to re-run on every boot.
        try:
            await app.state.db.warm_start_jobs.create_index(
                "started_at",
                expireAfterSeconds=3600,
                background=True,
            )
            logger.info("🔥 warm_start_jobs TTL index ensured (1h)")
        except Exception as _e:
            logger.warning(f"warm_start_jobs TTL index failed: {_e}")

        # Iter 182 — OAuth 2.1 + PKCE TTL indexes (for Claude Directory).
        # oauth_codes self-purge 10 min after `expires_at`, api_keys after
        # `expires_at` (30-day token TTL). Mongo's TTL monitor runs every
        # 60s so worst-case cleanup is ~60s past expiry — fine for OAuth.
        try:
            await app.state.db.oauth_codes.create_index(
                "expires_at",
                expireAfterSeconds=0,
                background=True,
            )
            await app.state.db.api_keys.create_index(
                "expires_at",
                expireAfterSeconds=0,
                background=True,
                partialFilterExpression={"source": "oauth"},
            )
            logger.info("🔐 oauth TTL indexes ensured (codes + api_keys[oauth])")
        except Exception as _e:
            logger.warning(f"oauth TTL indexes failed: {_e}")

        # Iter 212m-28 — TTL index on repo_context_timings so the
        # per-turn instrumentation never grows unbounded. 7 days is
        # enough for week-over-week regression checks.
        try:
            await app.state.db.repo_context_timings.create_index(
                "ts",
                expireAfterSeconds=7 * 24 * 60 * 60,
                background=True,
                name="ts_ttl_7d",
            )
            logger.info("📊 repo_context_timings TTL index ensured (7d)")
        except Exception as _e:
            logger.warning("repo_context_timings TTL index failed: %r", _e)

        # Iter 123 — wire deploy_logger.
        try:
            from services.deploy_logger import log_deploy_event
            evt = await log_deploy_event(app.state.db, trigger="boot")
            if evt:
                logger.info(
                    "📌 deploy recorded: %s %s",
                    evt.get("commit_sha", "")[:7],
                    evt.get("branch", ""),
                )
        except Exception as _e:
            logger.warning(f"log_deploy_event failed: {_e}")
        # Iter 123 — wire github_deploy_service DB so it doesn't depend on
        # legacy `server.db` fallback. Service handles connect/push-fix PRs.
        try:
            from services import github_deploy_service as _gh
            _gh.set_db(app.state.db)
        except Exception as _e:
            logger.warning(f"github_deploy_service.set_db failed: {_e}")

    app.state.bootstrap_task = _asyncio.create_task(_bg_bootstrap())
    yield
    if getattr(app.state, "digest_task", None):
        app.state.digest_task.cancel()
    if getattr(app.state, "nudge_task", None):
        app.state.nudge_task.cancel()
    if getattr(app.state, "eval_task", None):
        app.state.eval_task.cancel()
    if getattr(app.state, "bootstrap_task", None):
        app.state.bootstrap_task.cancel()
    if app.state.mongo:
        app.state.mongo.close()
    logger.info("AUREM Dev shutdown")


app = FastAPI(
    title="AUREM Dev API",
    version="1.0.0",
    lifespan=lifespan,
    # Iter 140 — expose interactive docs at /api/docs (avoids
    # default /docs being eaten by frontend SPA routing).
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    description=(
        "AUREM CTO Developer AI — reads your GitHub repo, writes "
        "code, ships commits. Authenticate with Bearer token from "
        "/api/aurem-dev/auth/login."
    ),
)

# CORS lockdown. allow_origins=["*"] meant ANY website could hit the API.
# Now we read ALLOWED_ORIGINS from env (comma-separated, settable in
# production), with auremcto.com as a safe default. Localhost dev ports
# are still allowed for tooling. Wildcard subdomain for the preview pod
# stays in place via allow_origin_regex.
_ALLOWED_ORIGINS = [
    o.strip()
    for o in os.getenv(
        "ALLOWED_ORIGINS",
        "https://auremcto.com,https://www.auremcto.com,"
        "http://localhost:3000,http://localhost:5173",
    ).split(",")
    if o.strip()
]
# Honour the legacy FRONTEND_URL env if set — keeps existing deploys working.
_frontend_url = os.getenv("FRONTEND_URL", "").strip()
if _frontend_url and _frontend_url not in _ALLOWED_ORIGINS:
    _ALLOWED_ORIGINS.append(_frontend_url)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    # Wildcard regex covers BOTH preview pods AND Emergent's production
    # routing layer. The production K8s ingress lands on either
    # *.emergent.host (default) or *.deploy.emergentcf.cloud (per the
    # nginx upstream we saw in iter 123c logs). Customers reach us via
    # the explicit https://auremcto.com domain in allow_origins above —
    # this regex is the fallback while DNS / Cloudflare cuts over.
    allow_origin_regex=(
        r"^https://.*\.("
        r"preview\.emergentagent\.com"
        r"|emergent\.host"
        r"|deploy\.emergentcf\.cloud"
        r")$"
    ),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
    allow_headers=["Authorization", "Content-Type", "X-Requested-With"],
)


# Iter 123g — GZip compression for every non-trivial response.
# JSON payloads (admin endpoints, /usage/me, /shipwall, code-surface)
# shrink 5–10× on the wire. minimum_size=512 skips tiny responses where
# the gzip-header overhead would be worse than the savings.
# SSE streams are excluded by Starlette automatically because they use
# StreamingResponse without Content-Length.
from starlette.middleware.gzip import GZipMiddleware
app.add_middleware(GZipMiddleware, minimum_size=512, compresslevel=5)


# ── Iter 212m-55 — LPDoS protection (request body size cap) ──
# Reject any request whose Content-Length advertises > 2 MB BEFORE
# Starlette buffers it into memory. Chat prompts are typically <8 KB;
# repo-context payloads from F12 capture stay under 200 KB; only
# pathological actors send 100+ MB JSON to memory-exhaust the worker.
# Configurable via MAX_REQUEST_BYTES; the /api/aurem-dev/uploads/*
# routes opt-out by tagging request.state.bypass_body_limit = True.
_DEFAULT_MAX_BYTES = int(os.getenv("MAX_REQUEST_BYTES", str(2 * 1024 * 1024)))
_BODY_LIMIT_BYPASS_PREFIXES = (
    "/api/aurem-dev/uploads",
    "/api/aurem-dev/cto-projects/repo/upload",
)


@app.middleware("http")
async def _body_size_guard(request, call_next):
    if request.method in ("POST", "PUT", "PATCH"):
        # Allow uploads route through (chunked file uploads).
        path = request.url.path or ""
        if not any(path.startswith(p) for p in _BODY_LIMIT_BYPASS_PREFIXES):
            cl = request.headers.get("content-length")
            if cl and cl.isdigit() and int(cl) > _DEFAULT_MAX_BYTES:
                from fastapi.responses import JSONResponse
                return JSONResponse(
                    {"detail": f"Request body too large (max {_DEFAULT_MAX_BYTES} bytes)"},
                    status_code=413,
                )
    return await call_next(request)


# ── Iter 212m-55 — NoSQL operator injection guard ──
# Strict deny-list for MongoDB query operators that should NEVER
# appear in a user-supplied JSON body. `$where` allows arbitrary JS
# execution server-side; `$expr` with $function lets attackers craft
# data-driven RCE chains. We reject at the API boundary so individual
# routes don't need per-field validation logic.
#
# This is DEFENCE IN DEPTH — all our existing routes already use
# strict Pydantic schemas, but a future loose-schema route would
# silently inherit this protection.
_NOSQL_DENY_KEYS = {
    "$where", "$expr", "$function", "$accumulator",
    "$lookup",  # rare but lets attacker traverse to other collections
}


def _contains_nosql_op(obj, depth: int = 0) -> str | None:
    """Recursively walk dict/list and return the first banned key
    found, or None when clean. Capped at depth=12 so a deeply-nested
    payload can't pin the CPU."""
    if depth > 12:
        return None
    if isinstance(obj, dict):
        for k in obj.keys():
            if isinstance(k, str) and k in _NOSQL_DENY_KEYS:
                return k
            hit = _contains_nosql_op(obj[k], depth + 1)
            if hit:
                return hit
    elif isinstance(obj, list):
        for item in obj:
            hit = _contains_nosql_op(item, depth + 1)
            if hit:
                return hit
    return None


@app.middleware("http")
async def _nosql_op_guard(request, call_next):
    """Pre-flight only: rejects if Content-Length suggests a body but
    we cannot safely parse the body here without breaking downstream
    receive() chains in BaseHTTPMiddleware. Deep recursive check is
    handled in the pure-ASGI NoSQLOpASGIGuard mounted below."""
    return await call_next(request)


# ── Iter 212m-55 fix — Pure-ASGI NoSQL guard that DOES parse the body ──
# Why pure ASGI: BaseHTTPMiddleware (which @app.middleware("http")
# wraps) buffers requests through anyio memory streams, so replacing
# request._receive after .body() corrupts the downstream consumer
# (RuntimeError: Unexpected message received). A pure ASGI app reads
# the wire-level receive() messages itself, validates the body, then
# forwards the SAME bytes downstream — no broken chains.

class NoSQLOpASGIGuard:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)
        if scope.get("method") not in ("POST", "PUT", "PATCH"):
            return await self.app(scope, receive, send)
        path = scope.get("path") or ""
        if any(path.startswith(p) for p in _BODY_LIMIT_BYPASS_PREFIXES):
            return await self.app(scope, receive, send)
        # Content-Type sniff.
        headers = dict(scope.get("headers") or [])
        ct = headers.get(b"content-type", b"").decode("latin-1", "ignore").lower()
        if "application/json" not in ct:
            return await self.app(scope, receive, send)

        # Collect the full body.
        chunks: list[bytes] = []
        more = True
        while more:
            msg = await receive()
            if msg["type"] != "http.request":
                # Disconnect mid-read; bail to downstream so it can 499 naturally.
                return await self.app(scope, receive, send)
            chunks.append(msg.get("body") or b"")
            more = msg.get("more_body", False)
        raw = b"".join(chunks)

        # Validate.
        if raw:
            import json as _json
            try:
                parsed = _json.loads(raw)
            except _json.JSONDecodeError:
                parsed = None
            if parsed is not None:
                hit = _contains_nosql_op(parsed)
                if hit:
                    client = scope.get("client") or ("?", 0)
                    logger.warning(
                        "nosql_op_blocked path=%s ip=%s key=%s",
                        path, client[0], hit,
                    )
                    from fastapi.responses import JSONResponse
                    resp = JSONResponse(
                        {"detail": "Disallowed query operator in request body"},
                        status_code=400,
                    )
                    return await resp(scope, receive, send)

        # Replay body downstream.
        _body_sent = False
        async def _replay():
            nonlocal _body_sent
            if not _body_sent:
                _body_sent = True
                return {"type": "http.request", "body": raw, "more_body": False}
            # After body delivered, defer to the real receive so the
            # client-disconnect message still propagates correctly.
            return await receive()
        return await self.app(scope, _replay, send)


app.add_middleware(NoSQLOpASGIGuard)


# ── Iter 44 — Security headers (Vanguard hardening) ──
# Drop these on every response. Cheap, zero functional impact.
@app.middleware("http")
async def _security_headers(request, call_next):
    # Iter 48 — slow-request capture. Anything > SLOW_API_MS (default 5s)
    # gets a Sentry warning event with the path + duration. Cheap because
    # Sentry only fires when slow.
    import time as _t
    _start = _t.perf_counter()
    # Iter 137 — defensive guard. FastAPI's BaseHTTPMiddleware (which
    # @app.middleware("http") wraps) has a well-known interaction bug
    # with StreamingResponse + client disconnects: when the SSE
    # generator is cancelled because the browser tab closed, no
    # response is "returned" and Starlette panics with
    # `RuntimeError: No response returned.` We surface a plain 499
    # (client closed request) instead of letting that bubble into
    # prod logs as a 500 storm.
    try:
        response = await call_next(request)
    except Exception as _mw_exc:
        # Cancellations / disconnects on long SSE streams are normal;
        # log at INFO so we still see real 500s, then return a quiet
        # placeholder response so Starlette has SOMETHING to send.
        import asyncio as _aio
        if isinstance(_mw_exc, _aio.CancelledError):
            logger.info(
                "client disconnected mid-stream: %s %s",
                request.method, request.url.path,
            )
        else:
            logger.exception(
                "middleware caught unhandled exception on %s %s",
                request.method, request.url.path,
            )
        return JSONResponse(
            status_code=499,
            content={"detail": "client disconnected or upstream error"},
        )
    _dur_ms = (_t.perf_counter() - _start) * 1000.0
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    # Iter 140 — Content Security Policy + version stamp.
    # Locked-down sources for default/script/style/img/connect/frame/font.
    # 'unsafe-inline' kept for style+script because lucide-react and our
    # markdown renderer inject inline CSS; we'll tighten with nonces in a
    # later iteration once the chat preview pane is on its own iframe.
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://auremcto.com; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: https:; "
        "connect-src 'self' https://auremcto.com wss://auremcto.com "
        "https://openrouter.ai https://api.github.com; "
        "frame-src 'self' blob:; "
        "font-src 'self' data:;"
    )
    response.headers["X-AUREM-Version"] = "1.0.0"
    response.headers["X-Response-Time-Ms"] = f"{_dur_ms:.0f}"
    if SENTRY_ACTIVE:
        _slow_thresh = float(os.getenv("SLOW_API_MS", "5000"))
        # Skip SSE streams (they're long by design)
        if _dur_ms > _slow_thresh and "/chat/stream" not in str(request.url.path):
            try:
                import sentry_sdk
                with sentry_sdk.push_scope() as scope:
                    scope.set_tag("kind", "slow_api")
                    scope.set_tag("path", request.url.path)
                    scope.set_extra("duration_ms", round(_dur_ms, 1))
                    sentry_sdk.capture_message(
                        f"Slow API: {request.method} {request.url.path} took {_dur_ms:.0f}ms",
                        level="warning",
                    )
            except Exception:
                pass
    return response


# ── Iter 118 — In-memory route cache for high-frequency polling endpoints ──
# Reduces DB query load by ~12x. See services/route_cache.py for the
# rules. Added AFTER _security_headers so this is the OUTERMOST
# middleware — a cache hit short-circuits before the security headers
# middleware runs (we add the headers manually on the cached Response).
from services import route_cache as _route_cache  # noqa: E402
from fastapi.responses import Response as _CacheResp  # noqa: E402


def _apply_security_headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    resp.headers["X-XSS-Protection"] = "1; mode=block"
    resp.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"


async def _safe_call_next(request, call_next):
    """Iter 142 — Harden the cancellation race between Sentry's ASGI
    wrapper and our route-cache middleware. Starlette's
    BaseHTTPMiddleware will surface a confusing
    `RuntimeError("No response returned.")` whenever the inner task
    is cancelled (client disconnect) or the upstream wrapper short-
    circuits before producing a response. We catch all three known
    failure modes and return a deterministic envelope instead of a
    500.
    """
    try:
        response = await call_next(request)
        if response is None:
            logger.error(
                "middleware: no response returned on %s %s",
                request.method, request.url.path,
            )
            return JSONResponse(
                {"ok": False, "error": "Internal routing error"},
                status_code=500,
            )
        return response
    except asyncio.CancelledError:
        logger.warning(
            "middleware: request cancelled on %s %s",
            request.method, request.url.path,
        )
        return JSONResponse(
            {"ok": False, "error": "Request cancelled"},
            status_code=499,
        )
    except RuntimeError as e:
        if "No response returned" in str(e):
            logger.error(
                "middleware: RuntimeError on %s %s: %s",
                request.method, request.url.path, e,
            )
            return JSONResponse(
                {"ok": False, "error": "Service temporarily unavailable"},
                status_code=503,
            )
        raise


@app.middleware("http")
async def _route_cache_mw(request, call_next):
    # Only GET requests, only configured paths.
    if request.method != "GET":
        return await _safe_call_next(request, call_next)
    path = request.url.path
    cfg = _route_cache.ROUTE_CONFIG.get(path)
    if cfg is None:
        return await _safe_call_next(request, call_next)
    ttl, requires_admin = cfg

    key = _route_cache.make_key(path, request.url.query)
    hit = _route_cache.get(key)
    if hit is not None:
        # Admin endpoints: verify the caller is an admin BEFORE serving
        # the cached body. Otherwise an anon request right after a warm
        # cache would leak admin-only aggregates.
        if requires_admin:
            from cto_services.auth import current_dev as _cd
            from fastapi import HTTPException as _AuthExc
            try:
                user = await _cd(request.headers.get("authorization"))
                if not user.get("is_admin") and user.get("tier") != "founder":
                    return _CacheResp(
                        content=b'{"detail":"Admin access required"}',
                        status_code=403, media_type="application/json",
                    )
            except _AuthExc as e:
                return _CacheResp(
                    content=(b'{"detail":"' + str(e.detail).encode() + b'"}'),
                    status_code=e.status_code, media_type="application/json",
                )
        status, body, ctype = hit
        resp = _CacheResp(content=body, status_code=status, media_type=ctype)
        resp.headers["X-Cache"] = "HIT"
        _apply_security_headers(resp)
        return resp

    # Miss — run the handler, capture body, store if 200.
    # Iter 137 — defensive against (a) call_next raising on
    # client-disconnect, (b) body_iterator yielding before erroring
    # mid-stream, (c) StreamingResponse types whose body is too large /
    # not idempotent to cache. Any failure mode falls through to a
    # plain proxy of whatever the handler produced.
    # Iter 142 — route through _safe_call_next so the
    # `No response returned` race with the Sentry ASGI wrapper is
    # absorbed deterministically instead of escalating to a 500.
    try:
        response = await _safe_call_next(request, call_next)
    except Exception:
        logger.exception(
            "route-cache: call_next failed on %s %s",
            request.method, request.url.path,
        )
        # Re-raise so the outer _security_headers + global exception
        # handler can serialise a proper error envelope.
        raise

    # StreamingResponse has its own body protocol — buffering the
    # whole thing into memory would defeat the cache and could OOM the
    # pod on a slow client. Skip caching for these.
    from starlette.responses import StreamingResponse as _StreamResp
    if isinstance(response, _StreamResp):
        return response

    if response.status_code == 200:
        try:
            body_chunks = []
            async for chunk in response.body_iterator:
                body_chunks.append(chunk)
            body = b"".join(body_chunks)
        except Exception:
            logger.exception(
                "route-cache: body_iterator failed on %s — passing through",
                request.url.path,
            )
            return response
        ctype = response.headers.get("content-type", "application/json")
        _route_cache.put(key, ttl, response.status_code, body, ctype)
        new_resp = _CacheResp(content=body, status_code=200, media_type=ctype)
        for k, v in response.headers.items():
            if k.lower() not in ("content-length", "content-type"):
                new_resp.headers[k] = v
        new_resp.headers["X-Cache"] = "MISS"
        return new_resp
    return response


# ── Iter 44 — Global exception handler ──
# Never leak stack traces. Log full error internally, return a stable
# 500 envelope to the caller.
from fastapi import Request as _FastReq
from fastapi.responses import JSONResponse as _JsonResp
from fastapi.exceptions import HTTPException as _HExc


@app.exception_handler(Exception)
async def _global_exc_handler(request: _FastReq, exc: Exception):
    # Re-raise HTTPExceptions — those have intentional messages.
    if isinstance(exc, _HExc):
        return _JsonResp(
            status_code=exc.status_code,
            content={"detail": exc.detail},
        )
    # Iter 137 — ALSO short-circuit asyncio cancellations so a
    # client-disconnect on /chat/stream doesn't escalate into a 500.
    import asyncio as _aio
    if isinstance(exc, _aio.CancelledError):
        logger.info(
            "request cancelled (client disconnect) on %s %s",
            request.method, request.url.path,
        )
        return _JsonResp(
            status_code=499,
            content={"detail": "client disconnected"},
        )
    try:
        logger.error(
            "unhandled exception on %s %s",
            request.method, request.url.path, exc_info=True,
        )
        # Iter 48 — defensive Sentry capture (FastApiIntegration usually
        # catches this automatically, but the global handler may intercept
        # before the integration sees it).
        if SENTRY_ACTIVE:
            try:
                import sentry_sdk
                with sentry_sdk.push_scope() as scope:
                    scope.set_tag("kind", "unhandled_500")
                    scope.set_tag("path", request.url.path)
                    scope.set_tag("method", request.method)
                    sentry_sdk.capture_exception(exc)
            except Exception:
                pass
    except Exception:
        # Iter 137 — if logging itself fails (e.g. Sentry SDK crashed
        # because of a quota or config issue), we MUST still return a
        # Response or Starlette panics with "No response returned."
        pass
    return _JsonResp(
        status_code=500,
        content={"detail": "An internal error occurred. Please try again."},
    )

_BUILD_HASH: str | None = None


def _resolve_build_hash() -> str:
    """Compute once at import — short git SHA of the current deploy.

    Resolution order:
      1. Explicit env var (BUILD_HASH / GIT_COMMIT / VERCEL_GIT_COMMIT_SHA)
         — set by CI / Emergent / Vercel during deploy.
      2. `git rev-parse --short HEAD` — works on dev pods.
      3. Last-modified time of this file as a deploy fingerprint — so
         Emergent containers (no git binary) still show SOMETHING the
         founder can compare across deploys.
    """
    global _BUILD_HASH
    if _BUILD_HASH is not None:
        return _BUILD_HASH
    env_h = (os.getenv("BUILD_HASH") or os.getenv("GIT_COMMIT")
             or os.getenv("VERCEL_GIT_COMMIT_SHA"))
    if env_h:
        _BUILD_HASH = env_h[:7]
        return _BUILD_HASH
    try:
        import subprocess
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
            cwd=os.path.dirname(os.path.abspath(__file__)) + "/..",
            timeout=2,
        )
        _BUILD_HASH = out.decode().strip()[:7]
        if _BUILD_HASH:
            return _BUILD_HASH
    except Exception:
        pass
    # Last resort — mtime of this file. Format: m<unix-mins>. Stable
    # within one deploy, changes whenever the container is rebuilt.
    try:
        mtime = int(os.path.getmtime(__file__) // 60)
        _BUILD_HASH = f"m{mtime:x}"
    except Exception:
        _BUILD_HASH = "unknown"
    return _BUILD_HASH


# ── Health ──
@app.get("/api/health")
async def health():
    return {
        "ok": True,
        "service": "aurem-dev",
        "uptime_s": round(time.time() - START_TIME, 2),
        "db": app.state.db is not None,
        "build_hash": _resolve_build_hash(),
        "env": os.getenv("ENVIRONMENT", "production"),
    }


# Iter 120 — Fast probe endpoint for Kubernetes liveness/readiness.
# No DB lookup, no lifespan dependency — must NEVER hang. If this
# endpoint can't answer within 1s, K8s should rightfully restart the
# pod. Production probes should be configured to hit /api/healthz.
@app.get("/api/healthz")
async def healthz():
    return {"ok": True}


# Iter 189 — Mirror the fast healthz at the prefix-less paths
# Kubernetes liveness/readiness probes hit by default when configured
# against the pod directly (bypassing ingress). Emergent's pod-level
# probe hits `/healthz` on pod IP:8001 — without these routes that
# returns 404 and the pod is restarted in a loop, even though the
# ingress-level health check at `/api/healthz` is green.
@app.get("/healthz")
@app.get("/health")
@app.get("/ping")
async def healthz_root():
    return {"ok": True}


# Iter 140 — Versioned health endpoint. Stable contract for v1 API
# consumers (mobile apps, third-party integrations) so /api/health
# stays free to evolve. Pings MongoDB so callers can distinguish a
# pod-up-but-DB-down situation from healthy.
@app.get("/api/v1/health", tags=["Health"])
async def health_v1():
    """Versioned health check — stable API contract."""
    from cto_services.db import get_db
    db = get_db()
    db_ok = False
    if db is not None:
        try:
            await db.command("ping")
            db_ok = True
        except Exception:
            pass
    return {
        "ok": db_ok,
        "service": "aurem-dev",
        "version": "1.0.0",
        "api_version": "v1",
        "db": db_ok,
        "env": os.getenv("APP_ENV", os.getenv("ENVIRONMENT", "production")),
        "uptime_s": round(time.time() - START_TIME, 2),
    }


# Iter 122 — memory diagnostic endpoint for restart-loop debugging.
# Admin-only. Returns RSS + tracemalloc top allocations so we can SEE
# what's eating memory between restarts. Read-only; safe in prod.
# Iter 145 — gated behind ENABLE_TRACEMALLOC=1. Previous unconditional
# tracemalloc.start(10) kept a 10-frame call stack for EVERY allocation
# in the process. In production this caused steady RSS growth → OOMKill
# → CrashLoopBackOff. Now strictly opt-in. /api/_diag/memory returns a
# 503 explaining how to enable when the flag is off.
import tracemalloc as _tm  # noqa: E402
_TRACEMALLOC_ENABLED = os.environ.get("ENABLE_TRACEMALLOC", "").lower() in ("1", "true", "yes")
try:
    if _TRACEMALLOC_ENABLED and not _tm.is_tracing():
        _tm.start(10)        # keep 10 frames per snapshot
except Exception:
    pass


@app.get("/api/_diag/memory")
async def diag_memory(authorization: str | None = Header(None)):
    # Reuse the existing admin auth check from cto_services.auth
    from cto_services.auth import current_dev
    user = await current_dev(authorization)
    if not (user.get("is_admin") or user.get("tier") == "founder"):
        raise HTTPException(403, "admin only")

    # RSS in MB (best-effort; /proc may not exist in some runtimes)
    rss_mb = None
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    rss_mb = int(line.split()[1]) / 1024.0
                    break
    except Exception:
        pass

    out = {
        "rss_mb": round(rss_mb, 1) if rss_mb is not None else None,
        "uptime_s": round(time.time() - START_TIME, 1),
        "tracemalloc_active": _tm.is_tracing(),
        "route_cache_size": None,
        "top": [],
    }

    # Route cache footprint (iter 118)
    try:
        from services import route_cache as _rc
        out["route_cache_size"] = _rc.size()
    except Exception:
        pass

    # Top 10 allocations grouped by filename
    if _tm.is_tracing():
        snap = _tm.take_snapshot()
        stats = snap.statistics("filename")[:10]
        out["top"] = [
            {"file": str(s.traceback[0].filename).replace("/app/", "") if s.traceback else "?",
             "size_kb": round(s.size / 1024, 1),
             "count":   s.count}
            for s in stats
        ]
    return out

# ── Routers ──
app.include_router(deploy_router,       prefix="/api/aurem-dev")
app.include_router(vault_router,        prefix="/api/aurem-dev")
app.include_router(stacks_router,       prefix="/api/aurem-dev")
app.include_router(domain_router,       prefix="/api/aurem-dev")
app.include_router(github_router,       prefix="/api/aurem-dev")
app.include_router(harden_router,       prefix="/api/aurem-dev")
app.include_router(trust_router,        prefix="/api/aurem-dev")
app.include_router(chat_commits_router, prefix="/api/aurem-dev")
app.include_router(engagement_router,   prefix="/api/aurem-dev")
app.include_router(projects_router,      prefix="/api/aurem-dev")
app.include_router(unlock_router,       prefix="/api/aurem-dev")
app.include_router(auth_router,         prefix="/api/aurem-dev")
app.include_router(mfa_router,          prefix="/api/aurem-dev")  # Iter 212m-20 — admin 2FA
app.include_router(chat_router,         prefix="/api/aurem-dev")
app.include_router(github_oauth_router, prefix="/api/aurem-dev")
app.include_router(cto_projects_router, prefix="/api/aurem-dev")
app.include_router(automations_router, prefix="/api/aurem-dev")
app.include_router(upload_router,        prefix="/api/aurem-dev")
app.include_router(admin_router,         prefix="/api/aurem-dev")
app.include_router(support_router,       prefix="/api/aurem-dev")
app.include_router(payments_router,      prefix="/api/aurem-dev")
app.include_router(usage_router,         prefix="/api/aurem-dev")
app.include_router(lint_preview_router,  prefix="/api/aurem-dev")
app.include_router(shipwall_router,      prefix="/api/aurem-dev")
app.include_router(wrapped_router,       prefix="/api/aurem-dev")
app.include_router(hosted_deploy_router, prefix="/api/aurem-dev")
app.include_router(codebase_router,      prefix="/api/aurem-dev")
app.include_router(github_deploy_router, prefix="/api/aurem-dev")   # iter 123
app.include_router(thinking_hints_router, prefix="/api/aurem-dev")  # iter 158
app.include_router(repo_indexing_router,  prefix="/api/aurem-dev")  # Iter 212m-30 PR-2
app.include_router(founder_offer_router,  prefix="/api/aurem-dev")  # Iter 212m-30 PR-2
app.include_router(onboarding_router,     prefix="/api/aurem-dev")  # Iter 212m-32 nudge emails
app.include_router(admin_vanguard_router, prefix="/api/aurem-dev")  # Iter 212m-42 vanguard config
app.include_router(security_scan_router,  prefix="/api/aurem-dev")  # Iter 212m-55 1-click vuln scanner
app.include_router(vanguard_ci_router,    prefix="/api/aurem-dev")  # Iter 212m-120 Phase 1: trufflehog CI ingest
app.include_router(fix_pipeline_router,   prefix="/api/aurem-dev")  # Iter 212m-121 bulk + SSE fix pipeline
app.include_router(repo_status_router,    prefix="/api/aurem-dev")  # Iter 212m-125 live repo connection ping
app.include_router(codebase_health_router, prefix="/api/aurem-dev")  # Iter 212m-72 5-cat health
app.include_router(loop_router,           prefix="/api/aurem-dev")  # Iter 212m-60 Loop Mode engine
# Iter 212m-117 — User trust-level (L1/L2/L3) for Loop Mode + Fix.
from routers.trust_level import router as trust_level_router
app.include_router(trust_level_router)
app.include_router(diagram_router,        prefix="/api/aurem-dev")  # Iter 212m-61 /diagram
app.include_router(feature_window_router, prefix="/api/aurem-dev")  # Iter 212m-64 system map
app.include_router(mcp_router,            prefix="/api/aurem-dev")  # iter 173 — MCP server
app.include_router(vercel_router,         prefix="/api/aurem-dev")  # Iter 212m-84 — Vercel tools for ORA
app.include_router(oauth_router,          prefix="/api/aurem-dev")  # iter 182 — OAuth 2.1 + PKCE for Claude Directory
# Iter 174 — root-level alias for the MCP well-known discovery URL so
# clients can probe `https://auremcto.com/.well-known/mcp` without
# knowing our internal /api/aurem-dev prefix.
app.add_route(
    "/.well-known/mcp",
    mcp_discovery_root,
    methods=["GET"],
)
