"""
Iter 285 — Chat-inline cards must match the composer's horizontal
inset so they visually align with the composer + messages.

Bug (user screenshot): PlanApprovalCard + LoopLiveFeed rendered
full-viewport-width, so both chips visibly overshot the composer's
edges on wide screens.

Fix: wrap both cards in a `<div className="chat-inline-card">`
whose horizontal padding uses the SAME `clamp(16px, 17.25%, 240px)`
as the composer.  Same rule cascades through the two container
queries that shrink the padding at <900px and <600px viewports.
"""
from __future__ import annotations
import re


def test_regression_iter285_chat_inline_card_class_declared_in_css():
    """
    `.chat-inline-card` MUST exist in index.css with the SAME
    horizontal padding as [data-testid="chat-form"].glass-composer.
    Otherwise the cards visibly overshoot the composer on wide
    viewports.
    """
    src = open("/app/frontend/src/index.css").read()

    # Extract the composer's horizontal clamp.
    m = re.search(
        r'\[data-testid="chat-form"\]\.glass-composer\s*\{'
        r'[^}]*padding:\s*[\d]+px\s+(clamp\([^)]*\))',
        src,
    )
    assert m, "composer padding rule with clamp() must exist"
    composer_clamp = m.group(1)

    # `.chat-inline-card` must use the same clamp for left+right padding.
    m2 = re.search(
        r'\.chat-inline-card\s*\{'
        r'[^}]*padding-left:\s*(clamp\([^)]*\))'
        r'[^}]*padding-right:\s*(clamp\([^)]*\))',
        src, re.DOTALL,
    )
    assert m2, ".chat-inline-card class must declare left+right padding clamps"
    assert m2.group(1) == composer_clamp, (
        f".chat-inline-card padding-left must match composer's "
        f"({m2.group(1)} != {composer_clamp})"
    )
    assert m2.group(2) == composer_clamp, (
        f".chat-inline-card padding-right must match composer's "
        f"({m2.group(2)} != {composer_clamp})"
    )


def test_regression_iter285_container_queries_include_chat_inline_card():
    """
    The two responsive rules (<900px and <600px viewport) must
    include `.chat-inline-card` in their selectors so the horizontal
    padding shrinks in step with the composer.
    """
    src = open("/app/frontend/src/index.css").read()
    for max_w, expected_px in [(900, 24), (600, 12)]:
        m = re.search(
            r"@container chat-panel \(max-width:\s*" + str(max_w) +
            r"px\)\s*\{([^}]*(?:\{[^}]*\})*[^}]*)\}",
            src, re.DOTALL,
        )
        assert m, f"@container max-width:{max_w}px rule must exist"
        block = m.group(1)
        assert ".chat-inline-card" in block, (
            f"@container ({max_w}px) block must include .chat-inline-card"
        )


def test_regression_iter285_plan_approval_and_live_feed_use_wrapper():
    """
    Both PlanApprovalCard and LoopLiveFeed MUST be rendered inside
    a `<div className="chat-inline-card">` wrapper in ChatPanel.jsx.
    """
    src = open("/app/frontend/src/components/ChatPanel.jsx").read()

    # PlanApprovalCard block
    idx = src.find("<PlanApprovalCard")
    assert idx > -1
    # Look 200 chars before for the wrapper.
    prefix = src[max(0, idx - 200): idx]
    assert 'className="chat-inline-card"' in prefix, (
        "PlanApprovalCard must be wrapped in a chat-inline-card div"
    )

    # LoopLiveFeed block
    idx2 = src.find("<LoopLiveFeed")
    assert idx2 > -1
    prefix2 = src[max(0, idx2 - 200): idx2]
    assert 'className="chat-inline-card"' in prefix2, (
        "LoopLiveFeed must be wrapped in a chat-inline-card div"
    )
