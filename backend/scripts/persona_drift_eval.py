"""
scripts/persona_drift_eval.py — Iter 124e

CI guard rail. Runs on every deploy to catch ORA persona drift before
users do.

What it checks:
  1. Sends a synthetic "how many routers in my backend" prompt to the
     orchestrator with a fake repo-connected system context.
  2. Asserts the reply ESCALATES correctly — either emits tool_call
     blocks (read-first behaviour) OR a numbered list of >= 10 items
     (the model already had the file tree and answered completely).
  3. Asserts the reply contains NONE of the forbidden permission-asking
     openers (Would you like / Shall I / Want me to / Should I).

Exit code 0 = pass. Exit code 1 = drift detected — block the deploy.

Skip behaviour:
  • If neither EMERGENT_LLM_KEY nor OPENROUTER_API_KEY is set, exits 0
    with a "skipped" log line. Local devs without keys don't get
    spurious red CI.
  • Set RUN_PERSONA_EVAL=0 to disable entirely.
"""
from __future__ import annotations

import asyncio
import os
import re
import sys

# Load backend/.env so the script works when run from CI / deploy hooks
# without an explicit env-var pre-load step.
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
except Exception:
    pass


# Synthetic repo-connected context — looks like a real GitHub fetch.
FAKE_REPO_CONTEXT = (
    "CONNECTED PROJECT: aurem-team/aurem-backend (branch: main)\n"
    "Top-level tree (truncated):\n"
    "  backend/routers/  (14 files)\n"
    "  backend/services/ (45 files)\n"
    "  frontend/src/pages/ (23 files)\n"
)

PROMPT = "how many routers do I have in my backend?"

FORBIDDEN = (
    r"would you like",
    r"shall i\b",
    r"want me to",
    r"should i\b",
    r"do you want me to",
)


async def _run_once() -> tuple[bool, str]:
    # Lazy import so the script can be run even when the venv has issues
    # with the heavier orchestrator deps.
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from services.orchestrator import chat_with_tools

    res = await chat_with_tools(
        prompt=PROMPT,
        jwt_token="",                    # tools won't be called — fake context
        system=FAKE_REPO_CONTEXT,
        max_iters=1,                     # just need the first turn
    )
    return bool(res.get("ok")), (res.get("content") or "")


def _verdict(reply: str, tool_calls_made: int) -> tuple[bool, list[str]]:
    """Return (passed, failures)."""
    failures: list[str] = []
    low = reply.lower()
    for pat in FORBIDDEN:
        if re.search(pat, low):
            failures.append(f"forbidden phrase matched: /{pat}/")
    # If the model went straight to tools, that's the read-first path — pass.
    if tool_calls_made >= 1:
        return (not failures, failures)
    # Otherwise we want a numbered list of >= 10 lines like "1. ..." "2. ..."
    numbered = re.findall(r"^\s*\d+\.\s+\S", reply, flags=re.MULTILINE)
    if len(numbered) < 10:
        failures.append(
            f"no tool calls AND only {len(numbered)} numbered items "
            "(expected ≥10 routers from the fake tree)"
        )
    return (not failures, failures)


def main() -> int:
    if os.getenv("RUN_PERSONA_EVAL", "1") == "0":
        print("[persona-eval] disabled via RUN_PERSONA_EVAL=0")
        return 0
    if not (os.getenv("EMERGENT_LLM_KEY") or os.getenv("OPENROUTER_API_KEY")):
        print("[persona-eval] skipped — no LLM key configured")
        return 0

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")
    from services.orchestrator import chat_with_tools  # noqa: F401

    async def _go():
        # Call once and inspect the raw result for both reply text and
        # tool_call count.
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from services.orchestrator import chat_with_tools as _c
        res = await _c(prompt=PROMPT, jwt_token="",
                       system=FAKE_REPO_CONTEXT, max_iters=1)
        return res

    res = asyncio.run(_go())
    reply = res.get("content") or ""
    tool_calls = int(res.get("tool_calls_run") or 0)
    ok, failures = _verdict(reply, tool_calls)
    print(f"[persona-eval] tool_calls_run={tool_calls} "
          f"reply_chars={len(reply)} failures={len(failures)}")
    if not ok:
        for f in failures:
            print(f"  FAIL: {f}")
        print("---REPLY---")
        print(reply[:1200])
        print("---END---")
        return 1
    print("[persona-eval] OK — ORA persona behaving correctly")
    return 0


if __name__ == "__main__":
    sys.exit(main())
