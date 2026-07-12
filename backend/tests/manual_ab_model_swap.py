"""Iter 212m-192 · Model swap A/B test.

Runs the same two failing Ask Advisor prompts against Claude Sonnet
4.5 AND GPT 5.2 (both via EMERGENT_LLM_KEY) and reports:
  * did the model emit a tool call in the ORCHESTRATOR-approved shape
    (fenced JSON) or a broken one (like GLM-5.2's malformed XML)?
  * did the extractor parse it into a real tool call?
  * output length + wall-time (proxy for cost)

Both models get the EXACT same system prompt and user prompts —
apples-to-apples. No blind swap; the winner is the one whose
tool-call emission the parser catches without XML-fallback recovery,
and (if both pass) the cheaper one wins on price.
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")

from services.tools_bridge import extract_tool_calls
from services.orchestrator import _TOOL_HELP_TEMPLATE  # exact prompt Ask Advisor uses

SYSTEM_PROMPT = (
    "You are ORA, an AI CTO. The user has a repo connected called "
    "'TJSNDHU/Aurem' (default branch `main`). Answer their question "
    "by calling the appropriate repo-read tool. Do NOT answer from "
    "memory — the tool call is mandatory before any prose."
    + _TOOL_HELP_TEMPLATE
)

USER_PROMPTS = [
    ("readme_read",
     "Read the file README.md from this repo and tell me what the "
     "first paragraph says."),
    ("routers_list",
     "List every file in the backend/routers/ directory of this repo."),
]

CANDIDATES = [
    ("claude-sonnet-4.5", "anthropic", "claude-sonnet-4-5-20250929"),
]


async def _run_one(label: str, provider: str, model: str,
                    user_prompt: str) -> dict:
    from emergentintegrations.llm.chat import LlmChat, UserMessage
    key = os.getenv("EMERGENT_LLM_KEY")
    assert key, "EMERGENT_LLM_KEY missing"
    chat = LlmChat(
        api_key=key,
        session_id=f"ab-{label}-{model}",
        system_message=SYSTEM_PROMPT,
    ).with_model(provider, model)

    t0 = time.time()
    reply = ""
    try:
        # send_message is fine for a one-shot A/B (no streaming needed).
        raw = await chat.send_message(UserMessage(text=user_prompt))
        reply = raw.text if hasattr(raw, "text") else str(raw)
    except Exception as e:
        return {
            "label":  label,
            "prompt": label,
            "model":  model,
            "error":  repr(e)[:400],
            "ms":     int((time.time() - t0) * 1000),
        }
    ms = int((time.time() - t0) * 1000)
    calls = extract_tool_calls(reply)

    # Which shape parser caught the emission?
    fenced_hit = "```tool_call" in reply
    xml_hit    = "<tool_call" in reply
    py_hit     = ("read_repo_file(" in reply or "list_repo_files(" in reply)

    return {
        "model":       model,
        "provider":    provider,
        "prompt":      label,
        "ms":          ms,
        "reply_len":   len(reply),
        "reply_first_200": reply[:200],
        "reply_last_120":  reply[-120:],
        "tool_calls":  calls,
        "shape": {
            "fenced_json": fenced_hit,
            "xml_fence":   xml_hit,
            "python_call": py_hit,
        },
        "clean": fenced_hit and calls and not xml_hit,
    }


async def main() -> None:
    results = []
    for label, provider, model in CANDIDATES:
        for prompt_id, prompt in USER_PROMPTS:
            print(f"\n=== {label} ({model}) · {prompt_id} ===")
            r = await _run_one(prompt_id, provider, model, prompt)
            r["prompt_id"] = prompt_id
            results.append(r)
            if "error" in r:
                print(f"  ERROR: {r['error']}")
                continue
            print(f"  time: {r['ms']} ms  ·  reply_len: {r['reply_len']}")
            print(f"  shape: fenced_json={r['shape']['fenced_json']} · xml_fence={r['shape']['xml_fence']} · python_call={r['shape']['python_call']}")
            print(f"  tool_calls parsed: {r['tool_calls']}")
            print(f"  clean emission: {r['clean']}")
            print(f"  reply.first_200: {r['reply_first_200']!r}")

    # Summary
    print("\n\n══════════════════════════════════════════════════════════════")
    print("SUMMARY — model swap comparison")
    print("══════════════════════════════════════════════════════════════")
    for label, provider, model in CANDIDATES:
        model_results = [r for r in results if r.get("model") == model]
        clean_count = sum(1 for r in model_results if r.get("clean"))
        parsed_count = sum(1 for r in model_results if r.get("tool_calls"))
        avg_ms = sum(r.get("ms", 0) for r in model_results) / max(len(model_results), 1)
        errors = sum(1 for r in model_results if "error" in r)
        print(f"\n  {label} ({model})")
        print(f"    tests run:            {len(model_results)}/2")
        print(f"    clean fenced-JSON:    {clean_count}/2  ← the shape the parser is designed for")
        print(f"    tool_calls parsed:    {parsed_count}/2  (incl. XML-fallback recovery)")
        print(f"    avg latency:          {avg_ms:.0f} ms")
        print(f"    errors:               {errors}")


if __name__ == "__main__":
    asyncio.run(main())
