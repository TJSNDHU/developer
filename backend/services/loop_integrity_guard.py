"""
loop_integrity_guard.py — Iter 318 · Data-loss prevention bundle

Pre-ship + verify-phase integrity guards that catch the "AI committed
`[Rest of existing README content remains unchanged...]` as literal
file content" class of data-loss defect. Live evidence:
loop_678eea28436c4e nearly wiped the whole README because the
executor emitted an elision marker instead of the full body and the
`.md → linter: skip` verify path treated skip as pass.

Spec: /app/memory/ITER_318_DATA_LOSS_SPEC.md (Bugs 1a + 1b + 2).

This module provides PURE functions. The engine wires them into
`_do_execute` (post-emission ban — Bug 1a safety net), `_do_ship`
(pre-commit gate — Bug 1b), and `_do_verify` (skip-linter → still
run these — Bug 2).

Rule 1: elision-marker regex sweep — reject if any submitted file
        body matches any of the documented placeholder shapes.
Rule 2: size-delta guard — reject if submitted body drops below
        SHRINK_FLOOR × repo body UNLESS original_request contains an
        explicit deletion / replacement intent.
Rule 3: byte-count sanity for `action == "edit"` — redundant with
        Rule 2 but catches ambiguous original_requests.
"""
from __future__ import annotations

import re
from typing import Optional


# ── Rule 1 · Elision-marker regex sweep ─────────────────────────────
# The spec ships this list as deliberately non-exhaustive because
# marker vocabulary drifts across LLM providers and prompt tweaks.
# 1a (executor ban) is the real fix; this list is defence in depth.
_ELISION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "bracket_rest_of",
        re.compile(
            r"\[\s*Rest\s+of\s+[^\]]*"
            r"(unchanged|remains|goes here|omitted|elided|truncated)"
            r"[^\]]*\]",
            re.IGNORECASE,
        ),
    ),
    (
        "ellipsis_unchanged",
        re.compile(
            r"\.{3,}\s*(unchanged|snip|omitted|remainder)",
            re.IGNORECASE,
        ),
    ),
    (
        "html_comment_snip",
        re.compile(
            r"<!--\s*(snip|elided|truncated|unchanged|omitted)\s*-->",
            re.IGNORECASE,
        ),
    ),
    (
        "double_slash_ellipsis",
        re.compile(
            r"//\s*\.{3}\s*(unchanged|remainder|omitted)",
            re.IGNORECASE,
        ),
    ),
    (
        "hash_ellipsis",
        re.compile(
            r"#\s*\.{3}\s*(rest|remainder|unchanged|omitted)",
            re.IGNORECASE,
        ),
    ),
    (
        "block_comment_snip",
        re.compile(
            r"/\*\s*(snip|elided|unchanged)\s*\*/",
            re.IGNORECASE,
        ),
    ),
    (
        "handlebars_placeholder",
        re.compile(
            r"\{\{\s*(rest|remainder|unchanged)\s*\}\}",
            re.IGNORECASE,
        ),
    ),
]


# ── Rule 2 · Deletion / replacement intent regex ───────────────────
# When the founder's original prompt explicitly requests a wipe or a
# full rewrite, a big shrink is legit. Only then is Rule 2 lifted.
_DELETION_INTENT = re.compile(
    r"\b(delete|remove|empty|clear|wipe|rewrite from scratch|"
    r"replace entirely|start over)\b",
    re.IGNORECASE,
)


# ── Threshold ──────────────────────────────────────────────────────
# Spec says 30 % floor (flag any shrink > 70 %) is defensible from
# the loop_678eea28436c4e evidence (>90 % shrink). Do NOT go below
# 20 % floor — that lets this exact bug through.
SHRINK_FLOOR: float = 0.30


