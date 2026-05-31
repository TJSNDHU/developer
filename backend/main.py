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
from services.daily_digest import schedule_daily_digest

# Services
from cto_services.db import set_db

load_dotenv()
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
