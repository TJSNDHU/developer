"""
services/skill_context_injector.py
==================================

Iter 44 — Vanguard skill injection.

When a Mode C task is auth-related, payment-related, frontend, or any
new API endpoint, we inject a short slice of the matching Vanguard
skill file into the AI's system prompt. Zero LLM cost — pure prompt
augmentation — and ORA writes more secure code by default.

Skills sourced from Antigravity Awesome Skills (May 2026).
Stored at: /app/backend/vanguard_skills/*.md

Each skill is capped (per-skill char budget) and we inject at most
N skills per task to keep total budget < ~3K tokens.
"""
from __future__ import annotations
import os
from functools import lru_cache
from typing import List

_SKILLS_DIR = os.path.join(os.path.dirname(__file__), "..", "vanguard_skills")

# Trigger keywords → skill file, slice limit (chars)
_INJECTION_RULES: list[tuple[list[str], str, int]] = [
    (
        ["login", "auth", "jwt", "oauth", "session", "password", "signin",
         "signup", "register", "rbac", "permissions", "role-based"],
        "auth-implementation.md", 2500,
    ),
    (
        ["api", "endpoint", "route", "fastapi", "rest", "graphql",
         "rate limit", "cors", "validation"],
        "api-security.md", 2000,
    ),
    (
        ["stripe", "payment", "billing", "checkout", "invoice",
         "subscription", "webhook"],
        "api-security.md", 2000,   # api-security covers webhooks too
    ),
    (
        ["react", "component", "jsx", "tsx", "frontend", "ui", "form",
         "dangerouslysetinnerhtml", "xss"],
        "frontend-security.md", 1800,
    ),
    (
        ["backend", "fastapi", "django", "flask", "middleware", "header",
         "server", "uvicorn"],
        "backend-security.md", 1800,
    ),
]

# Maximum 2 skills per task — keeps the prompt under ~5K extra chars.
_MAX_SKILLS_PER_TASK = 2

# Iter 44 — Always-inject security checklist for Mode C (any code task).
# Cap small (1000 chars) since it's the most generic.
_ALWAYS_INJECT = ("security-review.md", 1000)


@lru_cache(maxsize=16)
def _load_skill(filename: str, char_cap: int) -> str:
    """Disk-load a skill file once, then memoise. Returns sliced content."""
    path = os.path.abspath(os.path.join(_SKILLS_DIR, filename))
    try:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
        if len(text) > char_cap:
            text = text[:char_cap] + "\n…(truncated)"
        return text
    except FileNotFoundError:
        return ""
    except Exception:
        return ""


def select_skills(task_description: str) -> List[tuple[str, str]]:
    """Returns list of (skill_name, sliced_content) to inject."""
    task = (task_description or "").lower()
    picked: list[tuple[str, str]] = []
    seen_files: set[str] = set()
    for triggers, filename, cap in _INJECTION_RULES:
        if filename in seen_files:
            continue
        if any(kw in task for kw in triggers):
            content = _load_skill(filename, cap)
            if content:
                picked.append((filename, content))
                seen_files.add(filename)
        if len(picked) >= _MAX_SKILLS_PER_TASK:
            break
    # Always tack on the small security-review checklist
    always = _load_skill(*_ALWAYS_INJECT)
    if always and _ALWAYS_INJECT[0] not in seen_files:
        picked.append((_ALWAYS_INJECT[0], always))
    return picked


def build_skill_context(task_description: str) -> str:
    """Returns a single markdown block ready to slot into a system prompt.
    Empty string when no skills match (no token overhead)."""
    picks = select_skills(task_description)
    if not picks:
        return ""
    parts = ["[VANGUARD SECURITY SKILLS — apply these patterns by default]"]
    for name, content in picks:
        title = name.replace("-", " ").replace(".md", "").upper()
        parts.append(f"\n### {title}\n{content}")
    return "\n".join(parts)
