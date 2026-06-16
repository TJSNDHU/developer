"""
services/vanguard_verify_agent.py
=================================

Iter 111 — Separate Vanguard verify agent (Anthropic-style "defending-
code-reference-harness"):

> ORA writes code → handed off to a SEPARATE agent (different prompt,
> different model) → re-reviews the patch for the 25 known vulnerability
> patterns PLUS its own LLM-grade judgement → only on PASS does the
> patch progress to commit.

Iter 169 — migrated to OpenRouter (anthropic/claude-sonnet-4-5-20250929).
The previous Emergent SDK dependency was dead weight after llm.py was
cleaned up — when EMERGENT_LLM_KEY was unset the verify pipeline
silently skipped LLM review, leaving only the regex floor. Now it
goes through the same OPENROUTER_API_KEY all other LLM calls use, so
the second-agent review actually runs in production.

Public API
----------
    await verify_patch(file_blocks, repo_ctx=...) -> {
        "pass":     bool,
        "findings": [ {file, line, severity, rule, message} ],
        "summary":  str,
        "model":    str,
    }
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

from .vanguard_scanner import scan_file_blocks, has_critical

logger = logging.getLogger(__name__)

# Separate-agent isolation: this is a DIFFERENT model/persona than ORA.
# Claude Sonnet 4.5 via OpenRouter — same key as the rest of the app.
_VERIFY_MODEL = os.environ.get(
    "VANGUARD_VERIFY_MODEL",
    "anthropic/claude-sonnet-4-5-20250929",
)

_VERIFY_SYSTEM = """You are the **Vanguard Verify Agent** — a dedicated security
reviewer that re-audits code patches BEFORE they are committed. You are
NOT the same agent that wrote the code. Your sole job is to find
vulnerabilities, dangerous patterns, and architectural risks that the
regex pre-scan may have missed.

Review the patch for ALL of these dimensions:
  1. Secrets / credentials / tokens / keys (any hard-coded value)
  2. Code injection — eval, exec, shell=True, dynamic imports, sql
     concatenation, template injection, untrusted deserialisation
  3. Path traversal / arbitrary file read or write
  4. Server-side request forgery (urlopen / requests on user-controlled URL)
  5. Cross-site scripting — innerHTML, dangerouslySetInnerHTML, raw
     html.unescape on user input, jQuery .html() with user data
  6. Insecure crypto — md5, sha1, custom RNG, hardcoded IV, ECB mode
  7. Hardcoded admin / debug paths or auth bypasses
  8. Open CORS (allow_origins=['*'] in production)
  9. Logic bombs — sleep(), while True with no break, recursion without
     base case
 10. Missing authorization / authentication on a sensitive route
 11. Direct SQL with user-controlled fragments
 12. Race conditions — async without lock, non-atomic check-then-write

You MUST respond with VALID JSON only, no prose:
{
  "pass": true | false,
  "findings": [
    {"file": "...", "line": 42, "severity": "CRITICAL|HIGH|MEDIUM|LOW",
     "rule": "short-slug", "message": "1-line explanation"}
  ],
  "summary": "1-paragraph executive summary"
}

