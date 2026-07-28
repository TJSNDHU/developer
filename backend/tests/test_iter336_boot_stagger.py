"""Iter 336 — boot stagger locks (prod /health flap during deploy).

Prod deploy logs: nginx `upstream timed out` on /health + middleware
`RuntimeError: No response returned` right after boot. Cause: ~25
background tasks all fired at t=0 under the 500m CPU cap — worst
offender `npm install -g eslint` (60-90 s sustained CPU) — starving
the event loop past nginx's 1 s /health proxy timeout, so the
platform's post-deploy health check hit the flap window and reported
the deployment as failing.

These locks pin the stagger delays so they don't silently regress.
"""
import re
from pathlib import Path

MAIN = Path("/app/backend/main.py").read_text(encoding="utf-8")
CRON = Path("/app/backend/services/integration_health_cron.py").read_text(
    encoding="utf-8")


def _delay_before(marker: str, src: str = MAIN) -> float:
    """First asyncio.sleep(N) that appears AFTER `marker`."""
    seg = src.split(marker, 1)[1][:800]
    m = re.search(r"sleep\((\d+(?:\.\d+)?)\)", seg)
    assert m, f"no stagger sleep found after {marker!r}"
    return float(m.group(1))


def test_linter_install_deferred_past_readiness_window():
    d = _delay_before("async def _probe_loop_linters")
    assert d >= 90, (
        "npm install -g eslint must NOT run inside the post-deploy "
        f"readiness window (got stagger {d}s)"
    )


def test_codebase_index_warm_staggered():
    assert _delay_before("async def _warm_codebase_index") >= 30


def test_dev_users_backfills_staggered():
    assert _delay_before("async def _backfill_dev_users_created_at") >= 45
    assert _delay_before("async def _backfill_dev_users_track") >= 45


def test_integration_health_first_fire_after_boot_tasks():
    d = _delay_before("# First probe:", CRON)
    assert d >= 120, (
        "first 11-probe burst must land after the deferred boot tasks"
    )


def test_root_health_endpoint_is_trivial():
    """/health (nginx-probed) must stay a constant-time handler —
    no awaits, no DB, nothing that can exceed nginx's 1 s timeout."""
    seg = MAIN.split("async def healthz_root")[1][:200]
    assert 'return {"ok": True}' in seg
    assert "await" not in seg.split("return")[0]
