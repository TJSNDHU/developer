"""
services/mermaid_diagram.py — Iter 212m-215

Two-step LLM pipeline that turns a `project_graphs` document into an
interactive Mermaid.js architecture diagram — inspired by
GitDiagram (github.com/ahmedkhaleel2004/gitdiagram, MIT, 23k★).

Pipeline
--------

    graph_doc  ──►  step 1 (plain-English explanation)
                          ▼
                      step 2 (Mermaid.js flowchart with layer subgraphs)
                          ▼
                    persisted into `project_graphs.mermaid` so a
                    rebuild is a single write; the UI reads the
                    cached version until the graph itself changes
                    (tree_sha invalidation) or the user clicks
                    "Regenerate diagram".

Both LLM passes go through the SAME OpenRouter endpoint the Advisor
vision path uses (`services/advisor_vision.py`) — cheap models
first (Gemini 2.5 Flash), single-shot failover to GPT-5 Mini, no
Council chain / no `chat_with_tools` involvement.  A failure here
NEVER touches the rest of the graph pipeline; the caller catches
and returns a graceful "diagram unavailable, try Regenerate" state.

Rendering-time contract (MermaidBlock.jsx)
------------------------------------------

The generated code MUST:
  • start with `flowchart TD` (top-down) or `flowchart LR`
  • wrap layers in `subgraph`s (Frontend / Backend / Service / Data / Config)
  • use `click <NodeId> href "<url>"` for clickable navigation
    (works in mermaid's strict security mode; onClick JS does not)
  • mark "recently modified" files with the `:::hot` class + a
    `classDef hot fill:#FF6608,stroke:#FFC79A,color:#000;` line
  • cap the total nodes at 40 (Mermaid readability + LLM latency)
"""

from __future__ import annotations

import base64
import logging
import os
import re
import time
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

OPENROUTER_URL     = "https://openrouter.ai/api/v1/chat/completions"
PRIMARY_MODEL      = os.environ.get("MERMAID_DIAGRAM_MODEL",
                                     "google/gemini-2.5-flash")
FAILOVER_MODEL     = os.environ.get("MERMAID_DIAGRAM_FAILOVER",
                                     "openai/gpt-5-mini")
_HTTP_TIMEOUT_S    = 25.0
_MAX_NODES         = 40

# ── Recently-modified detection ─────────────────────────────────────

def _recent_paths(graph: dict) -> set[str]:
    """A file counts as "recently modified" if:

      1. it appears in `changed_top` (populated by graph_builder when
         the incremental rebuild sees new blob SHAs vs the prior tree),
      2. OR its layer is API/Service AND graph_builder just described
         it fresh this build (implies it was in `changed_top`).

    Falls back to: the top 5 highest-priority files by graph_builder's
    priority_score — so we always have SOME orange highlights, even
    on the very first build where nothing has "changed" yet.
    """
    nodes = graph.get("nodes") or {}
    changed_top = set(graph.get("changed_top") or [])
    if changed_top:
        return changed_top
    # Fallback — pick a small hero set so the diagram never looks flat.
    prio_layers = {"API", "Service"}
    hero = [
        p for p, n in nodes.items() if (n.get("layer") in prio_layers)
    ][:5]
    return set(hero)


# ── LLM calls ───────────────────────────────────────────────────────

async def _openrouter_call(model: str, system: str, user: str,
                            max_tokens: int = 900) -> Optional[str]:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        logger.warning("mermaid_diagram: OPENROUTER_API_KEY missing")
        return None
    payload = {
        "model": model,
        "temperature": 0.2,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
    }
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://auremcto.com",
        "X-Title": "Aurem CTO - Graph Diagram",
    }
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S) as cx:
            r = await cx.post(OPENROUTER_URL, json=payload, headers=headers)
    except Exception as e:
        logger.warning("mermaid_diagram: transport error on %s: %r", model, e)
        return None
    if r.status_code != 200:
        logger.warning(
            "mermaid_diagram: %s → %s %s",
            model, r.status_code, r.text[:200],
        )
        return None
    try:
        j = r.json()
        return ((j.get("choices") or [{}])[0]
                .get("message", {}).get("content") or "").strip() or None
    except Exception as e:
        logger.warning("mermaid_diagram: bad JSON from %s: %r", model, e)
        return None


