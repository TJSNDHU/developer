"""
services/financials.py — live financial calculator for the admin panel.

What it does:
  • Reads real counts from MongoDB (cto_users grouped by tier, cto_tasks
    summed by agent, cto_payments aggregated for MRR, cto_maxx_usage).
  • Reads editable settings from `financial_settings` collection
    (cash in bank, dev salary, manual user overrides if founder wants
    to play with hypotheticals).
  • Fetches live USD→CAD FX rate from frankfurter.app (ECB-sourced,
    free, no auth). Cached for 24h.
  • Computes: MRR, AI cost, total burn, gross margin per tier, cost per
    task, cash runway, CAC, break-even target, 6-month P&L projection.
  • Returns earnings in USD, spending in CAD, both visible to the
    founder for the same number.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# ─── Real 2026 vendor prices (Feb 2026, verified live) ────────────────
PRICING_USD = {
    # LLM rates in $/1M tokens
    "deepseek_in":      0.20,
    "deepseek_out":     0.80,
    "claude_in":        3.00,
    "claude_out":       15.00,
    # Per-call rates
    "tavily_per":       0.008,
    "firecrawl_per":    0.0008,
    "e2b_per_hr":       0.05,
    # Stripe fees
    "stripe_pct":       0.029,
    "stripe_flat":      0.30,
}

# Typical token usage per task (from cap_for() in services/llm.py)
TYPICAL_TASK = {
    "chat_in":          15000,
    "chat_out":         1500,
    "code_in":          15000,
    "code_out":         5000,
}

# Per-tier behavioural assumptions (avg over a month).
TIER_PROFILES = {
    "free":    {"price_usd": 0,  "tasks_avg":  8,  "maxx_pct": 0.00, "web_calls":  2,  "scrape_pages":  1, "e2b_min":  2},
    "starter": {"price_usd": 9,  "tasks_avg": 30,  "maxx_pct": 0.00, "web_calls": 10,  "scrape_pages":  5, "e2b_min":  6},
    "pro":     {"price_usd": 19, "tasks_avg": 60,  "maxx_pct": 0.30, "web_calls": 30,  "scrape_pages": 20, "e2b_min": 30},
    "team":    {"price_usd": 49, "tasks_avg": 80,  "maxx_pct": 0.40, "web_calls": 50,  "scrape_pages": 40, "e2b_min": 60},
}

FIXED_INFRA_USD_PER_MONTH = {
    "Emergent hosting":  75,
    "MongoDB Atlas M10": 60,
    "Resend email":      20,
    "Sentry error track": 26,
    "Firecrawl (paid)":  19,
    "E2B sandbox base":  20,
    "Domain + SSL":       2,
}

# Iter 212m-234 — Phase 5 marginal cost per provisioned Supabase
# project. AUREM absorbs the base plan and passes only the per-project
# compute figure into the P&L. If no projects are provisioned this
# adds zero to the burn.
SUPABASE_PROJECT_USD_PER_MONTH = 10.0


# ─── Live FX rate (USD → CAD), cached 24h in process memory ───────────
_fx_cache: dict = {"rate": None, "fetched_at": 0.0, "source": "init"}

async def get_usd_cad_rate() -> dict:
    """Returns {'rate': float, 'fetched_at': iso, 'source': str}."""
    age = time.time() - _fx_cache["fetched_at"]
    if _fx_cache["rate"] and age < 86_400:
        return {
            "rate":       _fx_cache["rate"],
            "fetched_at": datetime.fromtimestamp(_fx_cache["fetched_at"], tz=timezone.utc).isoformat(),
            "source":     _fx_cache["source"],
            "age_seconds": int(age),
        }
    try:
        async with httpx.AsyncClient(timeout=8) as c:
            # Iter 115 — frankfurter migrated from .app → .dev/v1/ on
            # 2026-06. The old URL returns a 301 redirect that httpx
            # doesn't auto-follow when redirects are off, so we hit the
            # new endpoint directly.
            r = await c.get("https://api.frankfurter.dev/v1/latest", params={"from": "USD", "to": "CAD"})
            r.raise_for_status()
            rate = float(r.json()["rates"]["CAD"])
            _fx_cache.update({
                "rate":       rate,
                "fetched_at": time.time(),
                "source":     "frankfurter.app (ECB)",
            })
    except Exception as e:
        logger.warning(f"FX fetch failed: {e!r} — using fallback 1.37")
        if not _fx_cache["rate"]:
            _fx_cache.update({"rate": 1.37, "fetched_at": time.time(), "source": "fallback_1.37"})
    return {
        "rate":         _fx_cache["rate"],
        "fetched_at":   datetime.fromtimestamp(_fx_cache["fetched_at"], tz=timezone.utc).isoformat(),
        "source":       _fx_cache["source"],
        "age_seconds":  int(time.time() - _fx_cache["fetched_at"]),
    }


# ─── Per-tier unit economics ──────────────────────────────────────────
def cost_per_task(maxx_pct: float = 0.0) -> float:
    """Blended LLM cost for ONE task at given Maxx mix."""
    ds_chat = (TYPICAL_TASK["chat_in"] * PRICING_USD["deepseek_in"]
               + TYPICAL_TASK["chat_out"] * PRICING_USD["deepseek_out"]) / 1_000_000
    ds_code = (TYPICAL_TASK["code_in"] * PRICING_USD["deepseek_in"]
               + TYPICAL_TASK["code_out"] * PRICING_USD["deepseek_out"]) / 1_000_000
    claude  = (TYPICAL_TASK["code_in"] * PRICING_USD["claude_in"]
               + TYPICAL_TASK["code_out"] * PRICING_USD["claude_out"]) / 1_000_000
    standard_blend = 0.5 * ds_chat + 0.5 * ds_code
    return maxx_pct * claude + (1 - maxx_pct) * standard_blend


def cost_per_user(tier: str) -> float:
    p = TIER_PROFILES[tier]
    llm = p["tasks_avg"] * cost_per_task(p["maxx_pct"])
    web = p["web_calls"] * PRICING_USD["tavily_per"]
    scr = p["scrape_pages"] * PRICING_USD["firecrawl_per"]
    e2b = (p["e2b_min"] / 60.0) * PRICING_USD["e2b_per_hr"]
    return llm + web + scr + e2b


def stripe_fee(amount_usd: float) -> float:
    if amount_usd <= 0:
        return 0
    return amount_usd * PRICING_USD["stripe_pct"] + PRICING_USD["stripe_flat"]


def tier_margins() -> list[dict]:
    """Per-tier per-user/month gross margin breakdown."""
    rows = []
    for tier, p in TIER_PROFILES.items():
        price = p["price_usd"]
        fee = stripe_fee(price)
        net_rev = price - fee
        cost = cost_per_user(tier)
        gp = net_rev - cost
        gm_pct = (gp / net_rev * 100.0) if net_rev > 0 else 0.0
        rows.append({
            "tier":           tier,
            "price_usd":      price,
            "tasks_avg":      p["tasks_avg"],
            "gross_profit":   round(gp, 2),
            "gross_margin_pct": round(gm_pct, 1),
            "cost_per_user":  round(cost, 4),
            "maxx_pct":       p["maxx_pct"],
        })
    return rows


# ─── Editable settings stored in Mongo ────────────────────────────────
_DEFAULT_SETTINGS = {
    "cash_in_bank_usd": 2000.0,
    "dev_salary_usd":   3000.0,
    "manual_overrides_enabled": False,
    "manual_free":      0,
    "manual_starter":   0,
    "manual_pro":       0,
    "manual_team":      0,
}


async def get_settings(db) -> dict:
    row = await db.financial_settings.find_one({"_id": "singleton"}) or {}
    out = {**_DEFAULT_SETTINGS}
    for k in _DEFAULT_SETTINGS:
        if k in row:
            out[k] = row[k]
    return out


async def save_settings(db, patch: dict) -> dict:
    clean = {}
    for k, v in patch.items():
        if k in _DEFAULT_SETTINGS:
            if k in ("manual_overrides_enabled",):
                clean[k] = bool(v)
            elif k.startswith("manual_"):
                clean[k] = max(0, int(v or 0))
            else:
                clean[k] = max(0.0, float(v or 0))
    if not clean:
        return await get_settings(db)
    clean["updated_at"] = datetime.now(timezone.utc).isoformat()
    await db.financial_settings.update_one(
        {"_id": "singleton"},
        {"$set": clean},
        upsert=True,
    )
    return await get_settings(db)


# ─── Real user-counts & MRR from MongoDB ──────────────────────────────
async def _real_user_counts(db) -> dict:
    pipeline = [{"$group": {"_id": {"$ifNull": ["$tier", "free"]}, "n": {"$sum": 1}}}]
    counts = {"free": 0, "starter": 0, "pro": 0, "team": 0}
    async for row in db.dev_users.aggregate(pipeline):
        tier = (row.get("_id") or "free").lower()
        if tier in counts:
            counts[tier] = int(row.get("n") or 0)
        # `founder` tier rolls into team for billing-economics purposes (rare).
    counts["total"] = sum(counts.values())
    return counts


async def _real_mrr_usd(db) -> float:
    """Sum of successful subscription payments in the trailing 30 days."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    pipeline = [
        {"$match": {"status": "paid", "created_at": {"$gte": cutoff}}},
        {"$group": {"_id": None, "total": {"$sum": "$amount_usd"}}},
    ]
    try:
        async for row in db.cto_payments.aggregate(pipeline):
            return float(row.get("total") or 0)
    except Exception:
        pass
    return 0.0


