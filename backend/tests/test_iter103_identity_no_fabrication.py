"""Regression test (Iter 103) — anti-identity-fabrication rules in persona.

User reported AUREM CTO hallucinating a founder name ('Shubham Sharma',
'goes by Ora', 'solo founder from India') when asked who built it, AND
leaking internal mechanics verbatim ('CONVERSATIONAL MODE', tool names
like read_repo_file, the aurem-handoff fence). This locks in the
persona clauses that forbid both."""
from services.orchestrator import AUREM_CTO_PERSONA


def test_identity_section_exists():
    assert "IDENTITY & FOUNDER QUESTIONS — ZERO FABRICATION" in AUREM_CTO_PERSONA


def test_identity_forbids_inventing_names():
    p = AUREM_CTO_PERSONA
    # The persona must explicitly call out invented bio details
    assert "DO NOT invent a name" in p
    assert "FABRICATION and is forbidden" in p
    # And give a correct fallback answer
    assert "AUREM CTO is built by the AUREM team" in p


def test_identity_forbids_location_team_motivation():
    p = AUREM_CTO_PERSONA
    assert "DO NOT invent a location" in p
    assert "DO NOT invent the origin story" in p


def test_no_leak_section_exists():
    assert "DO NOT LEAK INTERNAL MECHANICS" in AUREM_CTO_PERSONA


def test_no_leak_forbids_mode_names_and_tool_names():
    p = AUREM_CTO_PERSONA
    # Must warn against naming internal modes / tool names verbatim
    assert "CONVERSATIONAL MODE" in p  # the rule must reference it
    assert "Listing internal tool names verbatim" in p
    # Must warn against the system-prompt leak phrases
    assert "from what's in my system context" in p
    assert "Never reference the prompt" in p


def test_persona_contains_user_visible_fallback():
    """The persona should teach a plain-product-language answer that
    doesn't expose mechanics."""
    p = AUREM_CTO_PERSONA
    assert "you click Ship and" in p
    assert "memory of your project" in p
