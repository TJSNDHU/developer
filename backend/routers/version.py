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
#
# Iter 313 — the BUILD_INFO.txt marker path + emergent.yml candidate
# list are hoisted to module-level tuples so tests can monkeypatch
# them independently of the production filesystem. Do not inline
# these back into `_read_commit()` — the test panel relies on the
# indirection to simulate the .git-stripped prod container shape.
_BUILD_INFO_MARKER = (
    Path(__file__).resolve().parent.parent / "BUILD_INFO.txt"
)
_EMERGENT_YAML_CANDIDATES = (
    Path("/app/.emergent/emergent.yml"),
    Path("/app/.emergent/emergent.json"),
)


def _write_build_info_marker(sha: str) -> None:
    """Iter 313 — persist the git-derived SHA to disk so it survives
    Emergent's `.git`-strip during the deploy snapshot. Called ONLY
    from the git-success branch of `_read_commit()`. Silent on any
    IO error — the marker is a diagnostic aid, never a critical path.
    """
    try:
        _BUILD_INFO_MARKER.write_text(sha.strip())
    except Exception:
        # Never let a marker-write failure crash version.py import.
        # Worst case: prod falls back to emergent.yml job_id as
        # before Iter 313. Log-only.
        try:
            import logging
            logging.getLogger("aurem.version").warning(
                "iter313: BUILD_INFO.txt write failed for sha=%s at %s",
                sha, _BUILD_INFO_MARKER,
            )
        except Exception:
            pass


def _read_commit() -> str:
    # (1) Explicit env var wins — set this in prod if you have a
    # cleaner value (e.g. from CI).  Never blocked; if missing we
    # cascade to the next source.
    env = (
        os.environ.get("AUREM_COMMIT_SHA")
        or os.environ.get("GIT_COMMIT_SHA")
        or os.environ.get("EMERGENT_JOB_ID")
        or os.environ.get("EMERGENT_DEPLOY_ID")
    )
    if env:
        return env.strip().replace("-", "")[:12]

    # (2) Iter 313 — try cascade step (4) `git rev-parse HEAD`
    # BEFORE the emergent.yml / BUILD_INFO.txt fallbacks. Rationale:
    # in preview containers `.git` still exists and is the freshest
    # truth. Reading it now (a) gets us the real deploy-differentiating
    # SHA, and (b) lets us WRITE that SHA into BUILD_INFO.txt so the
    # marker travels with the deploy snapshot for prod's later use.
    # Prior order (git last, marker before it) meant prod always
    # landed on the stable-per-job emergent.yml job_id, which is
    # what produced the founder-reported "same SHA across two
    # deploys" bug on 2026-07-27.
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            timeout=2,
            stderr=subprocess.DEVNULL,
        )
        sha = out.decode().strip()[:12]
        if sha:
            # Iter 313 — write-through so prod (which will boot
            # without .git) can read the same SHA from the marker
            # via cascade step (3) below.
            _write_build_info_marker(sha)
            return sha
    except Exception:
        pass

    # (3) Iter 309-c / Iter 313 — static build marker written at
    # commit time (Iter 313 turned this from dead code into a real
    # fallback by adding the git-success write-through above). This
    # is the primary prod path: `.git` is stripped by Emergent's
    # deploy, so step (2) fails, and we read the SHA the preview
    # container wrote here on its last backend restart.
    try:
        if _BUILD_INFO_MARKER.exists():
            raw = _BUILD_INFO_MARKER.read_text().strip()
            if raw:
                return raw.replace("-", "")[:12]
    except Exception:
        pass

    # (4) Iter 309-b — /app/.emergent/emergent.yml holds a stable
    # per-JOB identifier that survives even when .git is stripped
    # from the container. This is the LAST resort now (Iter 313
    # demoted it below BUILD_INFO.txt) because job_id does not
    # change between deploys of the same job — only when the job
    # is recreated. Kept as a legitimate fallback for containers
    # where BUILD_INFO.txt somehow didn't ship.
    for candidate in _EMERGENT_YAML_CANDIDATES:
        try:
            if candidate.exists():
                raw = candidate.read_text()
                data = json.loads(raw)
                job_id = str(data.get("job_id") or data.get("id") or "")
                if job_id:
                    return job_id.replace("-", "")[:12]
        except Exception:
            continue

    return "unknown"


def _read_built_at() -> str:
    """Prefer explicit env; otherwise use the emergent.yml
    `created_at` timestamp (rewritten on every deploy); otherwise
    `datetime.now()` (dev fallback).  Never raises."""
    env = os.environ.get("AUREM_BUILT_AT")
    if env:
        return env
    for candidate in (
        Path("/app/.emergent/emergent.yml"),
        Path("/app/.emergent/emergent.json"),
    ):
        try:
            if candidate.exists():
                raw = candidate.read_text()
                data = json.loads(raw)
                ts = str(data.get("created_at") or "")
                if ts:
                    return ts.rstrip("Z") + "+00:00" if ts.endswith("Z") and "+" not in ts else ts
        except Exception:
            continue
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
    """Iter 309-c — env detection cascade.  Prod path in the
    Emergent K8s ingress rewrites the browser's Host header before
    it reaches the FastAPI app, so a naive `Host: auremcto.com`
    check fails.  We inspect a wider set of signals in priority
    order:
      1. Explicit AUREM_ENV env var (deploy-config override)
      2. request.headers["x-forwarded-host"]   (K8s / Cloudflare
         standard for preserving the original client host)
      3. request.headers["host"]               (bare request host)
      4. MONGO_URL analysis — Atlas SRV (`mongodb+srv://`) or any
         non-localhost cluster is a production tell.  Preview /
         dev backends run against localhost:27017.
    Anything else defaults to "preview" so a mis-detected prod
    fails safe (won't overstate its status)."""
    # (1)
    env_override = os.environ.get("AUREM_ENV", "").strip().lower()
    if env_override in ("production", "prod", "preview", "dev", "staging"):
        return "production" if env_override in ("production", "prod") else env_override

    # (2) + (3)  Host-header inspection.
    for candidate in (host,):
        h = (candidate or "").lower().split(",")[0].strip().split(":")[0]
        if not h:
            continue
        for marker in _PROD_HOST_MARKERS:
            if h == marker or h.endswith("." + marker):
                return "production"

    # (4) MONGO_URL inspection — last-resort tell.
    mongo = os.environ.get("MONGO_URL", "").lower()
    if mongo.startswith("mongodb+srv://") or "mongodb.net" in mongo:
        return "production"
    # A cluster running on a private hostname (not localhost / 127.*)
    # is almost certainly a shared prod instance too.
    stripped = mongo.split("//", 1)[-1].split("@", 1)[-1].split("/", 1)[0]
    stripped = stripped.split(":", 1)[0]
    if stripped and stripped not in ("localhost", "127.0.0.1", "mongo", "0.0.0.0"):
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
    # Iter 309-c — prefer X-Forwarded-Host (set by K8s ingress /
    # Cloudflare) over the bare Host header, then fall through to
    # MONGO_URL heuristics inside `_env_from_host`.
    fwd = request.headers.get("x-forwarded-host") or ""
    host = request.headers.get("host") or ""
    resolved = fwd or host
    return {
        "commit_sha":  _COMMIT_SHA,
        "built_at":    _BUILT_AT,
        "environment": _env_from_host(resolved),
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
