"""
services/ora_chat/cost_tracker.py — Iter 212m-238

Per-call token/cost logging + HARD monthly budget enforcement.

Two Mongo collections:
  - `ora_chat_usage` — one document per LLM call. Full detail
    (model, route, temperature, tokens, cost). Long-lived audit log.
  - `ora_chat_budget_alerts` — dedupes the 70%-threshold Resend email
    so we don't spam the founder every time a call crosses the line.

Budget check is O(1) — a single aggregate query over the current
calendar month. Fast because `ora_chat_usage` is indexed on `ts_month`.
"""
from __future__ import annotations

import calendar
import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional

from cto_services.db import get_db

logger = logging.getLogger(__name__)


# ── Model cost table (USD per 1M tokens, blended input/output) ──────
# Kept as a config dict so a new model addition is a one-line change.
# Values from OpenRouter public pricing (Feb 2026 snapshot).
_COST_PER_M_TOKENS: dict[str, tuple[float, float]] = {
    # slug: (input_$_per_M, output_$_per_M)
    "deepseek/deepseek-chat":                                    (0.14,  0.28),
    "deepseek/deepseek-r1":                                      (0.55,  2.19),
    "perplexity/llama-3.1-sonar-large-128k-online":              (1.00,  1.00),
    "z-ai/glm-5.2":                                              (0.30,  0.60),
    "anthropic/claude-sonnet-4.5":                               (3.00, 15.00),
}
_DEFAULT_COST = (1.0, 3.0)   # unknown model → conservative estimate


def compute_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    """Compute call cost in USD from token counts."""
    inp_price, out_price = _COST_PER_M_TOKENS.get(model, _DEFAULT_COST)
    return round(
        (input_tokens  * inp_price / 1_000_000) +
        (output_tokens * out_price / 1_000_000),
        6,
    )


# ── Month bucketing ─────────────────────────────────────────────────
def _current_month_key(now: Optional[float] = None) -> str:
    """Return "YYYY-MM" for the current UTC month."""
    dt = datetime.fromtimestamp(now if now is not None else time.time(),
                                tz=timezone.utc)
    return f"{dt.year:04d}-{dt.month:02d}"


def _month_bounds(month_key: str) -> tuple[float, float]:
    y, m = (int(x) for x in month_key.split("-"))
    _, last = calendar.monthrange(y, m)
    start = datetime(y, m, 1,        tzinfo=timezone.utc).timestamp()
    end   = datetime(y, m, last, 23, 59, 59, tzinfo=timezone.utc).timestamp()
    return start, end


# ── Budget ──────────────────────────────────────────────────────────
def budget_usd() -> float:
    """Monthly hard cap in USD. Configurable via env, defaults to $30."""
    try:
        return float(os.getenv("ORA_MONTHLY_BUDGET_USD", "30").strip())
    except (TypeError, ValueError):
        return 30.0


def alert_threshold_pct() -> float:
    """Percent-of-budget at which a warning email fires (default 70%)."""
    try:
        return float(os.getenv("ORA_BUDGET_ALERT_PCT", "70").strip())
    except (TypeError, ValueError):
        return 70.0


async def current_month_spend_usd(now: Optional[float] = None) -> float:
    """Total spend for the current calendar month."""
    db = get_db()
    if db is None:
        return 0.0
    mkey = _current_month_key(now)
    pipe = [
        {"$match": {"ts_month": mkey}},
        {"$group": {"_id": None, "total": {"$sum": "$cost_usd"}}},
    ]
    total = 0.0
    try:
        async for row in db.ora_chat_usage.aggregate(pipe):
            total = float(row.get("total") or 0.0)
    except Exception as e:  # pragma: no cover — defensive
        logger.warning("current_month_spend_usd failed: %r", e)
    return total


async def budget_status() -> dict:
    """Full budget snapshot for the admin dashboard."""
    spent = await current_month_spend_usd()
    cap   = budget_usd()
    pct   = round(100 * spent / cap, 1) if cap > 0 else 0.0
    return {
        "month":          _current_month_key(),
        "spent_usd":      round(spent, 4),
        "cap_usd":        cap,
        "remaining_usd":  round(max(0.0, cap - spent), 4),
        "used_pct":       pct,
        "over_budget":    spent >= cap,
        "alert_pct":      alert_threshold_pct(),
    }


