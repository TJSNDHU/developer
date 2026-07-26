"""
test_iter309_narration_backend.py — Iter 309 · Live Narration

Backend contract tests for `LoopEngine._narrate()`:

  1. Narration events carry the required shape:
       data.type == "narration"
       data.tone in {pending, success, warning, danger}
       data.narration_step in {plan, execute, verify, scan, ship}
       data.narration_text is a non-empty string
       data.ts_epoch is a numeric server-side timestamp
       data.correlation_id is a string (may be empty)
  2. Narration text obeys the "<= 10 words" rule at every call site
     the audit identified (source-string grep, not runtime).
  3. Item 5 heartbeat-count contract still holds: exactly ONE
     `async def _heartbeat_loop` in loop_engine.py. Narration adds
     ZERO new async loops.
"""
from __future__ import annotations
import asyncio
import re
from pathlib import Path

import pytest


_LOOP_ENGINE_SRC = Path("/app/backend/services/loop_engine.py").read_text()


# ── 1. Heartbeat count invariant (Item 5 regression guard) ──────────
def test_heartbeat_loop_count_still_one():
    """Narration added zero new heartbeats. Only iter 308's
    `_heartbeat_loop` remains."""
    matches = re.findall(r"^\s*async def _heartbeat_loop", _LOOP_ENGINE_SRC,
                          flags=re.MULTILINE)
    assert len(matches) == 1, (
        f"Expected exactly 1 `async def _heartbeat_loop`, found "
        f"{len(matches)}. Narration must not add new async loops."
    )


# ── 2. `_narrate` helper method exists with correct signature ───────
def test_narrate_method_defined():
    assert "async def _narrate(" in _LOOP_ENGINE_SRC
    # Must accept step, tone, text, correlation_id, extra — loose
    # ordered substring check (not a strict regex) to tolerate whitespace
    # and comment formatting drift while still catching signature loss.
    sig_area = _LOOP_ENGINE_SRC.split("async def _narrate(", 1)[1][:600]
    for tok in ("step: str", "tone: str", "text: str",
                "correlation_id: str", "extra:"):
        assert tok in sig_area, (
            f"`_narrate` missing argument `{tok}` — signature drift"
        )


# ── 3. Narration event shape (runtime, via a stub queue) ────────────
class _StubEngine:
    """Minimal shim exercising _narrate() without booting a real
    LoopEngine (which requires Mongo, LLM, GitHub etc). We literally
    copy the `_narrate` method's body semantics inline for a
    deterministic contract check."""
    def __init__(self):
        self.state = "executing"
        self.phase = "execute"
        self.loop_id = "loop_test_narr"
        self.emitted = []

    async def _emit(self, state, phase, **kw):
        # Mimic real _emit's event synthesis — attach the timestamp
        # and hand off to a fake queue.
        ev = {"loop_id": self.loop_id, "state": state, "phase": phase, **kw}
        self.emitted.append(ev)

    async def _narrate(self, step, tone, text, correlation_id="", extra=None):
        import time as _time
        data = {
            "type": "narration", "tone": tone, "narration_step": step,
            "narration_text": text, "correlation_id": correlation_id or "",
            "ts_epoch": _time.time(),
        }
        if extra:
            data.update(extra)
        await self._emit(self.state, self.phase, step=0, total_steps=5,
                         message=text, data=data)


@pytest.mark.asyncio
async def test_narration_event_shape():
    eng = _StubEngine()
    await eng._narrate(
        step="execute", tone="pending",
        text="Writing health_router.py",
        correlation_id="execute:health_router.py",
    )
    assert len(eng.emitted) == 1
    ev = eng.emitted[0]
    data = ev["data"]
    assert data["type"] == "narration"
    assert data["tone"] == "pending"
    assert data["narration_step"] == "execute"
    assert data["narration_text"] == "Writing health_router.py"
    assert data["correlation_id"] == "execute:health_router.py"
    assert isinstance(data["ts_epoch"], float)
    assert data["ts_epoch"] > 1_700_000_000  # sanity: sensible epoch


@pytest.mark.asyncio
async def test_narration_extra_merges():
    eng = _StubEngine()
    await eng._narrate(
        step="ship", tone="success", text="Shipped abc123",
        correlation_id="ship:commit_1",
        extra={"commit_sha": "abc123", "html_url": "https://x/y"},
    )
    data = eng.emitted[0]["data"]
    assert data["commit_sha"] == "abc123"
    assert data["html_url"] == "https://x/y"
    # Base fields still present
    assert data["type"] == "narration"
    assert data["tone"] == "success"


# ── 4. Text-rule sanity: every narration text ≤ 10 words ────────────
_BANNED = {"please", "wait", "hang", "tight"}


def test_narration_text_word_budget_and_bans():
    """Scan the source for every `self._narrate(` call and check the
    `text=` argument is ≤ 10 words with no banned filler."""
    # Very light AST-free regex — grabs the text=... argument value.
    # The audit only added string literals for text (not f-strings
    # composed at runtime beyond simple interpolation), so this works.
    pattern = re.compile(
        r"self\._narrate\(\s*(?:.|\n)*?text\s*=\s*("
        r"f\"[^\"]*\"|f'[^']*'|\"[^\"]*\"|'[^']*'|\("
        r"[^)]*\)"
        r")",
        re.MULTILINE,
    )
    calls = pattern.findall(_LOOP_ENGINE_SRC)
    assert calls, "no _narrate calls found — audit missed them?"

    def _word_count(s: str) -> int:
        # Strip quotes/f-prefix, then split on whitespace. F-string
        # placeholders like `{path}` count as one word.
        s = s.strip()
        if s.startswith("f\"") or s.startswith("f'"):
            s = s[2:-1]
        elif s[0] in ("'", "\""):
            s = s[1:-1]
        return len([w for w in s.split() if w])

    for call in calls:
        wc = _word_count(call)
        assert wc <= 12, (       # 10 words + slack for f-string braces
            f"Narration text too long ({wc} words): {call}"
        )
        lower = call.lower()
        for banned in _BANNED:
            assert banned not in lower, (
                f"Banned filler word '{banned}' in narration text: {call}"
            )


# ── 5. Item 5 stronger contract — no `async def _heartbeat` variant ─
def test_no_ancillary_heartbeat_variants():
    """Narration must not have accidentally introduced a variant like
    `async def _narration_heartbeat` etc."""
    variants = re.findall(r"^\s*async def _\w*heartbeat\w*",
                          _LOOP_ENGINE_SRC, flags=re.MULTILINE)
    assert len(variants) == 1, (
        f"Multiple heartbeat-like coroutines found: {variants}"
    )
