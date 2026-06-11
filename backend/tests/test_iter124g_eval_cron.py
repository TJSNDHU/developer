"""
Iter 124g — Daily persona-eval cron is wired into the FastAPI lifespan.
"""
from __future__ import annotations

import inspect


def test_scheduler_exists():
    """The lifespan attaches _schedule_daily_evals as a background task."""
    import main
    assert hasattr(main, "_schedule_daily_evals")
    src = inspect.getsource(main._schedule_daily_evals)
    # Sanity — the body references the runner and respects EVAL_HOUR_UTC.
    assert "EVAL_HOUR_UTC" in src
    assert "evals.runner" in src
    assert "ora_eval_runs" in src or "run_evals" in src or "_run_evals" in src


def test_lifespan_wires_eval_task():
    """The lifespan source must reference _schedule_daily_evals so we
    catch any accidental removal."""
    import main, inspect
    lifespan_src = inspect.getsource(main.lifespan)
    assert "_schedule_daily_evals" in lifespan_src

