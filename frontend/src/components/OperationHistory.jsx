/**
 * OperationHistory.jsx — Iter 330 · Path P1 · Ship + Rollback operations timeline.
 *
 * Design (per user spec + verified backend evidence):
 *   • Fetches `GET /loop/history?project_id=X&limit=20` on mount for
 *     the collapsed past-ops stack.
 *   • Opens ONE EventSource to `/loop/{loopId}/stream` when a new
 *     `activeLoopId` prop lands, consuming both ship AND rollback
 *     events off the same stream — differentiated by `phase`.
 *     Since ship progress already streams via LoopLiveFeed's own
 *     parent SSE subscription, we only open this stream to catch
 *     the POST-ship rollback flow (server's guard-loosen on
 *     terminal-break holds it open only while `rollback_status ∈
 *     {queued, running}`, so idle completed loops close in ~2s
 *     of listener time — no leak).
 *   • On terminal event, pushes op into `history` state + closes ES.
 *
 * Locked-in guards (user acceptance criteria for this build):
 *   (A) Parent re-render churn — component is React.memo-wrapped.
 *       Effect deps are the ONLY triggers for open/close; parent
 *       re-renders that don't change props are no-ops.
 *   (B) Double-click race — the effect gates on `handledLoopIdsRef`
 *       set. Even if `activeLoopId` re-appears (parent race or user
 *       spam), we open exactly ONE EventSource per loopId lifetime.
 *   (C) Post-terminal no-reopen — same set marks the loopId as
 *       handled BEFORE closing ES. On next parent render even if
 *       `activeLoopId` is still the same value, the effect early-
 *       returns and does not reopen.
 */
import React, {
  useCallback, useEffect, useMemo, useRef, useState,
} from "react";
import { streamLoopEvents, rollbackLoop } from "../lib/loopApi";

const API_BASE = process.env.REACT_APP_BACKEND_URL || "";

// Iter 342 — swallows the synthetic `click` that follows a handled
// `pointerdown` on the row rollback button (loopId → ts).
const _rbLastFire = new Map();

function formatTime(iso) {
  if (!iso) return "";
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return "";
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  } catch { return ""; }
}

function opLabel(op) {
  const typeLabel = op.op_type === "rollback" ? "Rollback" : "Ship";
  if (op.state === "failed")    return `${typeLabel} failed`;
  if (op.state === "completed") return `${typeLabel} finished`;
  return `${typeLabel} in progress`;
}

// Collapsed row — click to expand. `rb` (optional) wires the Iter 339
// persistent rollback path for completed ship rows.
function CollapsedOpRow({ op, onExpand, rb }) {
  const passed = op.all_passed;
  const lid8 = (op.loop_id || "").slice(0, 8);
  return (
    <div
      data-testid={`op-history-row-collapsed-${op.op_type}-${lid8}`}
      data-op-state={op.state}
      onClick={() => onExpand(op.loop_id, op.op_type)}
      style={{
        display: "flex", alignItems: "center", gap: 8,
        padding: "6px 12px", fontSize: 12,
        color: "#8b949e", cursor: "pointer",
        borderRadius: 4, transition: "background .15s",
      }}
      onMouseOver={(e) => (e.currentTarget.style.background = "#161b22")}
      onMouseOut={(e) => (e.currentTarget.style.background = "transparent")}
    >
      <span style={{ color: passed ? "#3fb950" : "#f85149" }}>▶</span>
      <span>{opLabel(op)} ({formatTime(op.finished_at || op.started_at)})</span>
      <span style={{ color: "#484f58" }}>
        {" | "}{op.step_count} step{op.step_count === 1 ? "" : "s"},
        {" "}{passed ? "all passed" : "failed"}
      </span>
      {rb && (
        <>
          <span style={{ flex: 1 }} />
          <button
            type="button"
            data-testid={`op-history-rollback-btn-${lid8}`}
            aria-label="Rollback this ship"
            disabled={rb.phase === "submitting"}
            onPointerDown={(e) => {
              // Iter 342 — pointerdown fires pre-remount; the row's
              // expand onClick must not swallow this.
              if (e.button !== undefined && e.button !== 0) return;
              e.stopPropagation();
              rb.onClick(op.loop_id, "pointerdown");
            }}
            onClick={(e) => { e.stopPropagation(); rb.onClick(op.loop_id, "click"); }}
            style={{
              appearance: "none",
              background: "transparent",
              color:      "#f87171",
              border:     "1px solid #EF444488",
              borderRadius: 5, padding: "2px 8px",
              fontFamily: "inherit", fontSize: 10,
              textTransform: "uppercase", letterSpacing: ".04em",
              cursor: rb.phase === "submitting" ? "wait" : "pointer",
              opacity: rb.phase === "submitting" ? 0.6 : 1,
            }}
          >
            {rb.phase === "submitting" ? "Rolling back…"
              : rb.phase === "failed" ? "Retry rollback"
              : "Rollback"}
          </button>
          {rb.phase === "failed" && rb.error && (
            <span
              data-testid={`op-history-rollback-error-${lid8}`}
              style={{ color: "#f85149", fontSize: 10 }}
              title={rb.error}
            >
              {String(rb.error).slice(0, 40)}
            </span>
          )}
        </>
      )}
    </div>
  );
}

