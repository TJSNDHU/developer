"""Iter 127 regression: lifespan startup must NOT block on network I/O.

Background: prod deploys were CrashLoopBackOff'ing because the FastAPI
`lifespan.startup` phase awaited a MongoDB Atlas ping (~15-17 s on
cold TLS+SRV lookup), then sequentially awaited index ensure +
collection bootstrap + deploy_event log. uvicorn doesn't bind port
8001 until lifespan.startup completes, so nginx upstream got
111/ECONNREFUSED for the whole ~19 s window and the K8s liveness
probe killed the pod.

The fix moved every nice-to-have boot await into a background
`_bg_bootstrap` task. Lifespan now creates the Motor client (cheap,
non-blocking) and yields immediately. This test pins the structure
so the regression cannot silently come back.
"""
from __future__ import annotations

import pathlib
import re


MAIN_PY = pathlib.Path(__file__).resolve().parents[1] / "main.py"


def _lifespan_block() -> str:
    """Return just the `lifespan` async-context-manager body."""
    src = MAIN_PY.read_text(encoding="utf-8")
    m = re.search(
        r"@asynccontextmanager\s*\nasync def lifespan\(.*?\n(    if.*?bootstrap_task.*?\.cancel\(\))",
        src,
        re.DOTALL,
    )
    assert m, "could not isolate lifespan() block from main.py"
    return m.group(0)


def test_lifespan_does_not_await_mongo_ping_inline() -> None:
    """The Atlas-ping (which took 15+ s on cold connections) MUST be
    inside a background task, not awaited at the top of lifespan."""
    block = _lifespan_block()
    # The ping line still exists, but only INSIDE the _bg_bootstrap
    # helper. We split on the helper definition and look for the ping
    # only in the post-helper region (i.e., it should not be in the
    # pre-helper region where lifespan still blocks the listener).
    pre, _, post = block.partition("async def _bg_bootstrap")
    # Strip comments before scanning so the explanatory comment about
    # the past blocking behaviour doesn't trip the guard.
    pre_code = "\n".join(
        line for line in pre.splitlines()
        if not line.lstrip().startswith("#")
    )
    assert 'await app.state.mongo.admin.command("ping")' not in pre_code, (
        "MongoDB ping is being awaited inline in lifespan — this "
        "blocks uvicorn from binding the port and recreates the "
        "ECONNREFUSED crash-loop from Iter 127."
    )
    # Sanity: the ping IS still present somewhere in the file (in the
    # background helper). We don't want to regress to "no ping ever".
    assert 'await app.state.mongo.admin.command("ping")' in post, (
        "MongoDB ping has been dropped entirely — the bg bootstrap "
        "should still run a connectivity check (logged, non-fatal)."
    )


def test_lifespan_offloads_heavy_boot_work_to_background() -> None:
    """init_prod_collections, ensure_indexes, log_deploy_event — none
    of these should be awaited inline in lifespan. They go in
    `_bg_bootstrap` so the listener binds the port in <500 ms."""
    block = _lifespan_block()
    pre, _, _ = block.partition("async def _bg_bootstrap")
    blockers = [
        "await _ora_idx()",
        "await init_prod_collections(",
        "await log_deploy_event(",
    ]
    for blocker in blockers:
        assert blocker not in pre, (
            f"`{blocker}` is awaited inline in lifespan — move it "
            f"into the _bg_bootstrap task so port 8001 binds fast."
        )


def test_lifespan_creates_bootstrap_background_task() -> None:
    """The background bootstrap task must actually be scheduled
    (otherwise the work never runs at all)."""
    block = _lifespan_block()
    assert "app.state.bootstrap_task = _asyncio.create_task(_bg_bootstrap())" in block, (
        "background bootstrap task is not scheduled — collections "
        "and indexes won't be created on boot."
    )