async def is_over_budget() -> bool:
    """True iff this-month spend has hit or exceeded the cap."""
    spent = await current_month_spend_usd()
    return spent >= budget_usd()


# ── Log a call + fire threshold alert if crossed ────────────────────
async def log_call(*, user_id: str, session_id: str, route: str,
                   model: str, temperature: float,
                   input_tokens: int, output_tokens: int,
                   error: Optional[str] = None) -> float:
    """Persist a usage row and fire the 70% alert email if this call
    is the one that crosses the threshold. Returns the cost of THIS call.
    """
    db = get_db()
    if db is None:
        return 0.0
    cost = compute_cost_usd(model, input_tokens, output_tokens)
    now = time.time()
    doc = {
        "user_id":         user_id,
        "session_id":      session_id,
        "route":           route,
        "model":           model,
        "temperature":     temperature,
        "input_tokens":    int(input_tokens or 0),
        "output_tokens":   int(output_tokens or 0),
        "cost_usd":        cost,
        "ts":              now,
        "ts_month":        _current_month_key(now),
    }
    if error:
        doc["error"] = str(error)[:200]
    try:
        await db.ora_chat_usage.insert_one(doc)
    except Exception as e:  # pragma: no cover
        logger.warning("ora_chat log_call insert failed: %r", e)

    # Threshold alert — best-effort, dedupe once per month per threshold.
    try:
        await _maybe_send_threshold_alert()
    except Exception as e:  # pragma: no cover
        logger.warning("threshold alert failed: %r", e)
    return cost


async def _maybe_send_threshold_alert() -> None:
    """Send a Resend email once per month when spend crosses the alert
    threshold. Idempotency is stored in `ora_chat_budget_alerts`."""
    db = get_db()
    if db is None:
        return
    status = await budget_status()
    if status["used_pct"] < status["alert_pct"]:
        return  # not there yet
    mkey = status["month"]
    already = await db.ora_chat_budget_alerts.find_one(
        {"month": mkey, "type": "threshold"},
    )
    if already:
        return

    # Resolve founder email — best-effort. Fall back to env if the DB
    # lookup fails so a critical alert isn't silenced by a DB blip.
    to = os.getenv("FOUNDER_EMAIL", "").strip()
    if not to:
        try:
            row = await db.dev_users.find_one(
                {"$or": [{"tier": "founder"}, {"is_admin": True}]},
                {"email": 1, "_id": 0},
                sort=[("created_at", 1)],
            )
            if row:
                to = row.get("email", "") or ""
        except Exception:
            pass
    if not to:
        return

    subject = f"ORA Chat: {status['used_pct']}% of ${status['cap_usd']} monthly budget used"
    text = (
        f"ORA Chat usage crossed {status['alert_pct']}% of this month's cap.\n\n"
        f"Month:      {mkey}\n"
        f"Spent:      ${status['spent_usd']}\n"
        f"Cap:        ${status['cap_usd']}\n"
        f"Remaining:  ${status['remaining_usd']}\n"
        f"Used:       {status['used_pct']}%\n\n"
        "At 100% new chat messages will be blocked with a plain-language "
        "message shown to the user. Adjust the cap via the "
        "ORA_MONTHLY_BUDGET_USD env var, or wait for the reset on the 1st."
    )
    html = f"<pre style='font-family:ui-monospace,monospace'>{text}</pre>"

    # Reuse the existing Resend HTTP client from onboarding_email
    from services.onboarding_email import _resend_send
    ok, err = await _resend_send(to, text=text, html=html)

    await db.ora_chat_budget_alerts.insert_one({
        "month":     mkey,
        "type":      "threshold",
        "sent_at":   time.time(),
        "to":        to,
        "used_pct":  status["used_pct"],
        "delivered": bool(ok),
        "error":     None if ok else (err or "")[:200],
        # Override the subject with a version that pre-fills the alert %.
        "subject":   subject,
    })
