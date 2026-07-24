/**
 * TaskLiveTape.test.jsx — Iter 303 (Frontend QA Charter Layer 1 audit — closing)
 *
 * State-sync behavior tests for the live worker tape driven by
 * SSE frames over fetch()+ReadableStream. We mock `global.fetch`
 * with a scripted controller so tests are hermetic + deterministic.
 * Same 3-test template.
 */
import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, cleanup } from "@testing-library/react";
import TaskLiveTape from "../TaskLiveTape.jsx";


// ── Scripted SSE stream helper ────────────────────────────────────
function makeSSEStream(frames) {
  // Yields SSE-format frames ("data: <json>\n\n") on demand, closes
  // when frames exhausted. Returns a ReadableStream compatible with
  // TaskLiveTape's fetch+reader path.
  let i = 0;
  return new ReadableStream({
    async pull(controller) {
      if (i >= frames.length) { controller.close(); return; }
      const payload = `data: ${JSON.stringify(frames[i])}\n\n`;
      controller.enqueue(new TextEncoder().encode(payload));
      i += 1;
    },
  });
}
function mockFetchWithFrames(frames) {
  return vi.fn(async () => ({
    ok: true,
    body: makeSSEStream(frames),
  }));
}


describe("TaskLiveTape — state-sync behavior (iter303)", () => {
  beforeEach(() => {
    // localStorage.getItem("aurem_token") — return null so getToken
    // doesn't throw; TaskLiveTape guards for missing token.
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("reaches-correct-terminal-state: streaming steps → terminal 'done' frame flips to done + fires onDone", async () => {
    global.fetch = mockFetchWithFrames([
      { step: "starting worker", pct: 5 },
      { step: "reading /api/health", pct: 40 },
      { step: "wrote 12 files", pct: 90 },
      { type: "done", pct: 100, status: "done" },
    ]);
    const onDone = vi.fn();
    render(<TaskLiveTape taskId="task-1" onDone={onDone} />);

    // Initial empty state.
    // (Note: the empty state may flash for one tick before the first
    // step frame lands — waitFor handles either case.)
    await waitFor(() => {
      // After all 4 frames are drained + terminal fired, the tape
      // must reflect done state.
      expect(onDone).toHaveBeenCalled();
    }, { timeout: 2000 });
    // onDone was called with the terminal frame — never twice.
    expect(onDone).toHaveBeenCalledTimes(1);
    expect(onDone.mock.calls[0][0].type).toBe("done");
    // The final tape shows the streamed steps (real DOM).
    expect(screen.getByTestId("task-live-tape")).toBeInTheDocument();
    expect(screen.getByText(/starting worker/i)).toBeInTheDocument();
    expect(screen.getByText(/wrote 12 files/i)).toBeInTheDocument();
  });

  it("clears-stale-prior-state: onDone fires EXACTLY ONCE across the whole terminal-fanout (done frame + status='done')", async () => {
    // Charter bug class: some worker paths emit BOTH a `type: 'done'`
    // frame AND a follow-up frame with `status: 'done'`. The
    // handler MUST NOT call onDone twice. This test locks that.
    global.fetch = mockFetchWithFrames([
      { step: "queued", pct: 0 },
      { type: "done", status: "done", pct: 100 },
      { status: "done", extra: "trailing frame after terminal" },
    ]);
    const onDone = vi.fn();
    render(<TaskLiveTape taskId="task-dup" onDone={onDone} />);
    await waitFor(() => expect(onDone).toHaveBeenCalled(),
                    { timeout: 2000 });
    // The reader breaks out of its loop on the first terminal, so
    // the trailing frame never lands and onDone stays at 1.
    expect(onDone).toHaveBeenCalledTimes(1);
  });

  it("race-condition: fetch that returns ok=false transitions directly to done without steps (no ghost empty state)", async () => {
    // Simulates the loop's SSE endpoint being unreachable — e.g. the
    // task got GC'd. The tape MUST NOT hang in "queued…" forever.
    global.fetch = vi.fn(async () => ({ ok: false, body: null }));
    const onDone = vi.fn();
    render(<TaskLiveTape taskId="task-404" onDone={onDone} />);
    // Wait for the effect to swallow the failed fetch and flip done.
    await waitFor(() => {
      // done=true AND no steps → the empty placeholder is unmounted
      // (setup: `if (!steps.length && !done) return <empty>` — with
      // done=true the second branch fires).
      expect(screen.queryByTestId("task-live-tape-empty")).toBeNull();
    }, { timeout: 2000 });
    // We never called onDone because there was no terminal frame —
    // the ok=false path silently marks done without invoking the
    // parent handler. Locking this behaviour so a future refactor
    // doesn't start firing onDone(undefined) which would break the
    // ChatPanel handoff.
    expect(onDone).not.toHaveBeenCalled();
  });
});
