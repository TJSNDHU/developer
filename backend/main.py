"""
AUREM Dev — Developer AI Platform
Clean FastAPI entry point — wired to all routers from aurem_cto
"""
import os
import time
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
from routers.chat import router as chat_router
from routers.github_oauth import router as github_oauth_router
from routers.cto_projects import router as cto_projects_router
from routers.automations import router as automations_router
from routers.upload import router as upload_router
from routers.admin import router as admin_router
from routers.support import router as support_router
from routers.payments import router as payments_router
from routers.usage import router as usage_router
from routers.lint_preview import router as lint_preview_router
from routers.shipwall import router as shipwall_router
from routers.wrapped import router as wrapped_router
from routers.hosted_deploy import router as hosted_deploy_router
from routers.github_deploy import router as github_deploy_router   # iter 123
from services.codebase_indexer import router as codebase_router
from services.daily_digest import schedule_daily_digest

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
    try:
        app.state.mongo = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
        await app.state.mongo.admin.command("ping")
        app.state.db = app.state.mongo[DB_NAME]
        set_db(app.state.db)
        logger.info("✅ MongoDB connected")
    except Exception as e:
        logger.warning(f"⚠️  MongoDB unreachable: {e}")
        app.state.mongo = None
        app.state.db    = None
    # Iter 25 — daily digest scheduler (runs forever, fires at DIGEST_HOUR_UTC)
    import asyncio as _asyncio
    app.state.digest_task = _asyncio.create_task(schedule_daily_digest())
    # Iter 40 — ORA council logs indexes (idempotent, safe to re-run)
    try:
        from services.ora_council_logger import ensure_indexes as _ora_idx
        await _ora_idx()
    except Exception as _e:
        logger.warning(f"ora council index ensure failed: {_e}")
    # Iter 116 — Idempotent collection bootstrap. Fresh Atlas DB on
    # production deploy doesn't auto-create collections; admin endpoints
    # that READ them (cto_payments / vanguard_audit / referrals / etc.)
    # would otherwise return empty until first write. This creates each
    # collection + its indexes on every boot. Safe to re-run.
    try:
        from scripts.init_prod_collections import init_prod_collections
        result = await init_prod_collections(app.state.db)
        if result.get("created"):
            logger.info("📦 collections created on boot: %s", result["created"])
        if result.get("errors"):
            logger.warning("init_prod_collections errors: %s", result["errors"])
    except Exception as _e:
        logger.warning(f"init_prod_collections failed: {_e}")

    # Iter 123 — wire deploy_logger. Records a single `deploy_events`
    # doc per (commit_sha × boot_id) so the founder timeline can render
    # "View commit" links and we can audit which builds went live.
    # Safe — idempotent for trigger=boot, swallows its own failures.
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
    yield
    if getattr(app.state, "digest_task", None):
        app.state.digest_task.cancel()
    if app.state.mongo:
        app.state.mongo.close()
    logger.info("AUREM Dev shutdown")


app = FastAPI(title="AUREM Dev", version="1.0.0", lifespan=lifespan)

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


# ── Iter 44 — Security headers (Vanguard hardening) ──
# Drop these on every response. Cheap, zero functional impact.
@app.middleware("http")
async def _security_headers(request, call_next):
    # Iter 48 — slow-request capture. Anything > SLOW_API_MS (default 5s)
    # gets a Sentry warning event with the path + duration. Cheap because
    # Sentry only fires when slow.
    import time as _t
    _start = _t.perf_counter()
    response = await call_next(request)
    _dur_ms = (_t.perf_counter() - _start) * 1000.0
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
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


@app.middleware("http")
async def _route_cache_mw(request, call_next):
    # Only GET requests, only configured paths.
    if request.method != "GET":
        return await call_next(request)
    path = request.url.path
    cfg = _route_cache.ROUTE_CONFIG.get(path)
    if cfg is None:
        return await call_next(request)
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
    response = await call_next(request)
    if response.status_code == 200:
        body_chunks = []
        async for chunk in response.body_iterator:
            body_chunks.append(chunk)
        body = b"".join(body_chunks)
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


# Iter 122 — memory diagnostic endpoint for restart-loop debugging.
# Admin-only. Returns RSS + tracemalloc top allocations so we can SEE
# what's eating memory between restarts. Read-only; safe in prod.
import tracemalloc as _tm  # noqa: E402
try:
    if not _tm.is_tracing():
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
