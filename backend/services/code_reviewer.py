"""
services/code_reviewer.py — ORA Two-Agent Maxx quality gate.

DeepSeek generates code (fast/cheap) → Claude reviews it (precision).
The frontend stays on the ORActo brand; this is invisible plumbing.

Wire-in: routers/cto_projects.py::_run_task_via_api
  - Call AFTER DeepSeek codegen, BEFORE gh_api_commit()
  - Safe fallback: any Claude failure returns {pass:True} so the
    commit pipeline is NEVER blocked by a reviewer outage.

NOTE: adapted to AUREM's call_llm_with_meta() which returns a dict
(ok/content/provider/...) rather than a bare string.
"""
from __future__ import annotations
import logging
from typing import Optional

from services.llm import call_llm_with_meta

logger = logging.getLogger(__name__)

REVIEWER_SYSTEM_PROMPT = """You are a Senior Technical Reviewer for an autonomous code-generation pipeline.

Your ONLY job: check the generated code for correctness before it gets committed to GitHub.

Check for:
1. Truncated or incomplete functions / placeholders like `// rest unchanged` or `# ... existing code ...`
2. Missing imports or undefined variables
3. Syntax errors
4. Logic that does NOT match the user's stated intent
5. Broken indentation or formatting that would cause runtime errors

RESPONSE RULES — follow exactly:
- If code is clean and correct: reply with exactly one word → PASS
- If code has issues: output ONLY the corrected file blocks in this format:

FILE: path/to/file.py
```
<corrected full file content>
```

FILE: path/to/other.py
```
<corrected full file content>
```

No explanations. No preamble. No "Here is the fix". Just PASS or the corrected FILE blocks.
"""


async def review_code_with_claude(
    file_blocks: dict,
    user_intent: str,
    repo_ctx: str,
) -> dict:
    """Sends DeepSeek-generated code to Claude for review.

    Args:
        file_blocks: {filepath: content} parsed from DeepSeek output
        user_intent: original task description
        repo_ctx:    short repo identifier e.g. "owner/repo@main"

    Returns:
        {pass: bool, corrected: dict|None, issues: list, raw_response: str}
    """
    if not file_blocks:
        return {"pass": True, "corrected": None, "issues": [], "raw_response": ""}

    user_msg = (
        f"User's intent: {user_intent}\n\n"
        f"Repository: {repo_ctx}\n\n"
        f"Generated code to review:\n\n"
        f"{_format_file_blocks(file_blocks)}"
    )

    try:
        resp = await call_llm_with_meta(
            system=REVIEWER_SYSTEM_PROMPT,
            user=user_msg,
            mode="review",         # routes to Claude Sonnet via Emergent
            max_tokens=4096,
        )
    except Exception as e:
        logger.warning("[code_reviewer] reviewer call raised: %r — defaulting to PASS", e)
        return {"pass": True, "corrected": None, "issues": [], "raw_response": ""}

    # call_llm_with_meta returns a dict; if it failed, default to PASS so
    # the commit pipeline is never blocked by an upstream Claude outage.
    if not resp.get("ok"):
        logger.warning("[code_reviewer] reviewer not ok (%s) — defaulting to PASS",
                       resp.get("error"))
        return {"pass": True, "corrected": None, "issues": [], "raw_response": ""}

    raw = (resp.get("content") or "").strip()

    if raw.upper() == "PASS" or raw.upper().startswith("PASS\n"):
        return {"pass": True, "corrected": None, "issues": [], "raw_response": raw}

    corrected = _parse_file_blocks(raw)
    if not corrected:
        logger.info("[code_reviewer] reviewer returned unparseable output, "
                    "committing original. head=%r", raw[:200])
        return {
            "pass": True, "corrected": None,
            "issues": ["Reviewer output unparseable — kept original"],
            "raw_response": raw,
        }

    return {
        "pass": False,
        "corrected": corrected,
        "issues": _extract_issues(raw),
        "raw_response": raw,
    }


def _format_file_blocks(file_blocks: dict) -> str:
    parts = [f"FILE: {p}\n```\n{c}\n```" for p, c in file_blocks.items()]
    return "\n\n".join(parts)


def _parse_file_blocks(raw: str) -> Optional[dict]:
    """Parses Claude's `FILE: <path>` ```...``` blocks back into a dict."""
    result: dict = {}
    current_file: Optional[str] = None
    in_block = False
    block_lines: list = []

    for line in raw.split("\n"):
        if line.startswith("FILE:"):
            if current_file and block_lines:
                result[current_file] = "\n".join(block_lines).strip()
                block_lines = []
                in_block = False
            current_file = line.replace("FILE:", "").strip()
        elif line.strip().startswith("```") and current_file:
            if not in_block:
                in_block = True
            else:
                result[current_file] = "\n".join(block_lines).strip()
                block_lines = []
                in_block = False
        elif in_block:
            block_lines.append(line)

    if current_file and block_lines and current_file not in result:
        result[current_file] = "\n".join(block_lines).strip()

    return result or None


def _extract_issues(raw: str) -> list:
    """Best-effort comment scrape — purely for logging."""
    issues: list = []
    for line in raw.split("\n"):
        ls = line.strip()
        if ls.startswith("#") or ls.startswith("//"):
            issues.append(ls[:200])
    return issues[:10]
