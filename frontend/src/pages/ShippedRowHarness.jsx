/**
 * ShippedRowHarness.jsx — Iter 330 · Path P1 · Full-flow diagnostic harness.
 *
 * PURPOSE
 * -------
 * Isolated mount of <ShippedRow /> + <OperationHistory /> on preview,
 * so we can exercise the full rollback confirm-click → POST /rollback →
 * OperationHistory /stream subscription → terminal-collapse flow
 * without needing a chat panel, active loop, or GitHub push. The
 * backend seed script (see /app/backend/... in the batch smoke test)
 * populates a completed loop_session + a linked project so the POST
 * has all the invariants it needs to reach the run_rollback background
 * task. The rollback WILL fail on the (intentionally) bogus PAT →
 * exercises the failure SSE path (state=failed) end-to-end, which is
 * exactly what we need to prove Test C (post-terminal reopen guard).
 *
 * ACCEPTANCE CRITERIA (user-locked for this build)
 * ------------------------------------------------
 *  (A) Parent re-render churn — the harness runs a 200 ms setInterval
 *      that bumps a counter in the harness's state, forcing a re-render
 *      of the entire parent every tick. ShippedRow + OperationHistory
 *      must NOT remount and must not lose internal state (phase, ES
 *      subscription) across these re-renders.
 *  (B) Double-click race — playwright fires 5 clicks on the CONFIRM
 *      button within ~50 ms. Exactly ONE POST /rollback should be
 *      observed AND exactly ONE `aurem:debug:op-history-es-open` event
 *      should fire.
 *  (C) Post-terminal reopen — after the rollback event stream reaches
 *      a terminal state, the harness continues to churn for another
 *      3 s. No additional `op-history-es-open` events should fire in
 *      that window (verify handledLoopIdsRef guard).
 *
 * Instrumentation exposes ALL of the above as data-testids so the
 * playwright script can scrape them deterministically:
 *   - #harness-metrics — JSON blob of the three counters + last-known
 *     internal states. Refreshed on every relevant event.
 *   - #harness-event-log — chronological list of every CustomEvent
 *     we caught (rollback-click, shipped-row-render, es-open, es-error).
 */
import React, {
  useCallback, useEffect, useMemo, useRef, useState,
} from "react";
import { ShippedRow } from "../components/LoopLiveFeed";
import OperationHistory from "../components/OperationHistory";

// ── Fetch interceptor for Test B (POST count) ──
// Installed ONCE on mount. Counts calls to POST …/loop/{id}/rollback so
// the harness can prove exactly-one-POST despite rapid double-clicks.
// Increments a window-scoped counter that the harness reads back into
// its metrics tile on every re-render.
function installFetchInterceptor() {
  if (window.__aurem_harness_fetch_installed__) return;
  window.__aurem_harness_fetch_installed__ = true;
  window.__aurem_harness_post_count__ = { rollback: 0 };
  const _origFetch = window.fetch.bind(window);
  window.fetch = async (...args) => {
    try {
      const url = typeof args[0] === "string"
        ? args[0]
        : (args[0] && args[0].url) || "";
      const method = (args[1] && args[1].method
                      || (args[0] && args[0].method)
                      || "GET").toUpperCase();
      if (method === "POST" && /\/loop\/[^/]+\/rollback\b/.test(url)) {
        window.__aurem_harness_post_count__.rollback += 1;
        try {
          window.dispatchEvent(new CustomEvent("aurem:debug:harness-fetch-post", {
            detail: { url, timestamp: Date.now() },
          }));
        } catch { /* noop */ }
      }
    } catch { /* noop */ }
    return _origFetch(...args);
  };
  // Also intercept axios if it uses XHR under the hood. Since the
  // codebase uses axios which internally uses XHR, we also monkey-
  // patch XMLHttpRequest.open to catch it.
  const _origOpen = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function (method, url, ...rest) {
    try {
      const m = String(method || "").toUpperCase();
      const u = String(url || "");
      if (m === "POST" && /\/loop\/[^/]+\/rollback\b/.test(u)) {
        window.__aurem_harness_post_count__.rollback += 1;
        try {
          window.dispatchEvent(new CustomEvent("aurem:debug:harness-xhr-post", {
            detail: { url: u, timestamp: Date.now() },
          }));
        } catch { /* noop */ }
      }
    } catch { /* noop */ }
    return _origOpen.call(this, method, url, ...rest);
  };
}

