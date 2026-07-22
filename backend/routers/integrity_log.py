"""
routers/integrity_log.py — Iter 273

Public, unauthenticated, read-only aggregate counts for the
"Live Integrity Log" tile on the /both dev landing page.

Contract (per user requirements):
  • No repo names exposed
  • No user IDs exposed
  • No message contents exposed
  • ONLY unfiltered totals from four collections

Cached in-memory for 60s so a marketing page can't spam
`estimated_document_count()` (which is already O(1), but we
still gate it to keep Atlas connection pool untouched under
traffic).

Endpoint:
    GET /api/aurem-dev/integrity-log   →
        {
          "available": bool,
          "hallucinations_caught": int,
          "adversarial_reviews":   int,
          "reviewer_errors":       int,
          "canary_prompts":        int,      # fixed = 8
          "cached_at":             int (unix seconds)
        }
"""
from __future__ import annotations

import logging
import time

from fastapi import APIRouter

from cto_services.db import get_db

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Integrity Log"])

_CACHE: dict = {"ts": 0.0, "data": None}
_TTL_S = 60.0

# Fixed size of the nightly canary set (see services/ora_chat/canary.py).
# Kept as a constant here so the /both page doesn't have to know the
# canary internals.
_CANARY_PROMPT_COUNT = 8


@router.get("/integrity-log")
async def integrity_log() -> dict:
    """Public read-only counts. No PII, no repo data. Cached 60s."""
    now = time.time()
    if _CACHE["data"] is not None and now - _CACHE["ts"] < _TTL_S:
        return _CACHE["data"]

    db = get_db()
    if db is None:
        return {"available": False}

    try:
        # All four are unfiltered totals — estimated_document_count
        # uses collection metadata (O(1)), safe under any traffic.
        #
        # Iter 274 note: `loop_verification_log` is INTENTIONALLY not
        # counted here. That collection is dual-mode — it holds both
        # dev-side loop-mode verifier rows (origin="loop_mode") AND
        # Personal Track scaffold-design-review rows
        # (origin="personal_track"). Public /both is a dev-trust
        # widget; scaffold reviews must never inflate its numbers.
        # If a future widget wants loop-mode verifier stats, query
        # `loop_verification_log` with `{"origin": "loop_mode"}`.
        hallucinations = await db.ora_hallucination_log.estimated_document_count()
        reviews        = await db.ora_review_log.estimated_document_count()
        reviewer_errs  = await db.ora_reviewer_errors.estimated_document_count()
    except Exception as e:                                    # noqa: BLE001
        logger.warning("integrity-log counts failed: %r", e)
        # Serve stale cache if we have it — better than 500 on the
        # public marketing tile.
        if _CACHE["data"] is not None:
            return _CACHE["data"]
        return {"available": False}

    data = {
        "available": True,
        "hallucinations_caught": int(hallucinations),
        "adversarial_reviews":   int(reviews),
        "reviewer_errors":       int(reviewer_errs),
        "canary_prompts":        _CANARY_PROMPT_COUNT,
        "cached_at":             int(now),
    }
    _CACHE["data"] = data
    _CACHE["ts"] = now
    return data
