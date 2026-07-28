/**
 * ShippedRowHarness.jsx — Iter 330 · Rollback confirm-click diagnostic harness.
 *
 * Purpose
 * -------
 * The founder reported (Iter 329, Deploy A-Recovery Fix C) that the
 * `<ShippedRow />` rollback confirm-click sequence FAILS on production:
 * click 1 does not change the button text to "Confirm rollback", and
 * click 2 does nothing. Synthetic tests pass locally + preview build,
 * so the bug survives to prod.
 *
 * To de-risk without needing prod access, this harness mounts
 * `<ShippedRow />` in COMPLETE isolation on preview — no auth, no
 * loop_engine, no GitHub PAT. That way the exact state-machine class
 * of the bug (idle → confirming → queued) is exercisable independent
 * of GitHub connectivity (which is broken on preview per handoff).
 *
 * Two diagnostic events already fire from ShippedRow itself:
 *   • `aurem:debug:rollback-click`       — fired every click
 *   • `aurem:debug:shipped-row-render`   — fired every render
 *
 * This page:
 *   1. Mounts ShippedRow with a fake ship prop.
 *   2. Captures ALL diagnostic events into a visible log.
 *   3. Snapshots outerHTML at moments (A) initial render, (B) after
 *      click 1, (C) after click 2.
 *   4. Exposes the collected data via data-testid attributes so a
 *      Playwright script can scrape them without any DOM guesswork.
 *
 * NOTE — This page is a preview-only diagnostic surface. It does NOT
 * proxy the real rollback API path; on click 2 the ShippedRow will
 * hit `rollbackLoop("fake-loop-id")` which 404s and drives the row
 * to `phase="failed"`. That's fine — the bug hypothesis lives in
 * the idle→confirming→queued transition BEFORE the API call fires.
 */
import { useEffect, useRef, useState } from "react";
import { ShippedRow } from "../components/LoopLiveFeed";

const FAKE_SHIP = {
  shortSha: "abc1234",
  commitSha: "abc1234deadbeef",
  htmlUrl: "https://example.invalid/commit/abc1234",
};

