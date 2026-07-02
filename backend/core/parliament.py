"""
core/parliament.py — Iter 212m-151 (production-ready)

Multi-agent code-generation orchestrator for Loop Mode.

Wired into ONLY two places (per founder spec):
  • services/loop_engine.py::_do_execute  — per-file LLM patch
  • services/loop_engine.py::_do_verify   — heal LLM call

Prompt Mode, ORA, Codebase Health, and every other path stay
exactly as they are.  This module is NEVER imported by chat.py,
orchestrator.py, or any non-loop router.

Architecture (Iter 212m-151 additions in **bold**):

  Parliament
    ├── TaskRouter   — picks which Council based on task_type
    ├── Council A    — code/security: 3 members @ temps 0.1/0.2/0.3
    │   (Council B + C are placeholders — task says "implement baad mein")
    ├── CEO          — final picker, **output-type-aware temperature**
    ├── SelfHeal     — healer.heal() for Verify-phase recovery
    │                  **respects caller's round counter, no own counter**
    ├── **ParliamentCircuitBreaker** — opens after 3 consecutive
    │                                  LLM failures; 45 s cooldown.
    ├── **Global asyncio.Semaphore(6)** — hard cap on concurrent
    │                                     LLM calls (was 9 worst-case).
    └── **Distributed trace_id** — uuid per run, propagated through
                                   every step's parliament_log row.

  All decisions logged to `parliament_log` Mongo collection.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from collections import deque
from typing import Any, Optional

from .observability import trace_llm  # Iter 212m-153 — safe Langfuse wrapper

logger = logging.getLogger("aurem-dev.parliament")


# ─────────────────────────────────────────────────────────────────────
#  Module-level concurrency cap (GAP 1).
#
#  3 files in parallel × 3 council members = 9 worst-case LLM calls.
#  We cap at 6 so the 9th request queues briefly instead of blowing up
#  the provider's rate limit.  Single source of truth — every LLM call
#  inside this module goes through `_GLOBAL_LLM_SEM`.
# ─────────────────────────────────────────────────────────────────────

MAX_CONCURRENT_LLM_CALLS = 6
_GLOBAL_LLM_SEM = asyncio.Semaphore(MAX_CONCURRENT_LLM_CALLS)


# ─────────────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────────────

_REFUSAL_RX = re.compile(
    r"\b(i (?:cannot|can't|won't|will not|am unable to)|"
    r"as an ai|i'm just an ai|i'm sorry, (?:but )?i can(?:not|'t))\b",
    re.IGNORECASE,
)

_FENCE_RX = re.compile(r"^```(?:[a-zA-Z0-9_-]+)?\s*\n([\s\S]*?)\n```\s*$")


def _strip_fences(text: str) -> str:
    """Strip a single outer ```lang … ``` if present (LLMs love these)."""
    if not text:
        return ""
    s = text.strip()
    m = _FENCE_RX.match(s)
    return m.group(1) if m else s


def _score_output(output: str, *, task_type: str = "code_fix",
                  expected_min_chars: int = 50) -> float:
    """Heuristic 0.0-1.0 score for a candidate output."""
    if not output:
        return 0.0
    text = output.strip()
    if _REFUSAL_RX.search(text):
        return 0.0
    if len(text) < expected_min_chars:
        return 0.2
    # Iter 212m-155 — task-type-aware scoring.
    if task_type == "analysis":
        return _score_analysis(text)
    if task_type == "writing":
        return _score_writing(text)
    if task_type == "code_fix":
        markers = ("def ", "class ", "import ", "function ", "const ",
                   "let ", "return ", "{", "}", "from ", "export ")
        hits = sum(1 for m in markers if m in text)
        if hits >= 6:
            return 0.92
        if hits >= 3:
            return 0.78
        if hits >= 1:
            return 0.60
        return 0.30
    return 0.65


# ─────────────────────────────────────────────────────────────────────
#  Iter 212m-155 — structural scoring for Council B (analysis) and
#  Council C (writing).  Returns a 0.0-1.0 float on the same scale as
#  the code scorer so the CEO can compare apples-to-apples.
# ─────────────────────────────────────────────────────────────────────

_NUMBER_RX = re.compile(r"\d+\.?\d*%?")


def _score_analysis(text: str) -> float:
    """Score analysis output by structural quality, not correctness."""
    score = 0.60
    # Has numbers / data points → +0.15
    if _NUMBER_RX.search(text):
        score += 0.15
    # Has clear structure (markdown headers or numbered lists or bullets)
    if any(tok in text for tok in ("## ", "1.", "2.", "- ")):
        score += 0.10
    # Length sanity.
    words = len(text.split())
    if words < 50:
        score -= 0.20
    elif words > 500:
        score -= 0.10
    # Has actionable conclusion.
    action_signals = ("recommend", "suggest", "should",
                      "consider", "next step", "action")
    if any(s in text.lower() for s in action_signals):
        score += 0.15
    return max(0.0, min(1.0, score))


def _score_writing(text: str) -> float:
    """Score writing/copy output by structural quality."""
    score = 0.60
    words = len(text.split())
    if 30 <= words <= 200:
        score += 0.15
    elif words > 300:
        score -= 0.15
    elif words < 20:
        score -= 0.20
    cta_signals = ("reply", "click", "schedule", "book", "call",
                   "reach out", "let me know", "interested", "response")
    if any(s in text.lower() for s in cta_signals):
        score += 0.15
    # Weak opening: starting with "I " bleeds the reader-focus.
    if text.lstrip().startswith("I "):
        score -= 0.10
    personal_signals = ("you", "your", "you're")
    if any(s in text.lower() for s in personal_signals):
        score += 0.10
    return max(0.0, min(1.0, score))


# ─────────────────────────────────────────────────────────────────────
#  GAP 1 — Circuit Breaker
# ─────────────────────────────────────────────────────────────────────

class ParliamentCircuitBreaker:
    """Tracks LLM call health.  Opens after `FAILURE_THRESHOLD`
    consecutive failures and stays open for `COOLDOWN_SECONDS`.

    States::

        CLOSED → OPEN → HALF_OPEN → CLOSED (or back to OPEN)

    Behaviour:
      - CLOSED    : every call goes through, results are recorded
      - OPEN      : `should_attempt()` returns False — callers
                    fall back to a single (non-council) LLM call
      - HALF_OPEN : exactly one probe call is allowed.  Success →
                    CLOSED; failure → back to OPEN with a fresh
                    cooldown.
    """

    FAILURE_THRESHOLD  = 3
    TIMEOUT_PER_CALL   = 25       # seconds — per single LLM call
    COOLDOWN_SECONDS   = 45       # OPEN → HALF_OPEN wait
    WINDOW_SECONDS     = 60       # sliding window for observability

    def __init__(self):
        self._state            = "closed"
        self._consec_failures  = 0
        self._opened_at        = 0.0
        self._half_open_probe  = False   # True while a probe is in flight
        self._window: deque    = deque(maxlen=128)   # (ts, ok, latency_ms)
        self._lock             = asyncio.Lock()

    # ── State machine ─────────────────────────────────────────────
    @property
    def state(self) -> str:
        # OPEN can auto-transition to HALF_OPEN on read if cooldown
        # has elapsed.  This avoids needing a background task.
        if self._state == "open":
            if time.monotonic() - self._opened_at >= self.COOLDOWN_SECONDS:
                self._state = "half_open"
                self._half_open_probe = False
                logger.info("[circuit_breaker] OPEN → HALF_OPEN "
                            "(cooldown elapsed)")
        return self._state

    def should_attempt(self) -> bool:
        """Returns True iff a call should be attempted right now."""
        st = self.state
        if st == "closed":
            return True
        if st == "open":
            return False
        if st == "half_open":
            # Only one probe at a time.
            if not self._half_open_probe:
                self._half_open_probe = True
                return True
            return False
        return True

    # ── Outcome recording ─────────────────────────────────────────
    def _trim(self, now: float) -> None:
        cutoff = now - self.WINDOW_SECONDS
        while self._window and self._window[0][0] < cutoff:
            self._window.popleft()

    def record_success(self, latency_ms: float = 0.0) -> None:
        now = time.monotonic()
        self._trim(now)
        self._window.append((now, True, latency_ms))
        self._consec_failures = 0
        if self._state in ("half_open", "open"):
            logger.info("[circuit_breaker] %s → CLOSED (probe succeeded)",
                        self._state.upper())
            self._state = "closed"
            self._half_open_probe = False

    def record_failure(self, latency_ms: float = 0.0,
                        kind: str = "error") -> None:
        now = time.monotonic()
        self._trim(now)
        self._window.append((now, False, latency_ms))
        self._consec_failures += 1
        if self._state == "half_open":
            logger.warning("[circuit_breaker] HALF_OPEN probe failed → "
                           "OPEN again (kind=%s)", kind)
            self._state = "open"
            self._opened_at = now
            self._half_open_probe = False
            return
        if (self._state == "closed"
                and self._consec_failures >= self.FAILURE_THRESHOLD):
            logger.warning(
                "[circuit_breaker] CLOSED → OPEN "
                "(%d consecutive failures; kind=%s)",
                self._consec_failures, kind,
            )
            self._state = "open"
            self._opened_at = now

    # ── Stats (for logs / introspection) ─────────────────────────
    def stats(self) -> dict:
        self._trim(time.monotonic())
        oks = sum(1 for _, ok, _ in self._window if ok)
        return {
            "state":             self._state,
            "consec_failures":   self._consec_failures,
            "window_total":      len(self._window),
            "window_ok":         oks,
            "window_seconds":    self.WINDOW_SECONDS,
            "cooldown_seconds":  self.COOLDOWN_SECONDS,
            "failure_threshold": self.FAILURE_THRESHOLD,
        }


# Module-level singleton — shared across all Parliament instances.
_GLOBAL_BREAKER = ParliamentCircuitBreaker()


# ─────────────────────────────────────────────────────────────────────
#  Task Router
# ─────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────
#  Iter 212m-177 — P0-3: automatic task_type inference.
#  Before this, task_type was ONLY an optional client override, so
#  every organic prompt fell through TaskRouter's keyword check and
#  landed on Council A — writing tasks ("Write a CONTRIBUTING.md")
#  never reached Council C. This is the single source of truth used
#  by /chat/send and /chat/stream when the client sends no override.
# ─────────────────────────────────────────────────────────────────────
_WRITING_NOUNS = re.compile(
    r"\b(email|e-mail|blog|post|readme|contributing|changelog|newsletter|"
    r"announcement|article|tweet|press release|copywrit\w*|marketing copy|"
    r"documentation|user guide|onboarding guide)\b", re.I)
_WRITING_VERBS = re.compile(
    r"\b(write|draft|compose|reword|rewrite|rephrase)\b", re.I)
_CODE_NOUNS = re.compile(
    r"\b(function|class|endpoint|bug|test|route|module|method|refactor|"
    r"lint|error|exception|api|migration|schema|regex|docstring)\b", re.I)
_ANALYSIS_PAT = re.compile(
    r"\b(analy[sz]e|analysis|summari[sz]e|summary|assess|evaluate|"
    r"insight|health of|codebase health|report on|breakdown of)\b", re.I)


def infer_task_type(text: Optional[str]) -> Optional[str]:
    """Deterministic task_type inference from the raw prompt.
    Returns 'write' (→ Council C), 'analysis' (→ Council B) or None
    (caller keeps the default Council A code path)."""
    t = text or ""
    if _WRITING_NOUNS.search(t) and (
            _WRITING_VERBS.search(t) or not _CODE_NOUNS.search(t)):
        return "write"
    if _ANALYSIS_PAT.search(t) and not _CODE_NOUNS.search(t):
        return "analysis"
    return None


class TaskRouter:
    """Picks which Council should handle a given task.

    Iter 212m-160 — task_type routing:
      • analysis / report / insight / summarize → Council B (GLM-5.2 + DeepSeek rescue)
      • email / copy / write / draft            → Council C (DeepSeek, creative voice)
      • code_fix / code_review / security / lint_heal → Council A (LongCat → Claude)
      • everything else                          → Council A (safe default)

    `context["council"]` still wins when explicitly set so unit tests
    and any future caller that wants to pin a specific council can do
    so without losing the keyword path.
    """

    _COUNCIL_A_KEYWORDS = (
        "code", "patch", "fix", "refactor", "implement",
        "security", "vuln", "scan", "lint", "syntax",
        "rewrite", "test", "validate",
    )

    # Iter 212m-160 — explicit task_type → council map. Keys mirror the
    # strings already used by callers (`code_fix`, `code_review`,
    # `security`, `lint_heal`); the new B/C entries unlock Council B's
    # GLM-5.2 V2 swap + Council C's creative DeepSeek path for analysis
    # and writing tasks respectively.
    _TASK_TYPE_TO_COUNCIL = {
        # Council A — code surgery
        "code_fix":     "A",
        "code_review":  "A",
        "security":     "A",
        "lint_heal":    "A",
        # Council B — analysis / advisory (V2 GLM-5.2 + DeepSeek rescue)
        "analysis":     "B",
        "report":       "B",
        "insight":      "B",
        "summarize":    "B",
        # Council C — creative / writing
        "email":        "C",
        "copy":         "C",
        "write":        "C",
        "draft":        "C",
    }

    def route(self, task: str, context: dict | None = None) -> str:
        ctx = context or {}
        forced = ctx.get("council")
        if forced in ("A", "B", "C"):
            return forced
        ttype = (ctx.get("task_type") or "").lower()
        if ttype in self._TASK_TYPE_TO_COUNCIL:
            return self._TASK_TYPE_TO_COUNCIL[ttype]
        text = (task or "").lower()
        if any(k in text for k in self._COUNCIL_A_KEYWORDS):
            return "A"
        return "A"


# ─────────────────────────────────────────────────────────────────────
#  Internal — single LLM call wrapped by the circuit breaker + sem.
# ─────────────────────────────────────────────────────────────────────

async def _llm_call_protected(*, system: str, user: str, max_tokens: int,
                              mode: str, review_mode: str,
                              user_id: Optional[str] = None,
                              temperature: float = 0.1,
                              trace_name: str = "parliament.llm_call",
                              trace_metadata: Optional[dict] = None,
                              ) -> tuple[str, float, Optional[str]]:
    """Make a single LLM call wrapped by the global semaphore + the
    circuit breaker's hard per-call timeout.

    Returns (content, latency_ms, error_str).  `error_str` is None on
    success, a short tag on failure (`"timeout"`, `"refused"`, or the
    exception class name).

    Iter 212m-153 — Every call is wrapped by a Langfuse generation
    observation.  Silent no-op when Langfuse keys are not configured.
    """
    from services.llm import call_llm_with_meta
    t0 = time.monotonic()
    md = {
        "mode":        mode,
        "review_mode": review_mode,
        "temperature": temperature,
        "max_tokens":  max_tokens,
    }
    if trace_metadata:
        md.update(trace_metadata)
    # Truncate the input for safety — Langfuse handles long strings
    # but we don't need to ship 6 KB of self-heal context every time.
    trace_input = {
        "system_preview": (system or "")[:240],
        "user_preview":   (user or "")[:1200],
    }
    with trace_llm(trace_name, input=trace_input, metadata=md) as span:
        async with _GLOBAL_LLM_SEM:
            try:
                kwargs = dict(
                    system=system, user=user, max_tokens=max_tokens,
                    mode=mode, user_id=user_id, review_mode=review_mode,
                )
                try:
                    meta = await asyncio.wait_for(
                        call_llm_with_meta(temperature=temperature, **kwargs),
                        timeout=ParliamentCircuitBreaker.TIMEOUT_PER_CALL,
                    )
                except TypeError:
                    # Older signature without temperature kwarg.
                    meta = await asyncio.wait_for(
                        call_llm_with_meta(**kwargs),
                        timeout=ParliamentCircuitBreaker.TIMEOUT_PER_CALL,
                    )
            except asyncio.TimeoutError:
                latency_ms = round((time.monotonic() - t0) * 1000, 1)
                _GLOBAL_BREAKER.record_failure(latency_ms, kind="timeout")
                span.set_metadata({"latency_ms": latency_ms, "error": "timeout"})
                span.record_error("timeout")
                return "", latency_ms, "timeout"
            except Exception as e:                          # noqa: BLE001
                latency_ms = round((time.monotonic() - t0) * 1000, 1)
                _GLOBAL_BREAKER.record_failure(latency_ms, kind=type(e).__name__)
                err_tag = f"{type(e).__name__}: {str(e)[:120]}"
                span.set_metadata({"latency_ms": latency_ms, "error": err_tag})
                span.record_error(e)
                return "", latency_ms, err_tag
        latency_ms = round((time.monotonic() - t0) * 1000, 1)
        content = (meta or {}).get("content", "") or ""
        if not content.strip():
            _GLOBAL_BREAKER.record_failure(latency_ms, kind="empty")
            span.set_metadata({"latency_ms": latency_ms, "error": "empty"})
            span.record_error("empty_response")
            return "", latency_ms, "empty"
        _GLOBAL_BREAKER.record_success(latency_ms)
        # Capture output + token usage (when llm meta provides it).
        span.set_output(content[:2000])
        usage_md = {"latency_ms": latency_ms}
        try:
            for k in ("input_tokens", "output_tokens", "total_tokens",
                      "provider", "model"):
                if isinstance(meta, dict) and meta.get(k) is not None:
                    usage_md[k] = meta[k]
        except Exception:
            pass
        span.set_metadata(usage_md)
        return content, latency_ms, None


# ─────────────────────────────────────────────────────────────────────
#  Council members
# ─────────────────────────────────────────────────────────────────────

class _CouncilMember:
    """A single voting member of a Council.  Calls an LLM at a fixed
    temperature with a fixed persona, via the global concurrency cap
    + circuit breaker."""

    def __init__(self, *, name: str, temperature: float, persona: str,
                 mode: str = "code", review_mode: str = "pro",
                 max_tokens: int = 4000):
        self.name        = name
        self.temperature = temperature
        self.persona     = persona
        self.mode        = mode
        self.review_mode = review_mode
        self.max_tokens  = max_tokens

    async def cast_vote(self, *, task: str, context: dict) -> dict:
        """Returns: {member, output, score, error, latency_ms, temp}."""
        # Iter 212m-159 — surface the V2 routing primary on each trace so
        # Langfuse dashboards can filter Parliament runs by model.
        from services.llm import (
            LONGCAT_ENABLED, COUNCIL_B_GLM_ENABLED, CEO_RESCUE_ENABLED,
            council_a_primary_model, council_b_primary_model,
        )
        council_id = context.get("council") or ""
        if council_id == "A":
            primary_model = council_a_primary_model()
        elif council_id == "B":
            primary_model = council_b_primary_model()
        else:
            primary_model = "deepseek/deepseek-chat"
        content, latency_ms, err = await _llm_call_protected(
            system=self.persona, user=task,
            max_tokens=self.max_tokens, mode=self.mode,
            review_mode=self.review_mode,
            user_id=context.get("user_id"),
            temperature=self.temperature,
            trace_name=f"parliament.council.{council_id or '?'}.{self.name}",
            trace_metadata={
                "trace_id":         context.get("parliament_trace_id"),
                "council":          council_id,
                "member":           self.name,
                "task_type":        context.get("task_type"),
                "user_id":          context.get("user_id"),
                "file_path":        context.get("file_path"),
                "primary_model":    primary_model,
                "v2_longcat":       LONGCAT_ENABLED,
                "v2_council_b_glm": COUNCIL_B_GLM_ENABLED,
                "v2_ceo_rescue":    CEO_RESCUE_ENABLED,
            },
        )
        if err:
            return {
                "member":     self.name,
                "output":     "",
                "score":      0.0,
                "error":      err,
                "latency_ms": latency_ms,
                "temp":       self.temperature,
            }
        out = _strip_fences(content)
        score = _score_output(
            out,
            task_type=context.get("task_type", "code_fix"),
        )
        return {
            "member":     self.name,
            "output":     out,
            "score":      score,
            "error":      None,
            "latency_ms": latency_ms,
            "temp":       self.temperature,
        }


# ─────────────────────────────────────────────────────────────────────
#  Councils
# ─────────────────────────────────────────────────────────────────────

class _Council:
    name: str = "?"
    members: list[_CouncilMember] = []

    async def vote(self, *, task: str, context: dict) -> list[dict]:
        if not self.members:
            return []
        logger.info("Council %s calling %d members in parallel "
                    "(global concurrency cap=%d)",
                    self.name, len(self.members), MAX_CONCURRENT_LLM_CALLS)
        for m in self.members:
            logger.info("Council %s member %s called — temp %.1f",
                        self.name, m.name, m.temperature)
        votes = await asyncio.gather(
            *[m.cast_vote(task=task, context=context) for m in self.members],
            return_exceptions=False,
        )
        return list(votes)


_COUNCIL_A_PERSONA = (
    "You are a senior AI software engineer participating in a small "
    "council that will collectively decide on a code fix.  Read the "
    "task carefully and write the COMPLETE final file contents.  Do "
    "NOT add commentary.  Do NOT wrap in code fences.  Preserve any "
    "existing functionality the task does not explicitly change.  "
    "If the task mentions a security vulnerability (SQL injection, "
    "secret leak, eval, command injection, path traversal, weak "
    "crypto), prioritise eliminating the vuln class first."
)


class CouncilA(_Council):
    name = "A"
    members = [
        _CouncilMember(name="A1-conservative",
                       temperature=0.1, persona=_COUNCIL_A_PERSONA),
        _CouncilMember(name="A2-balanced",
                       temperature=0.2, persona=_COUNCIL_A_PERSONA),
        _CouncilMember(name="A3-creative",
                       temperature=0.3, persona=_COUNCIL_A_PERSONA),
    ]


_COUNCIL_B_PERSONAS = (
    # 0.3 — precise data analyst
    "You are a data analyst.  Be precise, cite specific numbers when "
    "available.  Structure: key finding → supporting data → "
    "implication.  Return analysis only — no commentary about the task.",
    # 0.4 — strategic advisor
    "You are a strategic advisor.  Think about long-term implications "
    "and second-order effects.  Structure: situation → options → "
    "recommendation.  Return analysis only.",
    # 0.5 — skeptical reviewer
    "You are a skeptical reviewer.  Find gaps, risks, and what's "
    "missing.  Challenge assumptions.  Structure: what's claimed → "
    "what's missing → what could go wrong.  Return analysis only.",
)


class CouncilB(_Council):
    """Iter 212m-155 — analysis / advisory tasks.

    Three members with progressively higher temperatures to balance
    rigour (analyst) vs strategy (advisor) vs adversarial review
    (skeptic).  Scoring uses a structural heuristic — analysis has no
    binary pass/fail like code tests, so we credit numbers, structure,
    appropriate length, and the presence of an actionable conclusion.

    Iter 212m-159 — mode="analysis" instead of "chat".  When
    COUNCIL_B_GLM_ENABLED=true, services/llm.py routes analysis to
    GLM-5.2 (reasoning model) with DeepSeek V3 rescue.  When the flag
    is False, mode="analysis" falls through to the same DeepSeek path
    as mode="chat", so Council B is byte-identical to legacy.
    """
    name = "B"
    members = [
        _CouncilMember(name="B1-analyst",
                       temperature=0.3, persona=_COUNCIL_B_PERSONAS[0],
                       mode="analysis", max_tokens=1200),
        _CouncilMember(name="B2-advisor",
                       temperature=0.4, persona=_COUNCIL_B_PERSONAS[1],
                       mode="analysis", max_tokens=1200),
        _CouncilMember(name="B3-skeptic",
                       temperature=0.5, persona=_COUNCIL_B_PERSONAS[2],
                       mode="analysis", max_tokens=1200),
    ]


_COUNCIL_C_PERSONAS = (
    # 0.5 — direct copywriter
    "You are a direct copywriter.  Short sentences.  Active voice.  "
    "One clear call-to-action at the end.  No fluff.  Return the "
    "final copy only.",
    # 0.6 — relationship builder
    "You are a relationship builder.  Warm, personal, shows you "
    "understand the recipient's situation.  Build trust before "
    "asking.  Return the final copy only.",
    # 0.7 — data-driven marketer
    "You are a data-driven marketer.  Lead with a specific proof "
    "point or number.  Connect it to the recipient's problem.  Then "
    "ask.  Return the final copy only.",
)


class CouncilC(_Council):
    """Iter 212m-155 — writing tasks (emails, outreach, copy).

    Three voices: direct copy / relationship / data-led.  Scoring
    favours appropriate length, presence of a CTA, personalisation,
    and avoids the weak "I"-led opening anti-pattern.
    """
    name = "C"
    members = [
        _CouncilMember(name="C1-direct",
                       temperature=0.5, persona=_COUNCIL_C_PERSONAS[0],
                       mode="chat", max_tokens=600),
        _CouncilMember(name="C2-warm",
                       temperature=0.6, persona=_COUNCIL_C_PERSONAS[1],
                       mode="chat", max_tokens=600),
        _CouncilMember(name="C3-data",
                       temperature=0.7, persona=_COUNCIL_C_PERSONAS[2],
                       mode="chat", max_tokens=600),
    ]


# ─────────────────────────────────────────────────────────────────────
#  GAP 3 — Output-type detection + CEO temperature mapping
# ─────────────────────────────────────────────────────────────────────

CEO_TEMPS: dict[str, float] = {
    "code_output":     0.0,
    "json_output":     0.0,
    "tool_call":       0.0,
    "analysis_output": 0.3,
    "plan_output":     0.4,
    "writing_output":  0.65,
    "casual_output":   0.7,
}

OUTPUT_TYPE_SIGNALS: dict[str, tuple[str, ...]] = {
    "code_output":     ("fix", "patch", "implement", "refactor",
                        "write code", "function", "class", "bug",
                        "rewrite", "vulnerability", "exploit"),
    "json_output":     ("json", "schema", "structured", "format as"),
    "analysis_output": ("analyze", "report", "summary", "insights",
                        "what is", "how many", "show me", "summarize",
                        "summarise"),
    "writing_output":  ("email", "write", "draft", "message",
                        "outreach", "copy"),
}


def detect_output_type(task: str, *, council: str | None = None) -> str:
    """Explicit output-type classifier driving the CEO's temperature.

    Counts keyword matches across each registered output type and
    picks the highest-scoring class.  Ties resolve to `code_output`
    when the council is A (safer to default to t=0.0 for code
    contexts), otherwise to `analysis_output`."""
    task_lower = (task or "").lower()
    if not task_lower:
        return "code_output" if council == "A" else "analysis_output"
    scores = {k: 0 for k in OUTPUT_TYPE_SIGNALS}
    for output_type, signals in OUTPUT_TYPE_SIGNALS.items():
        for signal in signals:
            if signal in task_lower:
                scores[output_type] += 1
    best, best_score = max(scores.items(), key=lambda kv: kv[1])
    if best_score == 0:
        return "code_output" if council == "A" else "analysis_output"
    return best


# ─────────────────────────────────────────────────────────────────────
#  CEO — picks the winning vote, uses output-aware temperature.
# ─────────────────────────────────────────────────────────────────────

class CEO:
    SCORE_FLOOR = 0.55      # any candidate ≥ this is acceptable.

    async def decide(self, *, task: str, votes: list[dict],
                     context: dict) -> dict:
        """Returns {status, output, winner, scores, ceo_picked,
                    reasoning, ceo_temp_key, ceo_temp_value}."""
        # GAP 3 — explicit output-type detection, NOT council-based assumption.
        output_type = detect_output_type(task, council=context.get("council"))
        ceo_temp    = CEO_TEMPS.get(output_type, 0.0)
        if not votes:
            return {
                "status":         "manual_review",
                "output":         None,
                "winner":         None,
                "scores":         [],
                "ceo_picked":     False,
                "reasoning":      "No council votes were cast.",
                "ceo_temp_key":   output_type,
                "ceo_temp_value": ceo_temp,
            }
        usable = [v for v in votes if v.get("output") and v.get("score", 0) > 0]
        scores = [
            {"member": v["member"], "score": v["score"],
             "temp":   v["temp"],   "len":   len(v.get("output") or ""),
             "error":  v.get("error")}
            for v in votes
        ]
        if not usable:
            logger.warning("CEO deciding — temp %.2f — but no usable votes",
                           ceo_temp)
            return {
                "status":         "manual_review",
                "output":         None,
                "winner":         None,
                "scores":         scores,
                "ceo_picked":     False,
                "reasoning":      "All council members refused or errored.",
                "ceo_temp_key":   output_type,
                "ceo_temp_value": ceo_temp,
            }
        # Heuristic pick: best score, ties broken by lowest temperature.
        usable.sort(key=lambda v: (-v["score"], v["temp"]))
        winner = usable[0]
        logger.info(
            "Council %s winner: member %s — score %.2f (temp %.1f)",
            context.get("council", "A"), winner["member"],
            winner["score"], winner["temp"],
        )
        if winner["score"] >= self.SCORE_FLOOR:
            logger.info("CEO deciding — temp %.2f — accepting %s @ %.2f "
                        "(output_type=%s)",
                        ceo_temp, winner["member"], winner["score"],
                        output_type)
            return {
                "status":         "success",
                "output":         winner["output"],
                "winner":         winner["member"],
                "scores":         scores,
                "ceo_picked":     True,
                "reasoning":      (
                    f"Winner {winner['member']} scored {winner['score']:.2f} "
                    f">= floor {self.SCORE_FLOOR}.  Output type: "
                    f"{output_type}, CEO temp: {ceo_temp}"
                ),
                "ceo_temp_key":   output_type,
                "ceo_temp_value": ceo_temp,
            }
        # Below floor — invoke the LLM judge to break ties.
        ceo_pick = await self._llm_judge(
            task=task, candidates=usable, context=context,
            temperature=ceo_temp,
        )
        if ceo_pick is not None:
            chosen = usable[ceo_pick]
            return {
                "status":         "success",
                "output":         chosen["output"],
                "winner":         chosen["member"],
                "scores":         scores,
                "ceo_picked":     True,
                "reasoning":      "Below floor — CEO LLM picked best of class.",
                "ceo_temp_key":   output_type,
                "ceo_temp_value": ceo_temp,
            }
        return {
            "status":         "manual_review",
            "output":         None,
            "winner":         None,
            "scores":         scores,
            "ceo_picked":     False,
            "reasoning":      "All candidates below acceptance floor and CEO "
                              "judge could not break the tie.",
            "ceo_temp_key":   output_type,
            "ceo_temp_value": ceo_temp,
        }

    async def _llm_judge(self, *, task: str, candidates: list[dict],
                         context: dict, temperature: float) -> Optional[int]:
        if not candidates:
            return None

        def _excerpt(s):
            return (s or "")[:800]

        choices_text = "\n\n".join(
            f"--- CANDIDATE {i} (member={c['member']}, score={c['score']:.2f}) ---\n"
            f"{_excerpt(c['output'])}"
            for i, c in enumerate(candidates)
        )
        sys = (
            "You are the CEO of an engineering council.  Three members "
            "proposed candidate file contents for a task.  Pick the "
            "best one.  Reply ONLY with the candidate index (single "
            "digit, 0/1/2).  No explanation, no JSON, no commentary."
        )
        usr = f"TASK:\n{task[:1500]}\n\nCANDIDATES:\n{choices_text}"
        content, _ms, err = await _ceo_judge_call_with_rescue(
            system=sys, user=usr, max_tokens=8,
            user_id=context.get("user_id"),
            temperature=temperature,
            trace_metadata={
                "trace_id":   context.get("parliament_trace_id"),
                "council":    context.get("council"),
                "n_candidates": len(candidates),
                "task_type":  context.get("task_type"),
            },
        )
        if err:
            logger.warning("CEO judge LLM error: %s", err)
            return None
        m = re.search(r"\d", content)
        if not m:
            return None
        idx = int(m.group(0))
        return idx if 0 <= idx < len(candidates) else None


# ─────────────────────────────────────────────────────────────────────
#  Iter 212m-159 — CEO judge primary+rescue wrapper.
# ─────────────────────────────────────────────────────────────────────
async def _ceo_judge_call_with_rescue(
    *, system: str, user: str, max_tokens: int,
    user_id: Optional[str], temperature: float,
    trace_metadata: dict,
) -> tuple[str, float, Optional[str]]:
    """CEO judge LLM call with optional DeepSeek rescue.

    When `CEO_RESCUE_ENABLED=False` (default): single call to
    `_llm_call_protected` with the legacy params (mode=chat, review_mode=swift
    → GLM-5.2 primary via the V2 routing).

    When True (V2): wrap the primary call in `CEO_PRIMARY_TIMEOUT_S` seconds.
    On TimeoutError OR empty content, issue a second call with the rescue
    model (DeepSeek V3 by default) under a separate Langfuse span
    `parliament.ceo.rescue`.  This eliminates the single-point-of-failure
    that the CEO was previously.

    Returns the same (content, latency_ms, err_tag) tuple shape as
    `_llm_call_protected`.
    """
    from services.llm import CEO_RESCUE_ENABLED, CEO_PRIMARY_TIMEOUT_S

    md_primary = {**trace_metadata, "ceo_role": "primary", "ceo_rescue_enabled": CEO_RESCUE_ENABLED}

    if not CEO_RESCUE_ENABLED:
        return await _llm_call_protected(
            system=system, user=user, max_tokens=max_tokens,
            mode="chat", review_mode="swift",
            user_id=user_id, temperature=temperature,
            trace_name="parliament.ceo.judge",
            trace_metadata=md_primary,
        )

    # V2 — primary with hard timeout
    primary_task = _llm_call_protected(
        system=system, user=user, max_tokens=max_tokens,
        mode="chat", review_mode="swift",
        user_id=user_id, temperature=temperature,
        trace_name="parliament.ceo.judge",
        trace_metadata=md_primary,
    )
    t0 = time.monotonic()
    primary_timed_out = False
    primary_err: Optional[str] = None
    primary_content = ""
    primary_latency = 0.0
    try:
        primary_content, primary_latency, primary_err = await asyncio.wait_for(
            primary_task, timeout=CEO_PRIMARY_TIMEOUT_S
        )
    except asyncio.TimeoutError:
        primary_timed_out = True
        primary_err = "primary_timeout"
        primary_latency = round((time.monotonic() - t0) * 1000, 1)
        logger.warning(
            "CEO judge primary (GLM-5.2) exceeded %.1fs — firing DeepSeek rescue",
            CEO_PRIMARY_TIMEOUT_S,
        )

    # Decide whether to rescue: timeout OR primary failed OR empty content
    needs_rescue = primary_timed_out or bool(primary_err) or not (primary_content or "").strip()
    if not needs_rescue:
        return primary_content, primary_latency, None

    md_rescue = {
        **trace_metadata,
        "ceo_role":          "rescue",
        "rescue_reason":     "timeout" if primary_timed_out else (primary_err or "empty"),
        "primary_latency_ms": primary_latency,
    }
    # DeepSeek rescue via mode="chat" (no review_mode → bypasses GLM, uses DeepSeek)
    rescue_content, rescue_latency, rescue_err = await _llm_call_protected(
        system=system, user=user, max_tokens=max_tokens,
        mode="chat", review_mode="",
        user_id=user_id, temperature=temperature,
        trace_name="parliament.ceo.rescue",
        trace_metadata=md_rescue,
    )
    if rescue_err or not (rescue_content or "").strip():
        # Both primary and rescue failed → return whichever has signal.
        if (primary_content or "").strip():
            return primary_content, primary_latency, None
        return "", rescue_latency, (rescue_err or "rescue_empty")
    return rescue_content, rescue_latency, None


# ─────────────────────────────────────────────────────────────────────
#  GAP 2 — Self-Heal (caller owns the round counter)
# ─────────────────────────────────────────────────────────────────────

class SelfHeal:
    """Healer used by Verify phase to recover linter failures.

    Critical contract: this class **never** adds its own retry counter
    on top of the caller's.  The caller (loop_engine._do_verify)
    passes `round_num` and `max_rounds`; this class enforces only the
    contract supplied.  Without this, loop_engine's existing 2-round
    loop + a parliament internal round = 4 rounds total which is
    undefined behaviour."""

    SYS_PROMPT = (
        "You are ORA in self-heal mode.  A file you wrote failed static "
        "analysis.  Rewrite ONLY the file content to fix the reported "
        "errors.  Do not add commentary.  Do not wrap in code fences.  "
        "Preserve all existing functionality that wasn't responsible "
        "for the failure."
    )

    async def heal(self, *, task: str, all_attempts: list[dict],
                   round_num: int,
                   max_rounds: int = 2) -> dict:
        """Heal a file based on the failing attempts so far.

        `all_attempts` is a list of `{output, score, error}` dicts —
        the most recent is the current broken state.  `round_num` is
        the heal-round counter (caller-owned).  `max_rounds` is the
        caller's hard ceiling.  This class respects it; no internal
        retry is added.

        Returns: {status, output, round_num, max_rounds, temp_used,
                  reason}.  Status is `"retry"` (caller may re-verify),
                  `"escalate"` (caller's max reached or LLM gave up),
                  or `"circuit_open"` (breaker tripped — caller should
                  use its legacy fallback path).
        """
        if round_num >= max_rounds:
            return {
                "status":     "escalate",
                "output":     None,
                "round_num":  round_num,
                "max_rounds": max_rounds,
                "temp_used":  0.0,
                "reason":     "caller max rounds reached",
            }
        if not all_attempts:
            return {
                "status":     "retry",
                "output":     None,
                "round_num":  round_num,
                "max_rounds": max_rounds,
                "temp_used":  0.0,
                "reason":     "no attempts supplied",
            }
        # Circuit breaker — bail out cheaply if upstream LLM is sick.
        if not _GLOBAL_BREAKER.should_attempt():
            return {
                "status":     "circuit_open",
                "output":     None,
                "round_num":  round_num,
                "max_rounds": max_rounds,
                "temp_used":  0.0,
                "reason":     "global circuit breaker is OPEN",
            }

        last = all_attempts[-1]
        history_block = ""
        if len(all_attempts) > 1:
            history_block = "\n\n--- PRIOR FAILED ATTEMPTS ---\n"
            for i, prev in enumerate(all_attempts[:-1], start=1):
                err = (prev.get("error") or "")[:240]
                history_block += f"Attempt {i} error: {err}\n"
            history_block += "Do NOT repeat the same fix.\n"
        # Temperature escalation per round — bounded.
        temp = min(0.05 + 0.15 * round_num, 0.35)
        user_msg = (
            task + history_block + (
                f"\n\n--- CURRENT CONTENT ---\n{last.get('output', '')[:6000]}\n"
                f"--- END CONTENT ---\n\n"
                f"--- LAST ERROR ---\n{last.get('error', '')[:1000]}\n"
                f"--- END ERROR ---\n\nReturn the corrected file content only."
            )
        )
        content, _ms, err = await _llm_call_protected(
            system=self.SYS_PROMPT, user=user_msg,
            max_tokens=2500, mode="code", review_mode="pro",
            temperature=temp,
            trace_name="parliament.selfheal",
            trace_metadata={
                "round_num":   round_num,
                "max_rounds":  max_rounds,
                "temp":        temp,
            },
        )
        if err:
            return {
                "status":     "escalate",
                "output":     None,
                "round_num":  round_num,
                "max_rounds": max_rounds,
                "temp_used":  temp,
                "reason":     f"llm:{err}",
            }
        out = _strip_fences(content)
        if not out:
            return {
                "status":     "escalate",
                "output":     None,
                "round_num":  round_num,
                "max_rounds": max_rounds,
                "temp_used":  temp,
                "reason":     "empty_output",
            }
        return {
            "status":     "retry",
            "output":     out,
            "round_num":  round_num,
            "max_rounds": max_rounds,
            "temp_used":  temp,
            "reason":     None,
        }


# ─────────────────────────────────────────────────────────────────────
#  Parliament — top-level entry point
# ─────────────────────────────────────────────────────────────────────

class Parliament:
    """Top-level orchestrator.  Use `await Parliament().run(task, context)`."""

    def __init__(self, db=None):
        self.router    = TaskRouter()
        self.councils  = {"A": CouncilA(), "B": CouncilB(), "C": CouncilC()}
        self.ceo       = CEO()
        self.healer    = SelfHeal()
        self._db       = db
        self._breaker  = _GLOBAL_BREAKER

    # ── Public API ────────────────────────────────────────────────
    async def run(self, task: str, context: dict | None = None) -> dict:
        """Run a Council vote + CEO decision for a task.

        Returns: {status, output, winner, scores, ceo_picked,
                  reasoning, council, gateway_ms, trace_id,
                  circuit_breaker_state, circuit_breaker_fallback}
        """
        t0 = time.monotonic()
        ctx = dict(context or {})
        # GAP 4 — distributed tracing.
        trace_id = str(uuid.uuid4())[:8]
        ctx["parliament_trace_id"] = trace_id

        # Iter 212m-153 — top-level Langfuse span so every child LLM
        # observation (council members, CEO judge, self-heal, fallback)
        # rolls up into a single trace per Parliament.run().  Silent
        # no-op when Langfuse is disabled.
        parent_meta = {
            "trace_id":        trace_id,
            "task_type":       ctx.get("task_type"),
            "user_id":         ctx.get("user_id"),
            "tenant_id":       ctx.get("tenant_id"),
            "loop_session_id": ctx.get("loop_session_id"),
            "file_path":       ctx.get("file_path"),
        }
        with trace_llm(
            "parliament.run",
            input={"task_preview": (task or "")[:600]},
            metadata=parent_meta,
            as_type="chain",
        ) as parent_span:
            decision = await self._run_inner(task, ctx, t0, trace_id)
            try:
                parent_span.set_output({
                    "status":      decision.get("status"),
                    "winner":      decision.get("winner"),
                    "council":     decision.get("council"),
                    "ceo_picked":  decision.get("ceo_picked"),
                })
                parent_span.set_metadata({
                    "gateway_ms":               decision.get("gateway_ms"),
                    "ceo_temp_key":             decision.get("ceo_temp_key"),
                    "circuit_breaker_state":    decision.get("circuit_breaker_state"),
                    "circuit_breaker_fallback": decision.get("circuit_breaker_fallback"),
                })
            except Exception:
                pass
            return decision

    async def _run_inner(self, task: str, ctx: dict, t0: float,
                          trace_id: str) -> dict:
        """Inner pipeline kept separate from the Langfuse parent span
        wrapper so the existing logic stays untouched and testable."""
        council_name = self.router.route(task, ctx)
        ctx["council"] = council_name
        self._log_event("route", trace_id, ctx, {
            "council":   council_name,
            "task_type": ctx.get("task_type"),
            "task_preview": (task or "")[:120],
        })

        # GAP 1 — circuit breaker check.  If the upstream LLM is sick,
        # skip the council fan-out entirely and fall back to a single
        # protected LLM call.  This guarantees Loop Mode never fully
        # stops because of a transient provider issue.
        if not self._breaker.should_attempt():
            logger.warning(
                "[parliament %s] circuit breaker OPEN — using single-call "
                "fallback (trace=%s)", council_name, trace_id,
            )
            self._log_event("circuit_open_fallback", trace_id, ctx, {
                "state": self._breaker.state,
                "stats": self._breaker.stats(),
            })
            return await self._fallback_single_call(
                task=task, context=ctx, trace_id=trace_id, started=t0,
            )

        council = self.councils[council_name]
        member_temps = [m.temperature for m in council.members]
        self._log_event("council_start", trace_id, ctx, {
            "temps":       member_temps,
            "member_count": len(council.members),
            "circuit_state": self._breaker.state,
        })

        votes = await council.vote(task=task, context=ctx)
        self._log_event("council_done", trace_id, ctx, {
            "vote_count":  len(votes),
            "all_scores": [
                {"member": v["member"], "score": v["score"], "temp": v["temp"],
                 "error":  v.get("error")}
                for v in votes
            ],
        })

        decision = await self.ceo.decide(task=task, votes=votes, context=ctx)
        gateway_ms = round((time.monotonic() - t0) * 1000, 1)
        decision["council"]    = council_name
        decision["gateway_ms"] = gateway_ms
        decision["trace_id"]   = trace_id
        decision["circuit_breaker_state"]    = self._breaker.state
        decision["circuit_breaker_fallback"] = False
        decision["circuit_breaker_stats"]    = self._breaker.stats()

        self._log_event("ceo_decision", trace_id, ctx, {
            "status":         decision["status"],
            "winner":         decision.get("winner"),
            "ceo_picked":     decision.get("ceo_picked"),
            "ceo_temp_key":   decision.get("ceo_temp_key"),
            "ceo_temp_value": decision.get("ceo_temp_value"),
        })
        self._log_event("final", trace_id, ctx, {
            "status":      decision["status"],
            "duration_ms": gateway_ms,
            "council":     council_name,
        })
        # Aggregate row for analytics dashboards (kept for backward
        # compatibility with the original Iter 212m-150 schema).
        await self._log_aggregate(task=task, context=ctx,
                                  votes=votes, decision=decision,
                                  trace_id=trace_id)
        return decision

    # ── Fallback path when circuit breaker is OPEN (GAP 1) ────────
    async def _fallback_single_call(self, *, task: str, context: dict,
                                     trace_id: str, started: float) -> dict:
        """Single low-temperature LLM call to keep Loop Mode alive
        when the council fan-out would just multiply failures."""
        content, latency_ms, err = await _llm_call_protected(
            system=_COUNCIL_A_PERSONA, user=task,
            max_tokens=4000, mode="code", review_mode="pro",
            user_id=context.get("user_id"),
            temperature=0.1,
            trace_name="parliament.fallback_single",
            trace_metadata={
                "trace_id":   trace_id,
                "reason":     "circuit_breaker_open",
                "council":    context.get("council"),
                "task_type":  context.get("task_type"),
            },
        )
        gateway_ms = round((time.monotonic() - started) * 1000, 1)
        if err or not content.strip():
            decision = {
                "status":                  "manual_review",
                "output":                  None,
                "winner":                  None,
                "scores":                  [],
                "ceo_picked":              False,
                "reasoning":               f"Circuit-breaker fallback also failed: {err}",
                "council":                 context.get("council", "A"),
                "gateway_ms":              gateway_ms,
                "trace_id":                trace_id,
                "circuit_breaker_state":   self._breaker.state,
                "circuit_breaker_fallback": True,
                "circuit_breaker_stats":   self._breaker.stats(),
                "ceo_temp_key":            "code_output",
                "ceo_temp_value":          0.0,
            }
        else:
            out = _strip_fences(content)
            decision = {
                "status":                  "success",
                "output":                  out,
                "winner":                  "fallback-single",
                "scores":                  [{
                    "member":  "fallback-single",
                    "score":   _score_output(out, task_type="code_fix"),
                    "temp":    0.1,
                    "len":     len(out),
                    "error":   None,
                }],
                "ceo_picked":              False,
                "reasoning":               "Circuit-breaker fallback — "
                                           "council bypassed; single LLM call.",
                "council":                 context.get("council", "A"),
                "gateway_ms":              gateway_ms,
                "trace_id":                trace_id,
                "circuit_breaker_state":   self._breaker.state,
                "circuit_breaker_fallback": True,
                "circuit_breaker_stats":   self._breaker.stats(),
                "ceo_temp_key":            "code_output",
                "ceo_temp_value":          0.0,
            }
        self._log_event("final", trace_id, context, {
            "status":                  decision["status"],
            "duration_ms":             gateway_ms,
            "circuit_breaker_fallback": True,
        })
        await self._log_aggregate(task=task, context=context, votes=[],
                                  decision=decision, trace_id=trace_id)
        return decision

    # ── Logging helpers (GAP 4) ───────────────────────────────────
    def _log_event(self, event: str, trace_id: str,
                   context: dict, data: dict) -> None:
        """Non-blocking distributed-trace event.  Fire-and-forget so
        Mongo latency never blocks the LLM pipeline."""
        if self._db is None:
            return
        entry = {
            "trace_id":        trace_id,
            "event":           event,
            "data":            data,
            "ts":              time.time(),
            "loop_session_id": context.get("loop_session_id"),
            "user_id":         context.get("user_id"),
            "tenant_id":       context.get("tenant_id"),
            "file_path":       context.get("file_path"),
            "council":         context.get("council"),
        }
        try:
            asyncio.create_task(self._db.parliament_log.insert_one(entry))
        except Exception as e:                             # noqa: BLE001
            logger.debug("parliament_log event=%s insert failed: %r",
                         event, e)

    async def _log_aggregate(self, *, task: str, context: dict,
                              votes: list[dict], decision: dict,
                              trace_id: str) -> None:
        """The single roll-up row callers like analytics dashboards
        can scan without re-assembling individual trace events."""
        if self._db is None:
            return
        try:
            await self._db.parliament_log.insert_one({
                "event":           "aggregate",
                "trace_id":        trace_id,
                "loop_session_id": context.get("loop_session_id"),
                "user_id":         context.get("user_id"),
                "tenant_id":       context.get("tenant_id"),
                "file_path":       context.get("file_path"),
                "council":         decision.get("council") or context.get("council"),
                "task_type":       context.get("task_type"),
                "task_preview":    (task or "")[:200],
                "status":          decision.get("status"),
                "winner":          decision.get("winner"),
                "scores":          decision.get("scores"),
                "ceo_picked":      decision.get("ceo_picked"),
                "reasoning":       decision.get("reasoning"),
                "gateway_ms":      decision.get("gateway_ms"),
                "ceo_temp_key":    decision.get("ceo_temp_key"),
                "ceo_temp_value":  decision.get("ceo_temp_value"),
                "circuit_breaker_state":     decision.get("circuit_breaker_state"),
                "circuit_breaker_fallback":  decision.get("circuit_breaker_fallback"),
                "ts":              time.time(),
            })
        except Exception as e:                             # noqa: BLE001
            logger.debug("parliament_log aggregate insert failed: %r", e)


__all__ = [
    "Parliament", "TaskRouter", "CEO", "SelfHeal",
    "CouncilA", "CouncilB", "CouncilC",
    "ParliamentCircuitBreaker",
    "CEO_TEMPS", "OUTPUT_TYPE_SIGNALS", "detect_output_type",
    "MAX_CONCURRENT_LLM_CALLS",
]
