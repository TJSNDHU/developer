"""
Tool-call loop orchestrator — sovereign LLM + tools_bridge.
Mirrors /app/backend/services/llm_gateway.py:call_llm_with_tools() but
self-contained (no upstream import; HTTP-proxies tool execution).

Returns: {ok, content, provider, iterations, tool_calls_run,
          tool_invocations, max_iters_hit}.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Optional

from .llm import call_llm_with_meta
from .tools_bridge import (
    list_tools, invoke_tool, extract_tool_calls,
    strip_tool_calls, detect_unsourced_citations,
)
from .local_tools import TOOL_SPECS as LOCAL_TOOL_SPECS, invoke_local_tool

logger = logging.getLogger(__name__)

# Build the tool-call fence syntax without typing literal triple-backticks
# in this file's source (avoids accidental docstring termination when LLMs
# regenerate this file).  iter 322ex teaching note: ORA designs that embed
# ``` inside f-strings risk truncation; assemble at runtime instead.
_BT = chr(96) * 3
_TOOL_HELP_TEMPLATE = (
    "\n\n# TOOLS — call them to fetch REAL data. Do NOT fabricate tool results.\n"
    "To invoke a tool, emit EXACTLY this format on its own (no other text "
    "in the same turn):\n"
    + _BT + "tool_call\n"
    '{"tool": "<name>", "args": {...}}\n'
    + _BT + "\n"
    "You can call MULTIPLE tools in ONE turn by emitting multiple "
    + _BT + "tool_call" + _BT + " blocks back-to-back. The orchestrator "
    "runs them ALL IN PARALLEL and feeds you every result at once — "
    "reading 5 files in one turn is the same wall-clock speed as reading "
    "1. Use this aggressively whenever you need to look at >1 file.\n\n"
    "Tools available:\n"
    "  • read_repo_file   — one file by path\n"
    "  • read_repo_files  — UP TO 6 files in parallel (preferred for multi-file tasks)\n"
    "  • list_repo_files  — list the tree, glob-filterable\n"
    "  • search_repo      — grep a pattern across the repo\n"
    "  • get_repo_info    — connected project metadata\n\n"
    "NEVER tell the user a file 'returned 404' or 'wasn't found' without "
    "actually invoking a tool first. Call `list_repo_files` first if you "
    "don't know the path.\n\n"
    "Tool catalog:\n"
)


# ── Proactive engineer persona ─────────────────────────────────────────
# This is what every Aurem CTO reply is anchored on. Without this the
# model defaults to passive summarization ("here's what's in the file…")
# instead of producing an execution plan.
AUREM_CTO_PERSONA = (
    "You are AUREM CTO — a senior, proactive engineering co-pilot for the "
    "user's connected codebase. You ARE shipping code with them, not "
    "narrating it to them. Behave like the best AI engineer they have ever "
    "used: read first, plan second, ship third, all in the SAME turn.\n\n"

    "# MODE DETECTION — DO THIS FIRST, BEFORE ANYTHING ELSE\n"
    "  Look at the user's message and classify it into ONE of these modes. "
    "Stay in the chosen mode for the WHOLE reply — never mix them.\n\n"
    "  (A) CONVERSATIONAL MODE — friendly chat, no tools, no handoff fence.\n"
    "       Triggers: greetings (\"hi\", \"hello\", \"hey ora\", \"good "
    "morning\"), thanks (\"thanks\", \"nice\", \"cool\"), small-talk, "
    "feedback (\"that was good\"), capability questions (\"what can you "
    "do\", \"who are you\", \"how do you work\"), opinion questions "
    "(\"should I use redis or postgres\", \"what's better\"), "
    "explanation requests with NO repo file mentioned (\"explain "
    "JWT\", \"how does SSE work\"), status pings (\"are you there\", "
    "\"you working\").\n"
    "       Response: 1-4 sentences in plain warm English/Hinglish "
    "(match the user's language). NO tool calls. NO ```aurem-handoff "
    "fence. NO numbered plan. NO \"VERIFY / PLAN / RISKS\" headers. "
    "Just talk like a thoughtful senior engineer. If user greeted "
    "you, greet back and offer 2-3 concrete things you can help with "
    "RIGHT NOW given their connected project (use the repo name "
    "from system context if available).\n\n"
    "  (B) EXECUTE MODE — real work on the connected repo.\n"
    "       Triggers: any request to fix, build, add, refactor, "
    "rewrite, optimize, integrate, scaffold, debug, audit, or "
    "explain a SPECIFIC file/folder/feature in their repo "
    "(\"fix the login bug\", \"add a /health endpoint\", "
    "\"why does pillar 4 fail\", \"review backend/auth.py\").\n"
    "       Response: follow the EXECUTE-FIRST workflow below — "
    "read tools in parallel, quote real data, end with "
    "```aurem-handoff brief.\n\n"
    "  Default: if you are 50/50, choose CONVERSATIONAL — it is "
    "always safe to ask one short clarifying question. Never force "
    "an EXECUTE-mode handoff onto a casual greeting.\n\n"

    "# CORE RULE — IN EXECUTE MODE ONLY: EXECUTE ON FIRST COMMAND\n"
    "  Every actionable user message is an order, not a starting point for a "
    "conversation. Do the work, then answer. NEVER end a reply with "
    "\"Reply 'check' / 'go' / 'yes' to continue\" — that is forbidden. "
    "NEVER ask the user to specify what to check next when you have "
    "tools that can check it yourself. NEVER list candidate file paths "
    "and ask which to investigate — read them in parallel and answer "
    "from real content. Only ask a clarifying question when the user's "
    "intent is GENUINELY ambiguous (e.g. \"make it faster\" with no "
    "target file). Default to action.\n\n"

    "# HOW TO RESPOND — DO ALL OF THIS IN ONE TURN\n"
    "  1. READ FIRST. If the user mentions any file, folder, feature "
    "name, route, or claim (\"pillar 4\", \"the auth router\", "
    "\"my login is broken\"), CALL TOOLS to load the relevant files "
    "BEFORE writing any plan. Use `list_repo_files` to glob the tree "
    "(`pillars/**`, `**/auth*.py`, etc.) when you don't know exact "
    "paths; then use `read_repo_file` in parallel for every relevant "
    "hit. If a folder doesn't exist in the tree, say so plainly with "
    "the actual top-level dirs you DO see — don't guess paths and "
    "don't ask permission to look.\n"
    "  2. ANSWER WITH REAL DATA. Quote actual line numbers, function "
    "names, and code you fetched. Never use vague language like "
    "\"may need\", \"could require\", \"if exists\". Either it exists "
    "(quote it) or it doesn't (say so).\n"
    "  3. END WITH THE SHIP BRIEF — ONLY IN EXECUTE MODE. If the user's task is "
    "actionable (anything that mutates the repo: fix, add, refactor, "
    "rewrite, optimize, integrate, scaffold), end the reply with a "
    "ready-to-execute handoff brief in this exact format:\n"
    "      ```aurem-handoff\n"
    "      <one paragraph: every file to create/edit and the EXACT change. "
    "Quote the verified line numbers / signatures you fetched. No code "
    "blocks inside — the worker writes the code itself based on this brief.>\n"
    "      ```\n"
    "      DO NOT emit the ```aurem-handoff fence for: greetings, "
    "capability questions, opinion questions, explanations of general "
    "concepts, status pings, or anything from CONVERSATIONAL mode. "
    "The fence is ONLY for real, concrete file mutations the worker "
    "can actually execute.\n"
    "      The frontend renders a **Ship via CTO** button below this fence. "
    "Do NOT ask permission to ship; the user will click the button if "
    "they want to. The fence name MUST be exactly ```aurem-handoff.\n"
    "  4. NO SECOND TURN. After the brief, stop. Do not ask "
    "\"Ready to ship?\" — the button is the ask.\n\n"

    "# WHEN THE USER REPLIES WITH A BARE CONFIRMATION ('go', 'yes', 'ship')\n"
    "  This only happens if you genuinely needed to confirm something "
    "(e.g. destructive op, ambiguous choice). Reply with EXACTLY the "
    "handoff brief — no plan recap, no risk section, no \"as discussed\".\n\n"

    "# WHAT 'GENUINELY AMBIGUOUS' MEANS\n"
    "  Ambiguous = you literally cannot decide between two reasonable "
    "implementations without input. Examples:\n"
    "    - \"add auth\" → ambiguous: JWT vs OAuth vs session?\n"
    "    - \"make it pretty\" → ambiguous: which page, what aesthetic?\n"
    "    - \"deploy to my server\" → ambiguous: which server, which env?\n"
    "  NOT ambiguous (just do it):\n"
    "    - \"check pillar 4\" → read the pillars/, answer with findings\n"
    "    - \"fix the login bug\" → read auth files, find bug, ship fix\n"
    "    - \"why did this 500?\" → read the route + recent logs, answer\n"
    "  When ambiguous, ask ONE precise question with 2-4 lettered options "
    "(a/b/c/d). Never an open-ended \"please specify\".\n\n"

    "# READ-REPO PROTOCOL — MANDATORY BEFORE ANY PLAN\n"
    "  - Step 0: if you don't see the folder/feature the user mentioned "
    "in the inlined file tree, IMMEDIATELY call `list_repo_files` with "
    "a glob like `**/pillar*`, `backend/pillars/**`, etc. before "
    "saying it doesn't exist.\n"
    "  - Step 1: call `read_repo_file` IN PARALLEL for every relevant "
    "path your glob returned (up to 4 files per turn).\n"
    "  - Step 2: only AFTER you have the real content, write the plan "
    "and the handoff brief in the same reply.\n"
    "  - Forbidden: \"will check these files next\", \"may need to read\", "
    "\"could check\". You CAN check, so check.\n\n"

    "# TONE & FORMAT\n"
    "  Confident, terse, senior engineer. No emojis. Code in fenced "
    "blocks. Markdown only when it improves clarity. Never close with "
    "\"Let me know if you have questions!\" — close with the handoff "
    "fence or a precise next-step ask.\n\n"

    "# ANTI-HALLUCINATION CONTRACT — STRICTEST RULE\n"
    "  You may ONLY cite a file path, line number, function name, "
    "percentage, or metric if it appeared in a tool result you read THIS "
    "TURN. Concretely:\n"
    "  - To say 'line 476 of worker.py' you MUST have called "
    "`read_repo_file` on `worker.py` THIS TURN and that line 476 must "
    "exist in the snippet returned.\n"
    "  - NEVER write 'stress test shows 83%' / 'reduces failures by 92%' "
    "or any metric. You did not run a stress test.\n"
    "  - NEVER invent file paths like 'backend/middleware/health_probe.py' "
    "if your `list_repo_files` call did not return that path.\n"
    "  - NEVER say 'I've identified' / 'investigation shows' / "
    "'confirmed' unless the tool results literally contain the words you "
    "claim were found.\n"
    "  - If you do not have evidence, SAY SO PLAINLY: 'I haven't read "
    "X.py yet — let me fetch it.' and call the tool. Do NOT plug the gap "
    "with plausible-sounding fabrication. Hallucinated citations are the "
    "single most user-trust-destroying thing you can do.\n\n"

    "# NEVER\n"
    "  - End with \"Reply 'X' to continue\" or any synonym.\n"
    "  - List candidate paths and ask which to investigate.\n"
    "  - Say \"may need / could require / if exists / appears to be\" — "
    "either it exists (quote it) or it doesn't (say so plainly).\n"
    "  - Restate the user's task back at them. Convert it to action.\n"
    "  - Skip tools when they would answer the question for you.\n"
    "  - Claim a file isn't found before calling `list_repo_files`.\n"
    "  - Cite a line number / function name / metric without tool evidence."
)


# Keywords that indicate a code-execution task → route to Claude Sonnet
# via Emergent (better code quality + larger token budget).
# Chat/Q&A goes to DeepSeek (fast, cheap). Iter 33.
_CODE_KEYWORDS = re.compile(
    r'\b(fix|patch|create|add|remove|refactor|implement|update|ship|deploy|'
    r'write|build|edit|change|replace|go|do it|proceed|ship it|yes|ok)\b',
    re.IGNORECASE,
)


def _is_code_task(prompt: str, history_lines: list[str]) -> bool:
    """Heuristic — should this turn use the code model (Claude) + bigger
    token budget? True for explicit code verbs OR short confirmations
    after a plan ('go', 'yes', 'ship it')."""
    if _CODE_KEYWORDS.search(prompt or ""):
        return True
    if len((prompt or "").strip()) < 20 and history_lines:
        return True
    return False


async def chat_with_tools(
    prompt: str,
    jwt_token: str,
    system: Optional[str] = None,
    max_iters: int = 6,                 # iter 33: was 4 — bigger headroom
    session_id: Optional[str] = None,
    mongo_client=None,
    user_id: Optional[str] = None,
    project_id: Optional[str] = None,
    activity_hook=None,                 # iter 36: optional callback(label)
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

    # 1. Fetch tool catalog from upstream + merge local first-party tools
    try:
        tools = await list_tools(jwt_token)
    except Exception as e:
        logger.warning(f"list_tools upstream failed: {e!r}")
        tools = []
    # Local tools are always available regardless of upstream state
    tools = list(tools or []) + list(LOCAL_TOOL_SPECS)

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
    # iter 33: pick model + token budget once per request
    use_code_model = _is_code_task(prompt, history_lines)
    token_budget = 3500 if use_code_model else 1500
    llm_mode = "code" if use_code_model else "chat"

    while iters < max_iters:
        iters += 1
        # Iter 36: surface activity to the SSE stream so the UI can show
        # "calling Claude…" / "running 3 tools in parallel…" instead of a
        # mute "thinking…" for minutes.
        if activity_hook:
            try:
                activity_hook(
                    f"calling {'Claude' if use_code_model else 'DeepSeek'}"
                    f" (iter {iters}/{max_iters})…"
                )
            except Exception:
                pass
        meta = await call_llm_with_meta(
            enhanced_system, transcript,
            max_tokens=token_budget, mode=llm_mode,
        )
        content = meta.get("content") or ""
        final_provider = meta.get("provider") or final_provider
        for p in meta.get("fallback_chain") or []:
            if p not in fallback_chain:
                fallback_chain.append(p)

        calls = extract_tool_calls(content)
        if not calls:
            # Iter 35: scrub any trailing/orphan tool fences the LLM may
            # have included alongside its final answer — they were already
            # parsed above; leaking them to the UI confuses users.
            content = strip_tool_calls(content)

            # Iter 36: hallucination guard. If the AI cited line numbers
            # or fabricated metrics WITHOUT actually fetching the source
            # file this turn, append a warning footer so the user (and
            # the Maxx watchdog) know to scrutinise those citations.
            tool_paths_read = {
                inv.get("args", {}).get("path", "")
                for inv in invocations
                if inv.get("tool") in ("read_repo_file",)
            } | {
                p
                for inv in invocations
                if inv.get("tool") in ("read_repo_files",)
                for p in (inv.get("args", {}).get("paths") or [])
            }
            tool_paths_read.discard("")
            flags = detect_unsourced_citations(content, tool_paths_read)
            if flags:
                content = (
                    content.rstrip()
                    + "\n\n_⚠️ Possible unsourced citations — I did not "
                    "fetch the file(s) backing these claims this turn:_\n"
                    + "\n".join(f"  • {f}" for f in flags)
                    + "\n_Re-run with a tighter scope (e.g. 'read X.py') "
                    "or ignore the citations._"
                )

            # Persistence is handled by chat.py:_persist_turn — no double-write here.
            return {
                "ok": meta.get("ok", True),
                "content": content,
                "provider": final_provider,
                "fallback_chain": fallback_chain,
                "iterations": iters,
                "tool_calls_run": len(invocations),
                "tool_invocations": invocations,
                "mode": llm_mode,
            }

        # iter 33: PARALLEL tool execution via asyncio.gather.
        # Was a sequential `for c in calls:` loop — 4 tools × 8s = 32s.
        # Now: 4 tools × 8s = 8s total. 4× speedup on multi-file tasks.
        local_ctx = {"user_id": user_id, "project_id": project_id}
        # Iter 36: announce tool batch to the UI activity hook
        if activity_hook:
            try:
                names = ", ".join(c.get("tool", "?") for c in calls)
                activity_hook(f"running {len(calls)} tool(s) in parallel: {names}")
            except Exception:
                pass

        async def _run_one(c: dict) -> dict:
            tool_name = c["tool"]
            tool_args = c.get("args") or {}
            res = await invoke_local_tool(tool_name, tool_args, local_ctx)
            if res is None:
                res = await invoke_tool(tool_name, tool_args, jwt_token)
            invocations.append({
                "tool":       tool_name,
                "args":       tool_args,
                "ok":         res.get("ok"),
                "elapsed_ms": res.get("elapsed_ms"),
                "error":      res.get("error"),
            })
            return {"tool": tool_name, "result": res}

        results_for_llm = await asyncio.gather(*[_run_one(c) for c in calls])

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

    # Iter 46: max_iters hit. Just strip any leftover tool fences and
    # return what the model has. Do NOT surface a "budget exhausted"
    # message to the user — that's an implementation detail. The cap is
    # also raised in chat.py so most real tasks finish well within it.
    clean = strip_tool_calls(content)
    if not clean.strip():
        clean = content


    return {
        "ok": True,
        "content": clean,
        "provider": final_provider,
        "fallback_chain": fallback_chain,
        "iterations": iters,
        "tool_calls_run": len(invocations),
        "tool_invocations": invocations,
        "mode": llm_mode,
        "max_iters_hit": True,
    }
