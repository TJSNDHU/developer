# Parliament LLM Routing v2 — Post-Launch Roadmap

**Status:** APPROVED · Filed Feb 2026 by founder · DO NOT IMPLEMENT BEFORE LAUNCH

This roadmap captures the founder-approved Parliament LLM routing changes
identified in the Iter 212m-158 post-mortem.  Implementation is gated
behind launch — the current architecture (Iter 212m-155 mapping) is
production-stable and we don't want to introduce model swap risk inside
the 3-day pre-launch window.

---

## Current state (Iter 212m-155, as of Feb 2026)

| Surface | Mode | Primary LLM | Rescue |
|---|---|---|---|
| Council A — code/security/lint (3 members @ temps 0.1/0.2/0.3) | `mode=code review=pro` | **GLM-5.2** | Claude Sonnet 4.5 |
| Council B — analysis (3 members @ temps 0.3/0.4/0.5) | `mode=chat` | **DeepSeek V3/V4-flash** | — |
| Council C — writing (3 members @ temps 0.5/0.6/0.7) | `mode=chat` | **DeepSeek V3/V4-flash** | — |
| CEO judge | `mode=chat review=swift` | **GLM-5.2 only** | ❌ none — SPOF |

**Identified gaps:**
1. Council B (analysis) on the same flash model as Council C (writing) — reasoning quality cap.
2. CEO judge has no fallback — single hiccup hangs the whole parliament.
3. LongCat-2.0 (SWE-bench 59.5, 1M context, free cache hits on repeated repo context) not in the loop yet — exactly the model Council A was designed for.
4. `review_mode=maxx` is two sequential full calls — latency killer if it triggers often.

---

## Target state (Parliament v2)

| Surface | Primary LLM | Rescue | Rationale |
|---|---|---|---|
| **Council A** | **LongCat-2.0** | Claude Sonnet 4.5 | Purpose-built for repo-level coding. SWE-bench 59.5. 1M context = full repo in one call. Free cache hits on repeated context = major $/call saving. |
| **Council B** | **GLM-5.2** | DeepSeek V3 | GLM-5.2 is a reasoning model, not a coding model — analysis/skeptic tasks are exactly its strength. |
| **Council C** | DeepSeek V3/V4-flash | — (unchanged) | Writing at temps 0.5-0.7 doesn't need a frontier model.  Fast + cheap is correct. |
| **CEO judge** | GLM-5.2 (2 s timeout) | **DeepSeek V3** | Adds a silent rescue path on the SPOF.  GLM hiccup → DeepSeek takes over, parliament never hangs. |

---

## Implementation plan (week 1 post-launch)

**Day 1 — Council A swap (highest value, lowest risk: pure provider swap via OpenRouter):**
1. Confirm LongCat-2.0 is live on OpenRouter as of post-launch day 1 (search `meituan/longcat-2.0` or similar).  If still gated, fall back to Claude Sonnet 4.5 as Council A primary (still better than GLM for code).
2. In `services/llm.py`, add a new model alias `LONGCAT_MODEL = "meituan/longcat-2.0"` (or whatever the OpenRouter id is at the time).
3. Plumb through `mode="code"` → LongCat-2.0 → Claude rescue (replacing GLM).  Keep `review_mode=swift` semantics identical, just swap the underlying model.
4. Test surface: pick 20 real Council A prompts from `parliament_log` (Langfuse) — run old vs new in shadow mode, compare CEO win-rate and syntax-gate pass-rate.
5. Ship when shadow numbers are ≥ parity. Roll back via a single `LONGCAT_ENABLED=0` env flag.

**Day 3 — Council B GLM move:**
1. Add a new `mode="analysis"` or `council="B"` parameter on `call_llm_with_meta` so we can route Council B differently from Council C without disturbing the rest of `chat` mode.
2. Wire Council B's `_CouncilMember(... mode="analysis")` in `parliament.py`.
3. Same shadow-then-flip rollout as Day 1.

**Day 5 — CEO judge rescue:**
1. In `CEO.decide()`, wrap the GLM call in a `asyncio.wait_for(..., timeout=2.0)`.
2. On TimeoutError → fire a `mode="chat"` (DeepSeek) call with the same prompt.  Log `ceo_rescue_used=True` on the trace.
3. Add a Langfuse metric — `parliament.ceo.rescue_rate` — to watch how often the rescue actually triggers.  >5 % = GLM degradation, investigate.

**Day 7 — Telemetry pass:**
1. Add per-council per-model traces with explicit `model_id` tag.
2. Build a 7-day rollup widget on `SystemStatsPage` showing council win-rates per model.
3. Document the model swap in CHANGELOG.

---

## Constraints (DO NOT CHANGE):

- ✅ Parallel `asyncio.gather` structure — proven, don't touch.
- ✅ `SCORE_FLOOR = 0.55` — correct cut-off.
- ✅ Circuit breaker 3-strike / 30 s bypass — solid.
- ✅ Langfuse tracing wrapper — keep, just add new model tags.
- ✅ Council C on DeepSeek — unchanged.

---

## Rollback plan

Every step uses a single env flag (`LONGCAT_ENABLED`, `COUNCIL_B_GLM_ENABLED`, `CEO_RESCUE_ENABLED`).  Any regression → set the relevant flag to 0 and restart backend.  No DB migration, no code rollback, no user-visible churn.

---

## Pre-launch prep (zero-risk, can do now)

1. ✅ This roadmap file — *done*.
2. Add `LONGCAT_API_KEY` / `LONGCAT_MODEL` placeholders to `.env.example`.
3. Stub the new env flags as `False` by default in `services/llm.py` so the feature-flag plumbing is in place.  Behaviour unchanged because flags are off.

If founder wants me to do (2) + (3) right now (pure plumbing, zero runtime change), say "wire the flags".  Else this file just sits here until post-launch.

---

*Signed off by founder, Feb 2026.*
