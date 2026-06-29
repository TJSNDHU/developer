"""
routers/vanguard_ci.py — Phase 1 of the Vanguard scanner sidecar plan.

Receives secret-scan results from the GitHub Actions trufflehog job
(see .github/workflows/ci.yml — job `secret-scan`) and persists them
into Mongo so the dashboard can render them next to the on-demand
single-pass + two-round Vanguard findings without any change to the
backend runtime image (zero new binaries shipped here — Phase 2 will
add the Trivy + Semgrep sidecar via docker-compose).

Auth model
──────────
CI workers do NOT carry user JWTs.  We authenticate the ingest with a
shared secret pulled from `AUREM_CI_INGEST_TOKEN`.  The token is
provisioned into the GitHub repo secrets (`AUREM_CI_INGEST_TOKEN`)
and matched here.  If the env var is unset on the backend, the
endpoint hard-rejects every request — fail-closed, never open.

Storage
───────
Collection `vanguard_ci_findings` — one document per CI run.  Indexed
by (`repo`, `commit`) so the UI can fetch the latest run for a given
SHA in O(1).  We keep raw + normalised findings so future scanners
(trivy, semgrep) drop into the same document under different keys.

Endpoints
─────────
POST /vanguard/ci-findings   — ingest a fresh CI scan result
GET  /vanguard/ci-findings   — list latest runs for a repo
"""
from __future__ import annotations

import hmac
import logging
import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Header, HTTPException

from cto_services.auth import current_dev
from cto_services.db import get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/vanguard", tags=["Vanguard CI"])


_MAX_FINDINGS = 2000        # hard cap so a runaway scan can't bloat Mongo
_MAX_FINDINGS_RESPONSE = 200


def _shared_secret() -> str:
    """Read the ingest token once per request — env can be rotated
    without a process restart in production."""
    return (os.environ.get("AUREM_CI_INGEST_TOKEN") or "").strip()


def _verify_ci_auth(authorization: Optional[str]) -> None:
    """Constant-time compare the bearer token against the configured
    shared secret.  Raises 401/503 on any mismatch.  We fail closed
    when the backend has no token configured — that prevents a
    misconfigured deploy from silently accepting unauthenticated
    writes."""
    expected = _shared_secret()
    if not expected:
        raise HTTPException(503, "CI ingest disabled — AUREM_CI_INGEST_TOKEN not configured")
    if not authorization:
        raise HTTPException(401, "Authorization header missing")
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(401, "Invalid authorization format")
    if not hmac.compare_digest(parts[1].strip(), expected):
        raise HTTPException(401, "Invalid CI ingest token")


def _normalise_trufflehog(raw: list[dict]) -> list[dict]:
    """Trufflehog JSON-lines output uses nested keys we flatten so the
    UI can render uniformly alongside the in-process Vanguard rules.
    We never trust the input — every field is coerced to a string and
    truncated to a sane length."""
    out: list[dict] = []
    for f in raw[:_MAX_FINDINGS]:
        if not isinstance(f, dict):
            continue
        verified = bool(f.get("Verified") or f.get("verified"))
        detector = (f.get("DetectorName") or f.get("detector_name")
                    or f.get("detector") or "unknown")
        src = f.get("SourceMetadata") or {}
        data = src.get("Data") or src.get("data") or {}
        fs = data.get("Filesystem") or data.get("filesystem") or {}
        path = (fs.get("file") or f.get("file") or "").strip()
        line = int(fs.get("line") or f.get("line") or 0)
        raw_secret = (f.get("Raw") or f.get("raw") or "").strip()
        # Redact: never persist the raw secret value past the first 4
        # chars + last 2 chars — enough to identify, not enough to
        # exploit if the Mongo collection ever leaks.
        if len(raw_secret) > 8:
            redacted = f"{raw_secret[:4]}…{raw_secret[-2:]}"
        elif raw_secret:
            redacted = "…"
        else:
            redacted = ""
        out.append({
            "scanner":   "trufflehog",
            "rule_id":   f"trufflehog_{str(detector).lower()}",
            "vuln":      "secret_leak",
            "severity":  "critical" if verified else "high",
            "verified":  verified,
            "detector":  str(detector)[:64],
            "file":      path[:512],
            "line":      line,
            "redacted":  redacted[:32],
            "desc":      f"{'Verified' if verified else 'Potential'} {detector} secret",
        })
    return out


