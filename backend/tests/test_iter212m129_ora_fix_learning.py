"""
Iter 212m-129 — ORA fix-learning Phase-1 logging foundation.

Tests cover:
  • record_fix_outcome() writes the expected row shape for both
    success and failure paths.
  • Terminal error codes correctly flip `retryable: false`.
  • Vanguard vuln classes (sql_injection, secret_leak, etc.) are
    bucketed into category="vanguard".
  • record_scan_run() persists the per-rule + per-severity counts.
  • get_rule_stats() aggregates correctly across multiple rows.
  • ensure_indexes() is idempotent + creates all 5 indexes.
  • Mongo failures NEVER propagate — the learning service is a
    silent best-effort writer, not a critical path.
"""
from __future__ import annotations

import asyncio
import time

import pytest


# ─── Minimal Mongo double (re-used pattern from iter 212m-128) ────
class _Cursor:
    def __init__(self, rows):
        self._rows = list(rows)

    async def __anext__(self):
        if not self._rows:
            raise StopAsyncIteration
        return self._rows.pop(0)

    def __aiter__(self):
        return self


class _Coll:
    def __init__(self):
        self.rows: list[dict] = []
        self.indexes: list[tuple] = []

    async def insert_one(self, doc):
        self.rows.append(dict(doc))
        class _R:
            inserted_id = "x"
        return _R()

    async def create_index(self, keys, *, name=None, **_kw):
        self.indexes.append((tuple(keys) if isinstance(keys, list) else keys,
                             name))
        return name or "ix"

    def aggregate(self, pipeline):
        # Tiny pipeline executor — only $match + $group + $sort + $limit
        rows = [dict(r) for r in self.rows]
        for stage in pipeline:
            if "$match" in stage:
                m = stage["$match"]
                rows = [r for r in rows
                        if all(_match_value(r.get(k), v)
                               for k, v in m.items())]
            elif "$group" in stage:
                grp = stage["$group"]
                key_field = grp["_id"].lstrip("$")
                buckets: dict = {}
                for r in rows:
                    k = r.get(key_field)
                    b = buckets.setdefault(k, {"_id": k})
                    for out_key, op in grp.items():
                        if out_key == "_id":
                            continue
                        if "$sum" in op:
                            inner = op["$sum"]
                            if inner == 1:
                                b[out_key] = b.get(out_key, 0) + 1
                            elif isinstance(inner, str):
                                b[out_key] = b.get(out_key, 0) + (
                                    r.get(inner.lstrip("$")) or 0)
                            elif isinstance(inner, dict) and "$cond" in inner:
                                cond = inner["$cond"]
                                test = cond[0]
                                if "$eq" in test:
                                    lhs, rhs = test["$eq"]
                                    if r.get(lhs.lstrip("$")) == rhs:
                                        b[out_key] = b.get(out_key, 0) + cond[1]
                                    else:
                                        b[out_key] = b.get(out_key, 0) + cond[2]
                        elif "$max" in op:
                            v = r.get(op["$max"].lstrip("$"))
                            if v is not None:
                                b[out_key] = max(b.get(out_key, v), v)
                rows = list(buckets.values())
            elif "$sort" in stage:
                for key, direction in reversed(list(stage["$sort"].items())):
                    rows.sort(key=lambda r, _k=key: r.get(_k) or 0,
                              reverse=(direction == -1))
            elif "$limit" in stage:
                rows = rows[: stage["$limit"]]
        return _Cursor(rows)


def _match_value(actual, query):
    if isinstance(query, dict):
        if "$gte" in query and (actual is None or actual < query["$gte"]):
            return False
        return True
    return actual == query


class _DB:
    def __init__(self):
        self.ora_fix_learning  = _Coll()
        self.ora_scan_learning = _Coll()


# ──────────────────────────────────────────────────────────────────
# 1) Success path — full row shape
# ──────────────────────────────────────────────────────────────────
def test_record_fix_outcome_success_row():
    from services import ora_fix_learning as ofl
    db = _DB()
    finding = {
        "rule_id":  "eval_usage",
        "file":     "backend/x.py",
        "line":     42,
        "severity": "high",
        "title":    "eval is unsafe",
        "scanner":  "vanguard",
    }
    result = {
        "ok":         True,
        "commit_sha": "abc1234",
        "full_sha":   "abc1234" + "0" * 33,
        "html_url":   "https://github.com/u/r/commit/abc1234",
        "pr_url":     "https://github.com/u/r/pull/9",
        "branch":     "aurem/fix-eval_usage-1",
        "verified":   True,
    }
    asyncio.run(ofl.record_fix_outcome(
        db, user_id="u1", project_id="p1", finding=finding,
        result=result, attempts=1, duration_ms=2150,
        tokens_charged=5, scanner="vanguard",
    ))
    assert len(db.ora_fix_learning.rows) == 1
    row = db.ora_fix_learning.rows[0]
    assert row["outcome"] == "success"
    assert row["error_code"] is None
    assert row["rule_id"] == "eval_usage"
    assert row["file"]    == "backend/x.py"
    assert row["severity"] == "high"
    assert row["commit_sha"] == "abc1234" + "0" * 33
    assert row["html_url"]   == "https://github.com/u/r/commit/abc1234"
    assert row["pr_url"]     == "https://github.com/u/r/pull/9"
    assert row["verified"]   is True
    assert row["tokens_charged"] == 5
    assert row["duration_ms"]    == 2150
    assert row["attempts"]       == 1
    assert row["retryable"]      is False  # success → not applicable
    assert row["category"]       == "vanguard"
    assert row["scanner"]        == "vanguard"
    assert row["learning_id"].startswith("fl_")


