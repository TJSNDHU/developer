/**
 * StepCards.test.jsx — Iter 302 (Frontend QA Charter Layer 1 audit)
 *
 * State-sync tests for the inline step-cards rendered inside an
 * assistant chat bubble. Same 3-test template.
 */
import React from "react";
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import StepCards from "../StepCards.jsx";


describe("StepCards — state-sync behavior (iter302)", () => {
  it("reaches-correct-terminal-state: last step .done=true stops the streaming pulse indicator", () => {
    const { rerender } = render(
      <StepCards
        steps={[{ text: "Reading repo tree" }, { text: "Writing files" }]}
        streaming={true}
      />
    );
    // While streaming, the container carries a streaming marker.
    const wrap = screen.getByTestId("step-cards");
    expect(wrap.getAttribute("data-streaming")).toBe("true");
    // Both step texts render.
    expect(wrap.textContent).toContain("Reading repo tree");
    expect(wrap.textContent).toContain("Writing files");

    // Terminal — streaming ends. The marker must flip in the SAME
    // render — a lingering "true" is the exact bug class the charter
    // was written for.
    rerender(
      <StepCards
        steps={[{ text: "Reading repo tree" }, { text: "Done" }]}
        streaming={false}
      />
    );
    expect(
      screen.getByTestId("step-cards").getAttribute("data-streaming")
    ).toBe("false");
  });

  it("clears-stale-prior-state: rerender with an empty steps list unmounts the group entirely", () => {
    const { rerender, container } = render(
      <StepCards steps={[{ text: "Thinking" }]} streaming={true} />
    );
    expect(screen.getByTestId("step-cards")).toBeInTheDocument();

    // A new turn begins — parent resets steps to []. The group must
    // NOT survive; otherwise the previous turn's cards persist.
    rerender(<StepCards steps={[]} streaming={false} />);
    expect(screen.queryByTestId("step-cards")).toBeNull();
    expect(screen.queryByText(/thinking/i)).toBeNull();
    expect(container.firstChild).toBeNull();
  });

  it("race-condition: null / undefined steps return null just like []", () => {
    // Some upstream code paths pass `undefined` before the first
    // step lands. The component must handle this identically to
    // `[]` — no crash, no ghost card. Locks against a future
    // "if (!steps)" refactor forgetting the undefined branch.
    const { container: c1 } = render(<StepCards steps={null} streaming={false} />);
    expect(c1.firstChild).toBeNull();
    const { container: c2 } = render(<StepCards steps={undefined} streaming={false} />);
    expect(c2.firstChild).toBeNull();
  });
});
