"""
Iter 212m-236 — Tier 2 Parliament LLM scaffold generation + real
boilerplate for the 3 remaining stacks.

Locks in:
1. `services/scaffold_llm.py`:
   - JSON-only contract enforced.
   - Code-fence stripping tolerates ```json ... ``` wrappers.
   - Unsafe paths (../, absolute, disallowed extensions) dropped.
   - File cap = 20 (same as router).
   - Non-blocking fallback: any failure returns None; caller keeps
     the user's UX unbroken.
   - Cost constant exposed for financials.py.
2. `routers/scaffold.py`:
   - `_generate_file_tree` tries Parliament FIRST, falls back to
     heuristic on None.
   - LLM path guarantees README exists.
3. Real boilerplate for `nextjs-node`, `vue-express`, `plain-html`
   templates exists on disk and covers auth + AUREM DB SDK.
"""

from __future__ import annotations

import pytest


# ── Path safety ─────────────────────────────────────────────────
def test_path_safety_rejects_traversal_and_absolute():
    from services.scaffold_llm import _path_is_safe
    assert _path_is_safe("api/main.py") is True
    assert _path_is_safe("ui/src/App.jsx") is True
    assert _path_is_safe("README.md") is True
    assert _path_is_safe("Dockerfile") is True
    assert _path_is_safe(".gitignore") is True
    assert _path_is_safe(".env.example") is True

    assert _path_is_safe("../../etc/passwd") is False
    assert _path_is_safe("/etc/passwd") is False
    assert _path_is_safe("api/../../secret") is False
    assert _path_is_safe("") is False
    assert _path_is_safe(".hidden/secret") is False, "dot-prefixed dirs blocked"
    assert _path_is_safe("evil.exe") is False, "unknown extension blocked"
    assert _path_is_safe("evil.so") is False


# ── JSON parsing tolerance ──────────────────────────────────────
def test_parse_llm_response_bare_json():
    from services.scaffold_llm import _parse_llm_response
    raw = '{"files":[{"path":"a.py","content":"print(1)"}]}'
    r = _parse_llm_response(raw)
    assert r == [{"path": "a.py", "content": "print(1)"}]


def test_parse_llm_response_with_code_fence():
    from services.scaffold_llm import _parse_llm_response
    raw = '```json\n{"files":[{"path":"README.md","content":"hi"}]}\n```'
    r = _parse_llm_response(raw)
    assert r == [{"path": "README.md", "content": "hi"}]


def test_parse_llm_response_trailing_prose():
    from services.scaffold_llm import _parse_llm_response
    raw = 'Sure! Here you go:\n{"files":[{"path":"README.md","content":"hi"}]}\nHope that helps.'
    r = _parse_llm_response(raw)
    assert r is not None
    assert r[0]["path"] == "README.md"


def test_parse_llm_response_drops_unsafe_paths():
    from services.scaffold_llm import _parse_llm_response
    raw = (
        '{"files":['
        '{"path":"safe.py","content":"ok"},'
        '{"path":"../../evil","content":"bad"},'
        '{"path":"/absolute/path","content":"bad"}'
        ']}'
    )
    r = _parse_llm_response(raw)
    assert r == [{"path": "safe.py", "content": "ok"}]


def test_parse_llm_response_invalid_returns_none():
    from services.scaffold_llm import _parse_llm_response
    assert _parse_llm_response("not json at all") is None
    assert _parse_llm_response("") is None
    assert _parse_llm_response('{"other":"key"}') is None
    assert _parse_llm_response('{"files":"not-a-list"}') is None


def test_parse_llm_response_ignores_non_string_content():
    """LLM sometimes emits non-string content (e.g. arrays). Drop those."""
    from services.scaffold_llm import _parse_llm_response
    raw = (
        '{"files":['
        '{"path":"a.py","content":"ok"},'
        '{"path":"b.py","content":42},'          # invalid
        '{"path":"c.py"}'                        # missing
        ']}'
    )
    r = _parse_llm_response(raw)
    assert r == [{"path": "a.py", "content": "ok"}]


# ── Non-blocking fallback ────────────────────────────────────────
@pytest.mark.asyncio
async def test_generate_returns_none_when_llm_raises(monkeypatch):
    from services import scaffold_llm

    async def _raise(**_kw): raise RuntimeError("boom")

    monkeypatch.setattr("services.llm.call_llm_with_meta", _raise)
    r = await scaffold_llm.generate_scaffold_via_parliament(
        brief="A todo app", stack="react-fastapi",
        user_id="u1", draft_id="d1",
    )
    assert r is None


