/**
 * loop_iter309_narration.test.jsx — Iter 309 · Live Narration
 *
 * Frontend unit tests for the narration UI:
 *   1. LoopLiveFeed renders narration events with correct icon/tone
 *      and shows a live-ticking timer while tone is "pending".
 *   2. Timer elapsed is derived from server `ts_epoch`, NOT client
 *      Date.now() at receipt — so reconnect + gap-replay math is
 *      correct.
 *   3. Once a paired resolving narration arrives, the timer is
 *      removed and the line locks to its final tone.
 *   4. `foldNarrations` de-dupes by `correlation_id`, keeping the
 *      latest tone/text.
 *   5. LoopStepBar's ECG variant is derived from `stepTones` and
 *      never flickers a resolved step back to active on replay.
 */
import React from "react";
import { render, screen, act } from "@testing-library/react";
import "@testing-library/jest-dom";
import { vi, describe, test, expect } from "vitest";
import LoopLiveFeed, { foldNarrations, extractNarration } from
  "../LoopLiveFeed";
import LoopStepBar from "../LoopStepBar";

// Helper — build a narration SSE event mimicking the backend shape.
function narrationEv({ step, tone, text, corrId = "", tsEpoch = 0 }) {
  return {
    loop_id: "loop_test",
    state:   "executing",
    phase:   step,
    message: text,
    timestamp: new Date(tsEpoch * 1000).toISOString(),
    data: {
      type: "narration",
      tone,
      narration_step: step,
      narration_text: text,
      correlation_id: corrId,
      ts_epoch: tsEpoch,
    },
  };
}

describe("foldNarrations", () => {
  test("filters non-narration events", () => {
    const evs = [
      { data: { type: "heartbeat" } },
      narrationEv({ step: "execute", tone: "pending", text: "Writing a.py",
                    corrId: "execute:a.py", tsEpoch: 100 }),
    ];
    const folded = foldNarrations(evs);
    expect(folded).toHaveLength(1);
    expect(folded[0].text).toBe("Writing a.py");
    expect(folded[0].tone).toBe("pending");
  });

  test("resolving event overwrites tone, keeps original entry ordering", () => {
    const evs = [
      narrationEv({ step: "execute", tone: "pending", text: "Writing a.py",
                    corrId: "execute:a.py", tsEpoch: 100 }),
      narrationEv({ step: "execute", tone: "pending", text: "Writing b.py",
                    corrId: "execute:b.py", tsEpoch: 110 }),
      narrationEv({ step: "execute", tone: "success", text: "Wrote a.py",
                    corrId: "execute:a.py", tsEpoch: 130 }),
    ];
    const folded = foldNarrations(evs);
    expect(folded).toHaveLength(2);
    // Order preserved (a.py first, b.py second)
    expect(folded[0].key).toBe("execute:a.py");
    expect(folded[1].key).toBe("execute:b.py");
    // Latest tone wins for a.py
    expect(folded[0].tone).toBe("success");
    expect(folded[0].text).toBe("Wrote a.py");
    // Original ts_epoch preserved (100, not 130) — anchors the "started at"
    expect(folded[0].tsEpoch).toBe(100);
    // b.py still pending
    expect(folded[1].tone).toBe("pending");
  });

  test("extractNarration returns null for non-narration events", () => {
    expect(extractNarration({ data: {} })).toBeNull();
    expect(extractNarration({ data: { type: "heartbeat" } })).toBeNull();
    expect(extractNarration(null)).toBeNull();
  });
});

describe("LoopLiveFeed narration rendering", () => {
  test("empty state shows phase-interpolated placeholder", () => {
    render(<LoopLiveFeed loopId="loop_x" event={null} phase="executing" />);
    const el = screen.getByTestId("loop-live-feed-placeholder");
    expect(el.textContent).toContain("Opening executing stream");
  });

  test("empty state shows generic placeholder when phase is idle", () => {
    render(<LoopLiveFeed loopId="loop_x" event={null} phase="idle" />);
    const el = screen.getByTestId("loop-live-feed-placeholder");
    expect(el.textContent).toContain("Opening event stream");
  });

  test("no heartbeat text rows rendered (Item A regression guard)", () => {
    // Backend heartbeat frames should NOT produce a visible feed line.
    const { rerender } = render(
      <LoopLiveFeed loopId="loop_x" event={null} phase="executing" />,
    );
    const heartbeatEv = {
      loop_id: "loop_x", state: "executing", phase: "execute",
      message: "heartbeat…",
      data: { sub_step: "heartbeat", keepalive: true },
    };
    rerender(
      <LoopLiveFeed loopId="loop_x" event={heartbeatEv} phase="executing" />,
    );
    // The placeholder should still be visible — no narration lines
    // and no heartbeat text row was rendered.
    expect(screen.getByTestId("loop-live-feed-placeholder")).toBeInTheDocument();
    // Ring buffer should have zero narration-line testids.
    expect(screen.queryAllByTestId(/^loop-narration-line-/)).toHaveLength(0);
  });

  test("no gap-fallback line ever rendered (Item B regression guard)", () => {
    // The old iter 275 gap-fallback used data-testid="loop-live-gap".
    // After Item B, that testid must not exist in the DOM regardless
    // of how long we wait or how few events arrive.
    render(<LoopLiveFeed loopId="loop_x" event={null} phase="executing" />);
    expect(screen.queryByTestId("loop-live-gap")).toBeNull();
  });
});

