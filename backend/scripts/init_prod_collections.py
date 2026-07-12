"""
backend/scripts/init_prod_collections.py
========================================
Iter 116 — Idempotent collection bootstrap.

Why this exists:
  Production Atlas deploys to a fresh DB. Code paths that only WRITE
  to a collection (e.g. cto_payments, vanguard_audit) defer collection
  creation until the first write — meaning admin pages that READ those
  collections show empty / 404 / aggregate errors on a brand-new prod.

  This script touches every required collection so MongoDB materialises
  it, then creates the indexes the codebase depends on. Safe to run on
  every boot (lifespan startup) — every operation is idempotent.

Public API:
    await init_prod_collections(db) -> dict
        Returns {"created": [...], "indexed": [...], "errors": [...]}
"""
from __future__ import annotations

import logging
import time
from typing import Iterable

logger = logging.getLogger(__name__)

# Each tuple = (collection_name, [(keys_spec, options_dict), ...])
# keys_spec: list of (field, direction) tuples — same shape Motor expects
# options: passed verbatim to create_index (e.g. unique=True, expireAfterSeconds)
_BOOTSTRAP_SPEC: list[tuple[str, list[tuple[list, dict]]]] = [
    ("cto_payments", [
        ([("user_id", 1), ("created_at", -1)], {}),
        ([("stripe_session_id", 1)],           {"sparse": True}),
        ([("status", 1)],                       {}),
    ]),
    ("cto_support", [
        ([("user_id", 1), ("created_at", -1)], {}),
        ([("status", 1)],                       {}),
        ([("ticket_id", 1)],                    {"unique": True, "sparse": True}),
    ]),
    ("cto_support_messages", [
        ([("ticket_id", 1), ("ts", 1)], {}),
        ([("user_id", 1)],              {}),
    ]),
    ("cto_token_grants", [
        ([("user_id", 1), ("granted_at", -1)], {}),
        ([("source", 1)],                       {}),
    ]),
    ("cto_vault_audit_log", [
        ([("user_id", 1), ("ts", -1)], {}),
        ([("action", 1)],              {}),
        ([("project_id", 1)],          {"sparse": True}),
    ]),
    ("referrals", [
        ([("referrer_id", 1)],                   {}),
        ([("referred_user_id", 1)],              {"sparse": True}),
        ([("status", 1)],                        {}),
        ([("created_at", -1)],                   {}),
    ]),
    ("vanguard_audit", [
        ([("ts_unix", -1)],          {}),
        ([("user_id", 1)],            {}),
        ([("project", 1)],            {}),
        ([("rule_triggered", 1)],     {}),
    ]),
    ("cto_automations", [
        ([("user_id", 1), ("created_at", -1)], {}),
        ([("status", 1)],                       {}),
        ([("automation_id", 1)],                {"unique": True, "sparse": True}),
    ]),
    # Iter 212m-172 — project_plans collection removed together with
    # the /projects/plan + /projects/build Flow-B endpoints.  All
    # project state now lives exclusively in cto_projects.
    ("aurem_cto_unlock_requests", [
        ([("user_id", 1), ("ts", -1)], {}),
        ([("status", 1)],              {}),
        ([("email", 1)],               {"sparse": True}),
    ]),
    # Iter 121 — auditor found these large collections running on _id
    # only. chat_sessions (801 docs) is read on every chat-history load
    # via {user_id, updated_at}; without an index that's a full scan.
    # dev_users (297 docs) is the auth + admin search target; email
    # lookup is hot path.
    ("chat_sessions", [
        ([("user_id", 1), ("updated_at", -1)], {}),
        # NB: session_id is NOT made unique — historic data has at least
        # one legitimate test duplicate ("e2e-F12") and unique enforcement
        # would block new writes. Lookups by session_id still benefit
        # from the implicit _id-prefix.
    ]),
    ("dev_users", [
        ([("email", 1)],        {"unique": True, "sparse": True}),
        ([("user_id", 1)],      {"unique": True, "sparse": True}),
        ([("created_at", -1)],  {}),
    ]),
    ("cto_tasks", [
        ([("user_id", 1), ("created_at", -1)], {}),
        ([("project_id", 1), ("created_at", -1)], {}),
        ([("task_id", 1)],     {"unique": True, "sparse": True}),
        ([("status", 1)],      {}),
    ]),
    ("cto_projects", [
        ([("user_id", 1), ("created_at", -1)], {}),
        ([("project_id", 1)],  {"unique": True, "sparse": True}),
    ]),
    # Iter 123b — ORA skill usage analytics. The aggregation pipeline
    # in /admin/skills-usage groups by `tool` and filters by `ts`, so
    # both fields are indexed. Writes are fire-and-forget so the
    # workload is heavily write-skewed — keep indexes minimal.
    ("ora_skill_usage", [
        ([("ts", -1)],              {}),
        ([("tool", 1), ("ts", -1)], {}),
    ]),
    # Iter 140 — Feature flags (kill switches + canaries). Lookup
    # path is always by `flag` and the value is small, so a single
    # unique index covers all queries.
    ("feature_flags", [
        ([("flag", 1)],    {"unique": True}),
        ([("enabled", 1)], {}),
    ]),
    # ─────────────────────────────────────────────────────────────
    # Iter 212m-70 — Database performance audit.
    # 12 hot collections caught by the audit that were running on _id
    # only.  Every collection here is referenced by .find / .find_one
    # in routers/services at least twice and was triggering a full
    # collection scan on every read.  Adding these indexes flips the
    # query plan from COLLSCAN to IXSCAN — 10-100× speed-up.
    # ─────────────────────────────────────────────────────────────
    ("github_connections", [
        ([("user_id", 1), ("created_at", -1)], {}),
        ([("user_id", 1), ("github_user", 1)], {"sparse": True}),
    ]),
    ("aurem_cto_deploy_runs", [
        ([("user_id", 1), ("created_at", -1)],    {}),
        ([("project_id", 1), ("created_at", -1)], {"sparse": True}),
        ([("status", 1)],                          {}),
    ]),
    ("api_keys", [
        ([("user_id", 1)],            {}),
        ([("key_hash", 1)],           {"unique": True, "sparse": True}),
        ([("provider", 1)],           {}),
    ]),
    ("user_seo_claims", [
        ([("user_id", 1)],            {}),
        ([("domain", 1)],             {"unique": True, "sparse": True}),
        ([("status", 1)],             {}),
    ]),
    ("thinking_hints", [
        ([("user_id", 1), ("ts", -1)], {}),
        ([("project_id", 1)],          {"sparse": True}),
    ]),
    ("thinking_hints_config", [
        ([("user_id", 1)], {"unique": True, "sparse": True}),
    ]),
    # onboarding_projects removed from init on 2026-02-Session-5: the
    # collection had readers but zero writers, so all reads always
    # returned None. Callers now hit `cto_projects` (single source of
    # truth). Re-add here if a real onboarding writer is introduced.
    ("founder_offer", [
        ([("user_id", 1)], {"unique": True, "sparse": True}),
        ([("email", 1)],   {"sparse": True}),
    ]),
    ("cto_maxx_usage", [
        ([("user_id", 1), ("ts", -1)],          {}),
        ([("project_id", 1), ("ts", -1)],       {"sparse": True}),
    ]),
    ("cto_codebase_index", [
        ([("project_id", 1), ("path", 1)], {"unique": True, "sparse": True}),
        ([("project_id", 1), ("ts", -1)],  {}),
    ]),
    ("topup_alerts", [
        ([("user_id", 1)],          {}),
        ([("triggered_at", -1)],    {}),
        ([("alert_key", 1)],        {"unique": True, "sparse": True}),
    ]),
    ("project_graphs", [
        ([("project_id", 1)], {"unique": True, "sparse": True}),
    ]),
    ("ora_patterns", [
        ([("pattern_type", 1), ("ts", -1)], {}),
        ([("user_id", 1)],                   {"sparse": True}),
    ]),
    ("onboarding_emails", [
        # Batched eligibility lookup uses {user_id $in [...], campaign, stage}.
        # Not unique — historic rows have one legitimate duplicate per
        # campaign/stage pair from retry attempts, so we keep it as a
        # plain compound index that still satisfies the query planner.
        ([("user_id", 1), ("campaign", 1), ("stage", 1)], {}),
        ([("sent_at", -1)], {"sparse": True}),
    ]),
    # ─────────────────────────────────────────────────────────────
    # Iter 212m-190 (Directive Session 1 · Part E) — Scan backlog +
    # notification-strip persistence.
    #
    # cto_open_findings: canonical store for UNFIXED critical/high
    # findings across scans (Vanguard, Bug Hunt, Health, HTTP headers,
    # Docker CIS). Powers:
    #   • The scan-status strip's "30-day idle" backlog reminder
    #   • The Review-findings drawer
    #   • Auto-archive after 4 exposures ("aged-out" status)
    # Distinct from `cto_fixed_findings` (post-fix audit trail).
    #
    # Schema:
    #   { user_id, project_id, finding_id,
    #     category, severity ("critical"|"high"|"medium"|"low"),
    #     rule_id, file, line, title, message, fix_hint,
    #     status ("open"|"snoozed"|"fixed"|"aged-out"),
    #     first_seen_at, last_seen_at,
    #     exposure_count (int, caps at 4),
    #     last_exposed_at, snoozed_until }
    # ─────────────────────────────────────────────────────────────
    ("cto_open_findings", [
        # Hot path: dashboard + strip both filter by user + project +
        # status, sorted by severity/age. This composite covers those.
        ([("user_id", 1), ("project_id", 1), ("status", 1)], {}),
        # Per-finding upsert key. Sparse because pre-existing rows in
        # test fixtures may lack a canonical finding_id.
        ([("user_id", 1), ("project_id", 1), ("finding_id", 1)],
         {"unique": True, "sparse": True}),
        # Backlog-reminder scheduler query: findings idle 30+ days.
        ([("last_seen_at", 1), ("status", 1)], {}),
        # Severity dashboards.
        ([("severity", 1), ("last_seen_at", -1)], {}),
    ]),
    # cto_notification_dismissals: strip "X" (dismiss) persistence.
    # DB-backed (not sessionStorage) so a dismiss carries across
    # devices / logout+login.
    #
    # Schema:
    #   { user_id, project_id, finding_batch_id, dismissed_at,
    #     expires_at }   # 24 h TTL — see index below.
    ("cto_notification_dismissals", [
        # Fast lookup for "is this batch dismissed right now?"
        ([("user_id", 1), ("project_id", 1), ("finding_batch_id", 1)],
         {"unique": True, "sparse": True}),
        # TTL: MongoDB will delete docs automatically after
        # expires_at, so we don't accumulate stale dismissals forever.
        # `expireAfterSeconds=0` means "delete when expires_at is in
        # the past" — the doc supplies the actual timestamp.
        ([("expires_at", 1)], {"expireAfterSeconds": 0}),
    ]),
]

