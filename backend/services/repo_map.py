"""
services/repo_map.py — Iter 212m-116 (Aider-pattern compact repo map)

Builds a COMPACT symbol tree for a project — file paths + top-level
function/class names + imports only — instead of fetching full file
contents. Used as the initial context for Loop Mode's Plan phase.

Token economy: a 200-file repo previously sent ~150K tokens of raw
source to the planner. The compact map for the same repo is ~3-5K
tokens (≈97% reduction). The LLM still gets enough signal to pick
the right files; full content is fetched only for files it actually
chooses to edit (loop_execute.py already does this lazily).

Strategy:
  1. Reuse the existing `cto/projects/{id}/graph` build pipeline
     (graph_builder.py). It already extracts {symbols, imports,
     layer, path} per file and persists per-project / per-user.
  2. format_repo_map() turns the graph doc into a tight string the
     planner LLM can read in one prompt:

       src/api/users.py [API] · funcs: list_users, create_user · imports: db.session, models.User
       src/db/models.py [Data] · classes: User, Session
       …

Per-project gating is inherited from the underlying `get_graph_full`
which is already scoped by {project_id, user_id} (iter 212m-113).
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger("aurem-dev.repo_map")

# Maximum chars in the compact map sent to the planner. Tuned to fit
# ~4K tokens at typical token-per-char ratios.
MAX_MAP_CHARS = 16_000
# Per-file budget once the soft cap is hit — drop low-priority dirs.
LOW_PRIORITY_LAYERS = {"Test", "Build", "Doc"}


async def build_repo_map(
    db,
    project_id: Optional[str],
    user_id: str,
) -> dict:
    """Returns {ok, has_map, map_text, file_count, char_count}.
    Empty map (`has_map=False`) when no graph has been built yet —
    caller should fall back to the original flow (raw project_id only)."""
    if not project_id:
        return {"ok": True, "has_map": False, "map_text": "",
                "file_count": 0, "char_count": 0}
    try:
        from services.graph_builder import get_graph_full
        graph = await get_graph_full(db, project_id, user_id)
    except Exception as e:                                # noqa: BLE001
        logger.warning("repo_map graph load failed: %r", e)
        return {"ok": False, "has_map": False, "map_text": "",
                "file_count": 0, "char_count": 0, "error": str(e)}
    if not graph or not graph.get("nodes"):
        return {"ok": True, "has_map": False, "map_text": "",
                "file_count": 0, "char_count": 0}
    map_text = format_repo_map(graph)
    return {
        "ok": True, "has_map": True,
        "map_text":   map_text,
        "file_count": len(graph.get("nodes") or {}),
        "char_count": len(map_text),
    }


def format_repo_map(graph: dict) -> str:
    """Render the graph doc as a tight per-file one-liner. Sorts by
    layer (API → Service → Data → UI → Hook → Util → Config → other)
    so the planner reads top-down."""
    nodes: dict = graph.get("nodes") or {}
    if not nodes:
        return ""
    order = ["API", "Service", "Data", "UI", "Hook", "Util", "Config", "Other"]
    by_layer: dict[str, list[tuple[str, dict]]] = {ly: [] for ly in order}
    for path, node in nodes.items():
        layer = (node or {}).get("layer") or "Other"
        by_layer.setdefault(layer, []).append((path, node))
    lines: list[str] = []
    total_chars = 0
    for layer in order + [ly for ly in by_layer if ly not in order]:
        items = by_layer.get(layer) or []
        if not items:
            continue
        items.sort(key=lambda t: t[0])
        for path, node in items:
            symbols = (node.get("symbols") or [])[:8]
            imports = (node.get("imports") or [])[:5]
            desc    = (node.get("description") or "").strip()[:120]
            parts = [f"{path} [{layer}]"]
            if symbols:
                parts.append(f"symbols: {', '.join(symbols)}")
            if imports:
                parts.append(f"imports: {', '.join(imports)}")
            if desc:
                parts.append(f"// {desc}")
            line = " · ".join(parts)
            if total_chars + len(line) + 1 > MAX_MAP_CHARS:
                if layer in LOW_PRIORITY_LAYERS:
                    break
                # Soft cap hit on a high-priority layer — truncate the
                # line itself instead of dropping the file entirely.
                line = line[:MAX_MAP_CHARS - total_chars - 1]
                lines.append(line)
                total_chars += len(line) + 1
                return "\n".join(lines) + "\n[…truncated — repo too large for compact map]"
            lines.append(line)
            total_chars += len(line) + 1
    return "\n".join(lines)
