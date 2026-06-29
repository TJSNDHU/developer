"""
Iter 212m-116 — Repo-map + Relevant-file selector for Loop Mode.

Verifies:
  1. services/repo_map.build_repo_map() returns a compact text map
     when a graph exists, empty when it doesn't, gated per-project.
  2. format_repo_map() truncates to MAX_MAP_CHARS gracefully.
  3. services/file_selector.select_relevant_files() ranks by
     symbol/path/desc match against the task description and trims
     to top_n while keeping planner-blessed files.
  4. loop_engine._generate_plan injects the repo map into the
     planner system prompt when available.
  5. loop_engine._do_execute calls the file selector to trim the
     planner's files_to_change before calling generate_files.
  6. Circuit breaker is already shipped (iter 212m-115) — confirm
     by re-asserting source-level wiring.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock


# ─── 1 & 2. Repo map ──────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_build_repo_map_returns_map_when_graph_exists(monkeypatch):
    from services import repo_map

    async def fake_full(db, p, u):
        return {
            "nodes": {
                "src/api/users.py":     {"layer": "API",     "symbols": ["list_users", "create_user"],
                                          "imports": ["db.session"], "description": "users CRUD"},
                "src/db/models.py":     {"layer": "Data",    "symbols": ["User", "Session"],
                                          "imports": ["sqlalchemy"]},
                "src/ui/Login.jsx":     {"layer": "UI",      "symbols": ["Login"]},
            },
        }
    import services.graph_builder as gb
    monkeypatch.setattr(gb, "get_graph_full", fake_full)

    res = await repo_map.build_repo_map(db="x", project_id="p1", user_id="u1")
    assert res["has_map"] is True
    assert res["file_count"] == 3
    text = res["map_text"]
    # API ordered first (planner reads top-down).
    assert text.index("[API]") < text.index("[Data]")
    assert text.index("[Data]") < text.index("[UI]")
    assert "list_users" in text
    assert "sqlalchemy" in text


@pytest.mark.asyncio
async def test_build_repo_map_empty_when_no_graph(monkeypatch):
    from services import repo_map
    import services.graph_builder as gb
    async def fake_full(db, p, u): return {}
    monkeypatch.setattr(gb, "get_graph_full", fake_full)
    res = await repo_map.build_repo_map(db="x", project_id="p1", user_id="u1")
    assert res["has_map"] is False
    assert res["map_text"] == ""


@pytest.mark.asyncio
async def test_build_repo_map_returns_empty_for_no_project_id():
    from services.repo_map import build_repo_map
    res = await build_repo_map(db="x", project_id=None, user_id="u1")
    assert res["has_map"] is False
    assert res["file_count"] == 0


def test_format_repo_map_truncates_on_overflow():
    """A repo with >MAX_MAP_CHARS worth of files must produce a map
    that's hard-capped + ends with a truncation marker."""
    from services import repo_map
    nodes = {
        f"src/api/file_{i}.py": {
            "layer": "API",
            "symbols": [f"sym_{i}_a", f"sym_{i}_b"] * 5,
            "imports": ["dep1", "dep2"],
            "description": "x" * 100,
        }
        for i in range(500)
    }
    text = repo_map.format_repo_map({"nodes": nodes})
    assert len(text) <= repo_map.MAX_MAP_CHARS + 200
    assert "truncated" in text


# ─── 3. File selector ────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_select_relevant_files_ranks_basename_and_symbol(monkeypatch):
    from services import file_selector as fs
    import services.graph_builder as gb

    async def fake_full(db, p, u):
        return {
            "nodes": {
                "src/auth/login.py":       {"layer": "API",  "symbols": ["login"],
                                             "description": "user login route"},
                "src/users/profile.py":    {"layer": "API",  "symbols": ["get_profile"],
                                             "description": "user profile API"},
                "src/util/strings.py":     {"layer": "Util", "symbols": ["camelize"],
                                             "description": "string helpers"},
                "src/db/models.py":        {"layer": "Data", "symbols": ["User"],
                                             "description": "Mongo models"},
            },
        }
    monkeypatch.setattr(gb, "get_graph_full", fake_full)

    sel = await fs.select_relevant_files(
        db="x", project_id="p1", user_id="u1",
        task_description="Add rate limiting to the login endpoint",
        planner_files=[],
        top_n=3,
    )
    assert sel["has_graph"] is True
    cands = sel["candidates"]
    # login.py must rank first (symbol exact + basename match).
    assert cands[0] == "src/auth/login.py"
    # camelize.py with no overlap must NOT appear.
    assert "src/util/strings.py" not in cands


