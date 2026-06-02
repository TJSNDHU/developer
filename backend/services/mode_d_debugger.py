"""
services/mode_d_debugger.py
============================
AUREM Mode D — Debug / Investigate

Flow: READ → DIAGNOSE → CONFIRM → FIX (via Mode C)

Unlike Mode C which jumps straight to writing code,
Mode D first reads the repo + error context, diagnoses
the root cause, shows the plan, waits for user confirm,
THEN triggers a Mode C commit.

F12 INTEGRATION:
  Browser console errors, network 4xx/5xx, stack traces
  are captured by the frontend snippet and sent here as
  structured payloads. ORA maps them to exact file + line.

TOKEN OPTIMIZATION:
  - Only reads files mentioned in the stack trace (not full tree)
  - Diagnosis prompt is strict: max 500 tokens output
  - No LLM call if error is a known pattern (regex fast-path)

Wire-in:
  routers/chat.py::chat_stream — detect Mode D intent,
  call run_debug_session(). Stream diagnosis back to user.
  On user confirmation ("yes fix it" / "ship it") → trigger Mode C.
"""

from __future__ import annotations
import re
import json
from datetime import datetime, timezone
from typing import Optional
from motor.motor_asyncio import AsyncIOMotorDatabase
from services.llm import call_llm_with_meta
from services.ora_council_logger import log_conversational
from services.github_api_writer import fetch_file as _gh_fetch_file
import httpx


async def read_file(repo_owner: str, repo_name: str, path: str,
                    github_pat: Optional[str]) -> Optional[str]:
    """Mode-D adapter — reads a file from GitHub at HEAD via the PAT."""
    if not (repo_owner and repo_name and github_pat):
        return None
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            return await _gh_fetch_file(
                c, repo_owner, repo_name, path, "HEAD", github_pat,
            )
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Intent detection — is this a debug request?
# ─────────────────────────────────────────────────────────────────────────────

DEBUG_SIGNALS = [
    r"\b(error|bug|broken|crash|failing|exception|traceback|stacktrace)\b",
    r"\b(why is|what's wrong|something broke|not working|keeps failing)\b",
    r"\b(500|404|422|403|401|cors|undefined is not|cannot read prop)\b",
    r"\b(fix this error|debug|investigate|trace|diagnose)\b",
    r"f12\b",
    r"console\.log|console error",
    r"stack trace|stack overflow",
    r"\[object\s+\w+\]",
    r"TypeError|ValueError|KeyError|AttributeError|ImportError",
    r"ECONNREFUSED|ETIMEDOUT|ENOTFOUND",
]

DEBUG_PATTERN = re.compile("|".join(DEBUG_SIGNALS), re.IGNORECASE)


def is_debug_request(message: str) -> bool:
    """Returns True if message looks like a debug/investigation request."""
    return bool(DEBUG_PATTERN.search(message))


# ─────────────────────────────────────────────────────────────────────────────
# F12 payload parser
# ─────────────────────────────────────────────────────────────────────────────

