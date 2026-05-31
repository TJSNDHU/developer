"""
Tool-call loop orchestrator — sovereign LLM + tools_bridge.
Mirrors /app/backend/services/llm_gateway.py:call_llm_with_tools() but
self-contained (no upstream import; HTTP-proxies tool execution).

Returns: {ok, content, provider, iterations, tool_calls_run,
          tool_invocations, max_iters_hit}.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from .llm import call_llm_with_meta
from .tools_bridge import list_tools, invoke_tool, extract_tool_calls

logger = logging.getLogger(__name__)

# Build the tool-call fence syntax without typing literal triple-backticks
# in this file's source (avoids accidental docstring termination when LLMs
# regenerate this file).  iter 322ex teaching note: ORA designs that embed
# ``` inside f-strings risk truncation; assemble at runtime instead.
_BT = chr(96) * 3
_TOOL_HELP_TEMPLATE = (
    "\n\n# AVAILABLE TOOLS — call them when you need REAL data.\n"
    "Emit a JSON block (fenced with " + _BT + "tool_call) like:\n"
    + _BT + "tool_call\n"
    '{"tool": "<name>", "args": {...}}\n'
    + _BT + "\n"
    "Then STOP. The orchestrator will execute it and feed you the real "
    "result, after which give your final answer.\n\n"
    "Tool catalog:\n"
)


# ── Proactive engineer persona ─────────────────────────────────────────
# This is what every Aurem CTO reply is anchored on. Without this the
# model defaults to passive summarization ("here's what's in the file…")
# instead of producing an execution plan.
AUREM_CTO_PERSONA = (
    "You are AUREM CTO — a senior, proactive engineering co-pilot for the "
    "user's connected codebase. You ARE shipping code with them, not "
    "narrating it to them.\n\n"
    "When the user gives you a task, bug claim, request, error, or paste, "
    "follow this exact 6-step flow:\n"
    "  1. VERIFY (MANDATORY before any plan): Open the repo context above "
    "and confirm the claim is REAL. If the user says \"file X has bug Y\", "
    "QUOTE the offending line(s) verbatim from the inlined file contents "
    "and state whether the bug actually exists, was already fixed, or the "
    "file isn't visible in your current context. If the file is in the "
    "file tree but NOT inlined, say exactly: \"I can see "
    "`<path>` in the tree but its contents aren't loaded — paste the "
    "function or confirm and I'll pull it.\" Never invent line numbers or "
    "code you haven't actually seen. Never accept a fix request on faith.\n"
    "  2. ANALYZE: in 1 sentence, state the real goal now that you've "
    "verified the claim. If the bug doesn't exist, say so plainly and "
    "stop — don't fabricate a fix.\n"
    "  3. PLAN: numbered execution plan listing the *exact* files, "
    "functions, and changes (e.g. \"`routers/auth.py::verify_token` line "
    "47 — flip `verify_exp=False` → `True`\"). No vague bullets.\n"
    "  4. RISKS: breaking-change risk + any new env vars, in 1-2 lines.\n"
    "  5. VERIFY-AFTER: how the change will be tested (curl line, pytest "
    "name, manual UI step).\n"
    "  6. ASK TO PROCEED: end with exactly one line: "
    "\"Ready to ship? Reply 'go' and I'll start with step 1.\" "
    "Do NOT write the final code in the same turn unless the user said "
    "\"just do it\" / \"go ahead\".\n\n"
    "NEVER:\n"
    "  - Skip VERIFY. If you have no repo context (no project connected), "
    "say so and ask the user to connect a project first.\n"
    "  - Restate or summarize the user's own task list back at them. "
    "Convert it into YOUR verified execution plan.\n"
    "  - Claim you can't access something the system prompt clearly "
    "says you have (repo files, fetched URLs, etc.). Use what's loaded.\n"
    "  - Hedge with 'this appears to be...' — commit to your read.\n"
    "  - Close with 'Let me know if you have questions!' — always end "
    "with the concrete next-step ask in step 6.\n\n"
    "Tone: confident, terse, senior engineer. No emojis. Code in fenced "
    "blocks. Markdown only when it improves clarity."
)


async def chat_with_tools(
    prompt: str,
    jwt_token: str,
    system: Optional[str] = None,
    max_iters: int = 4,
    session_id: Optional[str] = None,
    mongo_client=None,
) -> dict:
    """Run the LLM tool-call loop until final answer (no more tool calls)
    or `max_iters` cap is hit.  Every tool call goes through `tools_bridge`
    which HTTP-proxies to upstream AUREM (`/api/ora-tools/execute`).

    iter 322fk-4 — when `session_id` is supplied, the previous turns of
    this session are prepended to the transcript so AUREM remembers
    context. After answering, the new prompt + reply are persisted back
    into `chat_sessions` by the chat router (see `_persist_turn`).
    """
    # iter 322fk-4 (fix 14B): load prior conversation from `chat_sessions`
    # (where chat.py:_persist_turn writes turns). The legacy code looked
    # at `aurem_cto_sessions` and required an explicit `mongo_client` arg
    # that chat.py never passes — so history was silently always empty.
    history_lines: list[str] = []
    if session_id:
        try:
            from cto_services.db import get_db
            db = get_db()
            if db is not None:
                doc = await db.chat_sessions.find_one(
                    {"session_id": session_id},
                    {"_id": 0, "turns": 1},
                )
                for t in (doc or {}).get("turns") or []:
                    role = t.get("role", "user")
                    content = (t.get("content") or "").strip()
                    if content:
                        # Hard-cap each turn so a long earlier answer
                        # doesn't eat the whole context window.
                        if len(content) > 4000:
                            content = content[:4000] + " …[truncated]"
                        history_lines.append(f"[{role.upper()}] {content}")
                # Keep the most recent N turns to stay within context.
                history_lines = history_lines[-20:]
        except Exception as e:
            logger.warning(f"session history load failed (continuing fresh): {e!r}")

    # 1. Fetch tool catalog from upstream
    try:
        tools = await list_tools(jwt_token)
    except Exception as e:
        logger.warning(f"list_tools upstream failed: {e!r}")
        tools = []

    catalog_lines = [
        f"- {t.get('name')}: {t.get('description', '')}\n"
        f"  args: {t.get('args_spec') or t.get('args') or {}}"
        for t in (tools or [])
    ]
    catalog_text = "\n".join(catalog_lines) or "(no tools available — answer from your own knowledge)"

    # Persona is always the floor; caller-provided `system` (repo + URL
    # context) is appended after it so the model gets persona first,
    # then specific data, then tool catalog.
    extra = system or ""
    base_system = AUREM_CTO_PERSONA + (("\n\n" + extra) if extra.strip() else "")
    enhanced_system = base_system + _TOOL_HELP_TEMPLATE + catalog_text

    # iter 322fk-4: stitch session memory into the transcript.
    if history_lines:
        transcript = (
            "=== PRIOR CONVERSATION (most recent last) ===\n"
            + "\n".join(history_lines)
            + "\n=== END PRIOR CONVERSATION ===\n\n"
            + f"[USER] {prompt}"
        )
    else:
        transcript = prompt
    invocations: list[dict] = []
    final_provider = "?"
    iters = 0
    fallback_chain: list[str] = []

    while iters < max_iters:
        iters += 1
        meta = await call_llm_with_meta(
            enhanced_system, transcript, max_tokens=1500,
        )
        content = meta.get("content") or ""
        final_provider = meta.get("provider") or final_provider
        for p in meta.get("fallback_chain") or []:
            if p not in fallback_chain:
                fallback_chain.append(p)

        calls = extract_tool_calls(content)
        if not calls:
            # Persistence is handled by chat.py:_persist_turn — no double-write here.
            return {
                "ok": meta.get("ok", True),
                "content": content,
                "provider": final_provider,
                "fallback_chain": fallback_chain,
                "iterations": iters,
                "tool_calls_run": len(invocations),
                "tool_invocations": invocations,
            }

        # Execute every tool call and feed results back into the transcript
        results_for_llm: list[dict] = []
        for c in calls:
            res = await invoke_tool(c["tool"], c.get("args") or {}, jwt_token)
            invocations.append({
                "tool": c["tool"],
                "args": c.get("args") or {},
                "ok": res.get("ok"),
                "elapsed_ms": res.get("elapsed_ms"),
                "error": res.get("error"),
            })
            results_for_llm.append({"tool": c["tool"], "result": res})

        # iter 323ad — per-tool truncation (was: total 4000 chars cut
        # across ALL results → ORA half-results dekh ke wrong conclusions).
        # Each tool result gets its own 2500-char budget so 4 tool calls
        # in one iter all reach the LLM with usable signal.
        results_truncated = []
        for r in results_for_llm:
            result_str = json.dumps(r["result"], default=str)
            if len(result_str) > 2500:
                result_str = (
                    result_str[:2500]
                    + "\n... [truncated — call again with narrower args/limit]"
                )
            results_truncated.append({"tool": r["tool"], "result": result_str})

        transcript = (
            f"{transcript}\n\n=== TOOL RESULTS (iter {iters}) ===\n"
            f"{json.dumps(results_truncated, default=str)}\n"
            f"=== END TOOL RESULTS ===\n"
            f"Now give your FINAL answer using only these real results "
            f"(or call more tools if needed)."
        )

    return {
        "ok": True,
        "content": content,
        "provider": final_provider,
        "fallback_chain": fallback_chain,
        "iterations": iters,
        "tool_calls_run": len(invocations),
        "tool_invocations": invocations,
        "max_iters_hit": True,
    }
