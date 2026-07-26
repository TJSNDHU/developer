"""Iter 111 — Vanguard Verify Agent (separate-agent security pass).

User-requested architecture (mirrors Anthropic's defending-code-
reference-harness):
  1. After ORA writes code, BEFORE commit, a SEPARATE verify agent
     (Claude Sonnet 4.5, not ORA's own model) re-audits the patch.
  2. The regex Vanguard scanner runs all 24 existing patterns.
  3. If patch contains executable Python, an E2B smoke-import gate runs.
  4. Only on all-pass does the commit proceed.

These tests lock in:
  - The combined verify_patch() entrypoint exists and returns the shape
    the cto_projects pipeline expects.
  - Regex CRITICAL findings block the commit (regression on iter 44).
  - LLM agent failure (network) does NOT silently block — falls back
    to regex-only floor.
  - E2B smoke-import is only invoked when Python with functions is in
    the patch.
"""
import asyncio
import pytest

from services import vanguard_verify_agent as vva


# ── _has_executable_python ─────────────────────────────────────
def test_executable_py_detected_via_def():
    blocks = {"app.py": "def foo():\n    return 1\n"}
    assert vva._has_executable_python(blocks) is True


def test_executable_py_detected_via_async_def():
    blocks = {"app.py": "async def foo():\n    pass\n"}
    assert vva._has_executable_python(blocks) is True


def test_executable_py_detected_via_class():
    blocks = {"models.py": "class Foo:\n    x = 1\n"}
    assert vva._has_executable_python(blocks) is True


def test_no_executable_py_in_constants_file():
    blocks = {"const.py": "FOO = 'bar'\nBAZ = 42\n"}
    assert vva._has_executable_python(blocks) is False


def test_no_executable_in_non_python_files():
    blocks = {"app.jsx": "function Foo() { return null; }",
              "README.md": "# AUREM"}
    assert vva._has_executable_python(blocks) is False


# ── verify_patch — happy-path & regex floor ──────────────────────
@pytest.mark.asyncio
async def test_clean_patch_passes(monkeypatch):
    """Clean Python — no secrets, no dangerous patterns. Mocks both the
    LLM agent (PASS) and the E2B sandbox (skipped)."""
    async def fake_llm(_blocks, _ctx, **_kw):
        return {"pass": True, "findings": [], "summary": "clean", "model": "test"}
    async def fake_e2b(_blocks):
        return {"pass": True, "skipped": True, "reason": "no E2B in test"}
    monkeypatch.setattr(vva, "_llm_review", fake_llm)
    monkeypatch.setattr(vva, "_e2b_smoke",   fake_e2b)

    result = await vva.verify_patch(
        {"app.py": "def hello():\n    return 'hi'\n"},
        repo_ctx="o/r@main",
    )
    assert result["pass"] is True
    assert result["regex"]["blocked"] is False


@pytest.mark.asyncio
async def test_hardcoded_secret_BLOCKS_via_regex_floor(monkeypatch):
    """Even if the LLM agent says PASS (or is unavailable), regex
    Vanguard scanner MUST still block a hardcoded AWS key."""
    async def fake_llm(_b, _c, **_kw):
        return {"pass": True, "findings": [], "summary": "missed it", "model": "test"}
    async def fake_e2b(_b):
        return {"pass": True, "skipped": True, "reason": "test"}
    monkeypatch.setattr(vva, "_llm_review", fake_llm)
    monkeypatch.setattr(vva, "_e2b_smoke",   fake_e2b)

    result = await vva.verify_patch(
        {"app.py": "API_KEY = 'AKIA12345678901234XX'\n"},
        repo_ctx="o/r@main",
    )
    assert result["pass"] is False
    assert result["regex"]["blocked"] is True


@pytest.mark.asyncio
async def test_llm_agent_block_blocks_overall(monkeypatch):
    """Regex passes but the verify agent finds something at the
    configured block_level → must block.  Iter 212m-41 / 212m-42
    tightened the policy: LLM findings only gate on severities
    meeting the mode's threshold. The default block_level in
    `vanguard_config` is CRITICAL (mode-agnostic), so the test
    supplies a CRITICAL LLM finding to exercise the block path."""
    async def fake_llm(_b, _c, **_kw):
        return {
            "pass": False,
            "findings": [{
                "file": "app.py", "line": 3, "severity": "CRITICAL",
                "rule": "missing_auth",
                "message": "/admin endpoint missing auth dep",
            }],
            "summary": "1 critical finding",
            "model": "claude",
        }
    async def fake_e2b(_b):
        return {"pass": True, "skipped": True, "reason": "test"}
    monkeypatch.setattr(vva, "_llm_review", fake_llm)
    monkeypatch.setattr(vva, "_e2b_smoke",   fake_e2b)

    result = await vva.verify_patch(
        {"app.py": "def add(a, b):\n    return a + b\n"},
        repo_ctx="o/r@main",
    )
    assert result["pass"] is False
    assert any(f.get("rule") == "missing_auth" for f in result["findings"])


