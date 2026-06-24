"""Iter 212m-12 — Friendly task-failure translator.

Hybrid pipeline:

  1. **Static catalog** — fast regex match against ~20 common
     failure shapes (PAT issues, GitHub merge conflicts, OpenRouter
     rate-limits, network/DNS, vault, etc.). Returns plain Hinglish
     + a concrete "what to do" step list. Zero LLM cost.

  2. **LLM fallback** — when no static match wins, ship the raw
     error string to Claude Haiku via the Emergent LLM key with a
     tight 200-token budget. Forces a stable JSON shape so the
     frontend can render the same `(plain, steps, suggestion)`
     contract regardless of how the failure originated.

Failure-mode philosophy: NEVER throw. If the LLM rewrite breaks
we fall back to a tiny generic message — it's better to render
*something* friendly than to wedge the worker on a translator
crash.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────
# Static catalog — ordered: most-specific patterns first.
# Each entry maps a regex match to a {plain, steps, suggestion} dict.
# ──────────────────────────────────────────────────────────────────

_RULES: List[Dict[str, Any]] = [
    # ── GitHub PAT issues ─────────────────────────────────────────
    {
        "pattern": re.compile(r"no\s+pat\b|pat\s+missing|missing\s+pat", re.I),
        "plain":   "Project pe GitHub access token (PAT) save nahi hai — isiliye ORA tumhare repo pe likh nahi paaya.",
        "steps":   [
            "Projects page open karo",
            "Apne project ke Edit (pencil) button pe click karo",
            "GitHub PAT field mein apna fine-grained token paste karo (https://github.com/settings/personal-access-tokens/new)",
            "Save karke wapas chat pe aao aur ye task retry karo",
        ],
        "suggestion": "PAT mein 'Contents: Read & write' aur 'Metadata: Read' permission zaroor honi chahiye.",
    },
    {
        "pattern": re.compile(r"invalid[_\s-]?token|401|bad\s+credentials", re.I),
        "plain":   "GitHub ne tumhara PAT reject kar diya — ya expire ho gaya hai, ya galat hai.",
        "steps":   [
            "GitHub → Settings → Developer settings → Personal access tokens",
            "Purane PAT ko delete karke naya fine-grained token banao",
            "Repository access scope mein sirf is project ka repo select karo",
            "Permissions: Contents (Read & write) + Metadata (Read)",
            "Project Edit dialog mein naya PAT paste karke retry karo",
        ],
        "suggestion": "Fine-grained PAT recommended hai — classic token avoid karo, woh broad access deta hai.",
    },
    {
        "pattern": re.compile(r"missing[_\s-]?scope|insufficient\s+scope|forbidden|403", re.I),
        "plain":   "PAT to valid hai but iss repo pe write permission nahi hai.",
        "steps":   [
            "GitHub Settings → Personal access tokens → apna PAT edit karo",
            "Repository access section mein is project ka repo add karo",
            "Permissions mein 'Contents' ko 'Read and write' karo",
            "Save token → wapas yahan retry button dabao",
        ],
        "suggestion": "Permission change save karne ke baad PAT instantly active ho jaata hai, nayi PAT generate karne ki zarurat nahi.",
    },

    # ── Repo / branch problems ───────────────────────────────────
    {
        "pattern": re.compile(r"repo[_\s-]?not[_\s-]?found|404.*repo|repository.*not\s+found", re.I),
        "plain":   "GitHub pe ye repository milti hi nahi — ya rename ho chuki hai ya delete ho gayi.",
        "steps":   [
            "GitHub pe jaake confirm karo ki repo abhi bhi exist karta hai aur naam wahi hai",
            "Agar rename hua hai: Projects → Edit → repo name update karo",
            "Agar delete ho gaya: project hata do aur naya banao",
        ],
        "suggestion": "Owner/org name bhi check karo — agar tumne handle change kiya hai to woh bhi sync karna padega.",
    },
    {
        "pattern": re.compile(r"branch[_\s-]?not[_\s-]?found|branch.*not\s+exist", re.I),
        "plain":   "Jis branch pe ORA push karne wala tha woh ab repo mein hai hi nahi.",
        "steps":   [
            "GitHub pe repo → branches list dekho",
            "Confirm karo ki target branch (jaise 'main' ya 'develop') exist karti hai",
            "Project Edit dialog mein correct branch name set karo aur retry",
        ],
        "suggestion": "Default branch 'main' set rakhna safest hai — kuch repos abhi bhi 'master' use karte hain.",
    },
    {
        "pattern": re.compile(r"merge\s+conflict|conflict.*on\s+push|409|fast[_\s-]?forward", re.I),
        "plain":   "Tumhare branch pe ORA ke working copy ke baad koi naya commit aa gaya — isliye push reject ho gaya.",
        "steps":   [
            "Locally repo pull karke check karo ki kya naya commit hai",
            "Decide karo: chahiye to manual merge karo, ya is task ko retry karo (ORA fresh fetch karega)",
            "Retry button dabao — ORA latest HEAD se dobara plan karega",
        ],
        "suggestion": "Agar tum aur ORA same branch pe parallel kaam kar rahe ho to chhoti tasks rakhna better hai — long-running tasks aksar conflict hit karte hain.",
    },

    # ── Rate-limits / LLM gateway ────────────────────────────────
    {
        "pattern": re.compile(r"429|rate[_\s-]?limit|too\s+many\s+requests", re.I),
        "plain":   "AI provider (OpenRouter) ne thoda load throttle kiya — ek pal mein wapas free ho jaayega.",
        "steps":   [
            "2-3 minute wait karo",
            "Phir Retry dabao — backend khud waapas connect kar lega",
        ],
        "suggestion": "Agar baar-baar 429 aata hai aur tum heavy user ho, Pro/Team plan pe priority queue milti hai jo public throttle bypass karti hai.",
    },
    {
        "pattern": re.compile(r"openrouter.*5\d\d|llm.*5\d\d|upstream.*5\d\d|bad\s+gateway|503", re.I),
        "plain":   "AI provider ka apna server abhi hicchki maar raha — humari taraf se kuch fix nahi karna.",
        "steps":   [
            "1-2 minute wait karo",
            "Retry karo — OpenRouter usually 30-60 seconds mein recover ho jaata hai",
            "Agar 5 minute baad bhi same error, status.openrouter.ai pe check karo",
        ],
        "suggestion": "Persistent ho to chat ke top-right Mode selector se Pro → Swift switch karke try karo — kabhi-kabhi sirf ek specific model down hota hai.",
    },

    # ── Network / DNS ────────────────────────────────────────────
    {
        "pattern": re.compile(r"timeout|timed\s+out|deadline\s+exceeded|connection.*reset", re.I),
        "plain":   "Network slow tha aur ek request bich mein hi kat gayi.",
        "steps":   [
            "Apna internet briefly check karo",
            "Retry karo — kaafi baar same network blip do baar nahi aati",
        ],
        "suggestion": "Agar har task pe timeout aa raha hai to repo size bahut bada ho sakta hai — small focused tasks (1-2 files) zyada reliable rehte hain.",
    },
    {
        "pattern": re.compile(r"dns|getaddrinfo|name\s+resolution|unreachable", re.I),
        "plain":   "Backend GitHub ya OpenRouter ke server tak pahonch hi nahi paaya.",
        "steps":   [
            "Apni internet connection check karo",
            "VPN on hai to off karke try karo (kuch VPNs github.com block kar dete hain)",
            "Retry button dabao",
        ],
        "suggestion": "Office/college networks me kabhi-kabhi outbound HTTPS blocked hota hai — agar persistent ho, mobile hotspot pe ek test karo.",
    },

    # ── Vault / encryption ───────────────────────────────────────
    {
        "pattern": re.compile(r"vault_unavailable|master[_\s-]?key|fernet|decrypt.*fail", re.I),
        "plain":   "Server pe encryption key configured nahi hai — admin ko set karni padegi.",
        "steps":   [
            "Ye admin-side issue hai, tum kuch nahi kar sakte",
            "polarisbuiltinc@gmail.com pe ek line bhejo: 'Vault unavailable error on task XYZ'",
            "Admin AUREM_CTO_MASTER_KEY env var set karke service restart kar dega",
        ],
        "suggestion": "Agar tum apna instance khud chala rahe ho (self-host), `backend/.env` mein `AUREM_CTO_MASTER_KEY` ek fernet key set karo (Python: `Fernet.generate_key()`).",
    },

    # ── Token / wallet exhausted ─────────────────────────────────
    {
        "pattern": re.compile(r"token.*exhaust|wallet.*empty|tokens?.*remaining.*0|out\s+of\s+tokens", re.I),
        "plain":   "Is mahine ka task quota khatam ho gaya hai.",
        "steps":   [
            "Pricing page pe jaao → Upgrade button dabao",
            "Pro ($19/mo) → 300 tasks, Team ($49/mo) → 400 tasks aur Maxx mode",
            "Subscription active hote hi naya quota turant unlock ho jaata hai",
        ],
        "suggestion": "Founder accounts (FOUNDER_EMAILS env var) ka quota unlimited hota hai — apne use case ke liye relevant ho to admin se baat karo.",
    },

    # ── Common test/lint ─────────────────────────────────────────
    {
        "pattern": re.compile(r"lint.*fail|eslint.*error|ruff.*error|syntax.*error", re.I),
        "plain":   "ORA ne code generate to kar diya but uska lint pass nahi hua — commit safety ke liye block kar diya.",
        "steps":   [
            "Retry button dabao — ORA second pass mein lint errors dekhke khud fix karega",
            "Agar dobara same error: chat pe likho 'fix the lint errors and try again'",
        ],
        "suggestion": "Maxx mode (Team plan) lint failures pe automatic 2-pass review karta hai — Pro/Swift mein manual retry chahiye.",
    },
]


# Generic fallback when nothing matches and LLM is also unreachable.
_GENERIC: Dict[str, Any] = {
    "plain":      "Task complete nahi ho paya — exact reason backend logs mein hai.",
    "steps":      [
        "Retry button dabao — kaafi failures one-time blips hote hain",
        "Agar dobara same error: chat mein likho 'last task kyun fail hua'",
        "Persistent ho to polarisbuiltinc@gmail.com pe error message bhej do",
    ],
    "suggestion": "Generally first retry ka success rate 60%+ hota hai.",
}


def _static_match(raw_error: str) -> Dict[str, Any] | None:
    """Return the first static rule whose regex matches the raw error."""
    if not raw_error:
        return None
    for rule in _RULES:
        if rule["pattern"].search(raw_error):
            return {
                "plain":      rule["plain"],
                "steps":      list(rule["steps"]),
                "suggestion": rule["suggestion"],
                "source":     "static_table",
            }
    return None


async def _llm_rewrite(raw_error: str) -> Dict[str, Any] | None:
    """LLM fallback. Wraps the raw error into a {plain, steps,
    suggestion} contract. Returns None on any failure — caller
    falls back to the generic template."""
    key = os.environ.get("EMERGENT_LLM_KEY") or os.environ.get("OPENROUTER_API_KEY")
    if not key:
        return None
    try:
        # Import lazily so the translator module stays cheap to import
        # on cold-start; the dependency is only touched on real failures.
        from services.llm import call_llm_with_meta
        sys_prompt = (
            "You translate raw backend error messages into a Hinglish "
            "(hindi+english) explanation for a non-technical founder. "
            "Output STRICT JSON only, no prose around it. Schema:\n"
            '{"plain": "<1-2 sentences why this failed in Hinglish>",\n'
            ' "steps": ["step 1", "step 2", "step 3"],\n'
            ' "suggestion": "<1 helpful tip in Hinglish>"}\n'
            "Constraints: total under 200 tokens, no markdown, no code "
            "fences, no extra keys. Steps must be concrete actions the "
            "user can do RIGHT NOW (not 'contact your admin' unless the "
            "error is clearly server-side)."
        )
        user_prompt = f"Raw error from backend worker:\n\n{raw_error[:1200]}"
        result = await call_llm_with_meta(
            sys_prompt, user_prompt,
            max_tokens=300, mode="chat",
        )
        text = (result.get("content") or "").strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        text = text.replace("True", "true").replace("False", "false").replace("None", "null")
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            m = re.search(r"\{.*\}", text, re.DOTALL)
            if not m:
                return None
            data = json.loads(m.group(0))
        plain = (data.get("plain") or "").strip()
        steps = data.get("steps") or []
        sugg  = (data.get("suggestion") or "").strip()
        if not plain or not isinstance(steps, list):
            return None
        return {
            "plain":      plain[:300],
            "steps":      [str(s)[:200] for s in steps[:6]],
            "suggestion": sugg[:240],
            "source":     "llm_rewrite",
        }
    except Exception as e:                # noqa: BLE001
        logger.warning("error_translator LLM rewrite failed: %r", e)
        return None


async def translate(raw_error: str | None) -> Dict[str, Any]:
    """Public entry point. Always returns a complete contract dict
    of `{plain, steps, suggestion, source, technical}` — never raises."""
    technical = (raw_error or "").strip()[:500]
    if not technical:
        return {**_GENERIC, "source": "empty", "technical": ""}

    hit = _static_match(technical)
    if hit:
        return {**hit, "technical": technical}

    llm = await _llm_rewrite(technical)
    if llm:
        return {**llm, "technical": technical}

    return {**_GENERIC, "source": "generic", "technical": technical}


__all__ = ["translate"]
