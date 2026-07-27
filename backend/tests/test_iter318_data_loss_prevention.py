"""
test_iter318_data_loss_prevention.py — Iter 318

Bug 1a + Bug 1b + Bug 2 invariants for the data-loss / ship-integrity
class. Live incident: loop_678eea28436c4e nearly wiped the entire
README because the executor emitted the literal string
`[Rest of existing README content remains unchanged...]` as file
content, and the `.md → linter: skip` verify branch reported ok:true.

Two layers of tests:
  1. Pure unit tests over `services.loop_integrity_guard` — the
     reusable module that _do_execute, _do_ship and _do_verify all
     call into.
  2. Source-inspection tests over `services/loop_engine.py` and
     `services/loop_execute.py` proving the guards are wired into
     the three call sites (executor prompt ban + post-emission
     re-emit guard, pre-ship gate, verify skip-linter branch).

Spec: /app/memory/ITER_318_DATA_LOSS_SPEC.md
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from services.loop_integrity_guard import (
    SHRINK_FLOOR,
    check_file_integrity,
    find_elision_markers,
    has_deletion_intent,
    size_delta_violation,
)


_ENGINE_SRC = Path("/app/backend/services/loop_engine.py").read_text()
_EXEC_SRC   = Path("/app/backend/services/loop_execute.py").read_text()


# ═══════════════════════════════════════════════════════════════════
# Rule 1 · Elision-marker regex sweep (Bug 1b Rule 1)
# ═══════════════════════════════════════════════════════════════════

class TestElisionMarkers:
    def test_catches_live_incident_bracket_marker(self):
        """The exact marker the live loop_678eea28436c4e shipped."""
        body = (
            "# README\n\n"
            "## Table of Contents\n- a\n- b\n\n"
            "[Rest of existing README content remains unchanged...]\n"
        )
        hits = find_elision_markers(body)
        assert hits, "live-incident marker was NOT caught by Rule 1"
        assert hits[0]["pattern"] == "bracket_rest_of"

    @pytest.mark.parametrize(
        "body",
        [
            "code line\n... unchanged\nmore",
            "code line\n... snip",
            "line\n<!-- snip -->\nnext",
            "line\n<!-- ELIDED -->\nnext",
            "def f():\n    // ... unchanged\n    return 1",
            "# ... rest of file omitted\n",
            "code\n/* snip */\nmore",
            "template {{ rest }} more",
            "template {{ REMAINDER }} more",
            "[rest of file omitted]",
            "[Rest of the class remains unchanged]",
        ],
    )
    def test_catches_documented_marker_shapes(self, body):
        assert find_elision_markers(body), f"missed marker: {body!r}"

    @pytest.mark.parametrize(
        "body",
        [
            "",
            None,
            "just clean prose no markers here at all",
            "def f():\n    return 1\n",
            "# README\n\nRegular text with dots... in a sentence.",
            "This template uses {{ user.name }} placeholder.",
        ],
    )
    def test_clean_bodies_produce_no_hits(self, body):
        assert find_elision_markers(body) == []


# ═══════════════════════════════════════════════════════════════════
# Rule 2 · Size-delta guard  (Bug 1b Rule 2 + Rule 3)
# ═══════════════════════════════════════════════════════════════════

class TestSizeDelta:
    def test_shrink_below_floor_for_edit_action_is_flagged(self):
        # Live incident: multi-thousand-word README → ~250 bytes.
        v = size_delta_violation(
            submitted_bytes=250, repo_bytes=10_000,
            original_request="add one comment line to the top of README",
            action="edit",
        )
        assert v is not None
        # Rule 3 (byte_count) fires first for action='edit' — tighter
        # than Rule 2 and catches ambiguous prompts.
        assert v["rule_fired"] == "byte_count"
        assert v["shrink_ratio"] < SHRINK_FLOOR

    def test_growing_file_never_flagged(self):
        assert size_delta_violation(
            submitted_bytes=12_000, repo_bytes=10_000,
            original_request="add a section", action="edit",
        ) is None

    def test_small_shrink_within_floor_passes(self):
        # 20 % shrink → ratio 0.80 → well above 0.30 floor.
        assert size_delta_violation(
            submitted_bytes=8_000, repo_bytes=10_000,
            original_request="tighten prose", action="edit",
        ) is None

    def test_deletion_intent_lifts_rule_2_but_not_rule_3(self):
        # action="delete" bypasses Rule 3, and the deletion prompt
        # bypasses Rule 2 → the shrink is allowed through.
        assert size_delta_violation(
            submitted_bytes=0, repo_bytes=10_000,
            original_request="delete the deprecated section",
            action="delete",
        ) is None

    def test_rewrite_intent_lifts_rule_2_but_not_rule_3(self):
        # Rule 3 still fires for action='edit' even with rewrite
        # wording — the founder must set action='replace_full' if
        # they really want a full rewrite through the edit path.
        v = size_delta_violation(
            submitted_bytes=100, repo_bytes=10_000,
            original_request="rewrite from scratch",
            action="edit",
        )
        assert v is not None
        assert v["rule_fired"] == "byte_count"

    def test_new_file_never_flagged(self):
        # repo_bytes==0 → nothing to shrink from.
        assert size_delta_violation(
            submitted_bytes=500, repo_bytes=0,
            original_request="create a new file",
            action="edit",
        ) is None

    def test_has_deletion_intent_regex(self):
        assert has_deletion_intent("please DELETE this file")
        assert has_deletion_intent("wipe the config")
        assert has_deletion_intent("rewrite from scratch")
        assert not has_deletion_intent("add a comment")
        assert not has_deletion_intent(None)


# ═══════════════════════════════════════════════════════════════════
# Combined check_file_integrity — Bug 1b + Bug 2 shared entry point
# ═══════════════════════════════════════════════════════════════════

class TestCheckFileIntegrity:
    def test_elision_marker_takes_priority_over_size(self):
        # Marker present AND size shrink — must report elision_marker
        # first (spec: Rule 1 is the P0 defect).
        v = check_file_integrity(
            path="README.md",
            submitted_content="[Rest of README unchanged]",
            repo_bytes=10_000,
            original_request="add a comment",
            action="edit",
        )
        assert v is not None
        assert v["rule_fired"] == "elision_marker"
        assert v["offending_path"] == "README.md"
        assert v["marker_pattern"] == "bracket_rest_of"
        assert "unchanged" in v["marker_text"].lower()

    def test_clean_body_within_floor_passes(self):
        assert check_file_integrity(
            path="src/app.py",
            submitted_content="def f():\n    return 1\n" * 200,
            repo_bytes=10_000,
            original_request="refactor f", action="edit",
        ) is None

    def test_pure_size_delta_when_no_marker(self):
        v = check_file_integrity(
            path="README.md", submitted_content="x" * 100,
            repo_bytes=10_000,
            original_request="tighten prose", action="edit",
        )
        assert v is not None
        assert v["rule_fired"] in ("byte_count", "size_delta")

    def test_legit_delete_action_with_intent_prompt_passes(self):
        assert check_file_integrity(
            path="OLD.md", submitted_content="",
            repo_bytes=10_000,
            original_request="delete OLD.md", action="delete",
        ) is None


# ═══════════════════════════════════════════════════════════════════
# Bug 1a · Executor placeholder/elision emission ban
# (source-inspection over the prompt + post-emission guard)
# ═══════════════════════════════════════════════════════════════════

class TestBug1aExecutorPromptBan:
    def test_executor_prompt_bans_elision_language_in_engine_parliament(self):
        """The Parliament code-gen path (`_gen_via_parliament` inside
        _do_execute) must inject an explicit ban on elision markers
        into the task text so the LLM stops producing them at the
        source. Guard-only fixes (1b/2) fail when marker vocabulary
        drifts — 1a is the actual defect closer."""
        # Extract the _gen_via_parliament function body.
        m = re.search(
            r"async def _gen_via_parliament\(.*?\n(?=(?:                async def|            \S|        \w))",
            _ENGINE_SRC, re.DOTALL,
        )
        # Fallback: at least grep the whole file for the ban language
        # in a prompt-shaped context (the important thing is that the
        # executor prompt tells the LLM to emit FULL content).
        assert (
            "no elision" in _ENGINE_SRC.lower()
            or "no placeholder" in _ENGINE_SRC.lower()
            or "no `[rest of" in _ENGINE_SRC.lower()
            or "do not use placeholder" in _ENGINE_SRC.lower()
            or "never emit placeholder" in _ENGINE_SRC.lower()
        ), (
            "Bug 1a: _do_execute Parliament task_text must contain an "
            "explicit ban on elision / placeholder markers (e.g. "
            "'[Rest of ... unchanged]'). Currently the prompt only "
            "says 'return the complete new content' which the LLM "
            "interprets loosely and inserts placeholders for brevity."
        )

    def test_executor_prompt_bans_elision_language_in_loop_execute_module(self):
        """The legacy `_generate_one_inner` sys_msg (services/
        loop_execute.py) must also carry the ban — both call paths
        can fire depending on which routing branch is taken."""
        low = _EXEC_SRC.lower()
        assert (
            "no elision" in low
            or "no placeholder" in low
            or "do not use placeholder" in low
            or "never emit placeholder" in low
        ), (
            "Bug 1a: services/loop_execute.py sys_msg must explicitly "
            "ban elision/placeholder markers. Current wording "
            "('preserve any existing functionality that the task does "
            "not explicitly change') is the ambiguous instruction "
            "the LLM misinterpreted in loop_678eea28436c4e."
        )

    def test_executor_has_post_emission_integrity_guard(self):
        """After the LLM returns content and fences are stripped,
        the executor must call `find_elision_markers` (or the
        higher-level `check_file_integrity`) and refuse the file
        if markers are present. This is the second layer of Bug 1a
        — the ban at the prompt might still fail; the post-emission
        grep is the enforcement."""
        assert "loop_integrity_guard" in _ENGINE_SRC, (
            "Bug 1a post-emission: services/loop_engine.py must "
            "import from services.loop_integrity_guard so the "
            "executor can grep-block placeholder output BEFORE it "
            "lands in submitted_files."
        )
        assert (
            "find_elision_markers" in _ENGINE_SRC
            or "check_file_integrity" in _ENGINE_SRC
        ), (
            "Bug 1a post-emission: _do_execute must call "
            "find_elision_markers / check_file_integrity on the "
            "content the LLM produced. Currently no such call "
            "exists — placeholder text lands in submitted_files "
            "silently."
        )


# ═══════════════════════════════════════════════════════════════════
# Bug 1b · Pre-ship guard (source-inspection over _do_ship)
# ═══════════════════════════════════════════════════════════════════

class TestBug1bPreShipGuard:
    def test_do_ship_calls_integrity_guard(self):
        """_do_ship must run the elision + size-delta gates against
        every file in `files_dict` BEFORE setting `ship_pending` /
        transitioning to PAUSED_FOR_USER. Live incident evidence:
        without this gate, [Rest of ... unchanged] reached the
        awaiting_ship card and would have been committed on the
        founder's next click."""
        # Isolate the _do_ship function body (up to next `async def`
        # or `def ` at 4-space class indent).
        m = re.search(
            r"async def _do_ship\(.*?(?=\n    async def |\n    def )",
            _ENGINE_SRC, re.DOTALL,
        )
        assert m, "_do_ship not found in loop_engine.py"
        body = m.group(0)
        assert (
            "check_file_integrity" in body
            or "find_elision_markers" in body
        ), (
            "Bug 1b: _do_ship must call check_file_integrity (or "
            "find_elision_markers + size_delta_violation) against "
            "each file in files_dict BEFORE writing ship_pending. "
            "Currently the pre-ship path has no integrity guard "
            "and the awaiting_ship card silently held placeholder "
            "content in the live incident."
        )

    def test_integrity_guard_uses_distinct_terminal_state(self):
        """Spec Bug 1b: 'Loop transitions to a NEW distinct terminal
        state (proposed: failed_integrity_guard).' Founder must see
        an explicit 'ship blocked: <reason>' — NOT generic 'verify
        failed'. Implementation choice: FAILED state with a
        `kind="integrity_guard_rejected"` marker in event data."""
        assert (
            "integrity_guard_rejected" in _ENGINE_SRC
            or "failed_integrity_guard" in _ENGINE_SRC
        ), (
            "Bug 1b: _do_ship must emit a distinct integrity-guard "
            "failure marker (kind='integrity_guard_rejected' or a "
            "new LoopState.FAILED_INTEGRITY_GUARD) so the founder "
            "sees WHY the ship was blocked, not a generic 'failed'."
        )

    def test_original_bytes_by_path_persisted_for_ship_gate(self):
        """The pre-ship size-delta gate needs `repo_bytes` per path.
        The cleanest place to capture that is `_do_execute` when it
        fetches `current` from GitHub. Persist to
        `context['original_bytes_by_path'][path] = len(current)`."""
        assert "original_bytes_by_path" in _ENGINE_SRC, (
            "Bug 1b: _do_execute must record `len(current)` per "
            "fetched file into `self.context['original_bytes_by_path']"
            "` so _do_ship + _do_verify can enforce the size-delta "
            "rule without re-fetching from GitHub."
        )


