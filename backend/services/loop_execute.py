"""
loop_execute.py — Iter 212m-112 (parallel + bounded per-file timeouts)

Real EXECUTE phase implementation for the LoopEngine.

Iter 212m-109 originally generated files SERIALLY which meant 6 files ×
~25s each easily blew the 120s phase budget the user reported as:

    Step 2 / 5 — Execute   Executing — 6 file(s) planned…
    Failed   Phase execute exceeded 120s budget.

Iter 212m-112 changes:
  • Files generated CONCURRENTLY (asyncio.Semaphore(3)) so 6 files run
    in 2 batches of 3 — total ~2× LLM latency instead of 6×.
  • PER-FILE timeout (default 60 s) so one slow file can't drag the
    whole batch past the engine's phase budget.
  • PARTIAL success — if some files succeed and others time out, we
    still return the successful set; the engine logs the misses and
    can self-heal them in VERIFY.

Flow per file:
  1. Fetch CURRENT content from GitHub via github_api_writer.fetch_file
  2. Ask the LLM to rewrite it per the user's task + approved plan
  3. Strip code fences from the LLM response
  4. Yield {path, content}

Returns the engine-friendly [{path, content}] list (possibly empty if
EVERY file failed — the engine handles that with `_fail("execute", ...)`).
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger("aurem-dev.loop_execute")


# ── Configurable runtime knobs (env-overridable for ops) ───────────────
def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (ValueError, TypeError):
        return default


# Per-file LLM+fetch timeout. With LLM_HTTP_TIMEOUT_S=25 + GitHub fetch
# ~3 s, 60 s leaves plenty of headroom for one retry.
PER_FILE_TIMEOUT_S = _env_int("LOOP_EXECUTE_PER_FILE_TIMEOUT_S", 60)
# Concurrent LLM calls. Conservative default — OpenRouter accepts more
# but we don't want to fan-out so wide that a single repo burst hits
# their rate limits.
MAX_PARALLEL_GENS  = _env_int("LOOP_EXECUTE_PARALLELISM",        3)


def _strip_fences(text: str) -> str:
    """Remove leading/trailing ``` fences the LLM may wrap around code."""
    s = (text or "").strip()
    if s.startswith("```"):
        nl = s.find("\n")
        if nl != -1:
            s = s[nl + 1:]
        if s.endswith("```"):
            s = s[:-3].rstrip()
    return s


async def _generate_one(
    *,
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    idx: int,
    total: int,
    path: str,
    plan: dict,
    user_message: str,
    owner: str,
    repo: str,
    branch: str,
    token: str,
    user_id: Optional[str],
) -> Optional[dict]:
    """Generate a single file. Returns {path, content} on success or
    None on any failure (timeout / empty LLM response / exception).
    Guaranteed to respect PER_FILE_TIMEOUT_S — one slow file can't
    drag the whole batch past the engine's phase budget."""
    from services.github_api_writer import fetch_file
    from services.llm import call_llm_with_meta

    async with sem:
        try:
            return await asyncio.wait_for(
                _generate_one_inner(
                    client=client, idx=idx, total=total, path=path,
                    plan=plan, user_message=user_message,
                    owner=owner, repo=repo, branch=branch, token=token,
                    user_id=user_id,
                    fetch_file=fetch_file,
                    call_llm_with_meta=call_llm_with_meta,
                ),
                timeout=PER_FILE_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "[execute] %d/%d TIMEOUT (>%ds) for %s — skipping",
                idx, total, PER_FILE_TIMEOUT_S, path,
            )
            return None
        except Exception as e:                          # noqa: BLE001
            logger.exception("[execute] %d/%d FAILED for %s: %r", idx, total, path, e)
            return None


async def _generate_one_inner(
    *,
    client, idx, total, path, plan, user_message,
    owner, repo, branch, token, user_id,
    fetch_file, call_llm_with_meta,
):
    logger.info("[execute] %d/%d fetching %s", idx, total, path)
    current = ""
    try:
        current = await fetch_file(client, owner, repo, path, token) or ""
    except Exception as e:                              # noqa: BLE001
        logger.warning("[execute] fetch_file failed for %s: %r (treating as new file)", path, e)
        current = ""

    sys_msg = (
        "You are ORA, an AI engineer. The user gave a task and an "
        "approved plan. Rewrite the entire file content to satisfy "
        "the task. Return ONLY the complete final file content. "
        "Do not add commentary. Do not wrap in code fences. "
        "Preserve any existing functionality that the task does NOT "
        "explicitly change. If the file is empty (new file), produce "
        "a sensible initial version."
    )
    plan_bullets = "\n".join(
        f"- {b}" for b in (plan.get("bullets") or [])[:12]
    )
    user_msg = (
        f"USER REQUEST:\n{user_message}\n\n"
        f"APPROVED PLAN:\n{plan.get('title', '')}\n{plan_bullets}\n\n"
        f"FILE PATH: {path}\n\n"
        f"--- CURRENT CONTENT ({len(current)} bytes) ---\n"
        f"{current}\n"
        f"--- END CURRENT CONTENT ---\n\n"
        "Return the complete new content for this file. No fences. "
        "No commentary. Just the file content as it should be written."
    )
    logger.info("[execute] %d/%d calling LLM for %s", idx, total, path)
    meta = await call_llm_with_meta(
        system=sys_msg, user=user_msg,
        max_tokens=4000, mode="code",
        user_id=user_id, review_mode="pro",
    )
    content = _strip_fences((meta or {}).get("content", ""))
    if not content:
        logger.warning("[execute] LLM returned empty content for %s", path)
        return None
    logger.info("[execute] %d/%d generated %d bytes for %s", idx, total, len(content), path)
    return {"path": path, "content": content}


async def generate_files(
    *,
    plan: dict,
    user_message: str,
    owner: str,
    repo: str,
    branch: str,
    token: str,
    user_id: Optional[str] = None,
) -> list[dict]:
    """Generate concrete file content for every path in the plan,
    CONCURRENTLY with a semaphore-bounded fan-out and a per-file
    timeout so a single slow file can't blow the engine's budget.

    Returns a list of `{path, content}` for the engine to feed into
    LoopEngine.submit_files() and then commit via SHIP. Partial
    success is acceptable — the engine reports the misses to VERIFY
    which can self-heal them.
    """
    paths: list[str] = list((plan or {}).get("files_to_change") or [])
    if not paths:
        logger.warning("[execute] plan has no files_to_change — nothing to generate")
        return []

    logger.info(
        "[execute] generating %d file(s) for %s/%s@%s "
        "(parallelism=%d, per-file timeout=%ds)",
        len(paths), owner, repo, branch,
        MAX_PARALLEL_GENS, PER_FILE_TIMEOUT_S,
    )
    sem = asyncio.Semaphore(MAX_PARALLEL_GENS)

    # Single httpx client shared across all parallel fetch_file calls.
    async with httpx.AsyncClient(timeout=20.0) as client:
        tasks = [
            _generate_one(
                client=client, sem=sem,
                idx=i, total=len(paths), path=p,
                plan=plan, user_message=user_message,
                owner=owner, repo=repo, branch=branch, token=token,
                user_id=user_id,
            )
            for i, p in enumerate(paths, start=1)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=False)

    out = [r for r in results if r]
    logger.info("[execute] generated %d/%d files successfully", len(out), len(paths))
    return out