// Expanded op — live step list, non-collapsible while live.
function ExpandedOp({ op, onCollapse, collapsible }) {
  return (
    <div
      data-testid={`op-history-expanded-${op.op_type}-${(op.loop_id||"").slice(0,8)}`}
      data-op-state={op.state}
      style={{
        border: "1px solid #21262d", borderRadius: 6,
        background: "#0d1117", padding: "10px 14px", marginBottom: 8,
      }}
    >
      <div
        onClick={collapsible ? () => onCollapse(op.loop_id) : undefined}
        style={{
          display: "flex", alignItems: "center", gap: 8,
          cursor: collapsible ? "pointer" : "default",
          fontSize: 13, fontWeight: 600, marginBottom: 8,
        }}
      >
        <span>▼</span>
        <span>{opLabel(op)} ({formatTime(op.started_at)})</span>
      </div>
      {(op.steps || []).map((step, i) => (
        <div
          key={i}
          data-testid={`op-step-${(op.loop_id||"").slice(0,8)}-${i}`}
          style={{
            display: "flex", alignItems: "center", gap: 8,
            fontSize: 12,
            color: step.status === "failed" ? "#f85149" : "#c9d1d9",
            padding: "3px 0",
          }}
        >
          <span style={{ width: 16, textAlign: "center" }}>
            {step.status === "done" && "✓"}
            {step.status === "in_progress" && "…"}
            {step.status === "pending" && "○"}
            {step.status === "failed" && "✗"}
          </span>
          <span>{step.label}</span>
        </div>
      ))}
      {op.state === "completed" && (
        <div style={{ marginTop: 6, fontSize: 12, color: "#3fb950" }}>
          ✓ {opLabel(op)}{op.commit_sha ? ` — ${op.commit_sha.slice(0, 7)}` : ""}
        </div>
      )}
      {op.state === "failed" && op.error && (
        <div style={{ marginTop: 6, fontSize: 12, color: "#f85149" }}>
          ✗ {op.error}
        </div>
      )}
    </div>
  );
}

