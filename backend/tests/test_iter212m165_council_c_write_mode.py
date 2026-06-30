"""
Iter 212m-165 — Council C dedicated "write" mode routing.

Verifies:
  • `MAX_TOKENS["write"]` and `TEMPERATURE["write"]` exist in
    services/llm.py.
  • The swift/pro/maxx review-mode block is BYPASSED when
    mode == "analysis" or mode == "write" so Council B/C see their
    own routing instead of being forced through the GLM swift path.
  • Council C tasks (email/copy/write/draft) set llm_mode="write" in
    orchestrator.py and the LLM call lands on DeepSeek
    (`provider=="deepseek-v3-council-c"`), NOT GLM-5.2.
  • Legacy callers using mode="code" or mode="chat" with
    review_mode=swift are still routed through the swift block
    (no regression).
"""

import pathlib
import sys

BACKEND = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


def test_write_mode_in_token_and_temperature_maps():
    import importlib
    import services.llm as llm
    importlib.reload(llm)
    assert "write" in llm.MAX_TOKENS
    assert "write" in llm.TEMPERATURE
    assert llm.MAX_TOKENS["write"] == 2500
    # Slightly more creative than chat.
    assert llm.TEMPERATURE["write"] >= 0.5
    assert llm.cap_for("write") == 2500
    assert llm.temperature_for("write") >= 0.5


def test_swift_block_bypasses_analysis_and_write():
    """The swift/pro/maxx routing block must NOT fire for
    mode='analysis' or mode='write' so Council B/C reach their own
    dispatch path."""
    src = pathlib.Path("/app/backend/services/llm.py").read_text()
    # The block-entry condition now ANDs `mode not in {"analysis","write"}`.
    assert 'mode not in {"analysis", "write"}' in src


def test_write_mode_routing_block_exists_in_llm_py():
    """The dedicated mode="write" block must dispatch to _call_deepseek
    and tag the provider as 'deepseek-v3-council-c' so dashboards can
    isolate Council C traffic."""
    src = pathlib.Path("/app/backend/services/llm.py").read_text()
    assert 'if mode == "write":' in src
    assert "deepseek-v3-council-c" in src
    # Must call _call_deepseek (not _call_glm)
    block_start = src.find('if mode == "write":')
    block_end   = src.find('# ── Legacy mode routing', block_start)
    block       = src[block_start:block_end]
    assert "_call_deepseek(" in block
    assert "_call_glm(" not in block, (
        "Council C must not call GLM-5.2 — writing tasks go to DeepSeek"
    )


def test_orchestrator_council_c_uses_write_mode():
    """orchestrator.py must set llm_mode='write' (not 'chat') for the
    email/copy/write/draft bucket so the DeepSeek dispatch path is
    actually hit."""
    src = pathlib.Path("/app/backend/services/orchestrator.py").read_text()
    # The write bucket block must set both llm_mode + council_letter.
    block_start = src.find('"email", "copy", "write", "draft"')
    block       = src[block_start:block_start + 400]
    assert 'llm_mode       = "write"' in block
    assert 'council_letter = "C"' in block


def test_council_b_analysis_block_still_above_legacy_routing():
    """Iter 212m-159's analysis block must remain — Iter 212m-165 only
    ADDED a 'write' block, didn't move analysis."""
    src = pathlib.Path("/app/backend/services/llm.py").read_text()
    a_idx = src.find('if mode == "analysis":')
    w_idx = src.find('if mode == "write":')
    legacy_idx = src.find("# ── Legacy mode routing")
    assert a_idx != -1 and w_idx != -1 and legacy_idx != -1
    # Both council blocks must sit BEFORE the legacy routing.
    assert a_idx < legacy_idx
    assert w_idx < legacy_idx


def test_legacy_swift_caller_unaffected():
    """A regression guard: mode='code' + review_mode='swift' (the most
    common production path) must STILL trigger the swift block, only
    'analysis' and 'write' are excluded."""
    src = pathlib.Path("/app/backend/services/llm.py").read_text()
    # Confirm the gating set is exactly {analysis, write} — nothing
    # else (especially not "code" or "chat") was accidentally added.
    assert '{"analysis", "write"}' in src