# ═══════════════════════════════════════════════════════════════════
# Bug 2 · Verify-phase skip ≠ pass
# ═══════════════════════════════════════════════════════════════════

class TestBug2VerifySkipNotPass:
    def test_do_verify_runs_guard_on_skip_linter_branch(self):
        """`_do_verify` currently returns ok=True whenever the linter
        is 'skip' (unknown extension, .md, .yaml, etc.). Spec says:
        'a skipped linter should still run the size-delta and
        elision-marker checks.'"""
        m = re.search(
            r"async def _do_verify\(.*?(?=\n    async def |\n    def )",
            _ENGINE_SRC, re.DOTALL,
        )
        assert m, "_do_verify not found in loop_engine.py"
        body = m.group(0)
        assert (
            "_apply_integrity_guard_to_report" in body
            or "check_file_integrity" in body
            or "find_elision_markers" in body
        ), (
            "Bug 2: _do_verify must run the integrity guard against "
            "each submitted file — especially when the linter row "
            "shows linter='skip'. Currently skip masquerades as pass "
            "and the ship gate opens with placeholder content."
        )

    def test_apply_integrity_guard_helper_exists(self):
        """bug_testing_agent regression: the guard must be a callable
        helper so it can be re-applied AFTER every subset reverify
        merge inside the self-heal loop. Otherwise `verify_files` on
        a healed subset overwrites the earlier downgrade back to
        ok:true (with linter='skip') and reopens the incident."""
        assert "def _apply_integrity_guard_to_report" in _ENGINE_SRC, (
            "Iter 318 hardening: the integrity guard must be a "
            "method on LoopEngine (e.g. "
            "_apply_integrity_guard_to_report) so it can be re-run "
            "after each subset reverify. The bug_testing_agent RCA "
            "showed that inline-only guard code is silently undone "
            "by the self-heal reverify merge."
        )

    def test_guard_reapplied_after_subset_reverify_merge(self):
        """The self-heal loop rebuilds `report` from subset results
        (line ~1731). The guard MUST be re-applied on that fresh
        report — otherwise a `.md skip → ok:true` row overwrites
        the earlier downgrade."""
        m = re.search(
            r"async def _do_verify\(.*?(?=\n    async def |\n    def )",
            _ENGINE_SRC, re.DOTALL,
        )
        assert m, "_do_verify not found"
        body = m.group(0)
        # Count the guard-helper invocations inside the function
        # body — at least 2 (initial pass + post-heal re-sweep).
        count = body.count("_apply_integrity_guard_to_report")
        assert count >= 2, (
            "Iter 318 hardening: _do_verify must call "
            "_apply_integrity_guard_to_report BOTH after the initial "
            "verify_files() AND after each subset reverify merge "
            f"(found only {count} call(s)). bug_testing_agent showed "
            "the single-call path lets .md skip rows escape."
        )
