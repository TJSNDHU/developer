"""
loop_execute.py — Iter 212m-109

Real EXECUTE phase implementation for the LoopEngine. Previously
`_do_execute` only emitted synthetic "Wrote {f}" events without
ever populating `submitted_files`, so SHIP found nothing to commit
and the user saw "Ship complete" with NO actual GitHub commit (the
P0 bug they hit 3 times today).

Flow:
  1. For each path in plan.files_to_change:
       a. Fetch CURRENT content from GitHub via github_api_writer.fetch_file
       b. Call the LLM with the user's original request + current content
       c. Strip code fences from the LLM response
  2. Return [{path, content}] for the engine to feed into VERIFY → SHIP.
"""
from __future__ import annotations

import logging
from typing import Optional

import httpx

logger = logging.getLogger("aurem-dev.loop_execute")


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
    """Generate concrete file content for every path in the plan.

    Returns a list of `{path, content}` ready to feed into
    LoopEngine.submit_files() and then commit via the SHIP phase.
    Logs every step verbosely so we can diagnose hangs in prod.
    """
    from services.github_api_writer import fetch_file
    from services.llm import call_llm_with_meta

    paths: list[str] = list((plan or {}).get("files_to_change") or [])
    if not paths:
        logger.warning("[execute] plan has no files_to_change — nothing to generate")
        return []

    logger.info("[execute] generating %d file(s) for %s/%s@%s", len(paths), owner, repo, branch)
    out: list[dict] = []

    async with httpx.AsyncClient(timeout=20.0) as client:
        for idx, path in enumerate(paths, start=1):
            logger.info("[execute] %d/%d fetching %s", idx, len(paths), path)
            current = ""
            try:
                current = await fetch_file(client, owner, repo, path, token) or ""
            except Exception as e:                          # noqa: BLE001
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
            try:
                logger.info("[execute] %d/%d calling LLM for %s", idx, len(paths), path)
                meta = await call_llm_with_meta(
                    system=sys_msg, user=user_msg,
                    max_tokens=4000, mode="code",
                    user_id=user_id, review_mode="pro",
                )
                content = _strip_fences((meta or {}).get("content", ""))
                if not content:
                    logger.warning("[execute] LLM returned empty content for %s — skipping", path)
                    continue
                out.append({"path": path, "content": content})
                logger.info("[execute] %d/%d generated %d bytes for %s", idx, len(paths), len(content), path)
            except Exception as e:                          # noqa: BLE001
                logger.exception("[execute] LLM call failed for %s: %r", path, e)
                # Don't abort the whole loop on one file failure — continue.
                continue

    logger.info("[execute] generated %d/%d files successfully", len(out), len(paths))
    return out
