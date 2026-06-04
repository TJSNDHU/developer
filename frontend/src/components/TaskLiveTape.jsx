/**
 * TaskLiveTape.jsx — live "worker tape" for a shipped task.
 *
 * Renders a terminal-style feed of SSE frames coming from
 *   GET /api/aurem-dev/cto/tasks/:id/stream
 * inside an assistant chat bubble. Shows:
 *   • thin orange progress bar 0→100%
 *   • timestamped log lines with colour by kind (step/done/fail)
 *   • blinking caret while the task is still running
 *
 * Auth: backend wants Bearer JWT (same as everywhere else), and
 * EventSource can't send custom headers — so we use fetch + a
 * ReadableStream parser (matches lib/api.js#streamChat).
 */
import { useEffect, useRef, useState } from "react";
import { API_BASE, getToken } from "../lib/api";

export default function TaskLiveTape({ taskId, onDone }) {
  const [steps, setSteps] = useState([]);
  const [pct, setPct] = useState(0);
  const [done, setDone] = useState(false);
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
            if (d.step) setSteps((p) => [...p, d]);
            if (typeof d.pct === "number") setPct(d.pct);
            if (d.type === "done" || d.type === "fail") {
              setDone(true);
              try { onDone?.(d); } catch { /* host handler crash */ }
            }
          }
        }
      } catch {
        // network drop or aborted — stop spinner so UI doesn't lie
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

      {steps.map((s, i) => (
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
            {s.type === "done" ? "✓" : s.type === "fail" ? "✕" : "›"}
          </span>
          <span style={{ flex: 1, wordBreak: "break-word" }}>{s.step}</span>
        </div>
      ))}

      {!done && <span data-testid="task-live-tape-caret" style={caretStyle} />}
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
