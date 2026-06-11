"""
Iter 124c — Persona must enforce ZERO permission-asking on read-only ops
and MUST have a dedicated INVENTORY MODE that mandates full answer in one
turn (no 'want me to summarise each?' stalls).
"""
from __future__ import annotations


def test_top_of_mind_rules_block_exists():
    from services.orchestrator import AUREM_CTO_PERSONA
    # Must be near the very top so the model attends to it first.
    head = AUREM_CTO_PERSONA[:2000]
    assert "TOP-OF-MIND" in head
    assert "READ-ONLY OPS NEVER REQUIRE PERMISSION" in head
    assert "ANSWER COMPLETELY ON FIRST TURN" in head


def test_inventory_mode_section_exists():
    from services.orchestrator import AUREM_CTO_PERSONA
    assert "INVENTORY MODE" in AUREM_CTO_PERSONA
    # The mode must explicitly cover counting/listing
    assert "how many" in AUREM_CTO_PERSONA.lower()
    assert "list_repo_files" in AUREM_CTO_PERSONA
    # Must require finishing the work in the same turn
    assert "ANSWER COMPLETELY" in AUREM_CTO_PERSONA


def test_persona_lists_forbidden_permission_openers():
    """Spell out every banned phrase so the model can't sneak one through."""
    from services.orchestrator import AUREM_CTO_PERSONA
    head = AUREM_CTO_PERSONA[:2500]
    for banned in (
        "Would you like me to",
        "Shall I",
        "Want me to",
        "Should I",
    ):
        assert banned in head, f"missing forbidden opener: {banned}"


def test_inventory_mode_mandates_parallel_reads():
    """Looking up 10 files must happen in ONE turn, not 10 turns."""
    from services.orchestrator import AUREM_CTO_PERSONA
    # Find the INVENTORY MODE section
    start = AUREM_CTO_PERSONA.find("INVENTORY MODE")
    assert start != -1
    section = AUREM_CTO_PERSONA[start:start + 3000]
    assert "PARALLEL" in section
    assert "ONE TURN" in section


def test_inventory_mode_forbids_followup_permission_ask():
    """The 'want me to detail each?' anti-pattern must be called out."""
    from services.orchestrator import AUREM_CTO_PERSONA
    start = AUREM_CTO_PERSONA.find("INVENTORY MODE")
    section = AUREM_CTO_PERSONA[start:start + 3000]
    # The example MUST show this anti-pattern as forbidden
    assert "want me to detail each" in section.lower() \
        or "Do NOT ask permission to keep going" in section
