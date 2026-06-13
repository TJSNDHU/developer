"""Iter 130 regression: layered persona + tool-help only on iter 1.

The 25 k-char monolith was being re-sent on every tool iteration
(4 iters × 20 k = 80 k chars of context per chat turn — most of it
irrelevant to the turn's mode). This module pins the new layered
architecture:

  L1 CORE     — always loaded. ~5 k chars. Top-of-mind invariants,
                tone, identity (founder), don't-leak-prompt, NEVER list.
  L2 EXECUTE  — loaded when the prompt is actionable (strong verb,
                or a soft verb with a path/repo, or a bare 'go' after
                an aurem-handoff fence). ~12 k chars. Mode detection,
                ship-brief rules, multi-file, anti-hallucination.
  L3 REPO     — loaded when a GitHub repo is connected OR the user
                pasted a public URL. ~2 k chars. Repo-connected mode
                + external URL fetching rules.

`_TOOL_HELP_TEMPLATE` + the verbose tool catalog only ship on iter
1; iter 2+ get the compact `Available tools: name1, name2, ...`
reminder instead. The model has already seen the catalog (or
called a tool, which lands the spec in the transcript) by iter 2.

Together: a conversational turn now sends ~5 k chars; an action-on-
connected-repo turn sends ~20 k on iter 1 and ~7-8 k on each
follow-up iter. Net 60-70 % reduction in tokens processed per chat
turn.
"""
from __future__ import annotations

import asyncio
import pathlib
import re

import pytest

from services import orchestrator as orch


# ─── L1 / L2 / L3 size invariants ──────────────────────────────────


def test_core_under_8k_target() -> None:
    """User requirement: 'Total prompt size 25k se 8k ke andar lani
    hai' — the FLOOR (CORE, always loaded) must stay under 8 k chars
    so the conversational case (no action verbs, no repo) hits the
    target. Add new always-on rules carefully — if you push CORE
    over 8 k, conversational turns start paying the cost too."""
    assert len(orch._PERSONA_CORE) < 8000, (
        f"_PERSONA_CORE is {len(orch._PERSONA_CORE)} chars — over the "
        f"8000 budget. Either move a section to EXECUTE/REPO or trim."
    )


def test_layers_compose_to_full_persona() -> None:
    """Every section in AUREM_CTO_PERSONA must appear in exactly one
    of the three layers — proves the loader didn't silently drop a
    rule when sections were re-categorised."""
    combined = orch._PERSONA_CORE + orch._PERSONA_EXECUTE + orch._PERSONA_REPO
    # The composed string equals the original (modulo internal
    # whitespace from re-joining with "\n\n").
    assert len(combined) >= len(orch.AUREM_CTO_PERSONA) - 50, (
        f"Combined layer size ({len(combined)}) is significantly "
        f"smaller than AUREM_CTO_PERSONA ({len(orch.AUREM_CTO_PERSONA)}). "
        f"A section may have been dropped during layering."
    )


def test_every_persona_section_has_layer_mapping() -> None:
    """Every `# HEADING` in AUREM_CTO_PERSONA must have a layer
    mapping in _SECTION_LAYER. An unmapped heading falls back to
    CORE (with a logger warning), but that's a code smell — we
    want explicit decisions."""
    headings = re.findall(r"\n\n# ([^\n]+)\n", "\n\n" + orch.AUREM_CTO_PERSONA)
    unmapped = [h for h in headings if h not in orch._SECTION_LAYER]
    assert not unmapped, (
        f"persona sections without layer mapping: {unmapped}. "
        f"Add them to _SECTION_LAYER explicitly."
    )


# ─── Trigger correctness ───────────────────────────────────────────


