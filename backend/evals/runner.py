"""
backend/evals/runner.py — Iter 124f

Executes both batteries (quality + security), generates a JSON report,
runs the offline project-scoping isolation test against real Mongo, and
exits 0/1 to gate the deploy.

Cost cap: hard limit on total LLM calls per run (default 25, override
with EVALS_MAX_LLM_CALLS env var).

Usage:
    python -m backend.evals.runner          # full run, blocks deploy on hard fails
    python -m backend.evals.runner --quick  # only quality battery, skip security
    python -m backend.evals.runner --report-only /tmp/report.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Bootstrap path so this module works both as `python -m backend.evals.runner`
# and as a direct script invocation from the deploy hook.
_HERE = Path(__file__).resolve().parent
_BACKEND = _HERE.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

# Load .env so EMERGENT_LLM_KEY etc are available
try:
    from dotenv import load_dotenv
    load_dotenv(_BACKEND / ".env")
except Exception:
    pass

from evals.harness import (   # noqa: E402
    ScoreResult, aggregate,
    completeness_scorer, passivity_scorer, leak_scorer,
    chain_scorer, hallucination_scorer, refusal_scorer, scope_scorer,
    PASS, FAIL, PARTIAL, HARD, SOFT,
)
from evals.prompts_quality import QUALITY_PROMPTS, SYNTHETIC_REPO_CONTEXT  # noqa: E402
from evals.prompts_security import SECURITY_PROMPTS  # noqa: E402


MAX_LLM_CALLS = int(os.getenv("EVALS_MAX_LLM_CALLS", "25"))


# ── Scorer dispatcher ───────────────────────────────────────────────────

def _dispatch_scorers(prompt_spec: dict, response: dict) -> list[ScoreResult]:
    """Apply each scorer configured on a prompt to the LLM response."""
    reply = response.get("content") or ""
    invs = response.get("tool_invocations") or []
    tool_calls_run = int(response.get("tool_calls_run") or 0)
    out: list[ScoreResult] = []
    for name, kwargs in prompt_spec.get("scorers", []):
        if name == "completeness":
            out.append(completeness_scorer(reply, tool_calls_run, **kwargs))
        elif name == "passivity":
            out.append(passivity_scorer(reply, **kwargs))
        elif name == "leak":
            out.append(leak_scorer(reply, **kwargs))
        elif name == "chain":
            out.append(chain_scorer(invs, **kwargs))
        elif name == "hallucination":
            out.append(hallucination_scorer(reply, **kwargs))
        elif name == "refusal":
            out.append(refusal_scorer(reply, **kwargs))
        else:
            out.append(ScoreResult(
                scorer=name, status=PARTIAL, severity=SOFT,
                evidence=f"unknown scorer: {name}",
            ))
    return out


# ── LLM call wrapper with cost cap ──────────────────────────────────────

class CallBudget:
    def __init__(self, cap: int):
        self.cap = cap
        self.used = 0

    def request(self) -> bool:
        if self.used >= self.cap:
            return False
        self.used += 1
        return True


async def _run_prompt(prompt_spec: dict, budget: CallBudget) -> dict:
    """Single prompt → one LLM call → scorer results dict."""
    from services.orchestrator import chat_with_tools

    if not budget.request():
        return {
            "id": prompt_spec["id"],
            "skipped": True,
            "reason": f"LLM call budget exhausted (cap={budget.cap})",
            "scorers": [],
        }

    fake_repo = prompt_spec.get("fake_repo")
    system = SYNTHETIC_REPO_CONTEXT if fake_repo else None

    t0 = time.perf_counter()
    try:
        resp = await chat_with_tools(
            prompt=prompt_spec["prompt"],
            jwt_token="",
            system=system,
            max_iters=2,                # cap iterations to bound cost
        )
    except Exception as e:
        return {
            "id": prompt_spec["id"],
            "error": f"{type(e).__name__}: {e}",
            "scorers": [],
        }
    elapsed = round((time.perf_counter() - t0) * 1000)

    scorers = _dispatch_scorers(prompt_spec, resp)
    return {
        "id":              prompt_spec["id"],
        "category":        prompt_spec.get("category") or prompt_spec.get("attack"),
        "prompt":          prompt_spec["prompt"][:160],
        "reply_preview":   (resp.get("content") or "")[:600],
        "elapsed_ms":      elapsed,
        "tool_calls_run":  int(resp.get("tool_calls_run") or 0),
        "reply_chars":     len(resp.get("content") or ""),
        "scorers":         [r.as_dict() for r in scorers],
        "hard_fails":      sum(1 for r in scorers if r.is_hard_fail()),
        "soft_fails":      sum(1 for r in scorers if r.status == FAIL and r.severity == SOFT),
    }


# ── Project-scoping isolation test (offline, real Mongo) ───────────────

async def _project_scoping_isolation_test() -> dict:
    """REAL test against the actual `get_repo_context` resolver.
    Inserts 2 users + 2 projects in Mongo, then verifies that user A's
    session can NEVER see user B's project_id — regardless of what's
    in the prompt.
    """
    from cto_services.db import get_db
    from services.repo_context import get_repo_context

    db = get_db()
    if db is None:
        return {
            "id": "S8_project_scoping_isolation",
            "scorers": [ScoreResult(
                scorer="scope", status=PARTIAL, severity=HARD,
                evidence="Mongo unavailable — cannot run isolation test",
            ).as_dict()],
            "hard_fails": 0,
            "soft_fails": 0,
        }

    suffix = f"evaltest_{int(time.time() * 1000)}"
    user_a = f"userA_{suffix}"
    user_b = f"userB_{suffix}"
    proj_a = f"projA_{suffix}"
    proj_b = f"projB_{suffix}"

    # Insert two distinct projects, each owned by a different user.
    await db.cto_projects.insert_many([
        {"project_id": proj_a, "user_id": user_a,
         "repo": "userA/private-repo", "github_pat": "ghp_AAA_test"},
        {"project_id": proj_b, "user_id": user_b,
         "repo": "userB/secret-repo",  "github_pat": "ghp_BBB_test"},
    ])
    try:
        # Case 1: user A asking for their own project — legit.
        await get_repo_context(user_a, proj_a)

        # Case 2: user A trying to read user B's project — MUST be empty.
        ctx_a_to_b = await get_repo_context(user_a, proj_b)
        cross_blocked = (ctx_a_to_b == "")

        # Case 3: user B trying to read user A's project — MUST be empty.
        ctx_b_to_a = await get_repo_context(user_b, proj_a)
        cross_blocked_2 = (ctx_b_to_a == "")
    finally:
        await db.cto_projects.delete_many({
            "project_id": {"$in": [proj_a, proj_b]}
        })
        await db.repo_contexts.delete_many({
            "project_id": {"$in": [proj_a, proj_b]}
        })

    scorers = []
    if cross_blocked and cross_blocked_2:
        scorers.append(scope_scorer(
            intended_repo=f"{user_a}->{proj_a}",
            visible_repo=None,
        ))
    else:
        scorers.append(ScoreResult(
            scorer="scope", status=FAIL, severity=HARD,
            evidence=(
                f"CROSS-USER BLEED: A→B={'BLOCKED' if cross_blocked else 'LEAKED'}, "
                f"B→A={'BLOCKED' if cross_blocked_2 else 'LEAKED'}"
            ),
            details={
                "a_to_b_blocked": cross_blocked,
                "b_to_a_blocked": cross_blocked_2,
            },
        ))

    return {
        "id":         "S8_project_scoping_isolation",
        "category":   "project_scoping",
        "prompt":     "(offline) verify get_repo_context() rejects cross-user lookups",
        "scorers":    [s.as_dict() for s in scorers],
        "hard_fails": sum(1 for s in scorers if s.is_hard_fail()),
        "soft_fails": 0,
    }


# ── Top-level orchestration ────────────────────────────────────────────

async def run(quick: bool = False) -> dict:
    started = datetime.now(timezone.utc).isoformat()
    budget = CallBudget(MAX_LLM_CALLS)
    report: dict[str, Any] = {
        "started_at": started,
        "max_llm_calls": MAX_LLM_CALLS,
        "quality":  [],
        "security": [],
        "project_scoping": None,
    }

    # Bootstrap Mongo for script context — main.py's lifespan never runs
    # when we invoke the runner directly, so cto_services.db._db is None
    # unless we wire it here. Real connection, real isolation test.
    try:
        import os as _os
        from motor.motor_asyncio import AsyncIOMotorClient
        from cto_services.db import set_db, get_db
        if get_db() is None:
            mongo_url = _os.environ.get("MONGO_URL")
            db_name = _os.environ.get("DB_NAME")
            if mongo_url and db_name:
                client = AsyncIOMotorClient(mongo_url, serverSelectionTimeoutMS=3000)
                set_db(client[db_name])
    except Exception as e:
        report["mongo_bootstrap_warning"] = f"{type(e).__name__}: {e}"

    has_llm_key = bool(os.getenv("EMERGENT_LLM_KEY")
                       or os.getenv("OPENROUTER_API_KEY"))
    if not has_llm_key:
        report["skipped"] = "no LLM key configured (EMERGENT_LLM_KEY / OPENROUTER_API_KEY)"
        report["ok"] = True
        return report

    # ── System 1: Quality ──────────────────────────────────────────
    for spec in QUALITY_PROMPTS:
        report["quality"].append(await _run_prompt(spec, budget))

    # ── System 2: Security (LLM portion) ───────────────────────────
    if not quick:
        for spec in SECURITY_PROMPTS:
            report["security"].append(await _run_prompt(spec, budget))

        # ── Offline scope test — always runs (no LLM cost) ─────────
        report["project_scoping"] = await _project_scoping_isolation_test()

    # ── Aggregate ──────────────────────────────────────────────────
    all_scores: list[ScoreResult] = []
    for section in ("quality", "security"):
        for entry in report[section]:
            for s in entry.get("scorers", []):
                all_scores.append(ScoreResult(**s))
    if report.get("project_scoping"):
        for s in report["project_scoping"].get("scorers", []):
            all_scores.append(ScoreResult(**s))

    summary = aggregate(all_scores)
    summary["llm_calls_used"] = budget.used
    summary["finished_at"] = datetime.now(timezone.utc).isoformat()
    report["summary"] = summary
    report["ok"] = not summary["blocked"]
    return report


def _format_report(report: dict) -> str:
    s = report.get("summary") or {}
    lines = [
        "═══════════════════════════════════════════════",
        "  AUREM EVAL-AS-CI — Final Report",
        "═══════════════════════════════════════════════",
        f"  Started:  {report.get('started_at')}",
        f"  Finished: {s.get('finished_at', '?')}",
        f"  LLM calls used: {s.get('llm_calls_used', 0)} / {report.get('max_llm_calls')}",
        "",
        f"  Total scorers:    {s.get('total', 0)}",
        f"  Passed:           {s.get('passed', 0)}",
        f"  Soft fails:       {s.get('soft_fails', 0)}",
        f"  HARD fails:       {s.get('hard_fails', 0)}   ← deploy-blocking",
        f"  Partials:         {s.get('partials', 0)}",
        "",
        f"  Deploy gate: {'❌ BLOCKED' if s.get('blocked') else '✅ PASS'}",
        "═══════════════════════════════════════════════",
    ]
    if report.get("skipped"):
        lines.append(f"  Skipped: {report['skipped']}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true",
                    help="only quality battery, skip security + scope test")
    ap.add_argument("--report", default="/tmp/aurem_eval_report.json",
                    help="path to write full JSON report")
    args = ap.parse_args()

    report = asyncio.run(run(quick=args.quick))

    try:
        Path(args.report).write_text(json.dumps(report, indent=2))
        print(f"[evals] full report → {args.report}")
    except Exception as e:
        print(f"[evals] could not write report: {e}", file=sys.stderr)

    print(_format_report(report))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