# ──────────────────────────────────────────────────────────────────
# 2) Failure with non-terminal error — retryable=True
# ──────────────────────────────────────────────────────────────────
def test_record_fix_outcome_failure_retryable():
    from services import ora_fix_learning as ofl
    db = _DB()
    finding = {"rule_id": "sql_injection", "file": "x.py", "severity": "critical"}
    result  = {"ok": False, "error": "llm_no_change"}
    asyncio.run(ofl.record_fix_outcome(
        db, user_id="u1", project_id="p1",
        finding=finding, result=result, attempts=3, duration_ms=1200,
    ))
    row = db.ora_fix_learning.rows[0]
    assert row["outcome"]    == "failure"
    assert row["error_code"] == "llm_no_change"
    assert row["retryable"]  is True
    assert row["attempts"]   == 3
    # sql_injection → vanguard bucket
    assert row["category"]   == "vanguard"


# ──────────────────────────────────────────────────────────────────
# 3) Failure with TERMINAL error — retryable=False
# ──────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("err", [
    "github_credentials_missing",
    "github_unauthorized",
    "insufficient_tokens",
    "insufficient_tokens_midbatch",
    "file_too_large",
    "project_not_found_or_not_yours",
])
def test_terminal_errors_flagged_non_retryable(err):
    from services import ora_fix_learning as ofl
    db = _DB()
    asyncio.run(ofl.record_fix_outcome(
        db, user_id="u1", project_id="p1",
        finding={"rule_id": "x", "file": "y.py", "severity": "low"},
        result={"ok": False, "error": err},
    ))
    row = db.ora_fix_learning.rows[0]
    assert row["outcome"]    == "failure"
    assert row["error_code"] == err
    assert row["retryable"]  is False


# ──────────────────────────────────────────────────────────────────
# 4) Vanguard vuln class mapping
# ──────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("cat_in,expected", [
    ("secret_leak",    "vanguard"),
    ("sql_injection",  "vanguard"),
    ("ssti",           "vanguard"),
    ("redos",          "vanguard"),
    ("chain",          "vanguard"),
    ("security",       "security"),      # untouched
    ("performance",    "performance"),   # untouched
    ("",               "unknown"),
])
def test_finding_category_buckets(cat_in, expected):
    from services import ora_fix_learning as ofl
    db = _DB()
    asyncio.run(ofl.record_fix_outcome(
        db, user_id="u1", project_id="p1",
        finding={"rule_id": "r", "file": "y.py", "severity": "low",
                 "category": cat_in},
        result={"ok": True},
    ))
    assert db.ora_fix_learning.rows[0]["category"] == expected


# ──────────────────────────────────────────────────────────────────
# 5) record_scan_run shape
# ──────────────────────────────────────────────────────────────────
def test_record_scan_run_shape():
    from services import ora_fix_learning as ofl
    db = _DB()
    asyncio.run(ofl.record_scan_run(
        db, user_id="u1", project_id="p1",
        scanner="codebase_health",
        categories=["security", "performance"],
        files_scanned=320,
        counts={"critical": 2, "high": 5, "medium": 11, "low": 4, "info": 0},
        rule_counts={"eval_usage": 3, "sql_injection": 1, "missing_index": 7},
        duration_ms=1850, score=72,
    ))
    row = db.ora_scan_learning.rows[0]
    assert row["scanner"]       == "codebase_health"
    assert row["files_scanned"] == 320
    assert row["counts"]["critical"] == 2
    assert row["rule_counts"]["eval_usage"] == 3
    assert row["score"] == 72
    assert row["scan_id"].startswith("sl_")
    assert row["categories"] == ["security", "performance"]


