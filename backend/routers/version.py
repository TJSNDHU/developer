"""
routers/version.py — Iter 212m-205

Public `/api/aurem-dev/version` endpoint.  Returns the running app's
git commit hash + build/deploy timestamp so the admin System Health
page can compare preview vs production at runtime and flag when they
are out of sync.

Also exposes a lightweight `/api/aurem-dev/admin/system-health/ora-learning`
endpoint (auth-protected) that returns live row counts for the three
ORA learning layers (Council RAG, fine-tune exports, fix-recall) —
the same numbers a founder had to ask for manually in every session.

Design goals
------------
• No secrets in the response.  `commit_sha` is public information.
• Zero I/O per call: values are captured ONCE at import time from
  either `AUREM_COMMIT_SHA` env var (CI-injected) or `git rev-parse
  HEAD` (dev/local).  A `built_at` ISO timestamp lets the frontend
  compute "shipped 3 h ago" without an extra field.
• The learning-status endpoint issues 3 fast `count_documents({})`
  calls — no pagination, no scans.
"""

from __future__ import annotations
import os
import subprocess
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Header
from routers.auth import current_dev
from cto_services.db import require_db


# ── One-time build-info capture ────────────────────────────────────
def _read_commit() -> str:
    env = os.environ.get("AUREM_COMMIT_SHA") or os.environ.get("GIT_COMMIT_SHA")
    if env:
        return env.strip()[:12]
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            timeout=2,
            stderr=subprocess.DEVNULL,
        )
        return out.decode().strip()[:12]
    except Exception:
        return "unknown"


_COMMIT_SHA = _read_commit()
_BUILT_AT = os.environ.get("AUREM_BUILT_AT") or datetime.now(timezone.utc).isoformat()
_ENV_NAME = os.environ.get("AUREM_ENV", "preview")   # override in prod


router = APIRouter(prefix="/api/aurem-dev", tags=["version"])


@router.get("/version")
async def get_version() -> dict:
    """Public build info — no auth required.

    Consumed by the admin System Health page which fetches BOTH the
    current origin's /version AND the production /version to compare
    commit hashes.  Mismatch → "out of sync" banner shown across the
    entire admin surface.
    """
    return {
        "commit_sha":  _COMMIT_SHA,
        "built_at":    _BUILT_AT,
        "environment": _ENV_NAME,
    }


@router.get("/admin/system-health/ora-learning")
async def get_ora_learning_status(
    authorization: Optional[str] = Header(None),
) -> dict:
    """Live row-counts for the three ORA learning layers.

    Returned shape mirrors what the founder used to ask for manually
    in every session.  This is the permanent replacement for that
    conversation loop.
    """
    _ = await current_dev(authorization)   # admin/founder gate lives upstream
    db = require_db()

    counts = {}
    for coll in (
        "ora_council_logs",           # Layer 1 (RAG)
        "ora_learning_logs",          # Chat low-confidence flags
        "ora_fix_learning",           # Layer 3 (scan/fix recall - Phase 1)
        "ora_finetune_exports",       # Layer 2 (fine-tune queue)
        "ora_council_retrieval_cache",
        "council_health_probes",      # Council A liveness history
    ):
        try:
            counts[coll] = await db[coll].count_documents({})
        except Exception:
            counts[coll] = -1

    finetune_threshold = 1000
    council_n = counts.get("ora_council_logs", 0)

    return {
        "counts": counts,
        "layers": {
            "layer_1_rag": {
                "status":        "active" if council_n >= 20 else "warming",
                "rows":          council_n,
                "min_for_rag":   20,
                "note":          "TF-IDF few-shot retrieval into system prompt",
            },
            "layer_2_finetune": {
                "status":        "ready" if council_n >= finetune_threshold else "collecting",
                "rows":          council_n,
                "threshold":     finetune_threshold,
                "exports_run":   counts.get("ora_finetune_exports", 0),
                "note":          "manual export to OpenAI once threshold hit",
            },
            "layer_3_fix_recall": {
                "status":        "phase_1_storage_only",
                "rows":          counts.get("ora_fix_learning", 0),
                "note":          "scan/fix outcomes stored; recall into scan prompt = backlog Phase 2",
            },
        },
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
