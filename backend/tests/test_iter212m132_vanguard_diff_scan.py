"""
Iter 212m-132 — Diff-aware Vanguard scan + send-button fix.

Coverage:
  • changed_lines_for_file() — returns the 1-indexed set of NEW/
    MODIFIED line numbers between base and new content.
  • changed_lines_map() — per-file roll-up.
  • filter_findings_to_changed_lines() — keeps findings on changed
    lines, drops the rest, returns both halves for audit.
  • verify_patch() with base_blocks — regex findings on
    pre-existing lines are skipped; findings on patched lines are
    still surfaced and still block per the configured threshold.
  • verify_patch() without base_blocks — legacy behaviour
    preserved bit-for-bit.

Send-button fix (Bug #1 of this iter) — tested via a source-
pattern contract: the onClick handler must NOT depend on
`e.currentTarget.disabled`, and the button must NOT have a
`disabled` attribute (we use `aria-disabled` instead so the click
always lands).
"""
from __future__ import annotations

import asyncio

import pytest


# ──────────────────────────────────────────────────────────────────
# 1) changed_lines_for_file — diff arithmetic
# ──────────────────────────────────────────────────────────────────
def test_changed_lines_pure_addition_at_end():
    from services.vanguard_verify_agent import changed_lines_for_file
    base = "line1\nline2\nline3\n"
    new  = "line1\nline2\nline3\nNEW_LINE\n"
    assert changed_lines_for_file(base, new) == {4}


def test_changed_lines_pure_addition_at_top():
    from services.vanguard_verify_agent import changed_lines_for_file
    base = "line1\nline2\n"
    new  = "NEW_LINE\nline1\nline2\n"
    # The new content gets a fresh line 1 inserted at the top.
    assert changed_lines_for_file(base, new) == {1}


def test_changed_lines_replace_middle():
    from services.vanguard_verify_agent import changed_lines_for_file
    base = "a\nb\nc\nd\ne\n"
    new  = "a\nb\nMODIFIED\nd\ne\n"
    assert changed_lines_for_file(base, new) == {3}


def test_changed_lines_delete_only_returns_empty():
    """Deleting lines from base doesn't introduce ANY new lines —
    nothing to scan in the patch."""
    from services.vanguard_verify_agent import changed_lines_for_file
    base = "a\nb\nc\n"
    new  = "a\nc\n"
    assert changed_lines_for_file(base, new) == set()


def test_changed_lines_no_base_means_all_lines_new():
    """Brand-new file (no base content) → every non-empty line in
    the new content counts as a changed line."""
    from services.vanguard_verify_agent import changed_lines_for_file
    new = "line1\nline2\nline3"
    assert changed_lines_for_file("", new) == {1, 2, 3}


def test_changed_lines_empty_new_returns_empty():
    from services.vanguard_verify_agent import changed_lines_for_file
    assert changed_lines_for_file("anything", "") == set()


def test_changed_lines_no_change_returns_empty():
    from services.vanguard_verify_agent import changed_lines_for_file
    src = "a\nb\nc\n"
    assert changed_lines_for_file(src, src) == set()


# ──────────────────────────────────────────────────────────────────
# 2) changed_lines_map — per-file
# ──────────────────────────────────────────────────────────────────
def test_changed_lines_map_multi_file():
    from services.vanguard_verify_agent import changed_lines_map
    base = {
        "a.py": "x = 1\ny = 2\n",
        "b.py": "p = 3\nq = 4\n",
    }
    new = {
        "a.py": "x = 1\nNEW\ny = 2\n",         # added at line 2
        "b.py": "p = 3\nq = 4\n",              # unchanged
        "c.py": "brand_new = True\n",          # brand-new file
    }
    m = changed_lines_map(base, new)
    assert m["a.py"] == {2}
    assert m["b.py"] == set()
    assert m["c.py"] == {1}


# ──────────────────────────────────────────────────────────────────
# 3) filter_findings_to_changed_lines — keep vs drop
# ──────────────────────────────────────────────────────────────────
def test_filter_findings_keeps_changed_drops_preexisting():
    from services.vanguard_verify_agent import filter_findings_to_changed_lines
    findings = [
        {"file": "a.py", "line": 2, "severity": "CRITICAL", "rule": "introduced"},
        {"file": "a.py", "line": 99, "severity": "CRITICAL", "rule": "preexisting"},
        {"file": "b.py", "line": 5, "severity": "HIGH",     "rule": "untouched_file_kept"},
        {"file": "a.py", "line": 0, "severity": "MEDIUM",   "rule": "no_line_kept"},
    ]
    line_map = {"a.py": {2, 10}}  # b.py NOT in map → kept as a safety default
    kept, dropped = filter_findings_to_changed_lines(findings, line_map)
    rules_kept    = {f["rule"] for f in kept}
    rules_dropped = {f["rule"] for f in dropped}
    assert "introduced"          in rules_kept
    assert "untouched_file_kept" in rules_kept
    assert "no_line_kept"        in rules_kept
    assert "preexisting"         in rules_dropped
    assert dropped[0]["_skipped_reason"] == "pre_existing"


