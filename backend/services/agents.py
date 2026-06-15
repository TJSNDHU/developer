"""
services/agents.py — Iter 165

Specialised agents for AUREM's coding pipeline. Each agent uses the
right model from smart_router so token cost matches task complexity.

Public agents:
  ReaderAgent      — reads repo files (cheapest, used by all modes)
  CoderAgent       — writes code from scratch (mode-aware model)
  ReviewerAgent    — reviews existing code, returns corrected text
  SecurityAgent    — scans for secrets / SQLi / XSS (always Claude)
  CoordinatorAgent — orchestrates the others; exposes:
      .review_tail(content, prompt, mode, file_path) — drop-in
        replacement for the legacy _swift_diff_review /
        _pro_parallel_review tail in orchestrator.py
      .run(task, file_contents, file_path) — full pipeline for
        direct callers (e.g. future cto_projects task pipeline)

All model calls go through llm.call_openrouter_model so we share the
single httpx client, the OpenRouter auth path, and the
LLM_HTTP_TIMEOUT_S tuning. Failures degrade gracefully — every agent
returns a safe value (original code, empty findings) rather than
raising, so the chat path can never be broken by a flaky reviewer.
"""
from __future__ import annotations
import asyncio
import json
import logging
from typing import Optional

from .smart_router import get_model, get_budget, get_provider_name, MODELS

logger = logging.getLogger(__name__)


async def _call(
    model: str,
    system: str,
    user: str,
    max_tokens: int,
    temperature: float,
    timeout_s: float = 25.0,
) -> str:
    """Single entry point — delegates to llm.call_openrouter_model
    with a hard `asyncio.wait_for` ceiling so a hung upstream cannot
    block the orchestrator past the per-turn budget."""
    from .llm import call_openrouter_model
    try:
        return await asyncio.wait_for(
            call_openrouter_model(
                model=model, system=system, user=user,
                max_tokens=max_tokens, temperature=temperature,
            ),
            timeout=timeout_s,
        )
    except asyncio.TimeoutError:
        logger.warning("agents: model %s timed out after %.1fs", model, timeout_s)
        return ""
    except Exception as e:
        logger.warning("agents: model %s failed: %r", model, e)
        # One fallback retry on the cheap deepseek model — keeps the
        # chat alive when Kimi is flaky.
        try:
            return await asyncio.wait_for(
                call_openrouter_model(
                    model=MODELS["fallback"], system=system, user=user,
                    max_tokens=max_tokens, temperature=temperature,
                ),
                timeout=timeout_s,
            )
        except Exception:
            return ""


# ── ReaderAgent ──────────────────────────────────────────────────────

class ReaderAgent:
    """Reads repo files using the cheapest model. Returns a short
    factual summary scoped to the caller's question."""

    async def summarize(self, path: str, content: str, question: str) -> str:
        return await _call(
            model=get_model("read"),
            system=(
                "You are a code reader. Given a file's content, answer "
                "the question concisely. Be factual. Quote actual code "
                "when relevant. Max 200 words."
            ),
            user=f"File: {path}\n\nContent:\n{content[:4000]}\n\nQuestion: {question}",
            max_tokens=get_budget("read"),
            temperature=0.0,
            timeout_s=15.0,
        )


# ── CoderAgent ───────────────────────────────────────────────────────

class CoderAgent:
    """Writes code. Model picked per mode by smart_router."""

    async def write(self, task: str, context: str, mode: str = "swift") -> str:
        provider = get_provider_name("code", mode)
        logger.info("CoderAgent[%s] using %s", mode, provider)
        return await _call(
            model=get_model("code", mode),
            system=(
                "You are an expert software engineer. Write clean, "
                "production-ready code. Return ONLY the code — no "
                "explanation, no markdown fences unless showing a "
                "complete file. If fixing a bug, return the complete "
                "fixed file."
            ),
            user=f"Context:\n{context[:3000]}\n\nTask: {task}",
            max_tokens=get_budget("code", mode),
            temperature=0.15,
            timeout_s=25.0,
        )


# ── ReviewerAgent ────────────────────────────────────────────────────

class ReviewerAgent:
    """Reviews existing code. Swift = diff-only (cheap), Pro = full
    correction. Maxx skips review entirely (Claude wrote it).

    Returns (corrected_or_original_code, was_corrected).
    Falls back to (original, False) on any error so a flaky reviewer
    never breaks the chat path.
    """

    async def review(
        self, code: str, task: str, mode: str = "swift"
    ) -> tuple[str, bool]:
        if mode == "maxx" or not code or len(code) < 150:
            return code, False

        model = get_model("review", mode)
        budget = get_budget("review", mode)

        if mode == "swift":
            system = (
                "Find REAL bugs only (wrong logic, bad import, "
                "security hole, syntax error). "
                "Reply format:\n"
                "PASS   ← if no bugs\n"
                "LINE <n> | wrong: <code> | right: <code>  "
                "← one line per bug, max 5\n"
                "No prose. No explanation. Just PASS or diffs."
            )
        else:  # pro
            system = (
                "Review this code for bugs. If correct, reply exactly: PASS\n"
                "If bugs found, reply: FIX:\n"
                "Then write ONLY the corrected code. One pass only. "
                "No style nitpicks."
            )

        result = await _call(
            model=model, system=system,
            user=f"Task: {task[:200]}\n\nCode:\n{code[:3000]}",
            max_tokens=budget, temperature=0.0,
            timeout_s=12.0 if mode == "swift" else 18.0,
        )

        if not result or result.strip().upper().startswith("PASS"):
            return code, False

        if mode == "pro" and result.strip().startswith("FIX:"):
            fixed = result.split("FIX:", 1)[1].strip()
            return (fixed, True) if len(fixed) > 50 else (code, False)

        if mode == "swift" and "LINE" in result.upper():
            # Apply diffs via a cheap CoderAgent pass — staying inside
            # the Swift cost envelope.
            coder = CoderAgent()
            fixed = await coder.write(
                task=f"Apply these fixes:\n{result}",
                context=code,
                mode="swift",
            )
            return (fixed, True) if fixed and len(fixed) > 100 else (code, False)

        return code, False