@pytest.mark.asyncio
async def test_e2b_block_blocks_overall(monkeypatch):
    """Regex + LLM pass, but the E2B smoke import fails (e.g.
    ImportError of a missing module) → must block."""
    async def fake_llm(_b, _c, **_kw):
        return {"pass": True, "findings": [], "summary": "clean", "model": "claude"}
    async def fake_e2b(_b):
        return {"pass": False, "skipped": False,
                "stderr": "ModuleNotFoundError: nonexistent_lib",
                "stdout": ""}
    monkeypatch.setattr(vva, "_llm_review", fake_llm)
    monkeypatch.setattr(vva, "_e2b_smoke",   fake_e2b)

    result = await vva.verify_patch(
        {"app.py": "def foo():\n    import nonexistent_lib\n"},
        repo_ctx="o/r@main",
    )
    assert result["pass"] is False
    assert "BLOCK" in result["summary"] or "fail" in result["summary"].lower()


# ── verify_patch — graceful degradation ──────────────────────────
@pytest.mark.asyncio
async def test_llm_agent_unavailable_does_NOT_block(monkeypatch):
    """If Claude / Emergent LLM key is unreachable, the verify agent
    must NOT silently block clean patches — fall back to regex floor."""
    async def fake_llm(_b, _c, **_kw):
        return {"pass": True, "findings": [],
                "summary": "skipped (network)", "model": ""}
    async def fake_e2b(_b):
        return {"pass": True, "skipped": True, "reason": "test"}
    monkeypatch.setattr(vva, "_llm_review", fake_llm)
    monkeypatch.setattr(vva, "_e2b_smoke",   fake_e2b)

    result = await vva.verify_patch(
        {"app.py": "def add(a, b):\n    return a + b\n"},
        repo_ctx="o/r@main",
    )
    assert result["pass"] is True


@pytest.mark.asyncio
async def test_e2b_skipped_when_no_executable_python(monkeypatch):
    """The actual _e2b_smoke implementation should early-return skipped
    when the patch contains no executable Python."""
    res = await vva._e2b_smoke({"const.py": "FOO = 1\n", "README.md": "x"})
    assert res["pass"] is True
    assert res["skipped"] is True
    assert "no executable python" in res["reason"]


# ── separate-agent architecture invariants ───────────────────────
def test_verify_model_is_a_different_model_than_ora_default():
    """ORA defaults to deepseek/openrouter. The verify agent MUST be a
    different vendor/family so we get true second-opinion isolation."""
    assert "deepseek" not in vva._VERIFY_MODEL.lower()
    # Default is Claude — matches Anthropic's reference-harness pattern
    assert "claude" in vva._VERIFY_MODEL.lower() or \
           "anthropic" in vva._VERIFY_MODEL.lower()


def test_verify_system_prompt_covers_all_12_dimensions():
    """The verify agent's system prompt MUST instruct it to look at the
    12 attack-surface dimensions the founder asked about — same coverage
    as the regex catalog plus LLM-only categories (logic bombs, missing
    authz, race conditions)."""
    p = vva._VERIFY_SYSTEM
    must_cover = [
        "Secrets", "Code injection", "Path traversal",
        "Server-side request forgery", "Cross-site scripting",
        "Insecure crypto", "auth bypasses", "Open CORS",
        "Logic bombs", "Missing authorization", "Direct SQL",
        "Race conditions",
    ]
    for term in must_cover:
        assert term in p, f"verify-agent prompt missing dimension: {term!r}"


def test_verify_prompt_requires_valid_json_output():
    """The pipeline depends on JSON; the prompt must mandate it."""
    p = vva._VERIFY_SYSTEM
    assert "VALID JSON only" in p
    assert "\"pass\":" in p
    assert "\"findings\":" in p
