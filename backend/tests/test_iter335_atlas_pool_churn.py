"""Iter 335 — prod Atlas pool-churn fix (deploy-log NetworkTimeout).

Prod deploy logs showed repeated pymongo NetworkTimeout tracebacks
from `_process_periodic_tasks → update_pool → remove_stale_sockets`
against Atlas shards. Cause: minPoolSize>0 + maxIdleTimeMS=30s forced
the pool-maintenance thread to re-dial Atlas TLS every 30 s to refill
the pool floor; any >10 s network wobble threw the traceback.

These source locks prevent the churn config from regressing.
"""
import re
from pathlib import Path

MAIN_SRC = Path("/app/backend/main.py").read_text(encoding="utf-8")


def _client_block() -> str:
    start = MAIN_SRC.index("app.state.mongo = AsyncIOMotorClient(")
    return MAIN_SRC[start:start + 400]


def test_min_pool_size_zero_no_maintenance_churn():
    assert "minPoolSize=0" in _client_block(), (
        "minPoolSize must stay 0 — any floor forces the pymongo "
        "maintenance thread to constantly re-dial Atlas "
        "(remove_stale_sockets NetworkTimeout storm in prod)."
    )


def test_max_idle_time_at_least_60s():
    m = re.search(r"maxIdleTimeMS=(\d[\d_]*)", _client_block())
    assert m, "maxIdleTimeMS missing from mongo client options"
    assert int(m.group(1).replace("_", "")) >= 60_000, (
        "maxIdleTimeMS < 60s recreates the Atlas TLS-handshake churn"
    )


def test_single_app_client():
    assert MAIN_SRC.count("AsyncIOMotorClient(") == 1, (
        "main.py must create exactly ONE motor client"
    )


def test_fail_fast_server_selection_kept():
    assert "serverSelectionTimeoutMS=5_000" in _client_block()
