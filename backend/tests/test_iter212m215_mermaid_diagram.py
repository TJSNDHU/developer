"""
Iter 212m-215 — Mermaid architecture diagram regression suite.

Locks the pipeline contracts introduced by the GitDiagram-style
approach (github.com/ahmedkhaleel2004/gitdiagram):

  1. `POST /cto/projects/{id}/graph/mermaid` on a seeded graph must
     return `{ok: True, mermaid_code, mermaid_explanation,
     mermaid_tree_sha, mermaid_recent_files}`.
  2. The Mermaid code MUST:
       - start with `flowchart` (TD or LR)
       - contain a `classDef hot fill:#FF6608` line — orange highlight
       - contain at least one `:::hot` node — the recently-modified
         file from `changed_top`
       - contain `click <id> href "github://..."` directives so the
         frontend can rewrite them to real deep-links
       - use `subgraph` blocks for the layers we passed in
  3. The pipeline NEVER reads a README (only symbol / import data
     from the graph doc).  Source-string check.
  4. Auto-invalidation: when the graph doc's `tree_sha` diverges
     from `mermaid_tree_sha`, the caller (frontend) will regenerate
     — we assert the persist step records both fields.
  5. Isolation from Council chain — the pipeline uses OpenRouter
     directly, not `chat_with_tools` / `services/llm.py`.  Source
     check.
"""

from __future__ import annotations

import os
import re
import time
import uuid

import pytest
import requests
from motor.motor_asyncio import AsyncIOMotorClient

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL")
if not BASE_URL:
    # Iter 309 · Phase 0.2 · Round 4 — guard the .env fallback + skip
    # cleanly if the env var is unset in CI.
    try:
        with open("/app/frontend/.env") as fh:
            for line in fh:
                if line.startswith("REACT_APP_BACKEND_URL"):
                    BASE_URL = line.split("=", 1)[1].strip()
                    break
    except FileNotFoundError:
        pass
if not BASE_URL:
    pytest.skip("REACT_APP_BACKEND_URL missing — skipping live-URL smoke tests",
                allow_module_level=True)
AUREM = BASE_URL.rstrip("/") + "/api/aurem-dev"


FOUNDER = ("test@aurem.dev", "AuremTest2026!")


def _login() -> tuple[str, str]:
    r = requests.post(f"{AUREM}/auth/login",
                       json={"email": FOUNDER[0], "password": FOUNDER[1]},
                       timeout=15)
    r.raise_for_status()
    d = r.json()
    return d["user_id"], d["token"]


async def _db():
    c = AsyncIOMotorClient(os.environ["MONGO_URL"])
    return c[os.environ["DB_NAME"]]


async def _seed_graph(project_id: str, user_id: str, *,
                       tree_sha: str = "sha-A") -> None:
    db = await _db()
    graph = {
        "project_id": project_id,
        "user_id":    user_id,
        "built_at":   time.time(),
        "tree_sha":   tree_sha,
        "status":     "ready",
        "file_count": 5,
        "nodes": {
            "backend/main.py":              {"path": "backend/main.py",         "layer": "Config",  "symbols": ["FastAPI"],           "imports": [],                      "description": "App entry",    "size": 500},
            "backend/routers/chat.py":      {"path": "backend/routers/chat.py", "layer": "API",     "symbols": ["stream_chat"],       "imports": ["services/orchestrator"], "description": "Chat SSE",    "size": 20000},
            "backend/services/orchestrator.py": {"path": "backend/services/orchestrator.py","layer": "Service", "symbols": ["chat_with_tools"], "imports": ["services/llm"],     "description": "Tool loop",   "size": 15000},
            "backend/services/llm.py":      {"path": "backend/services/llm.py","layer": "Service", "symbols": ["call_llm"],          "imports": [],                      "description": "LLM ladder",  "size": 10000},
            "frontend/src/lib/api.js":      {"path": "frontend/src/lib/api.js","layer": "Util",    "symbols": ["streamChat"],         "imports": [],                      "description": "HTTP client", "size": 3000},
        },
        "layers": {
            "API":     ["backend/routers/chat.py"],
            "Service": ["backend/services/orchestrator.py", "backend/services/llm.py"],
            "Util":    ["frontend/src/lib/api.js"],
            "Config":  ["backend/main.py"],
        },
        "edges": [
            {"from": "backend/main.py",                     "to": "backend/routers/chat.py"},
            {"from": "backend/routers/chat.py",             "to": "backend/services/orchestrator.py"},
            {"from": "backend/services/orchestrator.py",    "to": "backend/services/llm.py"},
        ],
        "changed_top": ["backend/routers/chat.py"],
    }
    await db.project_graphs.replace_one(
        {"project_id": project_id, "user_id": user_id},
        graph, upsert=True,
    )


async def _cleanup(project_id: str, user_id: str) -> None:
    db = await _db()
    await db.project_graphs.delete_many(
        {"project_id": project_id, "user_id": user_id}
    )


# ── 1. Full pipeline round-trip ─────────────────────────────────

