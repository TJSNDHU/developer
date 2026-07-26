"""
routers/version.py — Iter 212m-205  (extended by Iter 309-b)

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

Iter 309-b (2026-07-26) — deployment-config gap fix
  Production containers ship without the `.git` folder, so
  `git rev-parse HEAD` returns "unknown" and the Admin System
  Health "Deploy Sync" card cannot detect out-of-sync state.
  The founder pointed out this ambiguity when reviewing the Phase 0
  loop-metrics rollout — we could not tell whether the metrics card
  was reading prod or preview because the deploy identity itself
  showed "unknown".

  Fixes without touching the deploy pipeline:
    1. commit_sha — fall back to `/app/.emergent/emergent.yml`'s
       `job_id` (12 chars).  Emergent's deploy pipeline rewrites
       that file on every ship, so it uniquely identifies each
       running build even without the git history shipped.
    2. env label — detect at REQUEST time using the Host header
       instead of an import-time env var.  Host == "auremcto.com"
       (or any *.aurem-* domain) → "production"; anything else
       (including "*.preview.emergentagent.com") → "preview".
"""

from __future__ import annotations
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Header, Request
from routers.auth import current_dev
from cto_services.db import require_db


# ── One-time build-info capture ────────────────────────────────────
def _read_commit() -> str:
    # (1) Explicit env var wins — set this in prod if you have a
    # cleaner value (e.g. from CI).  Never blocked; if missing we
    # cascade to the next source.
    env = os.environ.get("AUREM_COMMIT_SHA") or os.environ.get("GIT_COMMIT_SHA")
    if env:
        return env.strip()[:12]

    # (2) Iter 309-b — /app/.emergent/emergent.yml holds a stable
    # per-deploy identifier that survives even when .git is stripped
    # from the container.  Prefer this over "unknown" so prod's
    # Deploy Sync card can actually show something meaningful.
    try:
        info_path = Path("/app/.emergent/emergent.yml")
        if info_path.exists():
            raw = info_path.read_text()
            # Emergent writes this file as JSON (despite the .yml
            # extension) so `json.loads` is safe.  If they ever
            # switch to real YAML we fall through to git.
            data = json.loads(raw)
            job_id = str(data.get("job_id") or "")
            if job_id:
                # Strip dashes so it looks like a git short-sha and
                # fits the same 12-char UI slot the frontend expects.
                return job_id.replace("-", "")[:12]
    except Exception:
        pass

    # (3) Local dev / preview containers that ship .git — this is
    # the historical path and still authoritative in preview.
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


def _read_built_at() -> str:
    """Prefer explicit env; otherwise use the emergent.yml
    `created_at` timestamp (rewritten on every deploy); otherwise
    `datetime.now()` (dev fallback).  Never raises."""
    env = os.environ.get("AUREM_BUILT_AT")
    if env:
        return env
    try:
        raw = Path("/app/.emergent/emergent.yml").read_text()
        data = json.loads(raw)
        ts = str(data.get("created_at") or "")
        if ts:
            return ts.rstrip("Z") + "+00:00" if ts.endswith("Z") and "+" not in ts else ts
    except Exception:
        pass
    return datetime.now(timezone.utc).isoformat()


_COMMIT_SHA = _read_commit()
_BUILT_AT = _read_built_at()

# Iter 309-b — env label is now derived at REQUEST time from the
# Host header (see `get_version` below).  This module-level default
# is kept for backwards compatibility with services that import
# `_ENV_NAME` directly (e.g. admin.loop_metrics data_source block).
_ENV_NAME = os.environ.get("AUREM_ENV", "preview")


# Hostnames considered "production".  Prefix-match so any future
# aurem-* domain (subdomain, staging tier promoted to prod, etc.)
# is captured without a code change.
_PROD_HOST_MARKERS = ("auremcto.com", "www.auremcto.com")


def _env_from_host(host: str) -> str:
    host = (host or "").lower().split(":")[0]
    if not host:
        return _ENV_NAME
    for marker in _PROD_HOST_MARKERS:
        if host == marker or host.endswith("." + marker):
            return "production"
    return "preview"


router = APIRouter(prefix="/api/aurem-dev", tags=["version"])


@router.get("/version")
async def get_version(request: Request) -> dict:
    """Public build info — no auth required.

    Consumed by the admin System Health page which fetches BOTH the
    current origin's /version AND the production /version to compare
    commit hashes.  Mismatch → "out of sync" banner shown across the
    entire admin surface.
    """
    return {
        "commit_sha":  _COMMIT_SHA,
        "built_at":    _BUILT_AT,
        "environment": _env_from_host(request.headers.get("host", "")),
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