def parse_f12_payload(payload: dict) -> dict:
    """
    Parses the structured F12 payload sent from the browser snippet.

    Expected payload shape:
    {
      "console_errors": [
        {"type": "error", "message": "...", "source": "file.js:42", "timestamp": "..."}
      ],
      "network_errors": [
        {"url": "/api/...", "method": "POST", "status": 422, "response_body": "...", "timestamp": "..."}
      ],
      "stack_traces": ["TypeError: Cannot read...\\n  at Component (App.jsx:88)\\n ..."],
      "page_url": "https://auremcto.com/dashboard",
      "user_agent": "...",
      "captured_at": "..."
    }
    """
    console_errors = payload.get("console_errors", [])
    network_errors = payload.get("network_errors", [])
    stack_traces   = payload.get("stack_traces", [])
    page_url       = payload.get("page_url", "unknown page")

    # Extract file references from stack traces
    file_refs = []
    for trace in stack_traces:
        # Match patterns like: at Component (App.jsx:88:12)  OR  App.jsx:88
        matches = re.findall(r'[\w/.-]+\.(jsx?|tsx?|py|ts)\s*[:(]\s*(\d+)', trace)
        for m in matches:
            file_refs.append(f"{m[0]}:{m[1]}" if len(m) > 1 else m[0])

    # Extract API routes from network errors
    api_routes = []
    for ne in network_errors:
        url = ne.get("url", "")
        status = ne.get("status", 0)
        method = ne.get("method", "GET")
        if url:
            api_routes.append({"route": url, "method": method, "status": status})

    # Build compact summary
    summary_parts = []
    if console_errors:
        msgs = [e.get("message", "")[:200] for e in console_errors[:5]]
        summary_parts.append("Console errors:\n" + "\n".join(f"  • {m}" for m in msgs))

    if network_errors:
        for ne in network_errors[:5]:
            body = str(ne.get("response_body", ""))[:300]
            summary_parts.append(
                f"Network error: {ne.get('method','GET')} {ne.get('url','')} "
                f"→ {ne.get('status',0)}\n  Response: {body}"
            )

    if stack_traces:
        for trace in stack_traces[:2]:
            summary_parts.append(f"Stack trace:\n{trace[:600]}")

    return {
        "summary": "\n\n".join(summary_parts),
        "file_refs": list(set(file_refs))[:10],
        "api_routes": api_routes[:5],
        "page_url": page_url,
        "has_network_error": len(network_errors) > 0,
        "has_console_error": len(console_errors) > 0,
        "error_count": len(console_errors) + len(network_errors),
    }