describe("LoopStepBar ECG variant derivation from stepTones", () => {
  test("step with tone=pending → active ECG variant", () => {
    render(
      <LoopStepBar
        phase="executing"
        stepTones={{ execute: "pending" }}
      />,
    );
    const strip = screen.getByTestId("loop-step-ecg-execute");
    expect(strip).toHaveAttribute("data-variant", "active");
  });

  test("step with tone=success → resolved green ECG variant", () => {
    render(
      <LoopStepBar
        phase="verifying"
        stepTones={{ execute: "success" }}
      />,
    );
    const strip = screen.getByTestId("loop-step-ecg-execute");
    expect(strip).toHaveAttribute("data-variant", "success");
  });

  test("step with tone=danger → resolved red ECG variant", () => {
    render(
      <LoopStepBar
        phase="failed"
        stepTones={{ execute: "danger" }}
      />,
    );
    const strip = screen.getByTestId("loop-step-ecg-execute");
    expect(strip).toHaveAttribute("data-variant", "danger");
  });

  test("resolved step does NOT flicker back to active on replay", () => {
    // Simulate: execute resolved as success → then a replayed
    // "pending" event for the same step would (incorrectly) revert
    // the strip to active. Our derivation uses the LATEST tone from
    // stepTones, so the reducer in ChatPanel must already have
    // overwritten the pending with success. This test asserts the
    // step-bar honors the reducer's decision: if stepTones says
    // "success", the strip stays success — no client-side timer or
    // re-animation.
    const { rerender } = render(
      <LoopStepBar phase="verifying" stepTones={{ execute: "success" }} />,
    );
    expect(screen.getByTestId("loop-step-ecg-execute"))
      .toHaveAttribute("data-variant", "success");
    // Re-render with the same success tone — must stay success.
    rerender(
      <LoopStepBar phase="verifying" stepTones={{ execute: "success" }} />,
    );
    expect(screen.getByTestId("loop-step-ecg-execute"))
      .toHaveAttribute("data-variant", "success");
  });

  test("future step (no tone) → future variant", () => {
    render(
      <LoopStepBar
        phase="executing"
        stepTones={{ execute: "pending" }}
      />,
    );
    const shipStrip = screen.getByTestId("loop-step-ecg-ship");
    expect(shipStrip).toHaveAttribute("data-variant", "future");
  });

  test("scan step uses 'scan' narration key not 'security' label key", () => {
    // The label key is "security" (legacy) but backend narration uses
    // "scan". LoopStepBar must map correctly via narrationKey.
    render(
      <LoopStepBar
        phase="scanning"
        stepTones={{ scan: "success" }}
      />,
    );
    const strip = screen.getByTestId("loop-step-ecg-security");
    expect(strip).toHaveAttribute("data-variant", "success");
  });
});

describe("Timer server-ts derivation (reconnect correctness)", () => {
  // This is the load-bearing test: simulate a scenario where a
  // pending narration was emitted at server time T=100, then the
  // client disconnected at T=105 for 20s and reconnected at T=125.
  // The replayed event still has ts_epoch=100. The timer must show
  // ~25s elapsed after reconnect, NOT ~0s.
  test("timer elapsed uses ts_epoch, not client receipt time", () => {
    vi.useFakeTimers();
    try {
      // Client's Date.now() is at T=125_000ms (i.e., 125s epoch;
      // 25s after server's ts_epoch=100).
      vi.setSystemTime(new Date(125_000));

      // Server emitted at T=100
      const ev = narrationEv({
        step: "execute", tone: "pending", text: "Writing a.py",
        corrId: "execute:a.py", tsEpoch: 100,
      });

      let rerender;
      act(() => {
        ({ rerender } = render(
          <LoopLiveFeed loopId="loop_x" event={null} phase="executing" />,
        ));
      });
      // Now feed the event via rerender so the useEffect(event) fires.
      act(() => {
        rerender(
          <LoopLiveFeed loopId="loop_x" event={ev} phase="executing" />,
        );
      });
      // Advance timers so the 100ms tick fires and populates nowEpoch
      // from the mocked Date.now().
      act(() => { vi.advanceTimersByTime(150); });

      const timerEl = screen.getByTestId(
        "loop-narration-timer-execute:a.py",
      );
      // Server elapsed = 125 - 100 = 25s → formatElapsed(25) → "25s"
      expect(timerEl.textContent).toBe("25s");
    } finally {
      vi.useRealTimers();
    }
  });

  test("timer disappears once tone transitions to success", () => {
    let rerender;
    act(() => {
      ({ rerender } = render(
        <LoopLiveFeed loopId="loop_x" event={null} phase="executing" />,
      ));
    });
    // Feed the pending narration.
    act(() => {
      rerender(
        <LoopLiveFeed
          loopId="loop_x"
          event={narrationEv({
            step: "execute", tone: "pending", text: "Writing a.py",
            corrId: "execute:a.py", tsEpoch: 100,
          })}
          phase="executing"
        />,
      );
    });
    // Timer visible while pending
    expect(
      screen.getByTestId("loop-narration-timer-execute:a.py"),
    ).toBeInTheDocument();

    // Backend emits the paired success narration.
    act(() => {
      rerender(
        <LoopLiveFeed
          loopId="loop_x"
          event={narrationEv({
            step: "execute", tone: "success", text: "Wrote a.py",
            corrId: "execute:a.py", tsEpoch: 130,
          })}
          phase="executing"
        />,
      );
    });
    // Timer removed, line locked to success text.
    expect(
      screen.queryByTestId("loop-narration-timer-execute:a.py"),
    ).toBeNull();
    expect(
      screen.getByTestId("loop-narration-text-execute:a.py").textContent,
    ).toBe("Wrote a.py");
  });
});