export default function ShippedRowHarness() {
  const [events, setEvents] = useState([]);
  const [snapshots, setSnapshots] = useState({
    momentA: null, // initial render
    momentB: null, // after click 1
    momentC: null, // after click 2
  });
  const rowContainerRef = useRef(null);
  const capturedInitialRef = useRef(false);

  // Wire up diagnostic listeners immediately on mount.
  useEffect(() => {
    const onClick = (e) => {
      setEvents((prev) => [
        ...prev,
        {
          type: "rollback-click",
          detail: e.detail,
          ts: Date.now(),
        },
      ]);
    };
    const onRender = (e) => {
      setEvents((prev) => [
        ...prev,
        {
          type: "shipped-row-render",
          detail: e.detail,
          ts: Date.now(),
        },
      ]);
    };
    window.addEventListener("aurem:debug:rollback-click", onClick);
    window.addEventListener("aurem:debug:shipped-row-render", onRender);
    return () => {
      window.removeEventListener("aurem:debug:rollback-click", onClick);
      window.removeEventListener("aurem:debug:shipped-row-render", onRender);
    };
  }, []);

  // Capture Moment A once the row first renders.
  useEffect(() => {
    if (capturedInitialRef.current) return;
    const el = rowContainerRef.current?.querySelector(
      `[data-testid="loop-shipped-row-${FAKE_SHIP.shortSha}"]`,
    );
    if (el) {
      capturedInitialRef.current = true;
      setSnapshots((s) => ({ ...s, momentA: el.outerHTML }));
    }
  }, [events]); // re-run after any event (including first render event)

  const captureRow = () => {
    const el = rowContainerRef.current?.querySelector(
      `[data-testid="loop-shipped-row-${FAKE_SHIP.shortSha}"]`,
    );
    return el ? el.outerHTML : null;
  };

  const takeMomentB = () => {
    setSnapshots((s) => ({ ...s, momentB: captureRow() }));
  };
  const takeMomentC = () => {
    setSnapshots((s) => ({ ...s, momentC: captureRow() }));
  };

  const clearEverything = () => {
    setEvents([]);
    setSnapshots({ momentA: null, momentB: null, momentC: null });
    capturedInitialRef.current = false;
  };

  return (
    <div
      data-testid="shipped-row-harness-page"
      style={{
        background: "#0a0e1a",
        color: "#e5e7eb",
        minHeight: "100vh",
        padding: "24px",
        fontFamily: "'JetBrains Mono', ui-monospace, monospace",
        fontSize: 12,
      }}
    >
      <h1 style={{ fontSize: 16, marginBottom: 6, letterSpacing: ".05em" }}>
        Iter 330 · ShippedRow diagnostic harness
      </h1>
      <p style={{ color: "#9ca3af", marginBottom: 20, maxWidth: 720 }}>
        Isolated mount of <code>&lt;ShippedRow /&gt;</code> — no loop, no auth,
        no GitHub. Click the rollback button twice in sequence and observe
        the diagnostic event stream + outerHTML snapshots below.
      </p>

      <div
        ref={rowContainerRef}
        data-testid="harness-row-container"
        style={{
          background: "#0f1626",
          border: "1px solid #1f2937",
          borderRadius: 6,
          padding: "10px 14px",
          marginBottom: 24,
        }}
      >
        <ShippedRow loopId="fake-loop-id" ship={FAKE_SHIP} />
      </div>

      <div style={{ display: "flex", gap: 8, marginBottom: 16 }}>
        <button
          data-testid="harness-capture-moment-b"
          onClick={takeMomentB}
          style={btnStyle}
        >
          Capture Moment B (after click 1)
        </button>
        <button
          data-testid="harness-capture-moment-c"
          onClick={takeMomentC}
          style={btnStyle}
        >
          Capture Moment C (after click 2)
        </button>
        <button
          data-testid="harness-clear"
          onClick={clearEverything}
          style={{ ...btnStyle, background: "#7f1d1d" }}
        >
          Clear
        </button>
      </div>

      <section style={sectionStyle}>
        <h2 style={h2Style}>Diagnostic event log ({events.length})</h2>
        <pre
          data-testid="harness-event-log"
          style={preStyle}
        >
{JSON.stringify(events, null, 2)}
        </pre>
      </section>

      <section style={sectionStyle}>
        <h2 style={h2Style}>Moment A — initial render outerHTML</h2>
        <pre data-testid="harness-moment-a" style={preStyle}>
{snapshots.momentA || "(not captured yet)"}
        </pre>
      </section>

      <section style={sectionStyle}>
        <h2 style={h2Style}>Moment B — after click 1 outerHTML</h2>
        <pre data-testid="harness-moment-b" style={preStyle}>
{snapshots.momentB || "(not captured yet)"}
        </pre>
      </section>

      <section style={sectionStyle}>
        <h2 style={h2Style}>Moment C — after click 2 outerHTML</h2>
        <pre data-testid="harness-moment-c" style={preStyle}>
{snapshots.momentC || "(not captured yet)"}
        </pre>
      </section>
    </div>
  );
}

const btnStyle = {
  background: "#1f2937",
  border: "1px solid #374151",
  color: "#e5e7eb",
  padding: "6px 12px",
  borderRadius: 4,
  cursor: "pointer",
  fontFamily: "inherit",
  fontSize: 11,
};

const sectionStyle = {
  marginBottom: 20,
};

const h2Style = {
  fontSize: 12,
  color: "#9ca3af",
  textTransform: "uppercase",
  letterSpacing: ".08em",
  marginBottom: 6,
};

const preStyle = {
  background: "#0f1626",
  border: "1px solid #1f2937",
  borderRadius: 4,
  padding: 10,
  fontSize: 11,
  color: "#c9cbcf",
  maxHeight: 300,
  overflow: "auto",
  whiteSpace: "pre-wrap",
  wordBreak: "break-all",
};