@pytest.mark.asyncio
async def test_generate_returns_none_on_empty_llm_content(monkeypatch):
    from services import scaffold_llm

    async def _empty(**_kw): return {"content": "", "model_used": "test"}

    monkeypatch.setattr("services.llm.call_llm_with_meta", _empty)
    r = await scaffold_llm.generate_scaffold_via_parliament(
        brief="A todo app", stack="react-fastapi",
        user_id="u1", draft_id="d1",
    )
    assert r is None


@pytest.mark.asyncio
async def test_generate_returns_none_on_unparseable(monkeypatch):
    from services import scaffold_llm

    async def _junk(**_kw):
        return {"content": "Hello, I cannot generate this.", "model_used": "test"}

    monkeypatch.setattr("services.llm.call_llm_with_meta", _junk)
    r = await scaffold_llm.generate_scaffold_via_parliament(
        brief="A todo app", stack="react-fastapi",
        user_id="u1", draft_id="d1",
    )
    assert r is None


@pytest.mark.asyncio
async def test_generate_happy_path(monkeypatch):
    from services import scaffold_llm

    payload = (
        '{"files":['
        '{"path":"README.md","content":"# Todo"},'
        '{"path":"api/main.py","content":"print(1)"}'
        ']}'
    )
    async def _ok(**_kw):
        return {"content": payload, "model_used": "claude-sonnet",
                "tokens_used": 1234}

    # Skip the accounting insert to avoid needing a real DB
    async def _no_log(**_kw): return None
    monkeypatch.setattr("services.llm.call_llm_with_meta", _ok)
    monkeypatch.setattr(scaffold_llm, "_log_generation_event", _no_log)

    r = await scaffold_llm.generate_scaffold_via_parliament(
        brief="A todo app", stack="react-fastapi",
        user_id="u1", draft_id="d1",
    )
    assert r is not None
    assert len(r) == 2
    assert r[0]["path"] == "README.md"
    assert r[1]["path"] == "api/main.py"


@pytest.mark.asyncio
async def test_generate_caps_files_at_max(monkeypatch):
    from services import scaffold_llm
    files_list = [
        {"path": f"f{i}.py", "content": "x"} for i in range(30)
    ]
    import json as _j
    async def _ok(**_kw):
        return {"content": _j.dumps({"files": files_list}),
                "model_used": "test", "tokens_used": 100}
    async def _no_log(**_kw): return None
    monkeypatch.setattr("services.llm.call_llm_with_meta", _ok)
    monkeypatch.setattr(scaffold_llm, "_log_generation_event", _no_log)

    r = await scaffold_llm.generate_scaffold_via_parliament(
        brief="Big app", stack="react-fastapi",
        user_id="u1", draft_id="d1",
    )
    assert r is not None
    assert len(r) == 20   # _MAX_FILES_PER_DRAFT


# ── Scaffold router integration ─────────────────────────────────
@pytest.mark.asyncio
async def test_scaffold_router_prefers_llm_over_heuristic(monkeypatch):
    """`_generate_file_tree` must try LLM first, fall back only on None."""
    from routers import scaffold as sr

    fake_llm_files = [
        {"path": "README.md", "content": "custom LLM readme"},
        {"path": "api/routes.py", "content": "# custom LLM code"},
    ]
    async def _fake_llm(**_kw): return fake_llm_files
    monkeypatch.setattr("services.scaffold_llm.generate_scaffold_via_parliament",
                        _fake_llm)

    result = await sr._generate_file_tree(
        brief="Book club voting app",
        stack="react-fastapi",
        user_id="u_test",
        draft_id="d_test",
    )
    assert result == fake_llm_files


@pytest.mark.asyncio
async def test_scaffold_router_falls_back_when_llm_returns_none(monkeypatch):
    from routers import scaffold as sr

    async def _fake_llm_fail(**_kw): return None
    monkeypatch.setattr("services.scaffold_llm.generate_scaffold_via_parliament",
                        _fake_llm_fail)

    result = await sr._generate_file_tree(
        brief="Habit tracker",
        stack="react-fastapi",
        user_id="u_test",
        draft_id="d_test",
    )
    # Heuristic path always returns >=1 file (README) — verify.
    assert result and len(result) >= 1
    assert any(f["path"] == "README.md" for f in result)


