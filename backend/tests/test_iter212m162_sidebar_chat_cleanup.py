"""
Iter 212m-162 — Sidebar Health Scanner + chat Security Scan removal.

Verifies via source-code scan that:
  • Health Scanner is removed from the sidebar TOOLS list.
  • The HeartPulse icon import is dropped (no orphan import).
  • The chat composer security-scan button + badge JSX is removed.
  • Health Scan + Security Scan cards still EXIST in /tools as
    "Coming soon" with disabled CTAs.
"""

import pathlib

SIDEBAR  = pathlib.Path("/app/frontend/src/components/dashboard/v2/SidebarBound.jsx")
CHATPANE = pathlib.Path("/app/frontend/src/components/ChatPanel.jsx")
TOOLS    = pathlib.Path("/app/frontend/src/pages/ToolsPage.jsx")


def test_sidebar_no_longer_lists_health_scanner():
    src = SIDEBAR.read_text()
    # The literal TOOLS-array row for the health scanner must be gone.
    assert "label: \"Health Scanner\"" not in src
    # The sidebar id "health" must no longer appear in the TOOLS list
    # block (we only check the snippet, not the surrounding comments
    # which may still mention "health" historically).
    tools_block_start = src.find("const TOOLS = [")
    tools_block_end   = src.find("];", tools_block_start)
    assert tools_block_start != -1 and tools_block_end != -1
    tools_block = src[tools_block_start:tools_block_end]
    assert 'id: "health"' not in tools_block


def test_sidebar_dropped_heartpulse_import():
    """HeartPulse was only used for the removed Health Scanner entry —
    it must be cleaned up so the bundle drops a kB."""
    src = SIDEBAR.read_text()
    assert "HeartPulse" not in src


def test_chat_security_scan_button_removed():
    """The composer Security Scan button and its badges (testid
    `chat-security-scan-btn`, `chat-security-scan-badge`,
    `chat-security-scan-auto-badge`) must no longer be rendered."""
    src = CHATPANE.read_text()
    assert 'testid="chat-security-scan-btn"' not in src
    assert 'data-testid="chat-security-scan-badge"' not in src
    assert 'data-testid="chat-security-scan-auto-badge"' not in src


def test_tools_page_still_lists_health_and_security_as_coming_soon():
    """Health Scan + Security Scan must still be listed as locked
    Coming-soon cards on /tools — that's where they were moved to."""
    src = TOOLS.read_text()
    # Health Scan card
    assert 'id: "health-scan"' in src
    assert '"Health Scan"' in src
    # Security Scan card
    assert 'id: "security-scan"' in src
    assert '"Security Scan"' in src
    # Both must carry the Coming-soon eta tag (one literal for ALL four)
    assert 'eta: "Coming soon"' in src
    # The CTA must remain disabled (locked Coming-soon state)
    assert "disabled" in src
    assert ">Coming soon<" in src or "Coming soon" in src


def test_tools_page_cta_button_is_disabled():
    """The Coming-soon CTA must be a `<button … disabled …>` — not a
    link.  Locked state is the entire point of this iteration."""
    src = TOOLS.read_text()
    # Find the CTA testid
    assert 'data-testid={`tools-card-${tool.id}-cta`}' in src
    # The button containing that testid must be `disabled`.  We scan
    # a 200-char window around the testid to assert.
    idx = src.find('data-testid={`tools-card-${tool.id}-cta`}')
    window = src[max(0, idx - 200): idx + 200]
    assert "disabled" in window, "CTA button must remain disabled"