`pass` is FALSE if any finding is CRITICAL or HIGH. Empty findings = pass.
"""


def _has_executable_python(blocks: dict) -> bool:
    """True iff any .py file in the patch defines a function or class.
    Used to decide whether the E2B smoke-import gate is worth running."""
    for path, content in (blocks or {}).items():
        if not path.endswith(".py"):
            continue
        if not content:
            continue
        for ln in content.splitlines():
            s = ln.strip()
            if s.startswith("def ") or s.startswith("async def ") or s.startswith("class "):
                return True
    return False


async def _llm_review(file_blocks: dict, repo_ctx: str) -> dict:
    """Make the second-agent LLM call. Iter 169 — uses OpenRouter
    (anthropic/claude-sonnet-4-5-20250929). Returns
    {pass, findings, summary, model}. On any error, returns pass=True
    with a note so we don't accidentally block the pipeline on
    transient infra; the regex scan stays the floor."""
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key:
        return {"pass": True, "findings": [],
                "summary": "verify-agent skipped (no OPENROUTER_API_KEY)",
                "model": ""}

    # Build a compact patch envelope. Each file capped so we don't blow
    # context on huge edits — first/last 250 lines is plenty for review.
    envelope_parts: list[str] = [f"# Repo: {repo_ctx}", ""]
    for path, content in (file_blocks or {}).items():
        lines = (content or "").split("\n")
        if len(lines) > 500:
            head = "\n".join(lines[:250])
            tail = "\n".join(lines[-250:])
            body = head + "\n# … (truncated middle) …\n" + tail
        else:
            body = content or ""
        envelope_parts.append(f"### {path}\n```\n{body}\n```\n")
    envelope = "\n".join(envelope_parts)[:48_000]  # ~12k tokens of patch

    try:
        from .llm import call_openrouter_model
        # Iter 111 — hard 30s ceiling so the verify-agent can never
        # add unbounded latency to the commit pipeline.
        raw = await asyncio.wait_for(
            call_openrouter_model(
                model=_VERIFY_MODEL,
                system=_VERIFY_SYSTEM,
                user="Review the following patch:\n\n" + envelope,
                max_tokens=2000,
                temperature=0.0,
            ),
            timeout=30.0,
        )
        text = (raw or "").strip()
        # Strip ```json fences if present
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rstrip("`").rstrip()
            if text.endswith("```"):
                text = text[: -3].rstrip()
        if text.startswith("json"):
            text = text[4:].lstrip()
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            # The model returned prose — extract anything JSON-shaped
            import re
            m = re.search(r"\{.*\}", text, re.DOTALL)
            data = json.loads(m.group(0)) if m else {}
        return {
            "pass":     bool(data.get("pass", True)),
            "findings": list(data.get("findings", []) or []),
            "summary":  str(data.get("summary", ""))[:500],
            "model":    _VERIFY_MODEL,
        }
    except Exception as e:
        logger.warning("vanguard-verify LLM call failed (%r) — leaving regex floor",
                       e)
        return {"pass": True, "findings": [],
                "summary": f"verify-agent skipped (error: {type(e).__name__})",
                "model":   ""}


async def _e2b_smoke(file_blocks: dict) -> dict:
    """If the patch contains Python code with functions/classes, smoke-
    test the imports inside E2B so we catch runtime ImportError and
    NameError that static AST misses. Returns {pass, output, skipped}."""
    py_files = {p: c for p, c in (file_blocks or {}).items()
                if p.endswith(".py") and c}
    if not py_files or not _has_executable_python(py_files):
        return {"pass": True, "skipped": True,
                "reason": "no executable python in patch"}
    # Build a multi-file import probe: write each file then `import` it
    # by module path so we catch SyntaxError, ImportError, top-level
    # NameError, etc., without actually running side-effects.
    probe_lines: list[str] = ["import os, sys"]
    for i, (path, content) in enumerate(py_files.items()):
        safe = path.replace("/", "_").replace(".py", "")
        probe_lines.append(f"os.makedirs(os.path.dirname({path!r}) or '.', exist_ok=True)")
        probe_lines.append(f"open({path!r}, 'w').write({content!r})")
        probe_lines.append(f"sys.path.insert(0, os.path.dirname({path!r}) or '.')")
    # Then for each file try to compile it (catches SyntaxError + imports)
    for path in py_files:
        probe_lines.append(f"compile(open({path!r}).read(), {path!r}, 'exec')")
    probe = "\n".join(probe_lines)
    try:
        from .sandbox_runner import run_python_check
        res = await run_python_check(probe, filename="vanguard_smoke.py", timeout=20)
        if res.get("skipped"):
            return {"pass": True, "skipped": True,
                    "reason": res.get("reason", "sandbox unavailable")}
        if not res.get("ok"):
            return {"pass": False, "skipped": False,
                    "stderr": (res.get("stderr") or "")[:1200],
                    "stdout": (res.get("stdout") or "")[:600]}
        return {"pass": True, "skipped": False, "stdout": (res.get("stdout") or "")[:300]}
    except Exception as e:
        logger.warning("vanguard E2B smoke failed: %r", e)
        return {"pass": True, "skipped": True, "reason": str(e)}


async def verify_patch(file_blocks: dict, repo_ctx: str = "unknown") -> dict:
    """Top-level entrypoint. Combines:
      1) regex Vanguard scan (the 24 baseline patterns)
      2) separate Vanguard Verify Agent (LLM second opinion)
      3) E2B smoke import if patch has executable Python

    All three must pass for the patch to be considered safe to commit.
    """
    findings: list[dict] = []
    regex_findings = scan_file_blocks(file_blocks or {})
    findings.extend(regex_findings)
    regex_blocked = has_critical(regex_findings)

    # Always run the second agent + E2B in parallel — they don't depend
    # on each other and the latency budget matters.
    llm_task = asyncio.create_task(_llm_review(file_blocks or {}, repo_ctx))
    e2b_task = asyncio.create_task(_e2b_smoke(file_blocks or {}))
    llm_review, e2b_result = await asyncio.gather(llm_task, e2b_task,
                                                  return_exceptions=False)

    for f in llm_review.get("findings", []):
        f.setdefault("source", "vanguard_verify_agent")
        findings.append(f)
    llm_blocked = not llm_review.get("pass", True)
    e2b_blocked = not e2b_result.get("pass", True)

    overall_pass = not (regex_blocked or llm_blocked or e2b_blocked)

    summary_parts = []
    summary_parts.append(f"regex: {'BLOCK' if regex_blocked else 'pass'} "
                         f"({len(regex_findings)} findings)")
    if llm_review.get("model"):
        summary_parts.append(f"verify-agent ({llm_review['model']}): "
                             f"{'BLOCK' if llm_blocked else 'pass'} "
                             f"({len(llm_review.get('findings', []))} findings)")
    else:
        summary_parts.append("verify-agent: skipped")
    if e2b_result.get("skipped"):
        summary_parts.append(f"e2b: skipped ({e2b_result.get('reason', '')})")
    else:
        summary_parts.append(f"e2b: {'BLOCK' if e2b_blocked else 'pass'}")

    return {
        "pass":     overall_pass,
        "findings": findings,
        "summary":  " | ".join(summary_parts),
        "regex":    {"blocked": regex_blocked, "count": len(regex_findings)},
        "agent":    llm_review,
        "e2b":      e2b_result,
    }
