"""
Iter 212m-163 — LoopModeToggle reinstated in chat composer.

After Iter 212m-149 temporarily replaced the toggle with the
IntentTierIndicator, Iter 212m-163 brings the toggle BACK alongside
the Tier Indicator so founder/admin/unlimited users can force the
full Plan → Execute → Verify → Ship pipeline regardless of what the
Intent Gateway picks.

Block 1 of the pre-launch aggression test (founder spec).
Verifies via source scan:
  • LoopModeToggle JSX is mounted in the composer toolbar.
  • IntentTierIndicator still rendered (the two coexist).
  • `locked={!isLoopUnlocked}` drives the variant — founder unlocked,
    everyone else sees the locked "Loop · soon" pill.
  • `isLoopUnlockedSync` still keys on is_admin / is_unlimited /
    tier==='founder' (no regression of the unlock helper).
"""

import pathlib

CHATPANE = pathlib.Path("/app/frontend/src/components/ChatPanel.jsx")
TOGGLE   = pathlib.Path("/app/frontend/src/components/LoopModeToggle.jsx")
UTILS    = pathlib.Path("/app/frontend/src/utils/chatTextUtils.js")


def test_loop_mode_toggle_jsx_invoked_in_composer():
    src = CHATPANE.read_text()
    # The JSX tag must exist (not just the import).
    import re
    m = re.search(r"<LoopModeToggle\s+value=\{execMode\}", src)
    assert m is not None, "<LoopModeToggle value={execMode}…> missing from ChatPanel"


def test_loop_mode_toggle_inside_composer_toolbar():
    """The toggle must sit AFTER the composer-toolbar opener and
    BEFORE the chat-send button (preserves Iter 212m-103 layout)."""
    src = CHATPANE.read_text()
    import re
    toolbar = src.find('<div className="composer-toolbar">')
    m = re.search(r"<LoopModeToggle\s+value=\{execMode\}", src)
    send    = src.find('data-testid="chat-send"')
    assert toolbar < m.start() < send


def test_intent_tier_indicator_still_rendered_alongside_toggle():
    """Iter 212m-163 keeps both signals visible — the Gateway's tier
    pick (auto-routing) AND the manual Loop pill.  Removing either
    one regresses the design contract."""
    src = CHATPANE.read_text()
    assert "<IntentTierIndicator liveText={input}" in src


def test_loop_mode_toggle_uses_islooptest_unlocked():
    """The `locked` prop must be driven by `isLoopUnlocked` so the
    pill auto-swaps between the orange unlocked and the dashed amber
    locked variants based on the user's tier/admin flags."""
    src = CHATPANE.read_text()
    assert "locked={!isLoopUnlocked}" in src


def test_isloopt_unlocked_keys_on_admin_unlimited_founder():
    src = UTILS.read_text()
    assert "u.is_admin" in src
    assert "u.is_unlimited" in src
    assert 'u.tier === "founder"' in src


def test_loop_mode_toggle_locked_variant_has_lock_icon():
    """The locked pill (non-founder) must show a lock icon + the
    'Loop · soon' label so users know it's coming."""
    src = TOGGLE.read_text()
    assert 'data-testid="loop-mode-toggle-locked"' in src
    assert "Loop · soon" in src
    assert "Lock" in src  # lucide-react Lock icon


def test_loop_mode_toggle_unlocked_variant_text():
    """The unlocked pill (founder) must render the OFF/ON state in
    the same JetBrains Mono pill that Iter 212m-103 specced."""
    src = TOGGLE.read_text()
    assert 'data-testid="loop-mode-toggle"' in src
    assert "Loop on"  in src
    assert "Loop off" in src
    # Orange #FF6608 fill on the ON state per design system.
    assert "#FF6608" in src
