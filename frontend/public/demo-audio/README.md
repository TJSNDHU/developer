# /demo Walkthrough — Voiceover / Audio Setup

Iter 212m-231 — README for adding narration audio to the demo at
[`/demo`](https://auremcto.com/demo) (also embedded on the homepage).

---

## What Was Added

1. **Generator script** — `backend/scripts/generate_demo_audio.py`
   Creates 7 MP3 files (one per step) via OpenAI TTS using your
   Emergent Universal Key. Written to `frontend/public/demo-audio/`.

2. **Audio-sync in `WalkthroughPlayer.jsx`** —
   The player now plays the matching MP3 whenever a step is active,
   pauses it when you pause, and resets on restart. A speaker
   mute/unmute button appears next to the play/pause control.

3. **`audioSrc` on every step** in `demoSteps.jsx` — points each
   step to its MP3 file. Missing files degrade gracefully (visuals
   still play).

---

## Generate the Audio

### 1. Set your Emergent LLM key

```bash
# Login → Profile → Universal Key → Copy
export EMERGENT_LLM_KEY="sk-emergent-XXXXXX"
```

### 2. Run the generator

```bash
cd /app/backend
python scripts/generate_demo_audio.py
```

You'll see:
```
→ Generating step-1.mp3 (105 chars)… ✅ 22.3 KB
→ Generating step-2.mp3 (78 chars)…  ✅ 18.1 KB
...
✅ Manifest written: /app/frontend/public/demo-audio/manifest.json
```

**Cost:** ~$0.02 total (7 short TTS clips at OpenAI's `tts-1` rate).

### 3. Verify locally

Open `https://auremcto.com/demo` (or your preview) and click the
🔊 icon next to Play — narration should start with step 1 and switch
automatically at each step transition.

---

## Customising

### Change the voice

Open `backend/scripts/generate_demo_audio.py`, edit:

```python
VOICE = "nova"     # try: alloy, echo, fable, onyx, nova, shimmer
MODEL = "tts-1"    # or "tts-1-hd" for higher fidelity (2× cost)
```

Re-run the script to regenerate.

### Switch to Hindi narration

```python
VOICE_LANG = "hi"   # in generate_demo_audio.py
```

Hindi (Hinglish) copies are already in `SCRIPT_HI` and read naturally
in the same MP3 filenames — no code change needed on the frontend.

### Edit the narration text

The 7 step narrations live in `SCRIPT_EN` (English) or `SCRIPT_HI`
(Hindi) inside `generate_demo_audio.py`. Each must fit within the
step's `duration` in `demoSteps.jsx`:

| Step | ID          | Duration | Rough word budget |
|------|-------------|----------|-------------------|
| 1    | signup      | 6.0 s    | ~15 words         |
| 2    | dashboard   | 5.0 s    | ~12 words         |
| 3    | connect     | 9.0 s    | ~22 words         |
| 4    | connected   | 5.5 s    | ~13 words         |
| 5    | chat        | 6.5 s    | ~16 words         |
| 6    | loop        | 9.5 s    | ~24 words         |
| 7    | ship        | 7.0 s    | ~17 words         |

If a clip overshoots the step, it fades naturally when the next
step's audio starts (browser handles the crossover).

### Bring your own recordings

You don't have to use TTS. Just drop pre-recorded MP3 files into
`frontend/public/demo-audio/` with the filenames:

```
step-1.mp3
step-2.mp3
step-3.mp3
step-4.mp3
step-5.mp3
step-6.mp3
step-7.mp3
```

The player picks them up on the next page reload.

---

## Frontend Toggle

You can disable audio globally (e.g. for the compact landing embed):

```jsx
<WalkthroughPlayer
  steps={FULL_STEPS}
  audioEnabled={false}   // ← default is true
/>
```

The mute button only renders when `audioEnabled` is `true`.

---

## Deployment

The MP3 files are static assets in `frontend/public/`. They ship with
the next Vercel/Netlify/Emergent deploy of the frontend — no backend
changes required. Total added size: ~150 KB (all 7 clips combined).

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Silent playback on first load | Browser autoplay policy blocks audio until user clicks | Click 🔊 icon once — subsequent step transitions play automatically |
| 404 on step-N.mp3 in devtools | Files never generated | Run the generator script |
| Voice doesn't match my brand | Wrong TTS voice | Change `VOICE = "..."` and re-run |
| Playback ahead of/behind visuals | Narration exceeds step duration | Trim the corresponding entry in `SCRIPT_EN` |