@pytest.mark.asyncio
async def test_scaffold_router_llm_guarantees_readme(monkeypatch):
    """If the LLM forgets to emit a README, the router must inject one."""
    from routers import scaffold as sr

    async def _no_readme(**_kw):
        return [{"path": "api/main.py", "content": "# no README"}]
    monkeypatch.setattr("services.scaffold_llm.generate_scaffold_via_parliament",
                        _no_readme)

    result = await sr._generate_file_tree(
        brief="Meal planner", stack="react-fastapi",
        user_id="u_test", draft_id="d_test",
    )
    paths = [f["path"] for f in result]
    assert "README.md" in paths


# ── Real boilerplate files exist for all 3 new stacks ────────────
def test_nextjs_boilerplate_files_exist():
    import os
    base = "/app/backend/templates/stacks/nextjs-node/boilerplate"
    for required in [
        "package.json",
        "lib/aurem-db.js",
        "app/page.jsx",
        "app/api/auth/signup/route.js",
        "app/api/auth/login/route.js",
    ]:
        p = os.path.join(base, required)
        assert os.path.isfile(p), f"Missing: {p}"
        assert os.path.getsize(p) > 100, f"Too small to be real: {p}"


def test_vue_express_boilerplate_files_exist():
    import os
    base = "/app/backend/templates/stacks/vue-express/boilerplate"
    for required in [
        "server/package.json",
        "server/aurem-db.js",
        "server/index.js",
        "ui/package.json",
        "ui/vite.config.js",
        "ui/src/main.js",
        "ui/src/App.vue",
    ]:
        p = os.path.join(base, required)
        assert os.path.isfile(p), f"Missing: {p}"
        # `main.js` is intentionally tiny (a 3-line bootstrap); keep the
        # existence check strong but relax the size gate to 60 bytes.
        min_size = 60 if required.endswith("main.js") else 100
        assert os.path.getsize(p) > min_size, f"Too small to be real: {p}"


def test_plain_html_boilerplate_files_exist():
    import os
    base = "/app/backend/templates/stacks/plain-html/boilerplate"
    for required in ["index.html", "main.js", "style.css"]:
        p = os.path.join(base, required)
        assert os.path.isfile(p), f"Missing: {p}"
        assert os.path.getsize(p) > 100, f"Too small to be real: {p}"


def test_all_stacks_include_aurem_db_client_or_equivalent():
    """The aurem-db SDK MUST be present in every stack that has a
    backend layer. Plain-HTML uses the browser fetch directly so it's
    exempt."""
    import os
    contents = {
        "react-fastapi": open("/app/backend/templates/stacks/react-fastapi/boilerplate/api/aurem_db_client.py").read(),
        "nextjs-node":   open("/app/backend/templates/stacks/nextjs-node/boilerplate/lib/aurem-db.js").read(),
        "vue-express":   open("/app/backend/templates/stacks/vue-express/boilerplate/server/aurem-db.js").read(),
    }
    for stack, src in contents.items():
        assert "AUREM_APP_ID"    in src, f"{stack} client missing AUREM_APP_ID"
        assert "AUREM_APP_TOKEN" in src, f"{stack} client missing AUREM_APP_TOKEN"


def test_no_raw_mongo_uri_in_any_boilerplate():
    """The whole point of the aurem-db SDK is that generated apps NEVER
    carry a raw mongodb:// URL. Guard against regressions."""
    import os, re
    root = "/app/backend/templates/stacks"
    for dirpath, _dirs, filenames in os.walk(root):
        if "node_modules" in dirpath: continue
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            try:
                with open(full, "r", encoding="utf-8") as f:
                    txt = f.read()
            except (OSError, UnicodeDecodeError):
                continue
            assert not re.search(r"mongodb(\+srv)?://[^ \n\"]+@", txt), (
                f"Raw mongo URI leaked into {full}"
            )


# ── Cost constant + accounting collection ───────────────────────
def test_cost_constant_exposed():
    from services.scaffold_llm import COST_USD_PER_SCAFFOLD_GENERATION
    assert 0 < COST_USD_PER_SCAFFOLD_GENERATION < 1
