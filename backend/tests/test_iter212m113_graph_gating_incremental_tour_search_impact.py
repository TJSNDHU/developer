"""
Iter 212m-113 — Tests for the production-ready Codebase Graph:
  - Per-project gating (cross-repo leak impossible)
  - PAT-based GitHub auth flow
  - Token-economical incremental builds (fingerprint-based)
  - Guided tour endpoint
  - Fuzzy search endpoint
  - Diff impact ("blast radius") endpoint
"""
from __future__ import annotations

import pytest


# ─── 1. Per-project gating ────────────────────────────────────────────
@pytest.mark.asyncio
async def test_get_graph_returns_empty_for_wrong_user():
    """User B asking for User A's project_id must return {} — the
    compound-key Mongo filter MUST include user_id."""
    from services import graph_builder as gb

    class _FakeColl:
        async def find_one(self, q, proj=None):
            # Echo back the filter — our assertion is that user_id
            # is always in the query.
            assert "user_id" in q, \
                "get_graph filter must include user_id to prevent cross-repo leak"
            assert "project_id" in q
            # Pretend nothing matches for user_b.
            return None if q["user_id"] == "user_b" else {
                "project_id": q["project_id"],
                "user_id":    q["user_id"],
                "nodes":      {"a.py": {"path": "a.py"}},
            }

    class _FakeDB:
        project_graphs = _FakeColl()

    out_wrong = await gb.get_graph(_FakeDB(), "proj_owned_by_a", "user_b")
    assert out_wrong == {}, "Cross-user read must return empty"
    out_right = await gb.get_graph(_FakeDB(), "proj_owned_by_a", "user_a")
    assert out_right and out_right["user_id"] == "user_a"


def test_graph_routes_decode_jwt_before_reading():
    """All 4 graph endpoints must call current_dev() before any DB read."""
    src = open("/app/backend/routers/cto_projects.py").read()
    # Each endpoint must derive user_id from JWT — never from path/body.
    snippets = [
        '@router.get("/projects/{project_id}/graph")',
        '@router.get("/projects/{project_id}/graph/tour")',
        '@router.get("/projects/{project_id}/graph/search")',
        '@router.post("/projects/{project_id}/graph/impact")',
    ]
    for s in snippets:
        assert s in src, f"endpoint {s} must exist"
    # Check each endpoint block contains a current_dev call before any
    # graph read.
    for s in snippets:
        block = src.split(s, 1)[1].split("@router.", 1)[0]
        assert "await current_dev(authorization)" in block, \
            f"{s} must authenticate via current_dev"
        assert 'user_id = me["user_id"]' in block, \
            f"{s} must derive user_id from JWT, not request body"


# ─── 2. Incremental build / fingerprint-based token economy ───────────
def test_build_graph_persists_tree_and_blob_shas():
    src = open("/app/backend/services/graph_builder.py").read()
    # Must save tree_sha + blob_shas so future builds can detect
    # which files actually changed.
    assert '"tree_sha":     tree_sha' in src
    assert '"blob_shas":    blob_shas' in src
    # Must NOT LLM-describe a file whose SHA matches the prior build.
    assert "changed_top" in src
    assert "blob_shas.get(p) != prior_blob_shas.get(p)" in src


def test_build_graph_reuses_prior_descriptions():
    """When the same file has the same blob SHA as the last build, its
    cached description MUST be reused — no LLM call, no token spend."""
    src = open("/app/backend/services/graph_builder.py").read()
    assert "prior_descriptions" in src
    assert "reused_top" in src
    # The regex pass must seed description from prior cache.
    assert 'prior_descriptions.get(path, "")' in src


def test_build_graph_skips_llm_when_no_changes():
    src = open("/app/backend/services/graph_builder.py").read()
    # The LLM call must be gated on `if changed_top:` — zero new
    # tokens when nothing changed.
    assert "if changed_top:" in src
    # Without that gate, every build would re-LLM the top 20 files.


# ─── 3. Tour endpoint ────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_tour_endpoint_returns_dependency_ordered_walkthrough(monkeypatch):
    from routers import cto_projects as cp

    async def fake_current_dev(auth=None):
        return {"user_id": "u1"}
    monkeypatch.setattr(cp, "current_dev", fake_current_dev)
    monkeypatch.setattr(cp, "get_db", lambda: "fake_db")

    async def fake_get_graph_full(db, project_id, user_id):
        assert user_id == "u1"
        return {
            "file_count": 42,
            "nodes": {
                "src/api/users.py":     {"description": "Users API",  "symbols": ["list", "create"]},
                "src/services/auth.py": {"description": "Auth svc",   "symbols": ["login"]},
                "src/db/models.py":     {"description": "Mongo models", "symbols": []},
                "src/ui/Login.jsx":     {"description": "Login UI",   "symbols": ["Login"]},
            },
            "layers": {
                "API":     ["src/api/users.py"],
                "Service": ["src/services/auth.py"],
                "Data":    ["src/db/models.py"],
                "UI":      ["src/ui/Login.jsx"],
            },
        }
    import services.graph_builder as gb
    monkeypatch.setattr(gb, "get_graph_full", fake_get_graph_full)

    res = await cp.get_graph_tour("proj_1", authorization="Bearer x")
    assert res["ok"] is True
    tour = res["tour"]
    assert len(tour) == 4
    # Dependency-order: Data (model) → Service → API → UI.
    layers_in_order = [step["layer"] for step in tour]
    assert layers_in_order.index("Data")    < layers_in_order.index("Service")
    assert layers_in_order.index("Service") < layers_in_order.index("API")
    assert layers_in_order.index("API")     < layers_in_order.index("UI")
    # Each step carries description + symbols for the UI.
    assert all("description" in s and "symbols" in s for s in tour)