def extract_error_context_from_text(user_message: str) -> dict:
    """
    Extracts error context from plain text (when user pastes error manually).
    Returns same shape as parse_f12_payload output.
    """
    # Extract file references
    file_refs = re.findall(r'[\w/.-]+\.(jsx?|tsx?|py|ts)[:(]\s*\d+', user_message)

    # Extract HTTP status codes
    statuses = re.findall(r'\b(4\d{2}|5\d{2})\b', user_message)

    # Extract API routes
    api_routes = re.findall(r'(?:GET|POST|PUT|PATCH|DELETE)\s+(/api/[\w/.-]+)', user_message, re.I)

    return {
        "summary": user_message[:1500],
        "file_refs": list(set(file_refs))[:10],
        "api_routes": [{"route": r, "method": "?", "status": 0} for r in api_routes[:5]],
        "page_url": "unknown",
        "has_network_error": bool(statuses),
        "has_console_error": True,
        "error_count": len(statuses) + len(file_refs),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Known error fast-path (no LLM needed)
# ─────────────────────────────────────────────────────────────────────────────

KNOWN_ERRORS = [
    {
        "pattern": r"CORS",
        "cause": "CORS policy blocking frontend → backend request",
        "fix": "Add CORS middleware in FastAPI: `app.add_middleware(CORSMiddleware, allow_origins=[...], allow_methods=['*'], allow_headers=['*'])`. Check that your frontend URL is in `allow_origins`.",
        "files_to_check": ["main.py", "app.py"],
        "severity": "high",
    },
    {
        "pattern": r"422 Unprocessable",
        "cause": "Request body doesn't match Pydantic schema",
        "fix": "Check the request payload matches the expected schema. Common causes: missing required field, wrong type (string sent as int), extra unexpected fields.",
        "files_to_check": ["routers/", "models/"],
        "severity": "medium",
    },
    {
        "pattern": r"Cannot read prop(?:ert(?:y|ies))? ['\"](\w+)['\"] of (undefined|null)",
        "cause": "Accessing property on undefined/null object in React",
        "fix": "Add optional chaining: `obj?.property` or check if data is loaded before rendering. Usually means API response hasn't arrived yet.",
        "files_to_check": ["components/", "pages/"],
        "severity": "medium",
    },
    {
        "pattern": r"Module not found|Cannot find module",
        "cause": "Missing npm package or wrong import path",
        "fix": "Run `npm install` or check the import path is correct (case-sensitive on Linux). Verify the package exists in package.json.",
        "files_to_check": ["package.json"],
        "severity": "medium",
    },
    {
        "pattern": r"ECONNREFUSED",
        "cause": "Backend server not running or wrong port",
        "fix": "Check your backend is running (`uvicorn main:app --port 8000`). Verify `REACT_APP_BACKEND_URL` env var points to the right port.",
        "files_to_check": [".env", ".env.local"],
        "severity": "high",
    },
    {
        "pattern": r"401 Unauthorized",
        "cause": "Missing or expired auth token",
        "fix": "Check Authorization header is being sent. Verify token hasn't expired. Check `auth.py` dependency is correctly applied to the route.",
        "files_to_check": ["routers/auth.py", "lib/api.js"],
        "severity": "high",
    },
    {
        "pattern": r"500 Internal Server Error",
        "cause": "Unhandled exception in backend",
        "fix": "Check backend logs for the actual traceback. The 500 is a symptom — the real error is in the server logs.",
        "files_to_check": ["routers/", "services/"],
        "severity": "critical",
    },
]


def fast_path_diagnosis(error_text: str) -> Optional[dict]:
    """
    Returns instant diagnosis for known error patterns.
    No LLM call — zero cost, <1ms.
    Returns None if no known pattern matched.
    """
    for known in KNOWN_ERRORS:
        if re.search(known["pattern"], error_text, re.IGNORECASE):
            return {
                "cause": known["cause"],
                "fix_suggestion": known["fix"],
                "files_to_check": known["files_to_check"],
                "severity": known["severity"],
                "fast_path": True,
                "needs_llm": False,
            }
    return None


# ─────────────────────────────────────────────────────────────────────────────
# LLM diagnosis — for unknown errors
# ─────────────────────────────────────────────────────────────────────────────

DIAGNOSIS_SYSTEM = """You are a senior debugging engineer. You receive error context from a developer's app.

Your job: identify the ROOT CAUSE and suggest the EXACT FIX.

CRITICAL ANTI-HALLUCINATION RULES (Iter 50):
  - DO NOT invent file paths. Only cite files that appear VERBATIM in the
    error context, stack traces, or file_contents below. If no real file
    is referenced, write "FILES TO CHECK: (unknown — error context too thin)".
  - DO NOT fabricate framework details. If the error doesn't mention a
    framework (React, FastAPI, etc.), don't assume one.
  - If the error context is empty, vague, or contains no real diagnostic
    signal (no status code, no stack trace, no message), output:
      ROOT CAUSE: insufficient signal to diagnose
      SEVERITY: low
      FILES TO CHECK: (none)
      FIX: Reproduce the error with a real stack trace or 4xx/5xx HTTP
           status, then re-run debug.
      NEEDS COMMIT: no
      COMMIT TASK: -
    Do not invent a plausible-sounding answer to fill the template.

Output format (strict — no other text):
ROOT CAUSE: <one sentence>
SEVERITY: critical | high | medium | low
FILES TO CHECK: <comma-separated file paths — only files that actually appear in the context>
FIX: <specific actionable fix, max 3 sentences>
NEEDS COMMIT: yes | no
COMMIT TASK: <if yes — one sentence describing exact code change needed>"""


async def llm_diagnosis(
    error_context: str,
    repo_ctx: str,
    file_contents: dict,
) -> dict:
    """
    Uses DeepSeek to diagnose an unknown error.
    Returns structured diagnosis dict.
    """
    file_section = ""
    if file_contents:
        file_section = "\n\nRelevant file contents:\n" + "\n---\n".join(
            f"FILE: {path}\n{content[:800]}"
            for path, content in list(file_contents.items())[:4]
        )

    user_msg = f"""Repo: {repo_ctx}

Error context:
{error_context[:1200]}
{file_section}"""

    try:
        resp = await call_llm_with_meta(
            system=DIAGNOSIS_SYSTEM,
            user=user_msg,
            mode="chat",
            max_tokens=500,
        )
        raw = (resp or {}).get("content", "") if isinstance(resp, dict) else str(resp or "")
    except Exception as e:
        return {
            "cause": f"Could not diagnose automatically: {e}",
            "fix_suggestion": "Check your backend logs manually for the full traceback.",
            "files_to_check": [],
            "severity": "unknown",
            "fast_path": False,
            "needs_llm": True,
            "needs_commit": False,
        }

    # Parse structured response
    result = {
        "cause": "",
        "severity": "medium",
        "files_to_check": [],
        "fix_suggestion": "",
        "needs_commit": False,
        "commit_task": "",
        "fast_path": False,
        "needs_llm": True,
    }

    for line in raw.split("\n"):
        line = line.strip()
        if line.startswith("ROOT CAUSE:"):
            result["cause"] = line.replace("ROOT CAUSE:", "").strip()
        elif line.startswith("SEVERITY:"):
            result["severity"] = line.replace("SEVERITY:", "").strip().lower()
        elif line.startswith("FILES TO CHECK:"):
            result["files_to_check"] = [
                f.strip() for f in line.replace("FILES TO CHECK:", "").split(",") if f.strip()
            ]
        elif line.startswith("FIX:"):
            result["fix_suggestion"] = line.replace("FIX:", "").strip()
        elif line.startswith("NEEDS COMMIT:"):
            result["needs_commit"] = "yes" in line.lower()
        elif line.startswith("COMMIT TASK:"):
            result["commit_task"] = line.replace("COMMIT TASK:", "").strip()

    return result


# ─────────────────────────────────────────────────────────────────────────────
# Main debug session runner
# ─────────────────────────────────────────────────────────────────────────────

async def run_debug_session(
    db: AsyncIOMotorDatabase,
    user_message: str,
    repo_owner: str,
    repo_name: str,
    repo_ctx: str,
    user_id: Optional[str] = None,
    project_id: Optional[str] = None,
    f12_payload: Optional[dict] = None,
    github_pat: Optional[str] = None,
) -> dict:
    """
    Full Mode D debug session.

    Returns:
    {
      "diagnosis": {...},
      "ora_reply": str,           # human-friendly reply to stream to user
      "can_auto_fix": bool,       # True if ORA can write a commit fix
      "commit_task": str,         # task description to pass to Mode C
      "files_to_read": list,      # files ORA wants to read
      "severity": str,
    }
    """

    # 1. Parse error context
    if f12_payload:
        error_ctx = parse_f12_payload(f12_payload)
    else:
        error_ctx = extract_error_context_from_text(user_message)

    error_text = error_ctx["summary"] or user_message

    # 2. Try fast-path first (no LLM cost)
    diagnosis = fast_path_diagnosis(error_text)

    # 3. If no fast-path, try to read relevant files then use LLM
    file_contents = {}
    if not diagnosis:
        if error_ctx["file_refs"] and github_pat:
            for ref in error_ctx["file_refs"][:3]:
                filepath = ref.split(":")[0]
                try:
                    content = await read_file(
                        repo_owner=repo_owner,
                        repo_name=repo_name,
                        path=filepath,
                        github_pat=github_pat,
                    )
                    if content:
                        file_contents[filepath] = content
                except Exception:
                    pass

        diagnosis = await llm_diagnosis(error_text, repo_ctx, file_contents)

    # 4. Build ORA reply
    severity_emoji = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"}.get(
        diagnosis.get("severity", "medium"), "🟡"
    )

    files_str = ""
    if diagnosis.get("files_to_check"):
        files_str = "\n\nFiles to check: `" + "`, `".join(diagnosis["files_to_check"][:4]) + "`"

    fix_str = diagnosis.get("fix_suggestion", "")

    can_auto_fix = diagnosis.get("needs_commit", False) and bool(diagnosis.get("commit_task"))

    if can_auto_fix:
        confirm_line = (
            f"\n\nI can fix this automatically. Want me to ship the fix?\n"
            f"Task: _{diagnosis['commit_task']}_"
        )
    else:
        confirm_line = "\n\nThis fix doesn't require a code change — you can apply it manually."

    ora_reply = (
        f"{severity_emoji} **Root cause:** {diagnosis.get('cause', 'Unknown error')}\n\n"
        f"**Fix:** {fix_str}"
        f"{files_str}"
        f"{confirm_line}"
    )

    # 5. Log as Mode D
    await log_conversational(
        db=db,
        mode="D",
        user_message=user_message,
        ora_reply=ora_reply,
        user_id=user_id,
        project_id=project_id,
    )

    return {
        "diagnosis": diagnosis,
        "ora_reply": ora_reply,
        "can_auto_fix": can_auto_fix,
        "commit_task": diagnosis.get("commit_task", ""),
        "files_to_read": diagnosis.get("files_to_check", []),
        "severity": diagnosis.get("severity", "medium"),
        "error_count": error_ctx["error_count"],
        "fast_path_used": diagnosis.get("fast_path", False),
    }