# ──────────────────────────────────────────────────────────────────
# 4) verify_patch with base_blocks — pre-existing critical does NOT
#    block, freshly-introduced critical DOES.
# ──────────────────────────────────────────────────────────────────
def test_verify_patch_diff_mode_skips_preexisting(monkeypatch):
    """End-to-end contract: when base_blocks is supplied, the
    overall `pass` reflects ONLY introduced findings. The regex
    floor + LLM agent are both bypassed via monkeypatch so the
    test is hermetic (no API key needed)."""
    import services.vanguard_verify_agent as vva
    import services.vanguard_config as vcfg

    # Force config: enabled=True, block_level=CRITICAL.
    async def fake_settings(_mode):
        return (True, "CRITICAL")
    monkeypatch.setattr(vcfg, "get_mode_settings", fake_settings)

    # Regex finds TWO criticals — line 1 (pre-existing) + line 5 (new).
    fake_regex = [
        {"file": "x.py", "line": 1, "severity": "CRITICAL",
         "rule": "preexisting_secret", "message": "old hardcoded key"},
        {"file": "x.py", "line": 5, "severity": "CRITICAL",
         "rule": "new_eval", "message": "new eval call"},
    ]
    monkeypatch.setattr(vva, "scan_file_blocks", lambda _b: fake_regex)

    # Stub LLM + E2B to no-op pass.
    async def fake_llm(*_a, **_kw):
        return {"pass": True, "findings": [], "summary": "stub", "model": "stub"}
    async def fake_e2b(*_a, **_kw):
        return {"pass": True, "skipped": True, "reason": "stub"}
    monkeypatch.setattr(vva, "_llm_review", fake_llm)
    monkeypatch.setattr(vva, "_e2b_smoke", fake_e2b)

    base = {"x.py": "OLD\nL2\nL3\nL4\nL5\n"}
    new  = {"x.py": "OLD\nL2\nL3\nL4\nINTRODUCED_EVAL\n"}

    res = asyncio.run(vva.verify_patch(
        new, repo_ctx="o/r@main", mode="swift", base_blocks=base,
    ))
    # Pre-existing critical was DROPPED — pass=False from the
    # introduced one only, but the dropped one is still surfaced
    # in regex.skipped_preexisting for audit.
    assert res["diff_mode"] is True
    assert res["regex"]["count"] == 1            # only the new eval kept
    assert res["regex"]["blocked"] is True       # blocks the commit
    assert res["pass"] is False                  # ← was the bug: pre-existing
                                                  #   would also block
    assert len(res["regex"]["skipped_preexisting"]) == 1
    assert res["regex"]["skipped_preexisting"][0]["rule"] == "preexisting_secret"


def test_verify_patch_diff_mode_clean_patch_passes(monkeypatch):
    """When the patch introduces NO new vulns but the base file is
    full of pre-existing ones, the commit must still pass."""
    import services.vanguard_verify_agent as vva
    import services.vanguard_config as vcfg

    async def fake_settings(_mode):
        return (True, "CRITICAL")
    monkeypatch.setattr(vcfg, "get_mode_settings", fake_settings)
    fake_regex = [
        {"file": "x.py", "line": 1, "severity": "CRITICAL",
         "rule": "preexisting1"},
        {"file": "x.py", "line": 2, "severity": "CRITICAL",
         "rule": "preexisting2"},
    ]
    monkeypatch.setattr(vva, "scan_file_blocks", lambda _b: fake_regex)
    async def fake_llm(*_a, **_kw):
        return {"pass": True, "findings": [], "summary": "stub", "model": "stub"}
    async def fake_e2b(*_a, **_kw):
        return {"pass": True, "skipped": True}
    monkeypatch.setattr(vva, "_llm_review", fake_llm)
    monkeypatch.setattr(vva, "_e2b_smoke", fake_e2b)

    base = {"x.py": "OLD_BAD\nMORE_BAD\nL3\nL4\nL5\n"}
    new  = {"x.py": "OLD_BAD\nMORE_BAD\nL3\nL4\nNEW_CLEAN_LINE\n"}

    res = asyncio.run(vva.verify_patch(
        new, repo_ctx="o/r@main", mode="swift", base_blocks=base,
    ))
    assert res["pass"] is True                    # ← real fix in action
    assert res["regex"]["count"] == 0             # both preexisting filtered out
    assert len(res["regex"]["skipped_preexisting"]) == 2


