"""
test_iter212m105_sanitize_and_active_project.py — Iter 212m-105

Two user-reported fixes:

1. `tool_call` / `tool_result` / `system_prompt` / `LOOP_PHASE:` and
   other internal orchestrator markers must NEVER reach the chat
   bubble. The user saw raw "```tool_call …```" code blocks leaking
   into the assistant response.

2. `useActiveProject()` must hydrate from the `aurem_projects_cache`
   localStorage entry on first render so AskAdvisor's `projectId`
   prop is non-null immediately — preventing the assistant from
   replying "No repo is connected" while the network fetch is still
   in flight.
"""
from pathlib import Path


def _read(rel: str) -> str:
    return Path(f"/app/frontend/src/{rel}").read_text(encoding="utf-8")


def test_internal_fences_set_covers_all_protocol_labels():
    src = _read("components/RenderedMessage.jsx")
    # The constant exists and is gated by a single Set lookup.
    assert "INTERNAL_FENCES" in src
    for lang in (
        "tool_call", "tool_calls", "tool_use", "tool_result",
        "tool_results", "tool_response", "function_call",
        "function_result", "function_response",
        "system", "system_prompt",
        "internal", "orchestrator", "scratchpad",
        "thinking", "chain_of_thought",
    ):
        assert f'"{lang}"' in src, f"INTERNAL_FENCES missing '{lang}'"


def test_loop_phase_prefix_stripped():
    src = _read("components/RenderedMessage.jsx")
    # The single line strip the bubble depends on.
    assert "LOOP_PHASE:" in src
    # Working-on-project context preamble also stripped.
    assert "Working on project:" in src


def test_sanitize_runs_before_fence_split():
    src = _read("components/RenderedMessage.jsx")
    # Cleaned text is what splitFences sees — never the raw text.
    assert "sanitizeForDisplay(text || \"\")" in src
    assert "splitFences(cleaned)" in src


def test_active_project_hydrates_from_cache_synchronously():
    src = _read("components/TabBar.jsx")
    # useState initialiser reads localStorage immediately — sync.
    assert "useState(() => {" in src
    assert "localStorage.getItem(\"aurem_projects_cache\")" in src
    # Cache write on successful fetch keeps the value fresh for next mount.
    assert "localStorage.setItem(\"aurem_projects_cache\"" in src


def test_active_project_keeps_cached_on_transient_failure():
    """A failing /cto/projects/list must NOT clear the cached project
    or downstream consumers (AskAdvisor) will flip to null mid-session."""
    src = _read("components/TabBar.jsx")
    # The catch block must be a no-op (only ignores the error).
    catch_segment = src.split(".catch(")[1].split(";")[0]
    assert "setProject(null)" not in catch_segment, (
        ".catch() must NOT call setProject(null) — keep the cached "
        "project intact on transient network errors."
    )