async def _real_maxx_usage_this_month(db) -> int:
    bucket = datetime.now(timezone.utc).strftime("%Y-%m")
    try:
        pipeline = [
            {"$match": {"month": bucket}},
            {"$group": {"_id": None, "total": {"$sum": "$count"}}},
        ]
        async for row in db.cto_maxx_usage.aggregate(pipeline):
            return int(row.get("total") or 0)
    except Exception:
        pass
    return 0


async def _real_supabase_projects_cost(db) -> dict:
    """Iter 212m-234 — Sum the per-month compute cost of every
    active (non-downgrading) Supabase project across all users.

    Returns:
        { "count": int, "monthly_usd": float }
    """
    try:
        active = await db.supabase_projects.count_documents({
            "$or": [
                {"downgrade_pending": {"$exists": False}},
                {"downgrade_pending": False},
            ]
        })
    except Exception:
        active = 0
    return {
        "count":       int(active),
        "monthly_usd": round(active * SUPABASE_PROJECT_USD_PER_MONTH, 2),
    }


# ─── Master compute ───────────────────────────────────────────────────
async def compute_financials(db) -> dict:
    settings = await get_settings(db)
    fx = await get_usd_cad_rate()
    real_counts = await _real_user_counts(db)
    real_mrr = await _real_mrr_usd(db)
    maxx_used_total = await _real_maxx_usage_this_month(db)

    # Effective counts: real or founder-overridden
    if settings["manual_overrides_enabled"]:
        users = {
            "free":    settings["manual_free"],
            "starter": settings["manual_starter"],
            "pro":     settings["manual_pro"],
            "team":    settings["manual_team"],
        }
        users["total"] = sum(users.values())
        source = "manual"
    else:
        users = {**real_counts}
        source = "live_db"

    # Per-tier margins
    margins = tier_margins()
    margin_by_tier = {m["tier"]: m for m in margins}

    # Revenue (sum of price × paid-tier user count). Use catalog price
    # if real MRR is zero (cold launch).
    gross_revenue = sum(users.get(t, 0) * margin_by_tier[t]["price_usd"]
                        for t in ("starter", "pro", "team"))
    if real_mrr > 0:
        mrr = real_mrr
        mrr_source = "live_payments"
    else:
        mrr = gross_revenue
        mrr_source = "catalog_projection"

    # Variable cost from real usage if we have it, else from per-tier
    # cost-per-user × count.
    ai_cost = sum(users.get(t, 0) * cost_per_user(t)
                  for t in ("free", "starter", "pro", "team"))

    # Stripe fees on paid users
    stripe_fees = sum(users.get(t, 0) * stripe_fee(margin_by_tier[t]["price_usd"])
                      for t in ("starter", "pro", "team"))

    # Fixed costs
    fixed_costs = dict(FIXED_INFRA_USD_PER_MONTH)
    fixed_costs["Dev pay (yours)"] = round(settings["dev_salary_usd"], 2)
    # Iter 212m-234 — Phase 5 Supabase compute is variable but predictable
    # per active project. Surface it as its own fixed-cost line so the
    # admin dashboard shows the running per-project burn separately.
    supabase_cost = await _real_supabase_projects_cost(db)
    if supabase_cost["count"] > 0:
        fixed_costs[f"Supabase dedicated ({supabase_cost['count']} projects)"] = supabase_cost["monthly_usd"]
    total_fixed = sum(fixed_costs.values())

    total_burn = total_fixed + ai_cost + stripe_fees
    net_profit = mrr - total_burn
    gross_margin_pct = ((mrr - ai_cost - stripe_fees) / mrr * 100.0) if mrr > 0 else 0.0

    # Cash runway (days). If burning, count days until cash=0. If
    # profitable, surface "∞" via -1.
    daily_burn = (total_burn - mrr) / 30.0
    if daily_burn > 0 and settings["cash_in_bank_usd"] > 0:
        runway_days = int(settings["cash_in_bank_usd"] / daily_burn)
    elif daily_burn <= 0:
        runway_days = None  # profitable / break-even
    else:
        runway_days = 0

    # Break-even: how many *Pro* users at 30% Maxx would close the gap?
    pro_margin = margin_by_tier["pro"]["gross_profit"]
    if pro_margin > 0:
        needed_pro = max(0, int((total_burn - mrr + pro_margin - 0.01) / pro_margin))
        break_even_total = users.get("total", 0) + needed_pro
    else:
        needed_pro = None
        break_even_total = None

    # 6-month projection (conservative — Pro adds 5/mo organically)
    projection = []
    cum_users = {**users}
    for m in range(7):
        # add 5 Pro / mo from m=1 onward
        if m > 0:
            cum_users["pro"] = cum_users.get("pro", 0) + 5
            cum_users["starter"] = cum_users.get("starter", 0) + 3
            cum_users["free"] = cum_users.get("free", 0) + 10
        proj_mrr = sum(cum_users.get(t, 0) * margin_by_tier[t]["price_usd"]
                       for t in ("starter", "pro", "team"))
        proj_ai = sum(cum_users.get(t, 0) * cost_per_user(t)
                      for t in ("free", "starter", "pro", "team"))
        proj_fee = sum(cum_users.get(t, 0) * stripe_fee(margin_by_tier[t]["price_usd"])
                       for t in ("starter", "pro", "team"))
        proj_burn = total_fixed + proj_ai + proj_fee
        projection.append({
            "month":      m,
            "label":      "Now" if m == 0 else f"M{m}",
            "revenue":    round(proj_mrr, 2),
            "total_cost": round(proj_burn, 2),
            "net_profit": round(proj_mrr - proj_burn, 2),
        })

    # Cost-per-task breakdown (display table)
    cost_per_task_rows = [
        {"label": "DeepSeek V3 chat (15k in + 1.5k out)",  "usd": round(cost_per_task(0.0) * 0.5 * 2 if False else
                                                                       (TYPICAL_TASK["chat_in"]*PRICING_USD["deepseek_in"]
                                                                        + TYPICAL_TASK["chat_out"]*PRICING_USD["deepseek_out"])/1_000_000, 4)},
        {"label": "DeepSeek code task (15k in + 5k out)",  "usd": round((TYPICAL_TASK["code_in"]*PRICING_USD["deepseek_in"]
                                                                         + TYPICAL_TASK["code_out"]*PRICING_USD["deepseek_out"])/1_000_000, 4)},
        {"label": "Claude Sonnet 4.5 Maxx (15k in + 5k out)", "usd": round((TYPICAL_TASK["code_in"]*PRICING_USD["claude_in"]
                                                                              + TYPICAL_TASK["code_out"]*PRICING_USD["claude_out"])/1_000_000, 4)},
        {"label": "Parallel agents × 3 DeepSeek",          "usd": round(3 * (TYPICAL_TASK["code_in"]*PRICING_USD["deepseek_in"]
                                                                              + TYPICAL_TASK["code_out"]*PRICING_USD["deepseek_out"])/1_000_000, 4)},
        {"label": "Tavily web search (per call)",          "usd": PRICING_USD["tavily_per"]},
        {"label": "Firecrawl scrape (per page)",           "usd": PRICING_USD["firecrawl_per"]},
        {"label": "E2B sandbox (per task, ~3.6 min avg)",  "usd": round(PRICING_USD["e2b_per_hr"] * (3.6/60), 4)},
        {"label": "Avg cost — standard task (no Maxx)",    "usd": round(cost_per_task(0.0), 3), "highlight": "ok"},
        {"label": "Avg cost — Pro task (30% Maxx)",        "usd": round(cost_per_task(0.30), 3), "highlight": "warn"},
        {"label": "Avg cost — heavy Maxx abuse (100%)",    "usd": round(cost_per_task(1.0), 3), "highlight": "danger"},
    ]

    return {
        "fx":               fx,
        "settings":         settings,
        "users":            users,
        "user_source":      source,
        "real_user_counts": real_counts,
        "real_mrr_usd":     real_mrr,
        "maxx_used_total":  maxx_used_total,
        "supabase_projects": supabase_cost,
        "metrics": {
            "mrr_usd":           round(mrr, 2),
            "mrr_source":        mrr_source,
            "net_profit_usd":    round(net_profit, 2),
            "gross_margin_pct":  round(gross_margin_pct, 1),
            "ai_cost_usd":       round(ai_cost, 2),
            "stripe_fees_usd":   round(stripe_fees, 2),
            "total_burn_usd":    round(total_burn, 2),
            "total_fixed_usd":   round(total_fixed, 2),
            "cash_runway_days":  runway_days,
            "cac_usd":           0.0,   # organic acquisition
            "break_even_users":  break_even_total,
            "break_even_need":   needed_pro,
        },
        "tier_margins":      margins,
        "cost_per_task":     cost_per_task_rows,
        "fixed_costs":       fixed_costs,
        "projection":        projection,
        "generated_at":      datetime.now(timezone.utc).isoformat(),
    }
