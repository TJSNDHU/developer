#!/usr/bin/env python3
"""
scripts/generate_demo_audio.py — Iter 212m-231

Generate voiceover MP3 files for the /demo walkthrough (7 steps).

Uses OpenAI TTS via the Emergent LLM key (gpt-4o-mini-tts, `nova` voice
by default — warm feminine "product demo" tone).  Adjust VOICE + SCRIPT
below to taste; re-run to regenerate.

Usage:
    export EMERGENT_LLM_KEY=<your_universal_key>
    python scripts/generate_demo_audio.py

Output: /app/frontend/public/demo-audio/step-{1..7}.mp3

Notes:
- All 7 files together clock in around 48s — matches the current
  `FULL_STEPS` durations in `components/demo/demoSteps.jsx`.
- If you want to swap the voice, change VOICE below.  Options for
  OpenAI TTS: alloy, echo, fable, onyx, nova, shimmer.
- For a Hindi voice, set VOICE_LANG = "hi" and provide the Hindi
  translations in the SCRIPT_HI dict; the same MP3 filenames will be
  overwritten.
- Total cost via Emergent LLM key: ~$0.02 for the whole run
  (7 short clips × ~$0.003/clip @ tts-1 pricing).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# ── OUTPUT ─────────────────────────────────────────────────────────
OUT_DIR = Path("/app/frontend/public/demo-audio")
OUT_DIR.mkdir(parents=True, exist_ok=True)

# ── VOICE + LANG ──────────────────────────────────────────────────
VOICE      = "nova"     # alloy, echo, fable, onyx, nova, shimmer
MODEL      = "tts-1"    # tts-1 (fast) or tts-1-hd (higher fidelity)
VOICE_LANG = "en"       # "en" or "hi"

# ── NARRATION SCRIPT (English) ────────────────────────────────────
# Each entry MUST be ≤ the step duration.  Rule of thumb: ~15 words
# per 6 seconds when read at ORA's calm, mid-pace tone.  If you want
# to time it exactly, run `open -a QuickTime step-N.mp3` after
# generation and eyeball the waveform.
SCRIPT_EN: dict[str, str] = {
    "step-1": (
        # 6s — signup
        "Getting started takes seconds. One-click sign-in with GitHub or "
        "Google — and your first thousand tokens are free."
    ),
    "step-2": (
        # 5s — empty dashboard
        "You land on a clean dashboard. One clear next step: connect "
        "your first repository."
    ),
    "step-3": (
        # 9s — add repo
        "Pick a repo from your GitHub account, or paste any URL. Drop in "
        "a Personal Access Token with read and write access — encrypted "
        "at rest, only used to read and push this one repo. Then hit "
        "Continue."
    ),
    "step-4": (
        # 5.5s — connected
        "A green dot means ORA now has full, secure context of your "
        "codebase. From here, everything is possible."
    ),
    "step-5": (
        # 6.5s — chat / slash commands
        "Chat naturally, or type slash for commands — scan, plan, fix — "
        "and let ORA handle the rest. She's already read your code."
    ),
    "step-6": (
        # 9.5s — LOOP mode
        "Loop mode kicks in — the autonomous five-phase pipeline. Plan. "
        "Execute. Verify. Scan. Ship. Every step is validated by a "
        "council of multiple language models before code ever hits your "
        "repository."
    ),
    "step-7": (
        # 7s — Ship
        "And the pull request is shipped — bugs fixed, Vanguard reviewed, "
        "and ready to merge. That's a full end-to-end loop, right from "
        "your browser."
    ),
}

# ── NARRATION SCRIPT (Hindi — Hinglish) ───────────────────────────
SCRIPT_HI: dict[str, str] = {
    "step-1": (
        "Shuru karna bahut aasan hai. Ek click mein GitHub ya Google "
        "se sign-in karo — aur pehle hazaar tokens bilkul free hain."
    ),
    "step-2": (
        "Ek saaf dashboard khulta hai. Sirf ek clear next step: apna "
        "pehla repository connect karo."
    ),
    "step-3": (
        "Apna GitHub repo pick karo, ya koi bhi URL paste karo. Ek "
        "Personal Access Token daalo — read aur write access ke saath. "
        "Encrypted rehta hai, sirf isi repo ke liye use hoga. Continue "
        "dabao."
    ),
    "step-4": (
        "Green dot ka matlab — ORA ke paas ab tumhare code ka poora "
        "secure context hai. Yahaan se sab kuch possible hai."
    ),
    "step-5": (
        "Naturally chat karo, ya slash type karke commands do — scan, "
        "plan, fix. ORA sab handle karegi. Wo pehle se code padh chuki "
        "hai."
    ),
    "step-6": (
        "Loop mode start hota hai — autonomous paanch-phase pipeline. "
        "Plan. Execute. Verify. Scan. Ship. Har step multiple language "
        "models ke council se validate hota hai."
    ),
    "step-7": (
        "Aur pull request ship ho jaata hai — bugs fixed, Vanguard "
        "reviewed, merge ke liye tayaar. Yeh ek complete end-to-end "
        "loop hai, seedha browser se."
    ),
}


def get_script() -> dict[str, str]:
    return SCRIPT_HI if VOICE_LANG == "hi" else SCRIPT_EN


# ── OpenAI TTS via Emergent LLM key ───────────────────────────────
def generate_all():
    key = os.environ.get("EMERGENT_LLM_KEY")
    if not key:
        print("❌ EMERGENT_LLM_KEY not set. Get it from Profile → "
              "Universal Key on the AUREM dashboard.")
        sys.exit(2)

    from openai import OpenAI
    client = OpenAI(
        api_key=key,
        base_url="https://integrations.emergentagent.com/llm/openai",
    )

    script = get_script()
    for step_id, text in script.items():
        out_path = OUT_DIR / f"{step_id}.mp3"
        print(f"→ Generating {out_path.name} ({len(text)} chars)…",
              end=" ", flush=True)
        try:
            resp = client.audio.speech.create(
                model=MODEL,
                voice=VOICE,
                input=text,
                response_format="mp3",
            )
            resp.stream_to_file(out_path.as_posix())
            size_kb = out_path.stat().st_size / 1024
            print(f"✅ {size_kb:.1f} KB")
        except Exception as e:
            print(f"❌ {type(e).__name__}: {e}")

    # Write a machine-readable manifest so the frontend can auto-load
    # per-step audio without hard-coding filenames.
    manifest = {
        "voice":      VOICE,
        "model":      MODEL,
        "lang":       VOICE_LANG,
        "generated":  __import__("time").time(),
        "files": {
            step_id: f"/demo-audio/{step_id}.mp3"
            for step_id in script.keys()
        },
    }
    import json
    (OUT_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"\n✅ Manifest written: {OUT_DIR / 'manifest.json'}")


if __name__ == "__main__":
    generate_all()