# Bootstrap sentinel — written then removed so collection materialises.
_SENTINEL_KEY  = "_init_prod_sentinel"
_SENTINEL_VAL  = "iter116-bootstrap"


async def _materialise(db, name: str) -> bool:
    """Force-create a collection by writing + deleting a sentinel doc.
    Returns True if we actually touched it (created or re-touched)."""
    try:
        existing = await db.list_collection_names()
        if name in existing:
            return False  # already there
        await db[name].insert_one({_SENTINEL_KEY: _SENTINEL_VAL,
                                    "ts": time.time()})
        await db[name].delete_one({_SENTINEL_KEY: _SENTINEL_VAL})
        return True
    except Exception as e:
        logger.warning("materialise %s failed: %r", name, e)
        return False


async def _ensure_indexes(db, name: str,
                          specs: Iterable[tuple[list, dict]]) -> int:
    n = 0
    for keys, opts in specs:
        try:
            await db[name].create_index(keys, **(opts or {}))
            n += 1
        except Exception as e:
            logger.warning("create_index %s %r failed: %r", name, keys, e)
    return n


_LAST_BOOTSTRAP: dict | None = None


def get_last_bootstrap() -> dict | None:
    """Return the result of the most recent init_prod_collections call,
    or None if it hasn't been called yet this process."""
    return _LAST_BOOTSTRAP