@pytest.mark.asyncio
async def test_mermaid_pipeline_full_roundtrip():
    user_id, tok = _login()
    project_id = f"p_mermaid_probe_{uuid.uuid4().hex[:6]}"
    await _seed_graph(project_id, user_id, tree_sha="sha-init")
    try:
        r = requests.post(
            f"{AUREM}/cto/projects/{project_id}/graph/mermaid",
            headers={"Authorization": f"Bearer {tok}"},
            timeout=60,
        )
        assert r.status_code == 200, r.text[:400]
        d = r.json()
        assert d.get("ok") is True
        code = d.get("mermaid_code") or ""
        explanation = d.get("mermaid_explanation") or ""
        assert code, "mermaid_code missing from response"
        assert explanation, "mermaid_explanation missing from response"

        # 1a. Shape rules from services/mermaid_diagram.py
        assert code.lower().startswith(("flowchart ", "graph ")), \
            f"first line must be `flowchart` — got {code[:40]!r}"
        assert "classDef hot fill:#FF6608" in code, \
            "orange highlight classDef missing"
        assert ":::hot" in code, \
            "no :::hot marker — recently-modified file wasn't highlighted"
        assert 'href "github://' in code, \
            "no `href \"github://\"` — nodes are not clickable"
        assert "subgraph" in code, \
            "no subgraph layer grouping"

        # 1b. tree_sha auto-invalidation contract
        assert d.get("mermaid_tree_sha") == "sha-init", \
            f"pinned tree_sha wrong: {d.get('mermaid_tree_sha')!r}"

        # 1c. Recent files were passed through as :::hot targets
        assert "backend/routers/chat.py" in code or "chat.py" in code, \
            "changed_top file not present in diagram"
    finally:
        await _cleanup(project_id, user_id)


# ── 2. Auto-invalidation: rerun after tree_sha change persists new pin ──

@pytest.mark.asyncio
async def test_mermaid_reruns_and_repins_tree_sha():
    user_id, tok = _login()
    project_id = f"p_mermaid_probe_{uuid.uuid4().hex[:6]}"
    try:
        # First build
        await _seed_graph(project_id, user_id, tree_sha="sha-v1")
        r1 = requests.post(
            f"{AUREM}/cto/projects/{project_id}/graph/mermaid",
            headers={"Authorization": f"Bearer {tok}"},
            timeout=60,
        )
        assert r1.status_code == 200, r1.text[:400]
        assert r1.json()["mermaid_tree_sha"] == "sha-v1"

        # Simulate a new commit — graph_builder would rewrite tree_sha.
        await _seed_graph(project_id, user_id, tree_sha="sha-v2")

        # Regenerate — must pin the new sha.
        r2 = requests.post(
            f"{AUREM}/cto/projects/{project_id}/graph/mermaid",
            headers={"Authorization": f"Bearer {tok}"},
            timeout=60,
        )
        assert r2.status_code == 200, r2.text[:400]
        assert r2.json()["mermaid_tree_sha"] == "sha-v2", (
            "regenerate did not re-pin the new tree_sha — "
            "auto-invalidation broken"
        )
    finally:
        await _cleanup(project_id, user_id)


# ── 3. 400 when the graph doesn't exist yet ────────────────────

def test_mermaid_400_when_graph_missing():
    _, tok = _login()
    missing_pid = f"p_never_built_{uuid.uuid4().hex[:6]}"
    r = requests.post(
        f"{AUREM}/cto/projects/{missing_pid}/graph/mermaid",
        headers={"Authorization": f"Bearer {tok}"},
        timeout=15,
    )
    assert r.status_code == 400, r.text[:200]
    assert "graph not built" in (r.text or "").lower()


# ── 4. Source-level contract locks ─────────────────────────────

def test_mermaid_pipeline_source_contracts():
    with open("/app/backend/services/mermaid_diagram.py") as fh:
        src = fh.read()
    with open("/app/backend/services/graph_builder.py") as fh:
        gb_src = fh.read()

    # No README leakage — the whole pipeline must be code-only.
    assert "readme" not in src.lower(), (
        "mermaid_diagram.py mentions README — the pipeline must "
        "stay code-only (imports/symbols/layers) per user directive"
    )
    assert "readme" not in gb_src.lower(), (
        "graph_builder.py mentions README — pipeline no longer "
        "code-only"
    )

    # Direct OpenRouter, not the Council ladder.
    assert "openrouter.ai/api/v1/chat/completions" in src, (
        "mermaid_diagram.py must call OpenRouter directly (bypass "
        "the Council chain for isolation)"
    )
    assert "from services.llm import chat_with_tools" not in src, (
        "mermaid_diagram.py must NOT import chat_with_tools — that "
        "would drag in the tool loop"
    )

    # Cache field is the correct one.
    assert '"mermaid_code":' in src
    assert '"mermaid_tree_sha":' in src

    # Shape rules from the system prompt.
    assert "classDef hot fill:#FF6608" in src, (
        "orange highlight rule missing from the system prompt — "
        "recently-modified files would render grey"
    )
    assert 'href \\"github://' in src or "href \"github://" in src, (
        "click-href directive missing — nodes wouldn't be "
        "clickable from the rendered SVG"
    )
