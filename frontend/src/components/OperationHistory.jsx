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
import { streamLoopEvents } from "../lib/loopApi";

const API_BASE = process.env.REACT_APP_BACKEND_URL || "";

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

// Collapsed row — click to expand.
function CollapsedOpRow({ op, onExpand }) {
  const passed = op.all_passed;
  return (
    <div
      data-testid={`op-history-row-collapsed-${op.op_type}-${(op.loop_id||"").slice(0,8)}`}
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

  // Guard set — loopIds we have already opened the stream for AND
  // consumed a terminal event on. Never open again for the same id.
  // This is the primary defense against Test C (post-terminal reopen)
  // AND Test B (rapid re-mount races). Using a ref (not state) means
  // no re-render triggers when we mutate it.
  const handledLoopIdsRef = useRef(new Set());
  const openStreamRef     = useRef(null); // { loopId, abortCtrl }

  // Fetch history on mount + projectId change.
  useEffect(() => {
    if (!projectId || !API_BASE) return;
    let cancelled = false;
    const url = `${API_BASE}/api/aurem-dev/loop/history`
                + `?project_id=${encodeURIComponent(projectId)}&limit=20`;
    fetch(url, {
      headers: authToken ? { Authorization: `Bearer ${authToken}` } : {},
    })
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then((d) => { if (!cancelled) setHistory(d.items || []); })
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
      setHistory((h) => [
        {
          ...finalOp,
          finished_at: ev?.timestamp,
          all_passed:  String(ev?.state).toLowerCase() === "completed",
        },
        ...h,
      ]);
      setCurrentOp(null);
    };

    const abortCtrl = streamLoopEvents(activeLoopId, {
      onEvent: (ev) => {
        const phase = String(ev.phase || "").toLowerCase();
        const state = String(ev.state || "").toLowerCase();
        // Only rollback events feed this timeline; ship events are
        // handled by LoopLiveFeed's parent stream (unchanged flow).
        if (phase !== "rollback") return;

        setCurrentOp((prev) => {
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
          // Roll prior in-progress steps to done as newer ones arrive.
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
          if (["completed", "failed", "aborted"].includes(state)) {
            Promise.resolve().then(() => finalize(merged, ev));
          }
          return merged;
        });
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
  }, [activeLoopId]);

  // Cleanup on unmount — nuke any lingering stream.
  useEffect(() => () => {
    if (openStreamRef.current) {
      try { openStreamRef.current.abortCtrl.abort(); } catch { /* noop */ }
      openStreamRef.current = null;
    }
  }, []);

  const onExpand   = useCallback((loopId) => setExpandedId(loopId), []);
  const onCollapse = useCallback(() => setExpandedId(null),         []);

  const items = useMemo(() => history || [], [history]);

  return (
    <div
      data-testid="operation-history-root"
      style={{ display: "flex", flexDirection: "column" }}
    >
      {items.map((op, i) => (
        expandedId === op.loop_id ? (
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
            onExpand={onExpand}
          />
        )
      ))}
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
