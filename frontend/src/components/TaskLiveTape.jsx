/**
 * TaskLiveTape.jsx — live "worker tape" for a shipped task.
 *
 * Renders a terminal-style feed of SSE frames coming from
 *   GET /api/aurem-dev/cto/tasks/:id/stream
 * inside an assistant chat bubble.
 *
 * Frame kinds rendered:
 *   • step           normal log line (›)
 *   • done           green ✓, closes the tape
 *   • fail           red ✕, closes the tape
 *   • parallel       agents=[…] — shows badges + per-agent mini-bars
 *   • parallel_agent role + ok=true|false — settles one mini-bar
 *
 * Auth: backend wants Bearer JWT (same as everywhere else), and
 * EventSource can't send custom headers — so we use fetch + a
 * ReadableStream parser (matches lib/api.js#streamChat).
 *
 * Iter 73 Task 1 — initial live tape.
 * Iter 73 Task 2 — parallel-mode agent badges + sub-tapes.
 */
import { useEffect, useRef, useState } from "react";
import { API_BASE, getToken } from "../lib/api";

export default function TaskLiveTape({ taskId, onDone }) {
  const [steps, setSteps] = useState([]);
  const [pct, setPct] = useState(0);
  const [done, setDone] = useState(false);
  // agents: { [Name]: "running"|"done"|"failed" }
  const [agents, setAgents] = useState(null);
  const abortRef = useRef(null);

  useEffect(() => {
    if (!taskId) return;
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    let cancelled = false;

    (async () => {
      try {
        const token = getToken();
        const res = await fetch(`${API_BASE}/cto/tasks/${taskId}/stream`, {
          headers: token ? { Authorization: `Bearer ${token}` } : {},
          signal: ctrl.signal,
        });
        if (!res.ok || !res.body) {
          setDone(true);
          return;
        }
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buf = "";
        while (!cancelled) {
          const { done: streamDone, value } = await reader.read();
          if (streamDone) break;
          buf += decoder.decode(value, { stream: true });
          const frames = buf.split("\n\n");
          buf = frames.pop() || "";
          for (const frame of frames) {
            const line = frame.split("\n").find((l) => l.startsWith("data:"));
            if (!line) continue;
            let d;
            try { d = JSON.parse(line.slice(5).trim()); }
            catch { continue; }
            if (!d || d.type === "ping") continue;

            // Parallel-mode bootstrap: register the agent roster.
            if (d.type === "parallel" && Array.isArray(d.agents)) {
              setAgents(() => {
                const next = {};
                for (const a of d.agents) next[a] = "running";
                return next;
              });
            }
            // Per-agent terminal result: settle the mini-bar.
            if (d.type === "parallel_agent" && d.role) {
              setAgents((cur) => ({
                ...(cur || {}),
                [d.role]: d.ok === false ? "failed" : "done",
              }));
            }

            if (d.step) setSteps((p) => [...p, d]);
            if (typeof d.pct === "number") setPct(d.pct);
            if (d.type === "done" || d.type === "fail") {
              setDone(true);
              try { onDone?.(d); } catch { /* host handler crash */ }
            }
          }
        }
      } catch {
        if (!cancelled) setDone(true);
      }
    })();

    return () => {
      cancelled = true;
      try { ctrl.abort(); } catch { /* ignore */ }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [taskId]);

  if (!steps.length && !done) {
    return (
      <div
        data-testid="task-live-tape-empty"
        style={{
          fontFamily: "var(--font-mono, ui-monospace, monospace)",
          fontSize: 11,
          color: "var(--text-faint, #807a68)",
          padding: "8px 14px",
          marginTop: 8,
        }}
      >
        <span style={caretStyle} />
        <span style={{ marginLeft: 8 }}>queued…</span>
      </div>
    );
  }

  return (
    <div
      data-testid="task-live-tape"
      data-task-id={taskId}
      style={{
        fontFamily: "var(--font-mono, ui-monospace, monospace)",
        fontSize: 11,
        background: "var(--panel-2, #161a25)",
        border: "1px solid var(--border, rgba(255,200,120,0.1))",
        borderRadius: 6,
        padding: "10px 14px",
        marginTop: 8,
      }}
    >
      <div
        aria-label={`progress ${pct}%`}
        style={{
          height: 2,
          background: "var(--border, rgba(255,200,120,0.12))",
          borderRadius: 2,
          marginBottom: 8,
          overflow: "hidden",
        }}
      >
        <div
          data-testid="task-live-tape-bar"
          style={{
            height: "100%",
            width: `${Math.min(100, Math.max(0, pct))}%`,
            background: "var(--accent, #ff8a2a)",
            borderRadius: 2,
            transition: "width .4s ease",
          }}
        />
      </div>

      {/* Parallel-mode agent badges + per-agent mini progress bars */}
      {agents && Object.keys(agents).length > 0 && (
        <div
          data-testid="task-live-tape-agents"
          style={{
            display: "grid",
            gridTemplateColumns: `repeat(${Object.keys(agents).length}, 1fr)`,
            gap: 8,
            margin: "4px 0 10px",
          }}
        >
          {Object.entries(agents).map(([name, status]) => (
            <AgentMini key={name} name={name} status={status} />
          ))}
        </div>
      )}

      {steps.map((s, i) => {
        if (s.type === "parallel_agent") {
          // already reflected in the mini-bars above — keep the feed clean
          return null;
        }
        if (s.type === "task_state" && s.files_total > 1) {
          // Multi-file write progress (pairs with TaskManagementPanel).
          const pctFiles = Math.round((s.files_done / s.files_total) * 100);
          return (
            <div
              key={i}
              data-testid={`task-live-tape-state-${i}`}
              style={{
                fontSize: 10,
                color: "var(--accent-2, #ffb347)",
                fontFamily: "var(--font-mono, ui-monospace, monospace)",
                padding: "3px 0 4px",
              }}
            >
              Writing {s.files_done}/{s.files_total} files
              <div style={{
                height: 2,
                background: "var(--border, rgba(255,200,120,0.12))",
                borderRadius: 2,
                overflow: "hidden",
                marginTop: 3,
                width: 140,
              }}>
                <div style={{
                  height: "100%",
                  width: `${pctFiles}%`,
                  background: "var(--accent, #ff8a2a)",
                  borderRadius: 2,
                  transition: "width .3s ease",
                }}/>
              </div>
            </div>
          );
        }
        return (
          <div
            key={i}
            data-testid={`task-live-tape-step-${i}`}
            style={{
              display: "flex",
              gap: 8,
              alignItems: "center",
              padding: "2px 0",
              color:
                s.type === "done"
                  ? "var(--ok, #6dd4a1)"
                  : s.type === "fail"
                  ? "var(--danger, #ff6b6b)"
                  : s.type === "parallel"
                  ? "var(--accent-2, #ffb347)"
                  : "var(--text-dim, #a39d8a)",
            }}
          >
            <span
              style={{
                fontSize: 9,
                color: "var(--text-faint, #807a68)",
                minWidth: 55,
                flexShrink: 0,
              }}
            >
              {new Date((s.ts || Date.now() / 1000) * 1000).toLocaleTimeString()}
            </span>
            <span style={{ width: 10, flexShrink: 0 }}>
              {s.type === "done" ? "✓"
                : s.type === "fail" ? "✕"
                : s.type === "parallel" ? "⚡"
                : "›"}
            </span>
            <span style={{ flex: 1, wordBreak: "break-word" }}>{s.step}</span>
          </div>
        );
      })}

      {!done && <span data-testid="task-live-tape-caret" style={caretStyle} />}
    </div>
  );
}

function AgentMini({ name, status }) {
  // Indeterminate pulse while running; settles to full green/red on
  // terminal events.
  const accent = "var(--accent, #ff8a2a)";
  const ok     = "var(--ok, #6dd4a1)";
  const bad    = "var(--danger, #ff6b6b)";
  const bgFill = status === "done" ? ok : status === "failed" ? bad : accent;
  const isRunning = status === "running";

  return (
    <div
      data-testid={`agent-mini-${name.toLowerCase()}`}
      data-status={status}
      style={{
        background: "var(--bg-elev, #0a0c10)",
        border: "1px solid var(--border, rgba(255,200,120,0.16))",
        borderRadius: 4,
        padding: "5px 7px",
      }}
    >
      <div style={{
        display: "flex", alignItems: "center", gap: 5,
        fontSize: 10, fontWeight: 600, letterSpacing: "0.04em",
        color: status === "failed" ? bad : "var(--text-dim, #a39d8a)",
        marginBottom: 4,
      }}>
        <span style={{
          width: 5, height: 5, borderRadius: 5,
          background: bgFill,
          boxShadow: isRunning ? `0 0 6px ${bgFill}` : "none",
          animation: isRunning ? "aurem-blink 1s ease-in-out infinite" : "none",
          flexShrink: 0,
        }}/>
        <span style={{
          textTransform: "uppercase",
          overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
        }}>{name}</span>
        <span style={{
          marginLeft: "auto", color: "var(--text-faint)",
          fontSize: 9,
        }}>
          {status === "done" ? "✓" : status === "failed" ? "✕" : "…"}
        </span>
      </div>
      <div style={{
        height: 2, background: "var(--border, rgba(255,200,120,0.12))",
        borderRadius: 2, overflow: "hidden",
      }}>
        <div style={{
          height: "100%",
          width: isRunning ? "40%" : "100%",
          background: bgFill,
          borderRadius: 2,
          animation: isRunning ? "aurem-mini-slide 1.4s ease-in-out infinite" : "none",
          transition: "width .4s ease",
        }}/>
      </div>
    </div>
  );
}

const caretStyle = {
  display: "inline-block",
  width: 6,
  height: 12,
  background: "var(--accent, #ff8a2a)",
  animation: "aurem-blink 1s ease-in-out infinite",
  marginLeft: 4,
  verticalAlign: "middle",
};
