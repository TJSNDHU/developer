"""
Iter 124g/i — Daily persona-eval cron is wired into the FastAPI lifespan
behind an opt-in env flag (ENABLE_EVAL_CRON=1). Off by default to prevent
the iter 124g first-fire crash loop where 22 sequential LLM calls hung
the pod past liveness-probe deadlines on production.
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
    # Iter 124i — must have a hard wall-clock cap on the whole battery
    assert "EVAL_TOTAL_BUDGET_S" in src
    assert "wait_for" in src


def test_lifespan_wires_eval_task_behind_flag():
    """The lifespan source must reference _schedule_daily_evals AND gate
    it behind the ENABLE_EVAL_CRON env var so production stays stable."""
    import main
    lifespan_src = inspect.getsource(main.lifespan)
    assert "_schedule_daily_evals" in lifespan_src
    assert "ENABLE_EVAL_CRON" in lifespan_src, (
        "cron must be opt-in — direct create_task without env gate is a regression"
    )


def test_runner_has_per_prompt_timeout():
    """Each prompt must be wall-capped so one runaway call can't hang the
    whole battery and trip the K8s liveness probe."""
    from evals import runner as r
    assert hasattr(r, "PROMPT_TIMEOUT_S")
    assert r.PROMPT_TIMEOUT_S > 0
    src = inspect.getsource(r._run_prompt)
    assert "wait_for" in src
    assert "PROMPT_TIMEOUT_S" in src