async def _call_with_failover(system: str, user: str,
                               max_tokens: int = 900) -> Optional[str]:
    out = await _openrouter_call(PRIMARY_MODEL, system, user, max_tokens)
    if out:
        return out
    return await _openrouter_call(FAILOVER_MODEL, system, user, max_tokens)


# ── Step 1 — plain-English architecture explanation ─────────────────

_EXPLAIN_SYSTEM = (
    "You are a senior code reviewer.  You receive a compact summary "
    "of a codebase (layer counts, top files per layer, imports).  "
    "In ≤180 words, describe the ARCHITECTURE in plain English: what "
    "layers exist, how they connect, what the entry points are, and "
    "what looks like the busiest area.  Do NOT invent files or "
    "layers not in the summary.  Do NOT speculate about business "
    "domain unless the filenames make it obvious.  End with one "
    "line listing the 5 highest-signal files by path."
)


def _compact_summary(graph: dict) -> str:
    """Emit ~1.5 KB text that describes the graph to an LLM without
    shipping the whole node dict (which can be 100+ KB)."""
    nodes    = graph.get("nodes") or {}
    layers   = graph.get("layers") or {}
    edges    = graph.get("edges") or []
    lines = [
        f"Project: {graph.get('project_id')}",
        f"Total files: {len(nodes)}",
        f"Total edges: {len(edges)}",
        "",
        "LAYERS (top files per layer):",
    ]
    priority = ["API", "Service", "Data", "UI", "Hook", "Util", "Config", "Test", "Other"]
    for layer in priority:
        paths = layers.get(layer) or []
        if not paths:
            continue
        head = [p for p in paths[:8]]
        lines.append(f"  {layer} ({len(paths)}): " + ", ".join(head))
    lines.append("")
    lines.append("TOP EDGES (from → to):")
    for e in edges[:24]:
        lines.append(f"  {e.get('from')} → {e.get('to')}")
    # Include the pre-built descriptions where present — they are the
    # cheap signal from the graph_builder DeepSeek pass.
    described = [
        f"  {p}: {n['description'][:160]}"
        for p, n in nodes.items()
        if n.get("description")
    ][:12]
    if described:
        lines.append("")
        lines.append("FILE DESCRIPTIONS (top 12):")
        lines.extend(described)
    return "\n".join(lines)


async def generate_explanation(graph: dict) -> Optional[str]:
    return await _call_with_failover(
        _EXPLAIN_SYSTEM,
        _compact_summary(graph),
        max_tokens=400,
    )


# ── Step 2 — Mermaid code generation ────────────────────────────────

_MERMAID_SYSTEM = (
    "You generate ONE Mermaid.js flowchart that visualises the "
    "architecture described below.  Output ONLY the Mermaid code — "
    "no markdown fences, no prose, no ``` markers.\n\n"
    "STRICT RULES:\n"
    "  1. First line MUST be `flowchart TD` or `flowchart LR`.\n"
    "  2. Group files into `subgraph` blocks by layer (Frontend, "
    "     Backend, Service, Data, Config).  Skip empty layers.\n"
    "  3. At most 40 nodes total.  Combine minor files into a "
    "     `misc[Others (N)]` node when you'd otherwise exceed 40.\n"
    "  4. Node IDs must be short, ASCII-only, unique.  Node labels "
    "     go in the `[label]` bracket, kept ≤ 30 chars.\n"
    "  5. For every node that corresponds to a real file, emit a "
    "     `click <NodeId> href \"github://<path>\" _blank` line — "
    "     the frontend rewrites the URL at render time.\n"
    "  6. Mark the RECENTLY-MODIFIED files with `:::hot` (e.g. "
    "     `mw[middleware.ts]:::hot`).  You'll be told which files "
    "     to mark.\n"
    "  7. AT THE END, include this literal classDef line:\n"
    "     `classDef hot fill:#FF6608,stroke:#FFC79A,color:#000;`\n"
    "  8. Edges: use `A --> B` for imports; keep the arrow count "
    "     under 60 to stay readable.\n"
    "  9. Do NOT invent files that aren't in the input.  If two "
    "     files look identical, keep only one.\n"
)


