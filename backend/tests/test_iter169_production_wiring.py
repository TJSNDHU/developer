"""Iter 169 — production-readiness fixes:
  1. security-review.md → Solana section removed
  2. frontend-security.md → PWA/Mobile/Biometric trimmed
  3. vanguard_verify_agent.py → OpenRouter (no Emergent SDK)
  4. agents.py → CoderAgent loads Rule 6/7 + Vanguard skills
  5. orchestrator.py → chat path injects skills on EXECUTE turns
  6. cto_projects.py → Brain V1 reads retired, V2 throughout
"""
from pathlib import Path

ROOT = Path(__file__).parent.parent
SKILLS = ROOT / "vanguard_skills"
SERVICES = ROOT / "services"
ROUTERS = ROOT / "routers"


# ── Fix 1 ───────────────────────────────────────────────────────────

def test_security_review_solana_section_removed():
    text = (SKILLS / "security-review.md").read_text()
    assert "Blockchain Security (Solana)" not in text
    assert "@solana/web3.js" not in text
    assert "verifyWalletOwnership" not in text


def test_security_review_renumbers_cleanly():
    text = (SKILLS / "security-review.md").read_text()
    # After removing section 9, the old section 10 must still exist as
    # a recognised header — even if renumbered to 9.
    assert "Dependency Security" in text
    # And the now-missing section 9 heading shouldn't reappear.
    assert "### 9. Blockchain" not in text


# ── Fix 2 ───────────────────────────────────────────────────────────

def test_frontend_security_pwa_mobile_biometric_trimmed():
    text = (SKILLS / "frontend-security.md").read_text()
    assert "Progressive Web App Security" not in text
    assert "Mobile and Responsive Security" not in text
    assert "WebAuthn implementation" not in text
    assert "Service Worker security" not in text


# ── Fix 3 ───────────────────────────────────────────────────────────

def test_vanguard_verify_uses_openrouter():
    text = (SERVICES / "vanguard_verify_agent.py").read_text()
    assert "from emergentintegrations" not in text
    assert "import emergentintegrations" not in text
    assert 'os.environ.get("EMERGENT_LLM_KEY"' not in text
    assert "OPENROUTER_API_KEY" in text
    assert "call_openrouter_model" in text


def test_vanguard_verify_uses_openrouter_slug():
    text = (SERVICES / "vanguard_verify_agent.py").read_text()
    assert "anthropic/claude-sonnet-4-5-20250929" in text


# ── Fix 4 ───────────────────────────────────────────────────────────

def test_agents_have_hard_rules_block():
    text = (SERVICES / "agents.py").read_text()
    assert "_AGENT_HARD_RULES" in text
    # Rule 6 — frontend build check
    assert "FRONTEND BUILD CHECK" in text
    # Rule 7 — read before you write
    assert "READ BEFORE YOU WRITE" in text
    # Escape hatch when the agent doesn't have the file in context
    assert "NEED_FILE" in text


def test_coder_agent_injects_skills():
    text = (SERVICES / "agents.py").read_text()
    coder_start = text.index("class CoderAgent")
    coder_end = text.index("\nclass ", coder_start + 1)
    coder_block = text[coder_start:coder_end]
    assert "build_skill_context" in coder_block, (
        "CoderAgent must inject Vanguard skills into its system prompt"
    )
    assert "_AGENT_HARD_RULES" in coder_block, (
        "CoderAgent system prompt must include the agent hard rules"
    )


# ── Fix 5 ───────────────────────────────────────────────────────────

def test_orchestrator_build_persona_injects_skills():
    text = (SERVICES / "orchestrator.py").read_text()
    # The wiring lives inside build_persona; pin both the import and the
    # call to avoid silent regressions.
    bp_start = text.index("def build_persona")
    bp_end = text.index("\ndef ", bp_start + 1)
    bp_block = text[bp_start:bp_end]
    assert "from .skill_context_injector import build_skill_context" in bp_block
    assert "skill_block = build_skill_context(prompt)" in bp_block


# ── Fix 6 ───────────────────────────────────────────────────────────

def test_cto_projects_uses_brain_v2_for_reads():
    text = (ROUTERS / "cto_projects.py").read_text()
    # V1 read function must be gone from this router.
    assert "get_brain_context" not in text, (
        "Brain V1 read path still imported in cto_projects.py"
    )
    # V2 must appear in at least the warm start + both worker paths.
    assert text.count("get_brain_v2") >= 4
    assert text.count("format_brain_for_agent") >= 2