# ── SecurityAgent ────────────────────────────────────────────────────

class SecurityAgent:
    """Scans code for security issues. ALWAYS Claude.

    Returns list of {severity, line, issue}. Empty list = clean.
    Malformed JSON or upstream failure → [] (never raises).
    """

    async def scan(self, code: str, file_path: str = "") -> list[dict]:
        if not code or len(code) < 80:
            return []
        result = await _call(
            model=get_model("security"),
            system=(
                "You are a security scanner. Check for: hardcoded "
                "secrets, SQL injection, XSS, insecure auth, exposed "
                "API keys, path traversal. Reply in JSON only:\n"
                '[{"severity":"HIGH","line":23,"issue":"hardcoded API key"}]\n'
                "If clean, reply exactly: []\n"
                "JSON only. No prose. No code fences."
            ),
            user=f"File: {file_path or '(unknown)'}\n\nCode:\n{code[:4000]}",
            max_tokens=get_budget("security"),
            temperature=0.0,
            timeout_s=12.0,
        )
        if not result:
            return []
        # Tolerate accidental ```json fences from over-eager models.
        text = result.strip()
        if text.startswith("```"):
            text = text.strip("`")
            if "\n" in text:
                text = text.split("\n", 1)[1]
            text = text.rsplit("```", 1)[0].strip()
        try:
            findings = json.loads(text)
            return findings if isinstance(findings, list) else []
        except Exception:
            return []


# ── CoordinatorAgent ─────────────────────────────────────────────────

class CoordinatorAgent:
    """Orchestrates Reader/Coder/Reviewer/Security agents.

    Two integration points:

    1. `review_tail(content, prompt, mode, file_path)` — drop-in
       replacement for the orchestrator's legacy review tail. Runs
       Reviewer + Security in parallel, returns
       `(corrected_content, was_reviewed, providers_used, findings)`.

    2. `run(task, file_contents, file_path)` — full pipeline for
       direct callers that have file contents in hand (future
       cto_projects task pipeline).
    """

    def __init__(self, mode: str = "swift"):
        self.mode = mode
        self.reader = ReaderAgent()
        self.coder = CoderAgent()
        self.reviewer = ReviewerAgent()
        self.security = SecurityAgent()

    # ── Review tail — used by orchestrator.py ───────────────────────

    async def review_tail(
        self,
        content: str,
        prompt: str,
        file_path: str = "",
    ) -> dict:
        """Run Reviewer + Security in parallel on already-generated
        content. Maxx mode skips Reviewer (Claude wrote it).

        Returns dict with:
          content              — possibly-corrected code
          was_reviewed         — True if Reviewer produced a fix
          providers_used       — ["Kimi K2.5", "Claude Sonnet"] etc.
          security_findings    — list of {severity, line, issue}
        """
        providers: list[str] = []

        if self.mode == "maxx":
            # Claude wrote the code; only Security scan needed.
            findings = await self.security.scan(content, file_path)
            providers.append("Claude Sonnet (security)")
            return {
                "content": content,
                "was_reviewed": False,
                "providers_used": providers,
                "security_findings": findings,
            }

        review_coro = self.reviewer.review(
            code=content, task=prompt, mode=self.mode,
        )
        security_coro = self.security.scan(content, file_path)
        (corrected, was_reviewed), findings = await asyncio.gather(
            review_coro, security_coro,
        )

        out_content = corrected if was_reviewed else content
        if was_reviewed:
            providers.append(get_provider_name("review", self.mode))
        providers.append("Claude Sonnet (security)")

        return {
            "content": out_content,
            "was_reviewed": was_reviewed,
            "providers_used": providers,
            "security_findings": findings,
        }

    # ── Full pipeline — for direct callers ──────────────────────────

    async def run(
        self,
        task: str,
        file_contents: dict[str, str],
        file_path: str = "",
    ) -> dict:
        """Read files → write code → review + security (parallel).
        Returns dict: {code, was_reviewed, security_findings,
        providers_used, ok}."""
        providers_used: list[str] = []

        # Step 1 — context
        context_parts: list[str] = []
        ctx_len = 0
        for path, raw in (file_contents or {}).items():
            chunk = f"=== {path} ===\n{(raw or '')[:2000]}"
            if ctx_len + len(chunk) > 6000:
                break
            context_parts.append(chunk)
            ctx_len += len(chunk)
        context = "\n\n".join(context_parts)

        # Step 2 — write
        code = await self.coder.write(task=task, context=context, mode=self.mode)
        providers_used.append(get_provider_name("code", self.mode))
        if not code:
            return {
                "code": "", "was_reviewed": False,
                "security_findings": [],
                "providers_used": providers_used,
                "ok": False,
            }

        # Step 3 — review + security in parallel
        tail = await self.review_tail(
            content=code, prompt=task, file_path=file_path,
        )
        providers_used.extend(tail["providers_used"])
        return {
            "code": tail["content"],
            "was_reviewed": tail["was_reviewed"],
            "security_findings": tail["security_findings"],
            "providers_used": providers_used,
            "ok": True,
        }
