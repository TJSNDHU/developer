/**
 * LiveStepFloatingCard.test.jsx — Iter 302 (Frontend QA Charter Layer 1 audit)
 *
 * State-sync tests for the floating step card driven by SSE step
 * frames. Uses vitest's fake timers to advance past the 3s auto-close
 * delay without waiting real time.
 */
import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, act } from "@testing-library/react";
import LiveStepFloatingCard from "../LiveStepFloatingCard.jsx";


describe("LiveStepFloatingCard — state-sync behavior (iter302)", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("reaches-correct-terminal-state: last-step .done=true flips data-done AND fires onClose after 3s", () => {
    const onClose = vi.fn();
    const { rerender } = render(
      <LiveStepFloatingCard
        steps={[{ text: "Reading repo", done: false }]}
        provider="claude" tokens={1234}
        onClose={onClose}
      />
    );
    const card = screen.getByTestId("live-step-floating-card");
    expect(card.getAttribute("data-done")).toBe("false");
    expect(onClose).not.toHaveBeenCalled();

    // Done frame arrives — data-done must flip immediately.
    rerender(
      <LiveStepFloatingCard
        steps={[
          { text: "Reading repo", done: false },
          { text: "Done", done: true },
        ]}
        provider="claude" tokens={1234}
        onClose={onClose}
      />
    );
    expect(
      screen.getByTestId("live-step-floating-card").getAttribute("data-done")
    ).toBe("true");
    // Auto-close scheduled at 3000ms — advance the fake clock.
    act(() => vi.advanceTimersByTime(3000));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("clears-stale-prior-state: empty steps rerender unmounts the card entirely", () => {
    const { rerender, container } = render(
      <LiveStepFloatingCard
        steps={[{ text: "Reading repo", done: false }]}
        provider="claude" tokens={0}
        onClose={vi.fn()}
      />
    );
    expect(screen.getByTestId("live-step-floating-card")).toBeInTheDocument();

    // Parent resets steps (new turn starting). The card MUST unmount
    // — otherwise a ghost card from the previous turn lingers.
    rerender(
      <LiveStepFloatingCard
        steps={[]} provider="claude" tokens={0}
        onClose={vi.fn()}
      />
    );
    expect(screen.queryByTestId("live-step-floating-card")).toBeNull();
    expect(container.firstChild).toBeNull();
  });

  it("race-condition: activePhase reflects THE LAST step, never a stale earlier one", () => {
    // If a stale step frame arrived out of order (or the parent
    // forgot to append and instead replaced), the active pill MUST
    // match the LAST step in the current array — not any previous one.
    const { rerender } = render(
      <LiveStepFloatingCard
        steps={[{ text: "Thinking hard", done: false }]}
        provider="claude" tokens={0}
        onClose={vi.fn()}
      />
    );
    // First pill active is the thinking phase.
    expect(
      screen.getByTestId("live-step-pill-thinking")
        .getAttribute("data-active")
    ).toBe("true");

    // Engine moved to writing. Active pill must FLIP to writing;
    // thinking must be seen-but-not-active.
    rerender(
      <LiveStepFloatingCard
        steps={[
          { text: "🤔 Thinking hard", done: false },
          { text: "✍️ Writing files to backend/", done: false },
        ]}
        provider="claude" tokens={0}
        onClose={vi.fn()}
      />
    );
    expect(
      screen.getByTestId("live-step-pill-writing")
        .getAttribute("data-active")
    ).toBe("true");
    expect(
      screen.getByTestId("live-step-pill-thinking")
        .getAttribute("data-active")
    ).toBe("false");
  });
});