async def init_prod_collections(db) -> dict:
    """Idempotent bootstrap. Safe to call on every boot."""
    from datetime import datetime, timezone
    out = {"created": [], "indexed": [], "errors": [], "ts": datetime.now(timezone.utc).isoformat()}
    if db is None:
        out["errors"].append("db is None")
        global _LAST_BOOTSTRAP
        _LAST_BOOTSTRAP = out
        return out
    for name, idx_specs in _BOOTSTRAP_SPEC:
        try:
            if await _materialise(db, name):
                out["created"].append(name)
            count = await _ensure_indexes(db, name, idx_specs)
            if count:
                out["indexed"].append(f"{name}:{count}")
        except Exception as e:
            out["errors"].append(f"{name}: {type(e).__name__}: {e}")
            logger.warning("init_prod_collections %s failed: %r", name, e)
    # Iter 140 — seed default feature flags if the collection is empty.
    # Idempotent: only runs once per fresh DB.
    try:
        if await db.feature_flags.count_documents({}) == 0:
            await db.feature_flags.insert_many([
                {"flag": "new_analytics_v2", "enabled": True,
                 "tier_allowlist": [], "user_allowlist": [],
                 "description": "New product analytics dashboard"},
                {"flag": "maxx_mode_beta", "enabled": True,
                 "tier_allowlist": ["pro", "team", "founder"],
                 "user_allowlist": [],
                 "description": "Maxx dual-model review mode"},
                {"flag": "parallel_agents", "enabled": True,
                 "tier_allowlist": ["pro", "team", "founder"],
                 "user_allowlist": [],
                 "description": "3-agent parallel task execution"},
                {"flag": "chrome_extension_beta", "enabled": False,
                 "tier_allowlist": [], "user_allowlist": [],
                 "description": "Chrome extension side panel (beta)"},
            ])
            out["created"].append("feature_flags:seeded(4)")
    except Exception as e:
        out["errors"].append(f"feature_flags seed: {type(e).__name__}: {e}")
        logger.warning("feature_flags seed failed: %r", e)
    logger.info("init_prod_collections done — created=%d, indexed=%d, errors=%d",
                len(out["created"]), len(out["indexed"]), len(out["errors"]))
    _LAST_BOOTSTRAP = out
    return out