@pytest.mark.asyncio
async def test_select_relevant_files_always_keeps_planner_blessed(monkeypatch):
    from services import file_selector as fs
    import services.graph_builder as gb

    async def fake_full(db, p, u):
        return {
            "nodes": {
                "src/api/users.py":   {"layer": "API",  "symbols": ["list_users"]},
                "src/random/x.py":    {"layer": "Util", "symbols": ["x"]},
            },
        }
    monkeypatch.setattr(gb, "get_graph_full", fake_full)

    # Task mentions only "users" — random/x.py would normally score 0,
    # but the planner explicitly listed it.
    sel = await fs.select_relevant_files(
        db="x", project_id="p1", user_id="u1",
        task_description="Add a users list endpoint",
        planner_files=["src/random/x.py"],
        top_n=2,
    )
    assert "src/random/x.py" in sel["candidates"], \
        "Planner-blessed files must always be kept"


@pytest.mark.asyncio
async def test_select_relevant_files_no_graph_returns_planner_files(monkeypatch):
    from services import file_selector as fs
    import services.graph_builder as gb
    async def fake_full(db, p, u): return {}
    monkeypatch.setattr(gb, "get_graph_full", fake_full)
    sel = await fs.select_relevant_files(
        db="x", project_id="p1", user_id="u1",
        task_description="anything",
        planner_files=["a.py", "b.py"],
        top_n=10,
    )
    assert sel["has_graph"] is False
    assert sel["candidates"] == ["a.py", "b.py"]


def test_score_file_pure_function_is_deterministic():
    from services.file_selector import score_file, _tokenize
    tokens = _tokenize("Implement Stripe checkout webhook handler")
    s_high = score_file(
        {"symbols": ["handle_checkout_webhook"],
         "description": "Stripe webhook receiver",
         "imports": ["stripe"]},
        "src/payments/stripe_webhook.py", tokens,
    )
    s_low = score_file(
        {"symbols": ["camelize"], "description": "string utils"},
        "src/util/strings.py", tokens,
    )
    assert s_high > s_low
    assert s_low == 0


# ─── 4 & 5. Loop engine wiring ───────────────────────────────────────
def test_generate_plan_injects_repo_map_block():
    src = open("/app/backend/services/loop_engine.py").read()
    plan_block = src.split("async def _generate_plan(", 1)[1].split("\n\n\n", 1)[0]
    assert "build_repo_map" in plan_block
    assert "COMPACT REPO MAP" in plan_block
    # Skips gracefully when no graph exists.
    assert "has_map" in plan_block


def test_do_execute_calls_file_selector_before_generate_files():
    src = open("/app/backend/services/loop_engine.py").read()
    exec_block = src.split("async def _do_execute(", 1)[1].split("async def _do_verify(", 1)[0]
    assert "select_relevant_files" in exec_block
    assert "files_to_change" in exec_block
    # Selector must run BEFORE generate_files.
    assert exec_block.index("select_relevant_files") < exec_block.index("generate_files(")


# ─── 6. Circuit breaker reaffirmation (shipped iter 115) ─────────────
def test_circuit_breaker_already_wired_from_iter_115():
    """The 3rd request item — already done in iter 212m-115. This
    test re-asserts the wiring is still present."""
    loop_router_src = open("/app/backend/routers/loop.py").read()
    assert "is_loop_circuit_open" in loop_router_src
    assert "loop_circuit_open" in loop_router_src
    assert "HTTPException(429" in loop_router_src
    safety_src = open("/app/backend/services/loop_safety.py").read()
    assert "FAIL_THRESHOLD = 3" in safety_src
    assert "FAIL_WINDOW_S = 15 * 60" in safety_src