def find_elision_markers(text: Optional[str]) -> list[dict]:
    """Return every elision-marker hit inside ``text``.

    Each hit is ``{"pattern": <pattern_name>, "match": <first 80 chars>}``.
    Returns an empty list when the body is clean (or empty / None).

    Used by:
      • Bug 1a — executor post-emission ban (fail the file if a match
        appears BEFORE the content lands in submitted_files).
      • Bug 1b Rule 1 — pre-ship sweep.
      • Bug 2   — skip-linter still runs this before verify.ok=True.
    """
    if not text:
        return []
    hits: list[dict] = []
    for name, pat in _ELISION_PATTERNS:
        m = pat.search(text)
        if m:
            snippet = m.group(0)[:80]
            hits.append({"pattern": name, "match": snippet})
    return hits


def has_deletion_intent(original_request: Optional[str]) -> bool:
    """True when the founder's verbatim prompt explicitly asks for
    a wipe / rewrite. Lifts Rule 2 for that path only."""
    if not original_request:
        return False
    return bool(_DELETION_INTENT.search(original_request))


def size_delta_violation(
    *,
    submitted_bytes: int,
    repo_bytes: int,
    original_request: Optional[str],
    action: Optional[str] = "edit",
    shrink_floor: float = SHRINK_FLOOR,
) -> Optional[dict]:
    """Rule 2 + Rule 3.

    Returns ``None`` when the size delta is safe. Otherwise returns
    a violation blob:

        {"rule_fired": "size_delta"|"byte_count",
         "submitted_bytes": int, "repo_bytes": int,
         "shrink_ratio": float, "shrink_floor": float}

    A brand-new file (``repo_bytes == 0``) can never trip either
    rule — you can't shrink zero.
    """
    if repo_bytes <= 0:
        return None
    ratio = submitted_bytes / repo_bytes if repo_bytes else 1.0

    # Rule 3 — non-deletion action ("edit") is stricter: byte floor
    # applies even when original_request has ambiguous wording.
    if (action or "").lower() == "edit" and ratio < shrink_floor:
        return {
            "rule_fired":      "byte_count",
            "submitted_bytes": submitted_bytes,
            "repo_bytes":      repo_bytes,
            "shrink_ratio":    round(ratio, 4),
            "shrink_floor":    shrink_floor,
            "action":          action,
        }

    # Rule 2 — general size-delta gate. Skipped when the founder
    # explicitly said "delete / rewrite from scratch / …".
    if ratio < shrink_floor and not has_deletion_intent(original_request):
        return {
            "rule_fired":      "size_delta",
            "submitted_bytes": submitted_bytes,
            "repo_bytes":      repo_bytes,
            "shrink_ratio":    round(ratio, 4),
            "shrink_floor":    shrink_floor,
            "action":          action,
        }
    return None


def check_file_integrity(
    *,
    path: str,
    submitted_content: str,
    repo_bytes: int,
    original_request: Optional[str],
    action: Optional[str] = "edit",
    shrink_floor: float = SHRINK_FLOOR,
) -> Optional[dict]:
    """Combined Rule 1 + Rule 2 + Rule 3 check for a single file.

    Returns ``None`` when the file is safe to ship. Otherwise
    returns the founder-visible violation blob:

        {"rule_fired":      "elision_marker" | "size_delta" | "byte_count",
         "offending_path":  <str>,
         "marker_pattern":  <name>       # only for elision_marker
         "marker_text":     <first 80 chars>  # only for elision_marker
         "submitted_bytes": <int>,       # size_delta / byte_count
         "repo_bytes":      <int>,
         "shrink_ratio":    <float>,
         "shrink_floor":    <float>,
         "action":          <str>}

    Rule 1 takes priority — elision markers are the P0 defect.
    """
    hits = find_elision_markers(submitted_content)
    if hits:
        # First hit only — keeps the founder-visible blob tight.
        first = hits[0]
        return {
            "rule_fired":     "elision_marker",
            "offending_path": path,
            "marker_pattern": first["pattern"],
            "marker_text":    first["match"],
        }

    sd = size_delta_violation(
        submitted_bytes=len(submitted_content or ""),
        repo_bytes=repo_bytes,
        original_request=original_request,
        action=action,
        shrink_floor=shrink_floor,
    )
    if sd:
        sd["offending_path"] = path
        return sd
    return None
