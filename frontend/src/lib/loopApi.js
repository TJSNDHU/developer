/**
 * lib/loopApi.js — Iter 212m-65 (Loop Mode Phase D wiring)
 *
 * Frontend client for the backend Phase B/C LoopEngine endpoints.
 * Replaces the Phase A prompt-suffix shortcut with the proper
 * /api/aurem-dev/loop/* SSE pipeline.
 *
 *   • startLoop(body)                 → POST /loop/start
 *   • confirmLoop(id, approved, fb)   → POST /loop/{id}/confirm
 *   • pauseResponse(id, action, fb)   → POST /loop/{id}/pause-response
 *   • cancelLoop(id)                  → POST /loop/{id}/cancel
 *   • streamLoopEvents(id, callbacks) → opens SSE on /loop/{id}/stream
 *     Returns an AbortController so the caller can stop the stream.
 *
 * All callbacks are optional. The stream auto-closes when the engine
 * reaches a terminal state (completed | failed | aborted) — the SSE
 * route on the backend closes the response in that case.
 */
import { api, API_BASE } from "./api";

export async function startLoop({ projectId, userMessage }) {
  const r = await api.post("/loop/start", {
    project_id:   projectId || null,
    user_message: userMessage,
  });
  return r?.data || r;
}

export async function confirmLoop(loopId, approved, feedback = "") {
  const r = await api.post(`/loop/${loopId}/confirm`, {
    approved, feedback: feedback || null,
  });
  return r?.data || r;
}

export async function pauseResponse(loopId, action, feedback = "") {
  const r = await api.post(`/loop/${loopId}/pause-response`, {
    action, feedback: feedback || null,
  });
  return r?.data || r;
}

export async function cancelLoop(loopId) {
  const r = await api.post(`/loop/${loopId}/cancel`, {});
  return r?.data || r;
}

/**
 * Open the SSE stream and dispatch events.
 *
 * @param {string} loopId
 * @param {object} cb
 * @param {(ev: object) => void} [cb.onEvent]     — called for every event
 * @param {(ev: object) => void} [cb.onTerminal]  — completed/failed/aborted
 * @param {(err: Error) => void}  [cb.onError]    — network / parse failure
 *
 * @returns {AbortController}
 */
export function streamLoopEvents(loopId, cb = {}) {
  const ctrl = new AbortController();
  const token = localStorage.getItem("aurem_token") || "";
  const url   = `${API_BASE}/loop/${loopId}/stream`;
  (async () => {
    try {
      const resp = await fetch(url, {
        method: "GET",
        headers: {
          "Authorization": token ? `Bearer ${token}` : "",
          "Accept": "text/event-stream",
        },
        signal: ctrl.signal,
      });
      if (!resp.ok || !resp.body) {
        cb.onError?.(new Error(`loop stream HTTP ${resp.status}`));
        return;
      }
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      try {
        // eslint-disable-next-line no-constant-condition
        while (true) {
          const { value, done } = await reader.read();
          if (done) break;
          buf += decoder.decode(value, { stream: true });
          // SSE frames are separated by \n\n. Each frame may have many
          // "data:" lines; comments (": keepalive") are ignored.
          let idx;
          while ((idx = buf.indexOf("\n\n")) >= 0) {
            const frame = buf.slice(0, idx);
            buf = buf.slice(idx + 2);
            const dataLines = frame
              .split("\n")
              .filter((l) => l.startsWith("data:"))
              .map((l) => l.slice(5).trim());
            if (!dataLines.length) continue;
            try {
              const ev = JSON.parse(dataLines.join("\n"));
              cb.onEvent?.(ev);
              const st = ev?.state;
              if (st === "completed" || st === "failed" || st === "aborted") {
                cb.onTerminal?.(ev);
              }
            } catch (e) {
              cb.onError?.(e);
            }
          }
        }
      } catch (e) {
        if (e?.name !== "AbortError") cb.onError?.(e);
      } finally {
        try { reader.cancel(); } catch { /* swallow */ }
      }
    } catch (e) {
      if (e?.name !== "AbortError") cb.onError?.(e);
    }
  })();
  return ctrl;
}
