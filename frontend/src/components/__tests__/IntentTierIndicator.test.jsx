/**
 * IntentTierIndicator.test.jsx — Iter 296 (Frontend Layer 1, Batch 2)
 *
 * State-sync behavior tests — same 3-test template as
 * LoopStepBar.test.jsx (iter294) / AgentStatusBar.test.jsx (iter295).
 *
 * The tier dot is fed by two independent sources:
 *   1. `lastTier` — sticky, comes from the SSE `intent` frame after
 *      a turn lands.
 *   2. `liveText` — a debounced live classify call while the user
 *      is typing. In these tests we deliberately pass an empty
 *      `liveText` so the debounce useEffect is a no-op (early-return
 *      branch) — the invariants we're proving belong to the
 *      lastTier state-sync path, not to the API round-trip.
 *
 * All assertions read from the rendered DOM via `screen`. No prop
 * inspection, no source-string grep — RTL guards this by construction.
 */
import React from "react";
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import IntentTierIndicator from "../IntentTierIndicator.jsx";


describe("IntentTierIndicator — state-sync behavior (iter296)", () => {
  it("reaches-correct-terminal-state: lastTier='agentic' paints the AGENTIC label and data-tier attribute", () => {
    render(<IntentTierIndicator liveText="" lastTier="agentic" />);
    const label = screen.getByTestId("intent-tier-label");
    expect(label.textContent).toBe("AGENTIC");
    // The wrapper's data-tier attribute is what CSS + downstream
    // observers key off — it MUST reflect the terminal prop, not a
    // stale internal state.
    const wrap = screen.getByTestId("intent-tier-indicator");
    expect(wrap.getAttribute("data-tier")).toBe("agentic");
    // data-pending exists ONLY when nothing has classified yet.
    // With lastTier set, the pending marker must be absent — this is
    // the "known-tier landed" branch of the useEffect.
    expect(wrap.getAttribute("data-pending")).toBeNull();
  });

  it("clears-stale-prior-state: rerendering with a different lastTier flips the label AND the data-tier in one render", () => {
    // Start with CASUAL — the muted baseline.
    const { rerender } = render(
      <IntentTierIndicator liveText="" lastTier="casual" />
    );
    expect(screen.getByTestId("intent-tier-label").textContent).toBe("CASUAL");
    expect(
      screen.getByTestId("intent-tier-indicator").getAttribute("data-tier")
    ).toBe("casual");

    // The SSE `intent` frame arrives with tier=query. Both the label
    // AND the data-tier must flip in the SAME render — no lingering
    // CASUAL text (state-sync bug class from iter288).
    rerender(<IntentTierIndicator liveText="" lastTier="query" />);
    expect(screen.getByTestId("intent-tier-label").textContent).toBe("QUERY");
    expect(
      screen.getByTestId("intent-tier-indicator").getAttribute("data-tier")
    ).toBe("query");
    // Guard against a stale label surviving because of a missed useEffect
    // dep — the CASUAL text must be gone from the DOM entirely.
    expect(screen.queryByText("CASUAL")).toBeNull();
  });

  it("race-condition: no lastTier + no liveText renders the casual default with data-pending='true' (never a null DOM)", () => {
    // The iter281 regression: an empty tier used to make the whole
    // dot+label disappear from the composer toolbar, which broke the
    // CSS sibling selectors anchoring LoopModeToggle. The invariant
    // this test locks: even with NO tier and NO liveText, the
    // component still renders — falling back to the CASUAL theme —
    // AND marks itself data-pending so downstream code can tell
    // "haven't classified yet" apart from "genuinely casual".
    render(<IntentTierIndicator liveText="" lastTier={null} />);
    const wrap = screen.getByTestId("intent-tier-indicator");
    expect(wrap).toBeInTheDocument();
    expect(wrap.getAttribute("data-tier")).toBe("casual");
    expect(wrap.getAttribute("data-pending")).toBe("true");
    // Label still visible — the CSS anchor guarantee.
    expect(screen.getByTestId("intent-tier-label").textContent).toBe("CASUAL");
    // Dot still visible — the visual guarantee.
    expect(screen.getByTestId("intent-tier-dot")).toBeInTheDocument();
  });
});
