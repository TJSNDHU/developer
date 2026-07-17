"""
services/ora_chat/ — Iter 212m-238

Internal admin "ORA Chat" — Sonnet-parity assistant built on top of
existing OpenRouter helpers (`_call_deepseek`, `_call_glm`) with hard
safety + cost boundaries.

Modules:
    router.py         — intent routing + per-route temperature/model config
    providers.py      — streaming OpenRouter caller (wraps existing key)
    session.py        — Mongo-backed conversation + sliding-window summary
    safety.py         — untrusted-content wrapper + system prompt
    slash_commands.py — deterministic DB queries (no LLM in fetch path)
    cost_tracker.py   — per-call token/cost log + hard monthly budget

Full spec: `/app/memory/PRD.md` Iter 212m-238 section.
"""