# ──────────────────────────────────────────────────────────────────
# 6) get_rule_stats aggregation
# ──────────────────────────────────────────────────────────────────
def test_get_rule_stats_aggregates_by_rule():
    from services import ora_fix_learning as ofl
    db = _DB()
    now = time.time()
    # eval_usage: 3 success + 1 failure
    for _ in range(3):
        asyncio.run(ofl.record_fix_outcome(
            db, user_id="u1", project_id="p1",
            finding={"rule_id": "eval_usage", "file": "a.py",
                     "severity": "high"},
            result={"ok": True},
        ))
    asyncio.run(ofl.record_fix_outcome(
        db, user_id="u1", project_id="p1",
        finding={"rule_id": "eval_usage", "file": "a.py",
                 "severity": "high"},
        result={"ok": False, "error": "llm_no_change"},
    ))
    # sql_injection: 1 failure
    asyncio.run(ofl.record_fix_outcome(
        db, user_id="u1", project_id="p1",
        finding={"rule_id": "sql_injection", "file": "b.py",
                 "severity": "critical"},
        result={"ok": False, "error": "github_unauthorized"},
    ))
    stats = asyncio.run(ofl.get_rule_stats(db, user_id="u1", limit=10))
    by_rule = {s["rule_id"]: s for s in stats}
    assert by_rule["eval_usage"]["total"]   == 4
    assert by_rule["eval_usage"]["success"] == 3
    assert by_rule["eval_usage"]["failure"] == 1
    assert by_rule["eval_usage"]["success_rate"] == 0.75
    assert by_rule["sql_injection"]["total"]   == 1
    assert by_rule["sql_injection"]["success"] == 0
    assert by_rule["sql_injection"]["success_rate"] == 0.0
    # Sorted by total desc → eval_usage first
    assert stats[0]["rule_id"] == "eval_usage"
    # last_at populated
    assert by_rule["eval_usage"]["last_at"] >= now - 1


def test_get_rule_stats_filters_by_user_and_rule():
    from services import ora_fix_learning as ofl
    db = _DB()
    asyncio.run(ofl.record_fix_outcome(
        db, user_id="u1", project_id="p1",
        finding={"rule_id": "r1", "file": "a", "severity": "low"},
        result={"ok": True},
    ))
    asyncio.run(ofl.record_fix_outcome(
        db, user_id="u2", project_id="p1",
        finding={"rule_id": "r1", "file": "a", "severity": "low"},
        result={"ok": True},
    ))
    # filter by user
    only_u1 = asyncio.run(ofl.get_rule_stats(db, user_id="u1"))
    assert only_u1[0]["total"] == 1
    # filter by rule
    only_r1 = asyncio.run(ofl.get_rule_stats(db, rule_id="r1"))
    assert only_r1[0]["total"] == 2


# ──────────────────────────────────────────────────────────────────
# 7) ensure_indexes — idempotent + all 5 created
# ──────────────────────────────────────────────────────────────────
def test_ensure_indexes_creates_all_five():
    from services import ora_fix_learning as ofl
    db = _DB()
    asyncio.run(ofl.ensure_indexes(db))
    names = {n for _, n in db.ora_fix_learning.indexes}
    assert "ix_ofl_user_rule_ts"     in names
    assert "ix_ofl_rule_outcome_ts"  in names
    assert "ix_ofl_project_ts"       in names
    names_scan = {n for _, n in db.ora_scan_learning.indexes}
    assert "ix_osl_user_scanner_ts"  in names_scan
    assert "ix_osl_project_ts"       in names_scan
    # Idempotent — second call shouldn't crash even though the
    # double would happily duplicate them.
    asyncio.run(ofl.ensure_indexes(db))


# ──────────────────────────────────────────────────────────────────
# 8) Mongo failures must NOT propagate
# ──────────────────────────────────────────────────────────────────
def test_record_fix_outcome_swallows_mongo_errors():
    """A broken insert_one (network blip, write conflict) must not
    take down the user-facing fix endpoint that called us."""
    from services import ora_fix_learning as ofl

    class _BrokenColl:
        async def insert_one(self, _doc):
            raise RuntimeError("simulated mongo down")
        async def create_index(self, *_a, **_kw):
            return "ix"

    class _BrokenDB:
        ora_fix_learning  = _BrokenColl()
        ora_scan_learning = _BrokenColl()

    # Must not raise — this is the entire contract.
    asyncio.run(ofl.record_fix_outcome(
        _BrokenDB(), user_id="u1", project_id="p1",
        finding={"rule_id": "x", "file": "y.py", "severity": "low"},
        result={"ok": True},
    ))
    asyncio.run(ofl.record_scan_run(
        _BrokenDB(), user_id="u1", project_id="p1",
        scanner="vanguard", categories=[],
        files_scanned=0, counts={}, rule_counts={},
    ))


def test_db_none_no_crash():
    from services import ora_fix_learning as ofl
    # None db (early-boot scenario) must short-circuit safely.
    asyncio.run(ofl.record_fix_outcome(
        None, user_id="u1", project_id="p1",
        finding={"rule_id": "x", "file": "y", "severity": "low"},
        result={"ok": True},
    ))
    asyncio.run(ofl.record_scan_run(
        None, user_id="u1", project_id="p1",
        scanner="vanguard", categories=[], files_scanned=0,
        counts={}, rule_counts={},
    ))
    res = asyncio.run(ofl.get_rule_stats(None))
    assert res == []
    asyncio.run(ofl.ensure_indexes(None))