def test_verify_patch_legacy_no_base_blocks_unchanged(monkeypatch):
    """When base_blocks is None/omitted, behaviour must match the
    pre-iter-132 full-file scan exactly. Backward-compat contract."""
    import services.vanguard_verify_agent as vva
    import services.vanguard_config as vcfg

    async def fake_settings(_mode):
        return (True, "CRITICAL")
    monkeypatch.setattr(vcfg, "get_mode_settings", fake_settings)
    fake_regex = [
        {"file": "x.py", "line": 1, "severity": "CRITICAL", "rule": "any"},
    ]
    monkeypatch.setattr(vva, "scan_file_blocks", lambda _b: fake_regex)
    async def fake_llm(*_a, **_kw):
        return {"pass": True, "findings": [], "summary": "stub", "model": "stub"}
    async def fake_e2b(*_a, **_kw):
        return {"pass": True, "skipped": True}
    monkeypatch.setattr(vva, "_llm_review", fake_llm)
    monkeypatch.setattr(vva, "_e2b_smoke", fake_e2b)

    new = {"x.py": "anything\n"}
    res = asyncio.run(vva.verify_patch(
        new, repo_ctx="o/r@main", mode="swift",
    ))
    # No diff awareness — finding was kept, blocks the commit.
    assert res["pass"] is False
    assert res["regex"]["count"] == 1
    assert res["diff_mode"] is False


# ──────────────────────────────────────────────────────────────────
# 5) Brand-new files have every line in the changed set
# ──────────────────────────────────────────────────────────────────
def test_verify_patch_new_file_treated_as_all_changed(monkeypatch):
    import services.vanguard_verify_agent as vva
    import services.vanguard_config as vcfg

    async def fake_settings(_mode):
        return (True, "CRITICAL")
    monkeypatch.setattr(vcfg, "get_mode_settings", fake_settings)
    fake_regex = [
        {"file": "new_file.py", "line": 1, "severity": "CRITICAL",
         "rule": "must_block"},
    ]
    monkeypatch.setattr(vva, "scan_file_blocks", lambda _b: fake_regex)
    async def fake_llm(*_a, **_kw):
        return {"pass": True, "findings": [], "summary": "stub", "model": "stub"}
    async def fake_e2b(*_a, **_kw):
        return {"pass": True, "skipped": True}
    monkeypatch.setattr(vva, "_llm_review", fake_llm)
    monkeypatch.setattr(vva, "_e2b_smoke", fake_e2b)

    # base_blocks present but path missing → treated as empty base.
    base = {"some_other.py": "x"}
    new  = {"new_file.py": "API_KEY = 'sk_xxx'\n"}
    res = asyncio.run(vva.verify_patch(
        new, repo_ctx="o/r@main", mode="swift", base_blocks=base,
    ))
    # Whole new file is "changed", critical introduced → still blocks.
    assert res["pass"] is False
    assert res["regex"]["count"] == 1


# ──────────────────────────────────────────────────────────────────
# 6) Send button — source-pattern contract test
# ──────────────────────────────────────────────────────────────────
def test_send_button_uses_aria_disabled_not_native_disabled():
    """Bug #1 of iter 212m-132: the native `disabled` attribute on
    the send button could be stale relative to the just-updated
    React `input` state on the click frame, so the browser
    suppressed the click event. We now use `aria-disabled` for
    screen-readers and let send() itself gate on the conditions.
    This pin protects the fix from a future refactor."""
    src = open("/app/frontend/src/components/ChatPanel.jsx").read()
    # The send button block must contain aria-disabled but NOT a
    # plain `disabled=` ATTRIBUTE referencing input.trim().
    send_idx = src.find('data-testid="chat-send"')
    assert send_idx > 0
    # Pluck just the JSX block — bounded by the closing </button>.
    end_idx = src.find("</button>", send_idx)
    block = src[send_idx:end_idx]
    assert "aria-disabled" in block
    # The literal `disabled={!input` ATTRIBUTE form is what triggered
    # the bug — but `aria-disabled={!input` contains it as a substring,
    # so we strip aria-disabled occurrences out before checking.
    import re
    stripped = re.sub(r"aria-disabled=\{[^}]+\}", "", block)
    assert "disabled={!input" not in stripped
    # And the click handler must no longer reference currentTarget.disabled.
    assert "currentTarget.disabled" not in block