@router.post("/ci-findings")
async def ingest_ci_findings(
    body: dict,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Ingest a single CI scan run.

    Body shape:
      • repo        — str  required (e.g. "aurem-ai/aurem-cto")
      • commit      — str  required (40-char git SHA)
      • branch      — str  optional (defaults to "")
      • scanner     — str  required ("trufflehog" for Phase 1)
      • findings    — list raw scanner output (jsonl rows)
      • run_url     — str  optional (link back to the GH Actions run)
      • triggered_by — str optional (github actor / "push" / "pr")

    Returns:
      { ok, stored, verified_count, total_count, run_id }
    """
    _verify_ci_auth(authorization)
    db = get_db()
    if db is None:
        raise HTTPException(503, "DB not connected")

    repo    = (body.get("repo") or "").strip()
    commit  = (body.get("commit") or "").strip()
    branch  = (body.get("branch") or "").strip()
    scanner = (body.get("scanner") or "").strip().lower()
    raw     = body.get("findings") or []

    if not (repo and commit and scanner):
        raise HTTPException(400, "repo, commit, scanner all required")
    if scanner not in ("trufflehog",):
        raise HTTPException(400, f"unsupported scanner: {scanner}")
    if not isinstance(raw, list):
        raise HTTPException(400, "findings must be a list")

    normalised = _normalise_trufflehog(raw) if scanner == "trufflehog" else []
    verified_count = sum(1 for f in normalised if f.get("verified"))

    doc = {
        "repo":           repo[:256],
        "commit":         commit[:64],
        "branch":         branch[:128],
        "scanner":        scanner,
        "total_count":    len(normalised),
        "verified_count": verified_count,
        "findings":       normalised,
        "run_url":        (body.get("run_url") or "")[:512],
        "triggered_by":   (body.get("triggered_by") or "")[:128],
        "created_at":     datetime.now(timezone.utc).isoformat(),
    }

    # Upsert keyed on (repo, commit, scanner) so re-runs of the same
    # commit overwrite — the dashboard only ever shows the latest
    # result per scanner per SHA.
    result = await db.vanguard_ci_findings.update_one(
        {"repo": doc["repo"], "commit": doc["commit"], "scanner": scanner},
        {"$set": doc},
        upsert=True,
    )

    logger.info(
        "vanguard_ci ingest repo=%s commit=%s scanner=%s total=%d verified=%d",
        repo, commit, scanner, len(normalised), verified_count,
    )
    return {
        "ok":             True,
        "stored":         len(normalised),
        "verified_count": verified_count,
        "total_count":    len(normalised),
        "upserted":       bool(result.upserted_id),
    }


@router.get("/ci-findings")
async def list_ci_findings(
    repo: Optional[str] = None,
    limit: int = 20,
    authorization: str = Header(None),
) -> dict:
    """Authenticated dashboard read.  Lists the latest scan runs (one
    per commit per scanner) for the calling user's repos.  Always
    uses the user JWT, never the CI token."""
    user = await current_dev(authorization)
    db = get_db()
    if db is None:
        raise HTTPException(503, "DB not connected")

    # Constrain to repos the caller actually owns — pulled from their
    # cto_projects rows.  This is a soft auth: if you have the repo
    # name and a valid JWT we let you see it, but we don't expose
    # cross-tenant findings.
    own_repos: set[str] = set()
    try:
        async for p in db.cto_projects.find(
            {"user_id": user["user_id"]},
            {"_id": 0, "github_owner": 1, "github_repo": 1},
        ):
            o = (p.get("github_owner") or "").strip()
            r = (p.get("github_repo") or "").strip()
            if o and r:
                own_repos.add(f"{o}/{r}")
    except Exception:
        pass

    query: dict = {}
    if repo:
        if repo not in own_repos and not user.get("is_admin"):
            raise HTTPException(403, "Not your repo")
        query["repo"] = repo
    elif own_repos:
        query["repo"] = {"$in": list(own_repos)}
    elif not user.get("is_admin"):
        return {"ok": True, "runs": []}

    runs: list[dict] = []
    limit = max(1, min(int(limit or 20), 100))
    cursor = (
        db.vanguard_ci_findings
          .find(query, {"_id": 0})
          .sort("created_at", -1)
          .limit(limit)
    )
    async for d in cursor:
        # Cap findings array on the wire — full payload is one extra
        # request away if the user clicks into a specific run.
        if isinstance(d.get("findings"), list) and len(d["findings"]) > _MAX_FINDINGS_RESPONSE:
            d["findings"] = d["findings"][:_MAX_FINDINGS_RESPONSE]
            d["truncated"] = True
        runs.append(d)

    return {"ok": True, "runs": runs}
