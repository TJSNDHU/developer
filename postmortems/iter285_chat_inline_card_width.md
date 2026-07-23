# Iter 285 — Chat-inline cards overshot the composer width
Date: 2026-02-05
Regression tests:
- `test_regression_iter285_chat_inline_card_class_declared_in_css`
- `test_regression_iter285_container_queries_include_chat_inline_card`
- `test_regression_iter285_plan_approval_and_live_feed_use_wrapper`

## What happened
User's own screenshot: on a wide viewport, two chips visibly
spanned edge-to-edge of the browser — the "Plan ready — your
approval needed" card and the "LOOP LOOP_903 · LIVE FEED" panel.
Meanwhile the composer form below sat centered inside a
`clamp(16px, 17.25%, 240px)` horizontal inset. The visual
mismatch made the cards look like they belonged to a different
container.

## Root cause
`[data-testid="chat-form"].glass-composer` had a horizontal
padding rule in `index.css` that constrained ONLY the composer.
`[data-testid="chat-messages"]` shared the same rule. But
`PlanApprovalCard` and `LoopLiveFeed` rendered as siblings with
no wrapper — inheriting full container width.

## Fix
Added a new CSS class `.chat-inline-card` with the same
`padding-left` and `padding-right` clamp as the composer, and
extended both `@container chat-panel (max-width: 900px)` and
`(max-width: 600px)` rules to include it. Then wrapped
PlanApprovalCard, LoopLiveFeed, and the Iter 284
agent-status-bar in `<div className="chat-inline-card">`.
Zero behavior change — pure CSS alignment.

## Why our tests missed it
- No fitness invariant asserted that sibling cards of the
  composer share its horizontal alignment.
- Prior visual QA relied on eye-balling; the mismatch was
  subtle on smaller windows (below 900px the padding shrinks
  to a flat 24px so all children looked aligned).

## Prevention (what's now permanent)
- 3 source-level regression tests:
  - `.chat-inline-card` class exists in CSS with matching clamps.
  - Both `@container` responsive blocks include the class.
  - PlanApprovalCard + LoopLiveFeed are wrapped in the class.

## MTTR
- Reported:  2026-02-06T00:05:00Z
- Deployed:  2026-02-06T00:30:00Z
- Total:     ~0.42 h

## Not-follow-ups
- Not migrating every sibling card in the chat area to the new
  class in one sweep — scope was the two visible in the user's
  screenshot. Others get retrofitted opportunistically as they
  are touched (AGENTS.md "characterization testing" rule).
