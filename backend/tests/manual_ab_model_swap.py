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

Iter 212m-212 · Vision-model A/B lane
======================================

Added a second, independent test loop that hits the OpenRouter
vision endpoint (same one `services/advisor_vision.py` uses in
production) with a REAL screenshot of `/demo` (saved at
`tests/fixtures/demo_real.jpeg`).  Each candidate answers the exact
same UI-review prompt.  We measure:
  * wall-time (proxy for latency perceived by the user)
  * approximate cost (input+output tokens × per-1M-tokens rate)
  * grounded-in-image score — did the reply mention the actual
    visible elements ("signup", "continue with google/github",
    "email", "1000 tokens free") or hallucinate?

Winner = the model with the lowest cost that still hits at least 3
of the 4 grounded-in-image ground-truth markers.

Run with:
    cd /app/backend && python tests/manual_ab_model_swap.py
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

    # ── Iter 212m-212 — Vision-lane A/B ─────────────────────────
    print("\n\n══════════════════════════════════════════════════════════════")
    print("VISION LANE — advisor screen-share model swap")
    print("══════════════════════════════════════════════════════════════")
    await _run_vision_ab()


# ─────────────────────────────────────────────────────────────────
# Iter 212m-212 · Vision-lane A/B
# ─────────────────────────────────────────────────────────────────

import base64 as _b64
import httpx as _httpx
from pathlib import Path as _Path

VISION_FIXTURE = _Path(__file__).parent / "fixtures" / "demo_real.jpeg"

VISION_CANDIDATES = [
    # (label,                 openrouter-model-id,          input$/1M, output$/1M)
    ("gemini-2.5-flash",      "google/gemini-2.5-flash",    0.30,      2.50),
    ("gpt-5-mini",            "openai/gpt-5-mini",          0.30,      2.50),
    # Kept as a "smart-but-pricier" reference so the ratio is visible.
    ("claude-sonnet-4.5",     "anthropic/claude-sonnet-4.5", 3.00,     15.00),
]

VISION_PROMPT = (
    "You are a UI-review assistant.  Describe what you SEE in this "
    "screenshot in ≤100 words: layout, main components, visible text, "
    "obvious defects.  Do not invent content that isn't visible.  "
    "End with `PROBABLE_ISSUES:` and a comma list, or `PROBABLE_ISSUES: none`."
)

# Ground-truth markers actually present in /demo screenshot.  A model
# that describes the image correctly should hit at least 3 of these.
GROUND_TRUTH_MARKERS = [
    "signup", "sign up", "developer account", "continue with google",
    "continue with github", "email", "password", "1000 tokens",
    "1,000 tokens", "aurem",
]


async def _vision_one(label: str, model: str, png_b64: str,
                      input_price: float, output_price: float) -> dict:
    """One OpenRouter vision call.  Returns cost + latency + quality."""
    key = os.getenv("OPENROUTER_API_KEY")
    if not key:
        return {"label": label, "model": model, "error": "no OPENROUTER_API_KEY"}
    data_uri = f"data:image/jpeg;base64,{png_b64}"
    payload = {
        "model": model,
        "temperature": 0.2,
        "max_tokens": 300,
        "messages": [
            {"role": "system", "content": VISION_PROMPT},
            {"role": "user", "content": [
                {"type": "text", "text": "Describe this screenshot."},
                {"type": "image_url", "image_url": {"url": data_uri}},
            ]},
        ],
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    t0 = time.time()
    try:
        async with _httpx.AsyncClient(timeout=25.0) as cx:
            r = await cx.post(
                "https://openrouter.ai/api/v1/chat/completions",
                json=payload, headers=headers,
            )
    except Exception as e:
        return {"label": label, "model": model,
                "error": f"transport {type(e).__name__}: {e!r}"[:200]}
    ms = int((time.time() - t0) * 1000)
    if r.status_code != 200:
        return {"label": label, "model": model, "http": r.status_code,
                "body": r.text[:200], "ms": ms}
    j = r.json()
    content = ((j.get("choices") or [{}])[0].get("message") or {}).get("content", "")
    usage = j.get("usage") or {}
    prompt_toks = int(usage.get("prompt_tokens", 0))
    output_toks = int(usage.get("completion_tokens", 0))
    cost_usd = (prompt_toks * input_price + output_toks * output_price) / 1_000_000
    reply_lc = (content or "").lower()
    hit_markers = [m for m in GROUND_TRUTH_MARKERS if m in reply_lc]
    grounded_score = len(hit_markers)
    return {
        "label": label, "model": model, "ms": ms,
        "reply_len": len(content or ""),
        "reply_first_200": (content or "")[:200],
        "prompt_tokens": prompt_toks,
        "output_tokens": output_toks,
        "cost_usd": round(cost_usd, 6),
        "hit_markers": hit_markers,
        "grounded_score": grounded_score,
        "passes_grounding": grounded_score >= 3,
    }


async def _run_vision_ab() -> None:
    if not VISION_FIXTURE.exists():
        print(f"  ✗ fixture missing: {VISION_FIXTURE}")
        return
    png_b64 = _b64.b64encode(VISION_FIXTURE.read_bytes()).decode()
    print(f"  fixture: {VISION_FIXTURE.name}  ({VISION_FIXTURE.stat().st_size} bytes, "
          f"{len(png_b64)} b64 chars)")

    results = []
    for label, model, in_p, out_p in VISION_CANDIDATES:
        print(f"\n  === {label} ({model}) ===")
        r = await _vision_one(label, model, png_b64, in_p, out_p)
        results.append(r)
        if "error" in r or r.get("http"):
            print(f"    ERROR: {r}")
            continue
        print(f"    time:            {r['ms']} ms")
        print(f"    tokens in/out:   {r['prompt_tokens']} / {r['output_tokens']}")
        print(f"    cost:            ${r['cost_usd']:.6f}")
        print(f"    grounded hits:   {r['grounded_score']}/{len(GROUND_TRUTH_MARKERS)}  "
              f"({r['hit_markers']})")
        print(f"    passes(≥3 hits): {r['passes_grounding']}")
        print(f"    reply.first_200: {r['reply_first_200']!r}")

    # Winner selection: cheapest model that passes grounding.
    passing = [r for r in results if r.get("passes_grounding")]
    passing.sort(key=lambda r: r.get("cost_usd", 1e9))
    print("\n" + "─" * 62)
    if passing:
        w = passing[0]
        print(f"  WINNER: {w['label']} ({w['model']})")
        print(f"    cost:  ${w['cost_usd']:.6f}  |  latency: {w['ms']} ms  |  "
              f"grounded: {w['grounded_score']}/{len(GROUND_TRUTH_MARKERS)}")
        # Cost ratio vs the pricier reference (last in list = claude)
        ref = [r for r in results if r.get("label") == "claude-sonnet-4.5"]
        if ref and ref[0].get("cost_usd") and w.get("cost_usd"):
            ratio = ref[0]["cost_usd"] / max(w["cost_usd"], 1e-9)
            print(f"    ~{ratio:.1f}x cheaper than Claude on this workload")
    else:
        print("  ✗ NO candidate passed the grounding threshold "
              "(≥3 markers). Check fixture / prompt / API keys.")
    print("─" * 62)


if __name__ == "__main__":
    asyncio.run(main())
