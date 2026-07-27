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
 * Iter 309 · Batch-2 aftermath — read-only helpers used by the
 * persistent LoopStatusChip (in ChatPanel) and the AdminInspectLoop
 * page. Both go through the authenticated `api` client so the JWT
 * Bearer header is attached uniformly; NO raw fetch / localStorage
 * token juggling — the chip must not diverge from the rest of the
 * app's auth flow.
 */
export async function getActiveLoop(projectId) {
  const q = projectId ? `?project_id=${encodeURIComponent(projectId)}` : "";
  const r = await api.get(`/loop/active${q}`);
  return r?.data || r;
}

export async function getLoopInspect(loopId, tail = 20) {
  const r = await api.get(`/admin/loop-inspect/${loopId}?tail=${tail}`);
  return r?.data || r;
}

// ── Iter 314 · Admin inspect wrappers ────────────────────────────
// Small read-only helpers so the admin inspect pages can hit these
// endpoints with the same axios instance every other admin surface
// uses (JWT-in-Authorization via api.js request interceptor). Zero
// mutations. Bounds mirror the backend clamps in routers/admin.py.
export async function getSpeedDiagnostic({ windowDays = 30, sample = 20 } = {}) {
  const q = `?window_days=${encodeURIComponent(windowDays)}&sample=${encodeURIComponent(sample)}`;
  const r = await api.get(`/admin/speed-diagnostic${q}`);
  return r?.data || r;
}

export async function getScopeDriftAudit({ days = 30, limit = 50 } = {}) {
  const q = `?days=${encodeURIComponent(days)}&limit=${encodeURIComponent(limit)}`;
  const r = await api.get(`/admin/scope-drift-audit${q}`);
  return r?.data || r;
}

/**
 * Iter 212m-111 — Manual Ship gate. Once the engine reaches
 * PAUSED_FOR_USER/phase=ship with data.kind="awaiting_ship", the user
 * sees a "Ship to GitHub" button. Clicking it calls this helper with
 * approved=true; cancelling calls it with approved=false (loop
 * aborts). The actual GitHub commit happens server-side after the
 * call returns; the SSE stream then delivers the COMPLETED event.
 */
export async function confirmShip(loopId, approved) {
  const r = await api.post(`/loop/${loopId}/confirm-ship`, {
    approved, feedback: null,
  });
  return r?.data || r;
}

/**
 * Open the SSE stream and dispatch events.  Iter 309 · Batch-2 Item 6
 * — this now auto-reconnects on network error and honors
 * `Last-Event-ID` so a mid-loop connection drop (mobile switch,
 * proxy timeout, our own STREAM_MAX_S cap at 20 min) does NOT
 * lose events.  The server buffers up to `MAX_EVENTS_PER_LOOP=200`
 * events per loop and replays anything with seq > Last-Event-ID
 * on the next connect.  We ALSO dedup client-side by seq so a
 * duplicate replay row never fires `onEvent` twice.
 *
 * IMPORTANT for callers (ChatPanel / LoopLiveFeed): do NOT clear
 * feed state on `onReconnecting` — leave the last-known state
 * visible with a small "reconnecting…" indicator.  The next
 * `onEvent` after reconnect will be the FIRST unseen event, not
 * a redo of the whole loop.
 *
 * @param {string} loopId
 * @param {object} cb
 * @param {(ev: object) => void} [cb.onEvent]        — every unique event
 * @param {(ev: object) => void} [cb.onTerminal]     — completed/failed/aborted
 * @param {(err: Error) => void}  [cb.onError]       — network / parse failure
 * @param {(attempt: number) => void} [cb.onReconnecting] — attempt count (1+)
 * @param {() => void}            [cb.onReconnected] — first event received after reconnect
 *
 * @returns {AbortController}
 */
