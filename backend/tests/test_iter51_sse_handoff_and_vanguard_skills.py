"""
tests/test_iter51_sse_handoff_and_vanguard_skills.py
====================================================

Iter 51 — covers two new features:

1) **SSE Task Progress Streamer** — when the chat orchestrator returns a
   result carrying a `task_id` (Mode D→C handoff, or any auto-enqueue
   path) the SSE stream MUST emit a `task_handoff` frame BEFORE the
   meta+content frames, and `_persist_turn` MUST store
   `shipped_task_id` on the assistant turn (so reload keeps the live
   ShipStatusCard rendered).

2) **Vanguard skills** — `pci-compliance.md` and `privacy-by-design.md`
   files exist on disk AND `skill_context_injector.select_skills`
   returns them for the expected trigger keywords.
"""
from __future__ import annotations
import os
import re

from services.skill_context_injector import (
    build_skill_context, select_skills,
)


# ─── Vanguard skills ─────────────────────────────────────────────────────

_SKILLS_DIR = os.path.join(
    os.path.dirname(__file__), "..", "vanguard_skills"
)


def test_pci_skill_file_exists():
    """The PCI skill markdown must ship on disk."""
    path = os.path.join(_SKILLS_DIR, "pci-compliance.md")
    assert os.path.exists(path), f"missing: {path}"
    with open(path, encoding="utf-8") as fh:
        body = fh.read()
    # Sanity: must mention the actual security rules, not be a stub.
    assert "PCI" in body
    assert "webhook" in body.lower()
    assert "CVV" in body or "cvv" in body.lower()


def test_privacy_skill_file_exists():
    """The privacy-by-design skill markdown must ship on disk."""
    path = os.path.join(_SKILLS_DIR, "privacy-by-design.md")
    assert os.path.exists(path), f"missing: {path}"
    with open(path, encoding="utf-8") as fh:
        body = fh.read()
    assert "GDPR" in body
    assert "data minimisation" in body.lower() or "data minimization" in body.lower()
    assert "right to be forgotten" in body.lower() or "right to erasure" in body.lower()


def test_pci_skill_triggers_on_stripe_keyword():
    """Asking for a Stripe checkout must surface the PCI skill."""
    picks = select_skills("Add stripe checkout for $29 pro plan")
    names = [p[0] for p in picks]
    assert "pci-compliance.md" in names, names


def test_pci_skill_triggers_on_payment_keyword():
    picks = select_skills("build a payment webhook handler")
    names = [p[0] for p in picks]
    assert "pci-compliance.md" in names, names


def test_privacy_skill_triggers_on_gdpr_keyword():
    picks = select_skills("Add GDPR data export endpoint")
    names = [p[0] for p in picks]
    assert "privacy-by-design.md" in names, names


def test_privacy_skill_triggers_on_user_data_keyword():
    picks = select_skills(
        "Implement right to be forgotten — delete user data on close"
    )
    names = [p[0] for p in picks]
    assert "privacy-by-design.md" in names, names


def test_skills_combine_when_both_relevant():
    """Stripe + GDPR-flavoured task gets both skills + the always-on
    security-review checklist."""
    picks = select_skills(
        "Add Stripe subscriptions and GDPR data export for EU users"
    )
    names = [p[0] for p in picks]
    assert "pci-compliance.md" in names
    assert "privacy-by-design.md" in names
    assert "security-review.md" in names


def test_no_skills_for_irrelevant_prompt():
    """Casual conversation must NOT inject heavy security context."""
    out = build_skill_context("hi how are you")
    # security-review.md is the always-inject baseline; it's allowed.
    # But pci / privacy / auth must NOT show up.
    assert "PCI" not in out
    assert "GDPR" not in out


def test_max_skills_cap_respected():
    """Even a kitchen-sink prompt mustn't blow up the prompt budget."""
    picks = select_skills(
        "Build login + jwt auth + stripe payment + gdpr export "
        "with react form and fastapi backend"
    )
    # _MAX_SKILLS_PER_TASK is 3; plus the always-inject security-review.
    assert len(picks) <= 4, [p[0] for p in picks]


def test_build_skill_context_returns_markdown_block():
    out = build_skill_context("Add stripe checkout")
    assert out.startswith("[VANGUARD SECURITY SKILLS")
    # Section header naming is uppercased filename.
    assert "PCI COMPLIANCE" in out


# ─── SSE task_handoff frame contract ────────────────────────────────────

def test_persist_turn_signature_accepts_shipped_task_id():
    """`_persist_turn` must accept `shipped_task_id` kw without raising
    a TypeError. Iter 51 added it; if a future refactor drops it, every
    Mode D→C handoff silently loses its progress card on refresh."""
    import inspect
    from routers.chat import _persist_turn
    sig = inspect.signature(_persist_turn)
    assert "shipped_task_id" in sig.parameters, (
        f"shipped_task_id missing from _persist_turn signature: {sig}"
    )


def test_chat_stream_emits_task_handoff_frame_format():
    """The SSE generator builds `task_handoff` frames as
    `data: {"type":"task_handoff","task_id":...}\\n\\n`. We can't easily
    exercise the full generator without a full app fixture, but we can
    assert the literal frame string the source code emits matches the
    contract the frontend `api.js` consumer expects."""
    src_path = os.path.join(
        os.path.dirname(__file__), "..", "routers", "chat.py"
    )
    with open(src_path, encoding="utf-8") as fh:
        src = fh.read()
    # Frame type literal must be present.
    assert '"type": "task_handoff"' in src or '"type":"task_handoff"' in src
    # Must include the task_id field.
    assert "handoff_task_id" in src


def test_frontend_api_js_dispatches_task_handoff():
    """Mirror check on the frontend SSE consumer: it must route
    `payload.type === 'task_handoff'` to an `onTaskHandoff` callback."""
    src_path = os.path.join(
        os.path.dirname(__file__),
        "..", "..", "frontend", "src", "lib", "api.js",
    )
    with open(src_path, encoding="utf-8") as fh:
        src = fh.read()
    assert "task_handoff" in src
    assert "onTaskHandoff" in src


def test_chatpanel_renders_auto_handoff_progress_card():
    """The auto-handoff branch in ChatPanel.jsx must render
    ShipStatusCard when `m.shipped_task_id` is present without a fence."""
    src_path = os.path.join(
        os.path.dirname(__file__),
        "..", "..", "frontend", "src", "components", "ChatPanel.jsx",
    )
    with open(src_path, encoding="utf-8") as fh:
        src = fh.read()
    # auto-handoff testid is the unique identifier of the new block.
    assert re.search(r"auto-handoff-row-", src), "auto-handoff block missing"
    # ShipStatusCard must be the renderer.
    assert "ShipStatusCard" in src
    # onTaskHandoff callback wired in send().
    assert "onTaskHandoff" in src
