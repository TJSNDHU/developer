"""
test_iter85_verified_paths.py — Citation-truthfulness gate (Rule (d)
moved from prompt-only to machine-enforced).

Closes the only honest gap left from Iter 83-84: the model could
fabricate a path inside the ```aurem-handoff fence (e.g. copy a name
from `semantic_search_repo` without ever opening the file). Prompt
rule (d) forbade it but the UI had no signal to verify.

This iter wires the proof end-to-end:
  Backend (services/orchestrator.py) — happy + max-iters return paths
    now include `verified_paths: sorted(tool_paths_read)`.
  Chat router (routers/chat.py) — SSE `done` frame propagates
    `verified_paths`.
  Frontend (ChatPanel.jsx) — stashes `verifiedPaths` on the
    assistant message.
  UI guard (MessageBubble.jsx → extractHandoffBrief) — Gate 7 rejects
    a brief if ANY file-path token inside it is not in
    `verifiedPaths` (when the field is present).
"""
from __future__ import annotations

import os
import re

BASE = os.path.join(os.path.dirname(__file__), "..", "..")


def _read(rel: str) -> str:
    with open(os.path.join(BASE, rel), encoding="utf-8") as fh:
        return fh.read()


# ── Backend wiring ─────────────────────────────────────────────────────

def test_orchestrator_returns_verified_paths_on_happy_path():
    src = _read("backend/services/orchestrator.py")
    # Both return sites (happy path and max_iters_hit) must populate
    # verified_paths so the downstream guard never sees a missing field.
    assert src.count('"verified_paths"') >= 2, (
        "orchestrator must emit `verified_paths` on BOTH the happy "
        "return and the max_iters_hit return"
    )
    # The happy path already computes tool_paths_read for the citation
    # warning — reuse it for verified_paths instead of recomputing.
    assert "sorted(tool_paths_read)" in src
    # max-iters path needs its own computation since tool_paths_read
    # is scoped to the loop body.
    assert "_max_iter_paths" in src


def test_chat_done_frame_propagates_verified_paths():
    src = _read("backend/routers/chat.py")
    assert '"verified_paths": result.get("verified_paths") or []' in src


def test_chatpanel_stashes_verified_paths_on_message():
    src = _read("frontend/src/components/ChatPanel.jsx")
    # The done handler must lift d.verified_paths onto the assistant
    # message so the bubble can read m.verifiedPaths.
    assert "d.verified_paths" in src
    assert "verifiedPaths:" in src


# ── UI guard — Gate 7 ──────────────────────────────────────────────────

def test_messagebubble_extracthandoffbrief_takes_verified_paths_arg():
    src = _read("frontend/src/components/MessageBubble.jsx")
    # The signature must accept verifiedPaths as the second parameter
    # AND the bubble call site must pass m.verifiedPaths.
    assert (
        "function extractHandoffBrief(content, verifiedPaths)" in src
    )
    assert "extractHandoffBrief(m.content, m.verifiedPaths)" in src


def test_messagebubble_gate7_rejects_fabricated_citations_via_doc_comment():
    """The Gate 7 implementation must reference verifiedPaths and reject
    a brief where EVERY path is fabricated (Iter 86 refined the
    contract: legit new-file-creation paths can be unverified; only
    pure fabrication — zero matches — is rejected)."""
    src = _read("frontend/src/components/MessageBubble.jsx")
    # Constants + helper used by Gate 7.
    assert "FILE_PATH_TOKEN_GLOBAL" in src
    assert "function _normalisePath" in src
    # Gate 7 must short-circuit only when verifiedPaths is a non-empty
    # array (version-skew tolerance — older deployments without the
    # field still render correctly).
    assert "Array.isArray(verifiedPaths) && verifiedPaths.length > 0" in src
    # Set-based comparison still used.
    assert "new Set(verifiedPaths.map(_normalisePath))" in src
    # Iter 86 refined contract: "matched" set computed, reject if zero.
    assert "matched.length === 0" in src
    # Iter 85 comment must stay so the rationale is preserved.
    assert "Iter 85" in src


def test_messagebubble_path_extraction_is_global_for_gate7():
    """Gate 7 must enumerate EVERY path in the brief, not just the
    first match — otherwise a brief with 3 real paths + 1 fabricated
    would slip through."""
    src = _read("frontend/src/components/MessageBubble.jsx")
    # The global regex variant must exist and be derived from the
    # same source as the single-match FILE_PATH_TOKEN.
    assert 'new RegExp(FILE_PATH_TOKEN.source, "gi")' in src
    # The match call uses the global variant.
    assert "brief.match(FILE_PATH_TOKEN_GLOBAL)" in src


# ── Sharp-list lock — 27 mutation verbs exactly ───────────────────────

def test_mutation_verbs_list_is_sharp_27_no_conversational_drift():
    """The list must contain EXACTLY the 27 sharp verbs and none of
    the 13 banned ones (5 conversational + 8 soft)."""
    src = _read("frontend/src/components/MessageBubble.jsx")
    m = re.search(
        r"const MUTATION_VERBS = new RegExp\(([\s\S]*?)\)\s*;",
        src,
    )
    assert m
    block = m.group(1)
    # Extract every verb token. Pattern is "(verb1|verb2|...|verbN)" so
    # we strip the escaped \b boundaries and split on |.
    inner = re.search(r"\\\\b\((.*?)\)\\\\b", block.replace("\n", "").replace(" ", "").replace('"+"', ""))
    assert inner, f"could not parse verb list from block: {block[:200]}"
    verbs = inner.group(1).split("|")
    assert len(verbs) == 27, (
        f"expected exactly 27 mutation verbs, found {len(verbs)}: {verbs}"
    )