export function streamLoopEvents(loopId, cb = {}) {
  const ctrl = new AbortController();
  const token = localStorage.getItem("aurem_token") || "";
  const url   = `${API_BASE}/loop/${loopId}/stream`;

  // Iter 309 · Batch-2 Item 6 — client-side reconnect + dedup state.
  let lastEventId = null;   // `{loop_id}:{seq}` string from SSE `id:` line
  let lastSeenSeq = -1;     // numeric seq for dedup on replay
  let terminal    = false;  // stop reconnecting after loop ended
  let attempt     = 0;      // 0 = initial, 1+ = reconnect count

  async function _openOne() {
    const resp = await fetch(url, {
      method: "GET",
      headers: {
        "Authorization": token ? `Bearer ${token}` : "",
        "Accept": "text/event-stream",
        // Native EventSource sends this automatically; we send it
        // manually since we use fetch() to get Authorization support.
        ...(lastEventId ? { "Last-Event-ID": lastEventId } : {}),
      },
      signal: ctrl.signal,
    });
    if (!resp.ok || !resp.body) {
      throw new Error(`loop stream HTTP ${resp.status}`);
    }
    if (attempt > 0) cb.onReconnected?.();
    const reader  = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    try {
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        let idx;
        while ((idx = buf.indexOf("\n\n")) >= 0) {
          const frame = buf.slice(0, idx);
          buf = buf.slice(idx + 2);
          // Extract id: (single line) and all data: lines.
          let frameId = null;
          const dataLines = [];
          for (const line of frame.split("\n")) {
            if (line.startsWith("id:")) frameId = line.slice(3).trim();
            else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
          }
          if (!dataLines.length) continue;
          // Iter 309 · Batch-2 Item 6 — dedup by seq.  On resume
          // after reconnect the server replays events with
          // seq > Last-Event-ID; a well-behaved server won't
          // give us duplicates but a proxy retry might, so
          // belt-and-braces skip anything ≤ lastSeenSeq.
          if (frameId) {
            const parts = frameId.split(":");
            const seq = Number(parts[parts.length - 1]);
            if (Number.isFinite(seq)) {
              if (seq <= lastSeenSeq) continue;   // already delivered
              lastSeenSeq = seq;
            }
            lastEventId = frameId;
          }
          try {
            const ev = JSON.parse(dataLines.join("\n"));
            cb.onEvent?.(ev);
            const st = ev?.state;
            if (st === "completed" || st === "failed" || st === "aborted") {
              terminal = true;
              cb.onTerminal?.(ev);
            }
          } catch (e) {
            cb.onError?.(e);
          }
        }
      }
    } finally {
      try { reader.cancel(); } catch { /* swallow */ }
    }
  }

  (async () => {
    // Reconnect loop.  Backs off exponentially with a hard cap so a
    // dead backend doesn't hammer.  Stops on abort OR terminal event.
    while (!ctrl.signal.aborted && !terminal) {
      try {
        if (attempt > 0) cb.onReconnecting?.(attempt);
        await _openOne();
        // Server closed cleanly (terminal state or stream_capped) —
        // if not terminal (i.e. stream_capped at 20 min), reconnect
        // immediately and the buffer replays the missed 20+ min.
        if (terminal) break;
      } catch (e) {
        if (e?.name === "AbortError") return;
        cb.onError?.(e);
        if (terminal) break;
      }
      attempt += 1;
      // Small backoff — 1 s, 2 s, 4 s, 8 s, cap 8 s.  Matches the
      // server's `retry: 3000` preamble spirit without being
      // aggressive on repeated failures.
      const backoffMs = Math.min(8000, 1000 * (2 ** Math.min(attempt - 1, 3)));
      try {
        await new Promise((resolve, reject) => {
          const t = setTimeout(resolve, backoffMs);
          ctrl.signal.addEventListener("abort", () => {
            clearTimeout(t);
            reject(new DOMException("aborted", "AbortError"));
          }, { once: true });
        });
      } catch { /* aborted during backoff — loop condition catches it */ }
    }
  })();
  return ctrl;
}
