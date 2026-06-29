"""
services/file_selector.py — Iter 212m-116 (Sweep-pattern relevant file picker)

Scores every file in the project graph by relevance to the user's task
description and returns the TOP-N candidates. No LLM. Pure server-side
ranking. Used by Loop Mode just before Execute so we only fetch + send
to the LLM the files actually needed for THIS task instead of the full
plan.files_to_change (which the planner LLM sometimes over-eagerly fills).

Scoring (transparent, debuggable):
  • exact-symbol-in-task        +120
  • path-basename-contains-tok  +80
  • description-contains-tok    +35
  • symbol-substring            +20
  • import-target-substring     +10
  • shared-layer-with-target    +5  (small boost for "feels related")

Stop-words removed. Multi-word tokens supported. Case-insensitive.

Gating: caller passes db + project_id + user_id; we go through
graph_builder.get_graph_full which already enforces per-project
isolation (iter 212m-113).
"""
from __future__ import annotations

import logging
import re
from typing import Optional

logger = logging.getLogger("aurem-dev.file_selector")

_STOP = {
    "a", "an", "the", "and", "or", "but", "to", "of", "in", "on",
    "for", "is", "are", "was", "were", "be", "been", "being",
    "do", "does", "did", "have", "has", "had", "this", "that",
    "these", "those", "with", "without", "from", "into", "as",
    "by", "at", "it", "its", "you", "your", "we", "our", "i",
    "my", "add", "make", "create", "build", "fix", "update",
    "change", "modify", "implement", "please", "can", "could",
    "should", "would", "want", "need", "new", "all", "any",
}


def _tokenize(text: str) -> list[str]:
    text = (text or "").lower()
    raw = re.findall(r"[a-z][a-z0-9_]+", text)
    return [t for t in raw if t not in _STOP and len(t) >= 2]


def score_file(node: dict, path: str, tokens: list[str]) -> int:
    if not tokens:
        return 0
    score = 0
    path_l    = path.lower()
    basename  = path_l.rsplit("/", 1)[-1]
    desc_l    = (node.get("description") or "").lower()
    syms_l    = [s.lower() for s in (node.get("symbols") or [])]
    imps_l    = [s.lower() for s in (node.get("imports") or [])]
    for tok in tokens:
        # Exact symbol match (e.g. user task mentions "checkout" and
        # the file exports a `checkout` function).
        if tok in syms_l:
            score += 120
        # Basename contains the token (e.g. token=auth, file=auth.py).
        if tok in basename:
            score += 80
        # Description match.
        if tok in desc_l:
            score += 35
        # Symbol substring (e.g. token=user matches `get_user_id`).
        if any(tok in s for s in syms_l):
            score += 20
        # Import-target substring.
        if any(tok in i for i in imps_l):
            score += 10
    return score


async def select_relevant_files(
    *,
    db,
    project_id: str,
    user_id: str,
    task_description: str,
    planner_files: Optional[list[str]] = None,
    top_n: int = 10,
) -> dict:
    """Returns {ok, has_graph, candidates, skipped, total_scored}.

    `candidates` is the merged + ranked list of:
      • Files the planner explicitly listed (always included with
        a base score of 200 — the planner saw the full task).
      • Files that scored above the cutoff via keyword matching.

    Output is capped at top_n to keep LLM context small.
    """
    if not project_id or not task_description:
        return {"ok": True, "has_graph": False, "candidates": list(planner_files or []),
                "skipped": [], "total_scored": 0}
    try:
        from services.graph_builder import get_graph_full
        graph = await get_graph_full(db, project_id, user_id)
    except Exception as e:                                # noqa: BLE001
        logger.warning("file_selector graph load failed: %r", e)
        return {"ok": False, "has_graph": False,
                "candidates": list(planner_files or []),
                "skipped": [], "total_scored": 0, "error": str(e)}
    if not graph or not graph.get("nodes"):
        return {"ok": True, "has_graph": False,
                "candidates": list(planner_files or []),
                "skipped": [], "total_scored": 0}

    nodes: dict = graph.get("nodes") or {}
    planner_set = set(planner_files or [])
    tokens = _tokenize(task_description)
    scored: list[tuple[str, int]] = []
    for path, node in nodes.items():
        s = score_file(node, path, tokens)
        if path in planner_set:
            s += 200          # planner-blessed → guaranteed inclusion
        if s > 0:
            scored.append((path, s))
    scored.sort(key=lambda t: t[1], reverse=True)
    top = scored[:max(1, top_n)]
    skipped = [p for p, _ in scored[top_n:]]
    candidates = [p for p, _ in top]
    # Fold in any planner files that scored 0 (kept by planner trust).
    for p in planner_set:
        if p not in candidates:
            candidates.append(p)
    return {
        "ok": True,
        "has_graph": True,
        "candidates":   candidates[:max(top_n, len(planner_set))],
        "skipped":      skipped,
        "total_scored": len(scored),
        "tokens":       tokens,
    }
