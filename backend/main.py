"""
AUREM Dev — Developer AI Platform
Clean FastAPI entry point — wired to all routers from aurem_cto
"""
import os
import time
import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI
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
from routers.upload import router as upload_router
from routers.admin import router as admin_router
from routers.support import router as support_router
from routers.payments import router as payments_router
from routers.usage import router as usage_router
from services.codebase_indexer import router as codebase_router
from services.daily_digest import schedule_daily_digest

load_dotenv()

# Iter 45 — Sentry (production error monitoring). Opt-in via SENTRY_DSN.
# In dev/preview without DSN it stays inert — zero perf cost.
_SENTRY_DSN = os.getenv("SENTRY_DSN", "").strip()
if _SENTRY_DSN:
    try:
        import sentry_sdk
        sentry_sdk.init(
            dsn=_SENTRY_DSN,
            traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.1")),
            environment=os.getenv("SENTRY_ENV", "production"),
            send_default_pii=False,
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
    yield
    if getattr(app.state, "digest_task", None):
        app.state.digest_task.cancel()
    if app.state.mongo:
        app.state.mongo.close()
    logger.info("AUREM Dev shutdown")


app = FastAPI(title="AUREM Dev", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Iter 44 — Security headers (Vanguard hardening) ──
# Drop these on every response. Cheap, zero functional impact.
@app.middleware("http")
async def _security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
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
    return _JsonResp(
        status_code=500,
        content={"detail": "An internal error occurred. Please try again."},
    )

# ── Health ──
@app.get("/api/health")
async def health():
    return {
        "ok": True,
        "service": "aurem-dev",
        "uptime_s": round(time.time() - START_TIME, 2),
        "db": app.state.db is not None,
    }

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
app.include_router(upload_router,        prefix="/api/aurem-dev")
app.include_router(admin_router,         prefix="/api/aurem-dev")
app.include_router(support_router,       prefix="/api/aurem-dev")
app.include_router(payments_router,      prefix="/api/aurem-dev")
app.include_router(usage_router,         prefix="/api/aurem-dev")
app.include_router(codebase_router,      prefix="/api/aurem-dev")