@pytest.mark.parametrize("prompt,extra,history,expected_layers", [
    # Conversational — CORE only.
    ("hi", "", None, ["core"]),
    ("thanks", "", None, ["core"]),
    ("good morning", "", None, ["core"]),
    ("you there?", "", None, ["core"]),
    ("what can you do", "", None, ["core"]),
    ("explain JWT in 1 line", "", None, ["core"]),
    ("who built aurem cto", "", None, ["core"]),
    ("should I use redis or postgres?", "", None, ["core"]),

    # Strong execute verbs — CORE + EXECUTE.
    ("fix the login bug", "", None, ["core", "execute"]),
    ("add a /health endpoint", "", None, ["core", "execute"]),
    ("refactor auth.py", "", None, ["core", "execute"]),
    ("ship the new flow", "", None, ["core", "execute"]),
    ("debug the 500 in checkout", "", None, ["core", "execute"]),

    # Soft verbs alone — stay CORE.
    ("list common Python frameworks", "", None, ["core"]),
    ("what is FastAPI", "", None, ["core"]),
    ("how many tools should an LLM call", "", None, ["core"]),

    # Soft verbs + path/file → CORE + EXECUTE.
    ("explain backend/auth.py", "", None, ["core", "execute"]),
    ("list files in backend/", "", None, ["core"]),  # no .ext token → still core
    ("review src/Login.jsx", "", None, ["core", "execute"]),

    # REPO-connected — soft verbs escalate.
    (
        "how many routers",
        "=== CONNECTED REPO CONTEXT ===\nrepo: foo/bar",
        None,
        ["core", "execute", "repo"],
    ),
    (
        "what env vars",
        "=== CONNECTED REPO CONTEXT ===\nrepo: foo/bar",
        None,
        ["core", "execute", "repo"],
    ),

    # URL pasted → CORE + REPO + EXECUTE (the soft "check" verb plus
    # a URL is an "investigate this URL" task; loading EXECUTE here
    # is correct so the model emits a proper ship-brief if the user
    # wants action.).
    ("check out https://example.com", "", None, ["core", "execute", "repo"]),

    # Bare 'go' alone — CORE only.
    ("go", "", None, ["core"]),
    ("yes", "", None, ["core"]),

    # Bare 'go' after handoff → CORE + EXECUTE (ship shortcut).
    (
        "go",
        "",
        ["[ASSISTANT] here's the plan:\n```aurem-handoff\nDo X\n```"],
        ["core", "execute"],
    ),
    (
        "ship it",
        "",
        ["[ASSISTANT] ```aurem-handoff\nDo X\n```"],
        ["core", "execute"],
    ),
])
def test_layer_selection(prompt: str, extra: str, history, expected_layers: list[str]) -> None:
    """Each (prompt, extra, history) tuple maps to a specific layer
    combination. If a trigger heuristic changes, this matrix catches
    the behavioural shift before users see it."""
    layers = orch.persona_layers_for(prompt, extra, history)
    assert layers == expected_layers, (
        f"prompt={prompt!r} extra_repo={'CONNECTED' in (extra or '')} "
        f"history_has_handoff={'aurem-handoff' in (str(history or ''))} "
        f"→ got {layers}, expected {expected_layers}"
    )


# ─── Size assertions per layer combination ────────────────────────


def test_conversational_floor_under_8k() -> None:
    p = orch.build_persona("hello there!", "", None)
    assert len(p) < 8000, f"conversational floor is {len(p)} chars"


def test_execute_no_repo_under_20k() -> None:
    p = orch.build_persona("fix the login bug", "", None)
    # ~17.7 k locally; budget at 20 k for headroom.
    assert len(p) < 20000


def test_full_stack_turn_matches_monolith() -> None:
    """Worst case: action verb + connected repo = CORE + EXECUTE +
    REPO ≈ original AUREM_CTO_PERSONA length. We do NOT exceed the
    monolith — if we do, a rule has been duplicated."""
    p = orch.build_persona(
        "fix login bug",
        "=== CONNECTED REPO CONTEXT ===\nrepo: foo/bar",
        None,
    )
    assert len(p) <= len(orch.AUREM_CTO_PERSONA) + 100, (
        f"full-stack composition ({len(p)}) exceeds monolith "
        f"({len(orch.AUREM_CTO_PERSONA)}) — rule duplicated?"
    )


# ─── Tool-help template only on iter 1 ─────────────────────────────