function OperationHistoryInner({ projectId, activeLoopId, authToken }) {
  const [history, setHistory]         = useState([]);
  const [currentOp, setCurrentOp]     = useState(null);
  const [expandedId, setExpandedId]   = useState(null);
  // Iter 339 — persistent rollback path. `internalActiveId` lifts a
  // history-row rollback into the SAME stream subscription surface the
  // ShippedRow prop-driven flow uses. `rbState` is the two-click
  // confirm machine for the row buttons.
  const [internalActiveId, setInternalActiveId] = useState(null);
  const [rbState, setRbState] = useState(null); // {loopId, phase, error}
  const rbStateRef = useRef(null);
  useEffect(() => { rbStateRef.current = rbState; }, [rbState]);
  const effectiveActiveLoopId = internalActiveId || activeLoopId;

  // Guard set — loopIds we have already opened the stream for AND
  // consumed a terminal event on. Never open again for the same id.
  // This is the primary defense against Test C (post-terminal reopen)
  // AND Test B (rapid re-mount races). Using a ref (not state) means
  // no re-render triggers when we mutate it.
  const handledLoopIdsRef = useRef(new Set());
  const openStreamRef     = useRef(null); // { loopId, abortCtrl }
  // Iter 330 · fix — finalizedForLoopIdRef guards against StrictMode
  // double-mount + updater-double-invoke both racing to schedule
  // finalize for the same terminal event. Root cause of the 4× dupe:
  // (a) StrictMode mounts the effect twice → 2 concurrent SSE
  //     subscriptions → each receives the terminal event once.
  // (b) React (StrictMode) invokes state updater fns twice to detect
  //     impure updaters. Our previous updater had a side-effect
  //     (`Promise.resolve().then(finalize)`) inside it, so each event
  //     scheduled finalize twice.
  // 2 (subscriptions) × 2 (StrictMode invocations) = 4 finalize calls.
  // This ref, set BEFORE scheduling finalize and BEFORE the microtask
  // fires, blocks all subsequent attempts for the same loopId.
  const finalizedForLoopIdRef = useRef(null);
  // Iter 330 · fix — pendingFinalizeMergedRef carries the merged op
  // synchronously to the finalize call. Mirrors currentOp state so
  // we don't depend on setCurrentOp's updater fn having run.
  const currentOpRef = useRef(null);

  // Fetch history on mount + projectId change.
  useEffect(() => {
    if (!projectId || !API_BASE) return;
    let cancelled = false;
    const url = `${API_BASE}/api/aurem-dev/loop/history`
                + `?project_id=${encodeURIComponent(projectId)}&limit=20`;
    // Iter 339 — the authToken prop is never passed by LoopLiveFeed, so
    // this fetch used to fire WITHOUT Authorization → 401 → history
    // permanently empty. Fall back to the same localStorage token
    // streamLoopEvents uses.
    const tok = authToken || localStorage.getItem("aurem_token") || "";
    fetch(url, {
      headers: tok ? { Authorization: `Bearer ${tok}` } : {},
    })
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then((d) => {
        if (cancelled) return;
        setHistory(d.items || []);
        // Iter 330 · diagnostic — expose the raw initial-fetch payload
        // so the harness can prove hypothesis (1) [stale /loop/history
        // returning a rollback row] vs (2) [finalize double-push].
        try {
          window.dispatchEvent(new CustomEvent("aurem:debug:op-history-initial-fetch", {
            detail: {
              count: (d.items || []).length,
              rollbackCount: (d.items || []).filter((i) => i.op_type === "rollback").length,
              shipCount:     (d.items || []).filter((i) => i.op_type === "ship").length,
              items: (d.items || []).map((i) => ({
                loop_id: (i.loop_id || "").slice(0, 12),
                op_type: i.op_type,
                state:   i.state,
              })),
              timestamp: Date.now(),
            },
          }));
        } catch { /* noop */ }
      })
      .catch(() => { /* fail-open — history empty is acceptable */ });
    return () => { cancelled = true; };
  }, [projectId, authToken]);

  // SSE subscription to /stream when activeLoopId changes.
  // Effect deps intentionally narrow — only `activeLoopId`. Auth is
  // read by streamLoopEvents from localStorage internally so the
  // effect doesn't need to depend on it. handledLoopIdsRef checked
  // sync at entry blocks Test B (double-click race → double-ES) and
  // Test C (post-terminal reopen on parent re-render).
  useEffect(() => {
    // Iter 339 — shadow the prop: the stream target is either the
    // prop-driven id (ShippedRow flow) or the internal history-row
    // rollback id. All guards below apply identically.
    const activeLoopId = effectiveActiveLoopId;
    if (!activeLoopId) return undefined;

    // Guard B/C: already handled → do nothing.
    if (handledLoopIdsRef.current.has(activeLoopId)) return undefined;

    // Guard B: an existing subscription for the same loop → do nothing.
    if (openStreamRef.current
        && openStreamRef.current.loopId === activeLoopId) {
      return undefined;
    }
    // Close any stale prior subscription for a different loop.
    if (openStreamRef.current) {
      try { openStreamRef.current.abortCtrl.abort(); } catch { /* noop */ }
      openStreamRef.current = null;
    }

    // Emit diagnostic — harness listens on this to prove exactly-one.
    try {
      window.dispatchEvent(new CustomEvent("aurem:debug:op-history-es-open", {
        detail: { loopId: activeLoopId, timestamp: Date.now() },
      }));
    } catch { /* noop */ }

    const finalize = (finalOp, ev) => {
      // Guard C: mark handled BEFORE close so any concurrent re-fire
      // of the effect on parent re-render sees us as done.
      handledLoopIdsRef.current.add(activeLoopId);
      try { openStreamRef.current?.abortCtrl?.abort(); } catch { /* noop */ }
      if (openStreamRef.current
          && openStreamRef.current.loopId === activeLoopId) {
        openStreamRef.current = null;
      }
      // Iter 330 · diagnostic — count every finalize call so the
      // harness can prove hypothesis (2) [finalize double-push].
      try {
        window.dispatchEvent(new CustomEvent("aurem:debug:op-history-finalize", {
          detail: {
            loopId: activeLoopId,
            op_type: finalOp.op_type,
            state: String(ev?.state).toLowerCase(),
            timestamp: Date.now(),
          },
        }));
      } catch { /* noop */ }
      // Iter 330 · polish — dedupe by (loop_id, op_type) so the
      // initial /loop/history fetch + live finalize can't both
      // produce a row for the same op. If an existing row matches,
      // REPLACE it with the fresher finalize payload; otherwise
      // prepend. Preserves ordering — the replaced row keeps its
      // original position, matching user expectation that a live op
      // completing doesn't jump to the top over the seed row.
      setHistory((h) => {
        const finalRow = {
          ...finalOp,
          finished_at: ev?.timestamp,
          all_passed:  String(ev?.state).toLowerCase() === "completed",
        };
        const key = (o) => `${o.loop_id}::${o.op_type}`;
        const targetKey = key(finalRow);
        const existingIdx = h.findIndex((o) => key(o) === targetKey);
        if (existingIdx >= 0) {
          const next = h.slice();
          next[existingIdx] = { ...next[existingIdx], ...finalRow };
          return next;
        }
        return [finalRow, ...h];
      });
      setCurrentOp(null);
      currentOpRef.current = null;
    };

    const abortCtrl = streamLoopEvents(activeLoopId, {
      onEvent: (ev) => {
        const phase = String(ev.phase || "").toLowerCase();
        const state = String(ev.state || "").toLowerCase();
        // Iter 330 · trace — dispatch EVERY onEvent for diagnosis.
        try {
          window.dispatchEvent(new CustomEvent("aurem:debug:op-history-on-event", {
            detail: { phase, state, step: ev.step, msg: ev.message, timestamp: Date.now() },
          }));
        } catch { /* noop */ }
        // Only rollback events feed this timeline; ship events are
        // handled by LoopLiveFeed's parent stream (unchanged flow).
        if (phase !== "rollback") return;

        const isTerminal =
          ["completed", "failed", "aborted"].includes(state);

        // Iter 330 · fix — compute merged op SYNCHRONOUSLY off the
        // ref mirror. Do NOT rely on setCurrentOp's updater running
        // before the microtask fires (React 18 batches state updates
        // and microtasks fire in the SAME task before the next
        // render, so the updater has typically NOT run yet — the
        // previous attempt read pendingFinalizeMergedRef=null and
        // silently skipped finalize).
        const prev = currentOpRef.current;
        const base = (prev && prev.loop_id === activeLoopId)
          ? prev
          : {
              loop_id:    activeLoopId,
              op_type:    "rollback",
              started_at: ev.timestamp,
              steps:      [],
            };
        const idx = (base.steps || [])
          .findIndex((s) => s.index === ev.step);
        const nextStep = {
          index:  ev.step,
          label:  ev.message,
          status:
            state === "failed" ? "failed"
              : state === "completed" ? "done"
                : "in_progress",
        };
        const nextSteps = [...(base.steps || [])];
        if (idx >= 0) nextSteps[idx] = nextStep;
        else          nextSteps.push(nextStep);
        for (let k = 0; k < nextSteps.length - 1; k++) {
          if (nextSteps[k].status === "in_progress") {
            nextSteps[k] = { ...nextSteps[k], status: "done" };
          }
        }
        const merged = {
          ...base,
          state,
          step_count: ev.total_steps,
          commit_sha: ev.data?.commit_sha,
          html_url:   ev.data?.html_url,
          error:      ev.data?.error,
          steps:      nextSteps,
        };
        // Update ref SYNC and state (React-async).
        currentOpRef.current = merged;
        setCurrentOp(merged);

        if (isTerminal
            && finalizedForLoopIdRef.current !== activeLoopId) {
          finalizedForLoopIdRef.current = activeLoopId;
          // Defer to microtask so React commits the last step visibly
          // before we collapse the card.
          Promise.resolve().then(() => finalize(merged, ev));
        }
      },
      onError: () => {
        try {
          window.dispatchEvent(new CustomEvent("aurem:debug:op-history-es-error", {
            detail: { loopId: activeLoopId, timestamp: Date.now() },
          }));
        } catch { /* noop */ }
        // streamLoopEvents auto-reconnects; only terminal marks handled.
      },
    });

    openStreamRef.current = { loopId: activeLoopId, abortCtrl };

    return () => {
      // Effect cleanup — activeLoopId change or unmount.
      try { abortCtrl.abort(); } catch { /* noop */ }
      if (openStreamRef.current
          && openStreamRef.current.loopId === activeLoopId) {
        openStreamRef.current = null;
      }
    };
  }, [effectiveActiveLoopId]);

  // Cleanup on unmount — nuke any lingering stream.
  useEffect(() => () => {
    if (openStreamRef.current) {
      try { openStreamRef.current.abortCtrl.abort(); } catch { /* noop */ }
      openStreamRef.current = null;
    }
  }, []);

  const onExpand   = useCallback((key) =>
    setExpandedId((cur) => (cur === key ? null : key)), []);
  const onCollapse = useCallback(() => setExpandedId(null),         []);

  // Iter 342 — native window.confirm replaces the fragile two-click
  // arm (same root fix as ShippedRow: a synchronous browser dialog is
  // immune to remounts/state resets/timers). Stream-independent: fires
  // POST /loop/{id}/rollback directly, then lifts the id into the
  // shared stream subscription for live progress.
  const handleRowRollback = useCallback(async (loopId, source) => {
    const now = Date.now();
    const last = _rbLastFire.get(loopId) || 0;
    if (source === "click" && now - last < 800) return;
    _rbLastFire.set(loopId, now);
    const cur = rbStateRef.current;
    // eslint-disable-next-line no-console
    console.debug("[op-history rollback] trigger", { loopId, source });
    if (cur && cur.loopId === loopId && cur.phase === "submitting") return;
    const ok = window.confirm(
      `Rollback this shipped loop (${String(loopId).slice(0, 8)})?\n\n`
      + "This creates a new revert commit on GitHub that undoes the ship. "
      + "No history is force-pushed.",
    );
    if (!ok) return;
    setRbState({ loopId, phase: "submitting" });
    try {
      await rollbackLoop(loopId);
      // eslint-disable-next-line no-console
      console.debug("[op-history rollback] POST ok — opening progress stream", loopId);
      setRbState(null);
      setInternalActiveId(loopId);
    } catch (e) {
      const msg = e?.response?.data?.detail || e?.message || "Rollback failed";
      // eslint-disable-next-line no-console
      console.warn("[op-history rollback] POST failed:", msg);
      setRbState({ loopId, phase: "failed", error: msg });
    }
  }, []);

  const items = useMemo(() => history || [], [history]);

  // Iter 339c — bounded, collapsible list. The founder's prod
  // screenshot showed 20+ rows spilling out of the LiveFeed card and
  // overlapping the step bar / composer. Default shows the 5 most
  // recent ops; "Show all" expands within a scrollable 190px pane.
  const [showAll, setShowAll] = useState(false);
  const visibleItems = showAll ? items : items.slice(0, 5);

  // Loop ids that already have a rollback op (row or live) — their
  // ship rows must NOT offer the rollback button.
  const rolledBackIds = useMemo(() => {
    const s = new Set();
    for (const o of (history || [])) {
      if (o.op_type === "rollback") s.add(o.loop_id);
    }
    if (currentOp && currentOp.op_type === "rollback") s.add(currentOp.loop_id);
    return s;
  }, [history, currentOp]);

  const opKey = (o) => `${o.loop_id}::${o.op_type}`;

  return (
    <div
      data-testid="operation-history-root"
      style={{ display: "flex", flexDirection: "column", minWidth: 0 }}
    >
      {items.length > 0 && (
        <div style={{
          display: "flex", alignItems: "center", gap: 6,
          padding: "4px 12px 2px 12px",
          fontSize: 10, letterSpacing: ".08em",
          color: "#6e7681", textTransform: "uppercase",
        }}>
          <span data-testid="op-history-header">
            Ops history · {items.length}
          </span>
          <span style={{ flex: 1 }} />
          {items.length > 5 && (
            <button
              type="button"
              data-testid="op-history-toggle-show-all"
              onClick={() => setShowAll((v) => !v)}
              style={{
                appearance: "none", background: "transparent",
                border: "none", color: "#8b949e", cursor: "pointer",
                fontFamily: "inherit", fontSize: 10,
                textTransform: "uppercase", letterSpacing: ".06em",
                padding: 0, textDecoration: "underline",
              }}
            >
              {showAll ? "Show recent" : `Show all (${items.length})`}
            </button>
          )}
        </div>
      )}
      {items.length > 0 && (
        <div
          data-testid="op-history-scroll-pane"
          style={{ maxHeight: 190, overflowY: "auto", minWidth: 0 }}
        >
        {visibleItems.map((op, i) => (
          expandedId === opKey(op) ? (
            <ExpandedOp
              key={`${op.loop_id}-${op.op_type}-${i}`}
              op={op}
              onCollapse={onCollapse}
              collapsible
            />
          ) : (
            <CollapsedOpRow
              key={`${op.loop_id}-${op.op_type}-${i}`}
              op={op}
              onExpand={() => onExpand(opKey(op))}
              rb={(op.op_type === "ship" && op.state === "completed"
                   && op.commit_sha && !rolledBackIds.has(op.loop_id))
                ? {
                    phase: (rbState && rbState.loopId === op.loop_id)
                      ? rbState.phase : "idle",
                    error: (rbState && rbState.loopId === op.loop_id)
                      ? rbState.error : null,
                    onClick: handleRowRollback,
                  }
                : null}
            />
          )
        ))}
        </div>
      )}
      {currentOp && (
        <ExpandedOp
          op={currentOp}
          onCollapse={() => {}}
          collapsible={false}
        />
      )}
    </div>
  );
}

// React.memo — Guard A: prop-stable parent re-renders (churn from
// unrelated SSE frames elsewhere in the app) no longer trigger this
// subtree's reconciliation. The effect deps are the only trigger.
const OperationHistory = React.memo(OperationHistoryInner, (prev, next) => {
  return prev.projectId    === next.projectId
      && prev.activeLoopId === next.activeLoopId
      && prev.authToken    === next.authToken;
});

export default OperationHistory;