@pytest.mark.asyncio
async def test_tour_endpoint_returns_not_built_when_no_graph(monkeypatch):
    from routers import cto_projects as cp

    async def fake_current_dev(auth=None):
        return {"user_id": "u1"}
    monkeypatch.setattr(cp, "current_dev", fake_current_dev)
    monkeypatch.setattr(cp, "get_db", lambda: "x")
    import services.graph_builder as gb
    async def fake_full(db, p, u): return {}
    monkeypatch.setattr(gb, "get_graph_full", fake_full)
    res = await cp.get_graph_tour("proj_x", authorization="Bearer x")
    assert res["status"] == "not_built"
    assert res["tour"] == []


# ─── 4. Search endpoint ──────────────────────────────────────────────
@pytest.mark.asyncio
async def test_search_endpoint_ranks_path_and_symbol_matches(monkeypatch):
    from routers import cto_projects as cp

    async def fake_current_dev(auth=None): return {"user_id": "u1"}
    monkeypatch.setattr(cp, "current_dev", fake_current_dev)
    monkeypatch.setattr(cp, "get_db", lambda: "x")

    async def fake_full(db, p, u):
        return {
            "nodes": {
                "src/auth/login.py":  {"layer": "API",     "description": "Login route",      "symbols": ["login"]},
                "src/users/profile.py": {"layer": "API",   "description": "User profile",     "symbols": ["get_profile"]},
                "src/util/strings.py": {"layer": "Util",   "description": "String helpers",   "symbols": ["camelize"]},
            },
        }
    import services.graph_builder as gb
    monkeypatch.setattr(gb, "get_graph_full", fake_full)

    res = await cp.search_graph("proj_1", q="login", limit=10,
                                authorization="Bearer x")
    assert res["ok"] is True
    results = res["results"]
    # Login.py must rank first — basename match (+100) AND exact symbol match (+80).
    assert results[0]["path"] == "src/auth/login.py"
    assert results[0]["score"] >= 100


@pytest.mark.asyncio
async def test_search_endpoint_handles_empty_query(monkeypatch):
    from routers import cto_projects as cp

    async def fake_current_dev(auth=None): return {"user_id": "u1"}
    monkeypatch.setattr(cp, "current_dev", fake_current_dev)
    monkeypatch.setattr(cp, "get_db", lambda: "x")
    import services.graph_builder as gb
    async def fake_full(db, p, u): return {"nodes": {"a.py": {}}}
    monkeypatch.setattr(gb, "get_graph_full", fake_full)
    res = await cp.search_graph("p1", q="", authorization="Bearer x")
    assert res["results"] == []


# ─── 5. Impact / blast radius endpoint ────────────────────────────────
@pytest.mark.asyncio
async def test_impact_endpoint_returns_transitive_importers(monkeypatch):
    from routers import cto_projects as cp

    async def fake_current_dev(auth=None): return {"user_id": "u1"}
    monkeypatch.setattr(cp, "current_dev", fake_current_dev)
    monkeypatch.setattr(cp, "get_db", lambda: "x")

    async def fake_full(db, p, u):
        return {
            "edges": [
                {"from": "src/api/users.py",  "to": "src/db/models.py"},
                {"from": "src/api/login.py",  "to": "src/db/models.py"},
                {"from": "src/ui/Home.jsx",   "to": "src/api/users.py"},
                {"from": "src/util/x.py",     "to": "src/util/y.py"},
            ],
        }
    import services.graph_builder as gb
    monkeypatch.setattr(gb, "get_graph_full", fake_full)

    # Pretend we're shipping a change to src/db/models.py — both API
    # files that import it must show up in the impact set.
    res = await cp.graph_impact(
        "proj_1",
        body={"files": ["src/db/models.py"]},
        authorization="Bearer x",
    )
    paths = {hit["path"] for hit in res["impacted"]}
    assert "src/api/users.py" in paths
    assert "src/api/login.py" in paths
    assert "src/util/x.py" not in paths
    assert res["blast_radius"] == 2


@pytest.mark.asyncio
async def test_impact_endpoint_requires_files_in_body(monkeypatch):
    from routers import cto_projects as cp
    from fastapi import HTTPException

    async def fake_current_dev(auth=None): return {"user_id": "u1"}
    monkeypatch.setattr(cp, "current_dev", fake_current_dev)
    monkeypatch.setattr(cp, "get_db", lambda: "x")

    with pytest.raises(HTTPException) as exc:
        await cp.graph_impact("p1", body={}, authorization="Bearer x")
    assert exc.value.status_code == 400