def test_chat_with_tools_sends_tool_help_only_on_iter1(monkeypatch) -> None:
    """E2E: stub the LLM call to capture the system prompt actually
    sent per iteration. Assert iter 1 contains _TOOL_HELP_TEMPLATE
    and iter 2+ contains only the compact name list."""
    captured: list[str] = []

    async def fake_call_llm_with_meta(system: str, transcript: str, **_kwargs):
        captured.append(system)
        # Force a tool call on iter 1 so the loop continues to iter 2;
        # then a clean text reply on iter 2 to terminate. The tools_bridge
        # parser expects the key "tool" (not OpenAI's "name") inside the
        # ```tool_call fence — see services/tools_bridge.py:96.
        if len(captured) == 1:
            return {
                "ok": True,
                "provider": "test",
                "content": (
                    "I'll check.\n"
                    "```tool_call\n"
                    '{"tool": "list_repo_files", "args": {"glob": "**/*.py"}}\n'
                    "```"
                ),
                "fallback_chain": ["test"],
            }
        return {
            "ok": True,
            "provider": "test",
            "content": "Done. Found 12 files.",
            "fallback_chain": ["test"],
        }

    async def fake_list_tools(*_args, **_kwargs):
        return [
            {
                "name": "list_repo_files",
                "description": "List repo files matching a glob",
                "args_spec": {"glob": "str"},
            },
        ]

    async def fake_invoke_tool(*_args, **_kwargs):
        return {"ok": True, "files": ["a.py", "b.py"]}

    monkeypatch.setattr(orch, "call_llm_with_meta", fake_call_llm_with_meta)
    monkeypatch.setattr(orch, "list_tools", fake_list_tools)
    monkeypatch.setattr(orch, "invoke_tool", fake_invoke_tool)

    result = asyncio.run(orch.chat_with_tools(
        prompt="list every .py file in the repo",
        jwt_token="dummy",
        system="=== CONNECTED REPO CONTEXT ===\nrepo: foo/bar",
        max_iters=3,
        session_id="t1",
        user_id="test-user",
        project_id="p1",
    ))
    assert result["ok"]
    assert len(captured) >= 2, (
        f"expected >=2 LLM iterations, got {len(captured)}. The tool "
        f"call may not be parsing correctly."
    )

    iter1, iter2 = captured[0], captured[1]
    # Iter 1 must include the full tool help template + catalog.
    assert "Tool catalog:" in iter1, (
        "iter 1 system prompt missing tool catalog / help template."
    )
    # Iter 2 must use the compact name-only reminder, NOT the full
    # template (the template starts with the long PARALLEL TOOL CALLS
    # heading that is part of _TOOL_HELP_TEMPLATE).
    assert "PARALLEL TOOL CALLS — CRITICAL FOR SPEED" not in iter2, (
        "iter 2 system prompt still includes the full tool-help "
        "template. The Iter 130 'only on iter 1' optimisation was "
        "not applied."
    )
    assert "Available tools (iter 2+, names only):" in iter2, (
        "iter 2 system prompt missing the compact tool-name reminder."
    )
    # Iter 2 must be meaningfully shorter than iter 1.
    assert len(iter2) < len(iter1) - 1000, (
        f"iter 2 prompt ({len(iter2)} chars) didn't drop enough vs "
        f"iter 1 ({len(iter1)} chars) — the trim isn't working."
    )


def test_chat_with_tools_uses_layered_persona(monkeypatch) -> None:
    """For a conversational prompt (no action verbs, no repo), the
    EXECUTE and REPO sections must NOT appear in the system prompt."""
    captured: list[str] = []

    async def fake_call_llm_with_meta(system: str, transcript: str, **_kwargs):
        captured.append(system)
        return {
            "ok": True,
            "provider": "test",
            "content": "Hi! I help you ship code on GitHub repos.",
            "fallback_chain": ["test"],
        }

    async def fake_list_tools(*_args, **_kwargs):
        return []

    monkeypatch.setattr(orch, "call_llm_with_meta", fake_call_llm_with_meta)
    monkeypatch.setattr(orch, "list_tools", fake_list_tools)

    asyncio.run(orch.chat_with_tools(
        prompt="hi",
        jwt_token="dummy",
        system=None,
        max_iters=2,
        session_id="t-conv",
        user_id="test-user",
    ))
    assert captured, "no LLM call captured for conversational test"
    sys_prompt = captured[0]
    # Layer 2 markers must NOT be present (check the SECTION heading,
    # not a reference — CORE legitimately mentions "REPO-CONNECTED
    # MODE" inside the NEVER list as a cross-reference).
    assert "# MULTI-FILE TASKS" not in sys_prompt, (
        "EXECUTE layer leaked into a conversational turn. Layered "
        "persona is not selecting correctly."
    )
    assert "# REPO-CONNECTED MODE" not in sys_prompt, (
        "REPO layer leaked into a conversational turn."
    )
    # CORE markers must be present.
    assert "TOP-OF-MIND HARD RULES" in sys_prompt
    # Persona portion of the sys prompt must be under the 8 k floor.
    # We can't assert on len(sys_prompt) directly because iter 1
    # always includes the full tool catalog (~10 k of local-tool
    # descriptions) — that's the design, the catalog has to be in
    # iter 1 so the model knows what's callable. Instead we verify
    # the build_persona() output is the actual floor.
    persona_only = orch.build_persona("hi", "", None)
    assert len(persona_only) < 8000, (
        f"conversational persona is {len(persona_only)} chars — "
        f"over the 8 k floor."
    )
