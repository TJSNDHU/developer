"""
services/scaffold_llm.py — Iter 212m-236 — Tier 2

Parliament-driven scaffold generation for the Personal Track.

Iter 212m-231 shipped a heuristic file tree (README + template load +
stack-specific stubs). That produced a runnable *template* but not
a **custom** app — every user got the same 12 files regardless of
their brief. Tier 2 wires Parliament in so the user's plain-English
brief actually shapes what gets generated: routes, models, UI copy.

Design principles
=================
1. **Non-blocking fallback.** If Parliament fails (network, quota, or
   malformed JSON) we fall back to the heuristic scaffolder — the
   user still gets a working draft, just less customised. The UX
   never breaks.
2. **Bounded output.** LLM must return ≤ `_MAX_FILES_PER_DRAFT` files.
   We enforce it locally too; anything beyond gets truncated with a
   `truncated=True` flag so the UI can offer a follow-up round.
3. **Reference boilerplate.** The stack's boilerplate skeleton is
   passed to the LLM as "seed material" so it always produces
   compatible code (uses the same aurem_db_client shape, same auth
   pattern, same env vars).
4. **Deterministic file-shape output.** We use a strict JSON contract
   — `{"files": [{"path": "...", "content": "..."}]}` — and reject
   anything that doesn't parse.
5. **Cost accounting.** Every generation logs an event so
   financials.py can price this feature into the paid tier.

Public API
==========
    generate_scaffold_via_parliament(
        brief: str, stack: str, user_id: str, draft_id: str,
    ) -> list[dict] | None
        Returns the file list on success, None on failure (caller
        falls back).
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Optional

logger = logging.getLogger(__name__)

# Same cap the router enforces, duplicated here as a defence-in-depth.
_MAX_FILES_PER_DRAFT = 20

# Cost accounting — a scaffold generation with the "code" mode uses
# Claude Sonnet with ~4-6K tokens per call. This constant surfaces on
# the admin financials dashboard.
COST_USD_PER_SCAFFOLD_GENERATION = 0.08

# Only files with these extensions may be emitted by the LLM.  Anything
# else is dropped silently — this stops the LLM from producing binaries
# or path-traversal shenanigans (../../etc/passwd).
_ALLOWED_EXTENSIONS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".vue", ".html", ".css",
    ".json", ".yml", ".yaml", ".md", ".txt", ".env.example",
    ".gitignore", ".sh", ".Dockerfile", "Dockerfile",
}


def _path_is_safe(path: str) -> bool:
    """Reject any path with directory traversal or absolute anchors.
    Additionally require a recognised extension OR a bare filename
    like `Dockerfile` / `.gitignore`. Never allow path components
    that start with a dot except the whitelisted `.env.example` and
    `.gitignore`."""
    if not path or ".." in path or path.startswith("/"):
        return False
    parts = path.split("/")
    if any(p == "" for p in parts):
        return False
    for p in parts[:-1]:
        if p.startswith("."):
            return False
    leaf = parts[-1]
    if leaf in ("Dockerfile", ".gitignore", ".env.example"):
        return True
    for ext in _ALLOWED_EXTENSIONS:
        if leaf.endswith(ext):
            return True
    return False


def _build_system_prompt(stack: str, boilerplate_hint: str) -> str:
    return f"""You are a senior full-stack engineer generating a starter
project for a **non-technical** user who has just described their
idea. Your only output is a JSON object shaped exactly like:

  {{"files": [{{"path": "relative/path.ext", "content": "..."}}]}}

Rules:
- **Stack**: {stack}. Every file must fit this stack.
- **Runnable**: the project must run with a single `docker compose up`.
- **Auth included**: include a JWT-based signup + login endpoint and a
  minimal UI that uses it. Never use plaintext passwords.
- **Data access**: use the AUREM shared-Mongo REST SDK (`aurem_db_client`
  for Python, `aurem-db` for JS) instead of a raw Mongo connection.
- **File count**: return AT MOST {_MAX_FILES_PER_DRAFT} files.
- **No markdown** in file contents — write real code.
- **Path safety**: relative paths only. Never `..` and never absolute.
- **Content length**: keep every file focused, ≤ 200 lines each.
- **No comments explaining the JSON contract itself** — just emit code.

