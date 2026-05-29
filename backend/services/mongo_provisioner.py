"""
mongo_provisioner.py — AUREM Dev
Provision a MongoDB database per customer/project using Atlas Admin API.

Each project gets:
  - Its own database (isolated, same cluster)
  - A dedicated database user (read/write on that db only)
  - A scoped connection string returned to the caller

Requires:
  ATLAS_PUBLIC_KEY  — Atlas API public key
  ATLAS_PRIVATE_KEY — Atlas API private key
  ATLAS_PROJECT_ID  — Atlas project ID
  ATLAS_CLUSTER     — Atlas cluster name (e.g. "Cluster0")
"""
from __future__ import annotations
import logging
import os
import secrets
import string

import httpx
from httpx import DigestAuth

logger = logging.getLogger(__name__)

ATLAS_API       = "https://cloud.mongodb.com/api/atlas/v2"
ATLAS_PUB       = os.getenv("ATLAS_PUBLIC_KEY", "")
ATLAS_PRIV      = os.getenv("ATLAS_PRIVATE_KEY", "")
ATLAS_PROJECT   = os.getenv("ATLAS_PROJECT_ID", "")
ATLAS_CLUSTER   = os.getenv("ATLAS_CLUSTER", "Cluster0")
ATLAS_BASE_URL  = os.getenv("MONGO_BASE_URL", "")  # mongodb+srv://...@cluster.xxx.mongodb.net


def _auth() -> DigestAuth:
    return DigestAuth(ATLAS_PUB, ATLAS_PRIV)


def _headers() -> dict:
    return {"Content-Type": "application/json", "Accept": "application/vnd.atlas.2023-01-01+json"}


def _random_password(length: int = 24) -> str:
    chars = string.ascii_letters + string.digits
    return "".join(secrets.choice(chars) for _ in range(length))


async def create_db_user(
    username: str,
    password: str,
    db_name: str,
) -> dict:
    """Create an Atlas database user scoped to one database."""
    if not ATLAS_PUB:
        raise RuntimeError("ATLAS_PUBLIC_KEY not set — cannot provision database")

    url = f"{ATLAS_API}/groups/{ATLAS_PROJECT}/databaseUsers"
    payload = {
        "databaseName": "admin",
        "username": username,
        "password": password,
        "roles": [{"databaseName": db_name, "roleName": "readWrite"}],
        "scopes": [{"name": ATLAS_CLUSTER, "type": "CLUSTER"}],
    }
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(url, auth=_auth(), headers=_headers(), json=payload)
        if r.status_code == 409:
            logger.info(f"[mongo_prov] user {username} already exists")
            return {"username": username, "existing": True}
        r.raise_for_status()
        logger.info(f"[mongo_prov] created user {username} for db {db_name}")
        return r.json()


async def provision_project_db(project_id: str) -> dict:
    """
    Provision a scoped database for a project.
    Returns connection string the project's backend can use.
    """
    db_name  = f"auremdev_{project_id}"
    username = f"dev_{project_id}"
    password = _random_password()

    try:
        await create_db_user(username, password, db_name)
    except RuntimeError as e:
        # Atlas not configured — return a local fallback connection string
        logger.warning(f"[mongo_prov] Atlas not configured ({e}), returning local fallback")
        return {
            "ok": True,
            "db_name": db_name,
            "username": username,
            "password": password,
            "connection_string": f"mongodb://localhost:27017/{db_name}",
            "source": "local_fallback",
        }
    except Exception as e:
        logger.error(f"[mongo_prov] failed: {e}")
        return {"ok": False, "error": str(e)}

    if not ATLAS_BASE_URL:
        conn = f"mongodb://localhost:27017/{db_name}"
        source = "local_fallback"
    else:
        # Build scoped SRV connection string
        # e.g. mongodb+srv://dev_abc123:pass@cluster.xxx.mongodb.net/auremdev_abc123
        base = ATLAS_BASE_URL.rstrip("/")
        if "?" in base:
            base_url, qs = base.split("?", 1)
            conn = f"{base_url.rstrip('/')}/{db_name}?{qs}"
        else:
            conn = f"{base}/{db_name}"
        # Inject credentials
        conn = conn.replace("mongodb+srv://", f"mongodb+srv://{username}:{password}@")
        source = "atlas"

    logger.info(f"[mongo_prov] provisioned {db_name} via {source}")
    return {
        "ok": True,
        "db_name": db_name,
        "username": username,
        "password": password,
        "connection_string": conn,
        "source": source,
    }