async def generate_mermaid(graph: dict, explanation: str,
                            recent_paths: set[str]) -> Optional[str]:
    summary = _compact_summary(graph)
    hot_lines = "\n".join(f"  - {p}" for p in list(recent_paths)[:10])
    user = (
        "ARCHITECTURE EXPLANATION (from step 1, use as your outline):\n"
        + (explanation or "(explanation unavailable — infer from the "
                          "summary below)")
        + "\n\nGRAPH SUMMARY:\n"
        + summary
        + f"\n\nRECENTLY MODIFIED FILES (mark these with `:::hot`):\n"
        + (hot_lines or "  (none — pick the 3 most central nodes)")
        + "\n\nRemember: OUTPUT ONLY the Mermaid code, first line "
          "starts with `flowchart`."
    )
    raw = await _call_with_failover(_MERMAID_SYSTEM, user, max_tokens=1400)
    if not raw:
        return None
    # Belt-and-braces: strip any accidental ```mermaid fences.
    code = raw.strip()
    code = re.sub(r"^```(?:mermaid)?\s*", "", code, flags=re.I)
    code = re.sub(r"\s*```$", "", code)
    code = code.strip()
    if not code.lower().startswith(("flowchart", "graph ")):
        # Model didn't obey the shape rule — refuse rather than
        # ship a broken diagram to the UI.
        logger.warning("mermaid_diagram: bad shape from LLM: %s", code[:120])
        return None
    return code


# ── Top-level orchestration + persistence ───────────────────────────

async def build_and_persist_mermaid(db, project_id: str,
                                     user_id: str) -> dict:
    """Full 2-step pipeline.  Reads the existing graph doc, generates
    explanation + Mermaid, and writes both back into `project_graphs`.

    Returns:
        {"ok": True,  "mermaid_code": ..., "explanation": ..., ...}
        {"ok": False, "reason": <safe string>}
    """
    if db is None or not project_id or not user_id:
        return {"ok": False, "reason": "invalid arguments"}
    doc = await db.project_graphs.find_one(
        {"project_id": project_id, "user_id": user_id},
        {"_id": 0},
    )
    if not doc:
        return {"ok": False, "reason": "graph not built yet"}

    explanation = await generate_explanation(doc)
    if not explanation:
        return {"ok": False, "reason": "LLM explanation step failed"}

    recent = _recent_paths(doc)
    code = await generate_mermaid(doc, explanation, recent)
    if not code:
        return {"ok": False, "reason": "LLM Mermaid step failed"}

    payload = {
        "mermaid_code":         code,
        "mermaid_explanation":  explanation,
        "mermaid_generated_at": time.time(),
        "mermaid_model":        PRIMARY_MODEL,
        "mermaid_recent_files": sorted(recent),
        # Iter 212m-215 — pin the tree_sha we generated against so the
        # frontend can auto-invalidate when the underlying repo moves
        # ahead.  A new commit → graph_builder rebuilds and stamps a
        # new tree_sha → next Graph-tab open sees a mismatch and
        # regenerates the diagram automatically.  User never has to
        # click "Regenerate" for a fresh commit.
        "mermaid_tree_sha":     doc.get("tree_sha"),
    }
    try:
        await db.project_graphs.update_one(
            {"project_id": project_id, "user_id": user_id},
            {"$set": payload},
        )
    except Exception as e:
        logger.warning("mermaid_diagram: persist failed: %r", e)
        # We still return success — the frontend can render the code
        # even if caching didn't take.
    return {"ok": True, **payload}
