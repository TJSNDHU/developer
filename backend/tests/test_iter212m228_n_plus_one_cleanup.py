"""
Iter 212m-228 — N+1 query cleanup + scanner precision.

Fixes 2 real N+1 patterns and tightens the perf scanner regex so it
stops false-positiving on unrelated for-loops that happen to sit
near a db call.

Real N+1s fixed:
1. `billing_cron.bill_maxx_overages`: was N sequential `dev_users.find_one`
   per overage row; now prefetches every user in ONE `$in` batch.
2. `findings.expose_findings`: was up to 100 sequential `find_one` on
   `cto_open_findings`; now prefetches with `$in`.

Scanner precision:
- N+1 regex now requires the await-db call to be indented ≥8 spaces
  (inside a real loop body, not just a sibling statement at the same
  scope). Kills 8+ FPs on files where a `for` iterates a computed
  list and a sibling db call sits underneath.
- Excludes `for X in ...aggregate(...)` / `.to_list()` / `.find(...)` —
  those are cursor iterations of a batch, not per-item N+1.
"""

from __future__ import annotations


def test_billing_cron_uses_in_batch():
    """bill_maxx_overages must NOT do a `find_one` inside a for loop."""
    src = open("/app/backend/services/billing_cron.py").read()
    # The old code had: `async for row in cursor:` followed by
    # `user = await db.dev_users.find_one(...)`. Fix uses a prefetch
    # dict `users_map` populated via ONE `$in` query.
    assert "users_map" in src, (
        "bill_maxx_overages must prefetch users into a map"
    )
    assert '"user_id": {"$in": uids}' in src, (
        "bill_maxx_overages must batch dev_users lookup via $in"
    )


def test_findings_expose_uses_in_batch():
    """expose_findings must NOT do a `find_one` inside a for loop."""
    src = open("/app/backend/routers/findings.py").read()
    assert "existing_map" in src, (
        "expose_findings must prefetch findings into a map"
    )
    assert '"finding_id": {"$in": fids}' in src, (
        "expose_findings must batch cto_open_findings lookup via $in"
    )


def test_perf_scanner_n_plus_one_rejects_sibling_calls():
    """A `for` loop followed by a sibling (same-indent) db call at
    module/function scope must NOT trigger n_plus_one — the db call
    isn't inside the loop."""
    from routers.codebase_health import _scan_performance

    # 4-space indent — sibling of the for, NOT inside it.
    src = (
        "async def foo(db):\n"
        "    intent_rows = await db.intent.aggregate(pipe).to_list(10)\n"
        "    tier_dist = {}\n"
        "    for r in intent_rows:\n"
        "        tier_dist[r['_id']] = r['n']\n"
        "    # This is a SIBLING call — outside the loop body\n"
        "    llm_count = await db.intent.count_documents({'x': 1})\n"
    )
    findings = _scan_performance({"backend/routers/example.py": src})
    n1 = [f for f in findings if f["title"] == "n_plus_one"]
    assert n1 == [], (
        f"Sibling-scope db call must not trigger n_plus_one: {n1}"
    )


def test_perf_scanner_n_plus_one_flags_real_case():
    """Actual N+1 (db call inside loop body, 8-space indent) MUST fire."""
    from routers.codebase_health import _scan_performance
    src = (
        "async def foo(db, uids):\n"
        "    for uid in uids:\n"
        "        u = await db.dev_users.find_one({'user_id': uid})\n"
        "        do(u)\n"
    )
    findings = _scan_performance({"backend/services/real_n1.py": src})
    n1 = [f for f in findings if f["title"] == "n_plus_one"]
    assert n1, f"Real N+1 must fire: {findings}"


def test_perf_scanner_ignores_aggregate_cursor_iteration():
    """`async for x in db.X.aggregate(...)` is NOT N+1 — it's a batch
    cursor. Same for `.to_list()` and single `.find()`."""
    from routers.codebase_health import _scan_performance
    src = (
        "async def stats(db):\n"
        "    async for d in db.cto_tasks.aggregate(pipe):\n"
        "        total += d.get('tokens', 0)\n"
    )
    findings = _scan_performance({"backend/services/agg.py": src})
    n1 = [f for f in findings if f["title"] == "n_plus_one"]
    assert n1 == [], (
        f"Aggregate cursor iteration must not trigger n_plus_one: {n1}"
    )