Reference skeleton (use these files as a starting shape, but shape the
routes/models/UI to fit the user's brief):

```
{boilerplate_hint[:4000]}
```

Return ONLY the JSON object. No prose, no markdown fences, no explanation.
"""


def _boilerplate_hint(stack: str) -> str:
    """Load a small excerpt of the stack's boilerplate as reference.
    Used to keep the LLM's output shape compatible with our runtime
    (aurem_db_client, JWT auth pattern, docker-compose port layout)."""
    import os
    root = os.path.dirname(__file__)
    tpl_dir = os.path.abspath(os.path.join(
        root, "..", "templates", "stacks", stack, "boilerplate",
    ))
    if not os.path.isdir(tpl_dir):
        return ""
    hint_parts: list[str] = []
    for dirpath, _dirs, filenames in os.walk(tpl_dir):
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, tpl_dir)
            try:
                with open(full, "r", encoding="utf-8") as f:
                    content = f.read()
            except (OSError, UnicodeDecodeError):
                continue
            hint_parts.append(f"--- {rel} ---\n{content[:800]}\n")
            if sum(len(p) for p in hint_parts) > 4000:
                break
        if sum(len(p) for p in hint_parts) > 4000:
            break
    return "\n".join(hint_parts)


def _strip_code_fences(raw: str) -> str:
    """LLMs sometimes wrap JSON in triple-backtick fences even when
    told not to. Strip them if present."""
    s = (raw or "").strip()
    # Match ```json ... ``` or ``` ... ```
    m = re.match(r"^```(?:json)?\s*\n(.+?)\n```\s*$", s, re.DOTALL)
    return m.group(1) if m else s


def _parse_llm_response(raw: str) -> Optional[list[dict]]:
    """Parse the LLM's `{"files": [...]}` payload with best-effort
    recovery — trim fences, tolerate trailing prose after the closing
    brace."""
    cleaned = _strip_code_fences(raw)
    # Try full parse first.
    try:
        obj = json.loads(cleaned)
    except json.JSONDecodeError:
        # Try grabbing just the first top-level object.
        m = re.search(r'(\{.*"files".*\})', cleaned, re.DOTALL)
        if not m:
            return None
        try:
            obj = json.loads(m.group(1))
        except json.JSONDecodeError:
            return None
    files = obj.get("files") if isinstance(obj, dict) else None
    if not isinstance(files, list):
        return None
    out: list[dict] = []
    for f in files:
        if not isinstance(f, dict): continue
        path    = f.get("path")
        content = f.get("content")
        if not isinstance(path, str) or not isinstance(content, str):
            continue
        if not _path_is_safe(path):
            logger.warning("[scaffold-llm] dropped unsafe path: %r", path)
            continue
        out.append({"path": path, "content": content})
    return out or None


async def _log_generation_event(
    user_id: str, draft_id: str, stack: str, brief: str,
    tokens_used: int, model_used: str,
) -> None:
    """Best-effort accounting log — one document per generation into
    `db.scaffold_generations` so financials.py can aggregate spend."""
    try:
        from cto_services.db import get_db
        db = get_db()
        if db is None: return
        await db.scaffold_generations.insert_one({
            "user_id":     user_id,
            "draft_id":    draft_id,
            "stack":       stack,
            "brief_first_80": brief[:80],
            "tokens_used": int(tokens_used or 0),
            "model_used":  model_used or "unknown",
            "cost_usd":    COST_USD_PER_SCAFFOLD_GENERATION,
            "created_at":  time.time(),
        })
    except Exception as e:                                # noqa: BLE001
        logger.warning("[scaffold-llm] accounting log failed: %r", e)


async def generate_scaffold_via_parliament(
    brief: str,
    stack: str,
    user_id: str,
    draft_id: str,
) -> Optional[list[dict]]:
    """Ask Parliament to produce a file tree tailored to `brief`.

    Returns:
        list[dict] — [{"path": "...", "content": "..."}] on success.
        None       — any failure (caller falls back to heuristic).

    Never raises — a failure here MUST NOT break the scaffold endpoint.
    """
    try:
        from services.llm import call_llm_with_meta
    except Exception:                                     # noqa: BLE001
        logger.warning("[scaffold-llm] llm module not importable — fallback")
        return None

    system = _build_system_prompt(stack, _boilerplate_hint(stack))
    user_msg = (
        f"User's idea (verbatim, do not paraphrase):\n\n"
        f"{brief.strip()[:2000]}\n\n"
        f"Generate the project files now."
    )
    try:
        result = await call_llm_with_meta(
            system=system, user=user_msg,
            max_tokens=6000, mode="code",
            user_id=user_id,
        )
    except Exception as e:                                # noqa: BLE001
        logger.warning("[scaffold-llm] LLM call raised: %r — fallback", e)
        return None

    content = (result or {}).get("content") or ""
    model   = (result or {}).get("model_used") or "unknown"
    tokens  = (result or {}).get("tokens_used") or 0
    if not content.strip():
        logger.warning("[scaffold-llm] empty LLM response — fallback")
        return None

    files = _parse_llm_response(content)
    if not files:
        logger.warning("[scaffold-llm] unparseable LLM response — fallback")
        return None

    # Enforce the file cap here as defence-in-depth (the router also
    # truncates but we want the accounting number to reflect what we
    # actually kept).
    if len(files) > _MAX_FILES_PER_DRAFT:
        logger.info("[scaffold-llm] truncating %d → %d files",
                    len(files), _MAX_FILES_PER_DRAFT)
        files = files[:_MAX_FILES_PER_DRAFT]

    await _log_generation_event(
        user_id=user_id, draft_id=draft_id,
        stack=stack, brief=brief,
        tokens_used=tokens, model_used=model,
    )
    logger.info("[scaffold-llm] draft=%s user=%s stack=%s files=%d model=%s",
                draft_id, user_id, stack, len(files), model)
    return files


__all__ = [
    "COST_USD_PER_SCAFFOLD_GENERATION",
    "generate_scaffold_via_parliament",
]