export default function ShippedRowHarness() {
  const params = new URLSearchParams(window.location.search);
  const seededLoopId  = params.get("loop_id")    || "iter330-harness-loop";
  const seededProject = params.get("project_id") || "iter330-harness-proj";
  const seededSha     = params.get("sha")        || "abc1234";

  // ── State ──
  const [events, setEvents]         = useState([]); // chronological log
  const [churnTick, setChurnTick]   = useState(0);  // Test A driver
  const [churnOn, setChurnOn]       = useState(false);
  const [postCount, setPostCount]   = useState(0);
  const [esOpenCount, setEsOpenCount] = useState(0);
  const [esErrorCount, setEsErrorCount] = useState(0);
  const [terminalObservedAt, setTerminalObservedAt] = useState(null);
  const [postTerminalEsOpens, setPostTerminalEsOpens] = useState(0);
  const [finalizeCount, setFinalizeCount] = useState(0);
  const [initialFetch, setInitialFetch] = useState(null);

  // Refs so listeners don't require re-registration on every render.
  const terminalAtRef = useRef(null);

  useEffect(() => { installFetchInterceptor(); }, []);

  // Global diagnostic listeners.
  useEffect(() => {
    const onClick = (e) => setEvents((p) => [
      ...p, { type: "rollback-click", detail: e.detail, ts: Date.now() },
    ].slice(-50));
    const onRender = (e) => setEvents((p) => [
      ...p, { type: "shipped-row-render", detail: e.detail, ts: Date.now() },
    ].slice(-50));
    const onEsOpen = (e) => {
      setEsOpenCount((c) => c + 1);
      // Test C — count opens that happen AFTER terminalObservedAt.
      if (terminalAtRef.current != null) {
        setPostTerminalEsOpens((c) => c + 1);
      }
      setEvents((p) => [
        ...p, { type: "op-history-es-open", detail: e.detail, ts: Date.now() },
      ].slice(-50));
    };
    const onEsError = (e) => {
      setEsErrorCount((c) => c + 1);
      setEvents((p) => [
        ...p, { type: "op-history-es-error", detail: e.detail, ts: Date.now() },
      ].slice(-50));
    };
    const onPost = (e) => {
      setPostCount((c) => c + 1);
      setEvents((p) => [
        ...p, { type: "harness-post-detected", detail: e.detail, ts: Date.now() },
      ].slice(-50));
    };
    const onFinalize = (e) => {
      setFinalizeCount((c) => c + 1);
      setEvents((p) => [
        ...p, { type: "op-history-finalize", detail: e.detail, ts: Date.now() },
      ].slice(-50));
    };
    const onInitialFetch = (e) => {
      setInitialFetch(e.detail);
      setEvents((p) => [
        ...p, { type: "op-history-initial-fetch", detail: e.detail, ts: Date.now() },
      ].slice(-50));
    };
    // Also count on-event dispatches from OperationHistory.
    const onOnEvent = (e) => setEvents((p) => [
      ...p, { type: "op-history-on-event", detail: e.detail, ts: Date.now() },
    ].slice(-80));
    window.addEventListener("aurem:debug:op-history-on-event", onOnEvent);
    window.addEventListener("aurem:debug:rollback-click",       onClick);
    window.addEventListener("aurem:debug:shipped-row-render",   onRender);
    window.addEventListener("aurem:debug:op-history-es-open",   onEsOpen);
    window.addEventListener("aurem:debug:op-history-es-error",  onEsError);
    window.addEventListener("aurem:debug:op-history-finalize",  onFinalize);
    window.addEventListener("aurem:debug:op-history-initial-fetch", onInitialFetch);
    window.addEventListener("aurem:debug:harness-fetch-post",   onPost);
    window.addEventListener("aurem:debug:harness-xhr-post",     onPost);
    return () => {
      window.removeEventListener("aurem:debug:rollback-click",      onClick);
      window.removeEventListener("aurem:debug:shipped-row-render",  onRender);
      window.removeEventListener("aurem:debug:op-history-es-open",  onEsOpen);
      window.removeEventListener("aurem:debug:op-history-es-error", onEsError);
      window.removeEventListener("aurem:debug:op-history-finalize", onFinalize);
      window.removeEventListener("aurem:debug:op-history-initial-fetch", onInitialFetch);
      window.removeEventListener("aurem:debug:harness-fetch-post",  onPost);
      window.removeEventListener("aurem:debug:harness-xhr-post",    onPost);
      window.removeEventListener("aurem:debug:op-history-on-event", onOnEvent);
    };
  }, []);

  // Detect terminal from event log — the last op-history-es-error OR
  // observed handoff to "handed-off" phase. Simpler approach: watch
  // for a rollback-click detail with phase transitioning to submitting.
  // Even simpler for Test C: an op that finalizes closes its stream,
  // which we can't observe directly. Use a reasonable heuristic: 3 s
  // after the FIRST es-open, treat as terminal.
  useEffect(() => {
    if (esOpenCount > 0 && terminalAtRef.current == null) {
      // Give the ES ~4 s to receive a terminal event (backend PAT will
      // fail → run_rollback emits state=failed almost immediately).
      const t = setTimeout(() => {
        terminalAtRef.current = Date.now();
        setTerminalObservedAt(Date.now());
      }, 4000);
      return () => clearTimeout(t);
    }
    return undefined;
  }, [esOpenCount]);

  // ── Test A: parent churn driver ──
  useEffect(() => {
    if (!churnOn) return undefined;
    const iv = setInterval(() => setChurnTick((t) => t + 1), 200);
    return () => clearInterval(iv);
  }, [churnOn]);

  // ── ShippedRow's onRollbackStarted callback ──
  // Lifts the loopId → OperationHistory picks it up via prop → opens
  // /stream. This is the EXACT wiring used in production LoopLiveFeed.
  const [activeLoopId, setActiveLoopId] = useState(null);
  const onRollbackStarted = useCallback((lid) => {
    setActiveLoopId(lid);
  }, []);

  const shipInfo = useMemo(() => ({
    shortSha:  seededSha,
    commitSha: `${seededSha}deadbeef`,
    htmlUrl:   `https://example.invalid/commit/${seededSha}`,
  }), [seededSha]);

  const metrics = {
    churnTick,
    churnOn,
    postCount,
    esOpenCount,
    esErrorCount,
    finalizeCount,
    initialFetch,
    activeLoopId,
    terminalObservedAt,
    postTerminalEsOpens,
    seededLoopId,
    seededProject,
  };

  return (
    <div
      data-testid="shipped-row-harness-page"
      style={{
        background: "#0a0e1a",
        color: "#e5e7eb",
        minHeight: "100vh",
        padding: 24,
        fontFamily: "'JetBrains Mono', ui-monospace, monospace",
        fontSize: 12,
      }}
    >
      <h1 style={{ fontSize: 16, marginBottom: 6, letterSpacing: ".05em" }}>
        Iter 330 · ShippedRow + OperationHistory harness
      </h1>
      <p style={{ color: "#9ca3af", marginBottom: 20, maxWidth: 760 }}>
        Real POST /rollback → real /stream subscription → real
        OperationHistory finalize. Backend seed required (see Iter 330
        batch smoke test script). Churn interval: 200 ms.
      </p>

      {/* Parent churn control */}
      <div style={{ display: "flex", gap: 8, marginBottom: 16 }}>
        <button
          data-testid="harness-churn-toggle"
          onClick={() => setChurnOn((v) => !v)}
          style={btnStyle}
        >
          Churn: {churnOn ? "ON" : "OFF"}  (tick {churnTick})
        </button>
      </div>

      {/* ShippedRow — the component under test */}
      <div
        data-testid="harness-row-container"
        style={{
          background: "#0f1626",
          border: "1px solid #1f2937",
          borderRadius: 6,
          padding: "10px 14px",
          marginBottom: 12,
        }}
      >
        {/*
          Churn note: `churnTick` is intentionally referenced in a
          className so React sees a prop change on the WRAPPER div
          every tick, forcing full parent re-render. The direct
          <ShippedRow /> props are stable across ticks — that's the
          whole point of the test. If ShippedRow remounts, its
          `phase` state resets → the button's "Confirm rollback"
          text will regress to "Rollback" mid-flow, which the
          playwright script asserts against.
        */}
        <div data-tick={churnTick}>
          <ShippedRow
            loopId={seededLoopId}
            ship={shipInfo}
            onRollbackStarted={onRollbackStarted}
          />
        </div>
      </div>

      {/* OperationHistory */}
      <div
        data-testid="harness-op-history-container"
        style={{
          background: "#0f1626",
          border: "1px solid #1f2937",
          borderRadius: 6,
          padding: "10px 14px",
          marginBottom: 16,
        }}
      >
        <div style={{ color: "#9ca3af", fontSize: 10, marginBottom: 6 }}>
          OperationHistory
        </div>
        <OperationHistory
          projectId={seededProject}
          activeLoopId={activeLoopId}
        />
      </div>

      {/* Metrics — sole source of truth for the playwright asserts */}
      <section style={sectionStyle}>
        <h2 style={h2Style}>Harness metrics (JSON)</h2>
        <pre data-testid="harness-metrics" style={preStyle}>
{JSON.stringify(metrics, null, 2)}
        </pre>
      </section>

      {/* Event log */}
      <section style={sectionStyle}>
        <h2 style={h2Style}>Diagnostic event log (last 50)</h2>
        <pre data-testid="harness-event-log" style={preStyle}>
{JSON.stringify(events, null, 2)}
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
const sectionStyle = { marginBottom: 20 };
const h2Style = {
  fontSize: 12, color: "#9ca3af",
  textTransform: "uppercase", letterSpacing: ".08em",
  marginBottom: 6,
};
const preStyle = {
  background: "#0f1626",
  border: "1px solid #1f2937",
  borderRadius: 4,
  padding: 10,
  fontSize: 11,
  color: "#c9cbcf",
  maxHeight: 320,
  overflow: "auto",
  whiteSpace: "pre-wrap",
  wordBreak: "break-all",
};
