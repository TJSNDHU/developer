/**
 * api.js — Single source for backend URL + auth helpers.
 */
import axios from "axios";

// Backend base URL: REACT_APP_BACKEND_URL (preview) or VITE_API_URL (local)
const BACKEND =
  (typeof process !== "undefined" && process.env && process.env.REACT_APP_BACKEND_URL) ||
  (typeof import.meta !== "undefined" && import.meta.env && import.meta.env.VITE_API_URL) ||
  "https://auremcto.com";

export const API_BASE = `${BACKEND}/api/aurem-dev`;
export const api = axios.create({
  baseURL: API_BASE,
  timeout: 60000,
});

// Attach JWT from localStorage on every request
api.interceptors.request.use((config) => {
  const token = localStorage.getItem("aurem_token");
  if (token) {
    config.headers = config.headers || {};
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// Iter 212m-48 — auto-pick-up refreshed JWTs.
// The backend `/auth/me` endpoint now returns a freshly re-signed
// token on every call (TTL is 7d, but active users glide
// indefinitely while idle / leaked tokens die within the window).
// Any other endpoint that returns `{ token: "..." }` in the body is
// likewise treated as an authoritative re-issue.
api.interceptors.response.use((response) => {
  try {
    const t = response?.data?.token;
    if (typeof t === "string" && t.length > 20 && t.split(".").length === 3) {
      const current = localStorage.getItem("aurem_token");
      if (t !== current) localStorage.setItem("aurem_token", t);
    }
  } catch { /* never let interceptor errors break the call */ }
  return response;
});

// Health check (no /aurem-dev prefix — it's on /api/health)
export const healthApi = axios.create({
  baseURL: `${BACKEND}/api`,
  timeout: 10000,
});

// Iter 170 — Request dedup cache for `GET /cto/tasks/{id}`.
//
// MessageBubble (one per shipped turn) and LiveTaskPopup each spin up
// their own 1s/2s poll loops against the same task id. With ~3-4
// streaming bubbles in view + the floating popup that's been
// observed firing ~80 requests in 30 s for a single task — heavy on
// the DB and our preview-edge bandwidth for zero new info per call.
//
// We coalesce identical in-flight calls and replay the resolved
// response for up to 1.5 s. The pattern is intentionally narrow
// (`/cto/tasks/<id>` with no trailing path) so the dedup never
// touches `submit`, `rollback`, `/scan`, etc.
const _TASK_DETAIL_RX = /^\/cto\/tasks\/[^/?]+\/?$/;
const _TASK_CACHE_TTL_MS = 1500;
const _taskGetCache = new Map(); // url -> { ts, promise }

const _origApiGet = api.get.bind(api);
api.get = function dedupedGet(url, config) {
  if (typeof url === "string" && _TASK_DETAIL_RX.test(url.split("?")[0])) {
    // Key on full url incl. query so different `?fields=` calls don't collide
    const cached = _taskGetCache.get(url);
    const now = Date.now();
    if (cached && now - cached.ts < _TASK_CACHE_TTL_MS) {
      return cached.promise;
    }
    const promise = _origApiGet(url, config).catch((err) => {
      // Evict on failure so the next caller actually retries.
      _taskGetCache.delete(url);
      throw err;
    });
    _taskGetCache.set(url, { ts: now, promise });
    return promise;
  }
  return _origApiGet(url, config);
};

export function setToken(t) {
  if (t) localStorage.setItem("aurem_token", t);
  else localStorage.removeItem("aurem_token");
}

export function getToken() {
  return localStorage.getItem("aurem_token");
}

export function setUser(u) {
  if (u) localStorage.setItem("aurem_user", JSON.stringify(u));
  else localStorage.removeItem("aurem_user");
}

export function getUser() {
  try {
    const raw = localStorage.getItem("aurem_user");
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

export function logout() {
  setToken(null);
  setUser(null);
  window.location.href = "/login";
}

/** Generate a stable session id for a chat thread. */
export function newSessionId() {
  if (typeof crypto !== "undefined" && crypto.randomUUID) {
    return crypto.randomUUID();
  }
  return `s-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

/** SSE-style stream over fetch (we POST JSON, so EventSource won't work). */
export async function streamChat({ prompt, sessionId, maxToolIters = 2,
                                    maxxMode = false, projectId = null,
                                    agent = "auto", mode = "swift",
                                    executionMode = "prompt",  // Iter 212m-58
                                    f12Payload = null,
                                    onMeta, onMode, onToken, onWatchdog, onWatchdogPending,
                                    onOpsRedirect,
                                    onThinking, onTaskHandoff, onDone, onError,
                                    onStep,   // Iter 212m-19 — live step cards
                                    signal }) {
  const token = getToken();
  const res = await fetch(`${API_BASE}/chat/stream`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({
      prompt,
      session_id: sessionId,
      max_tool_iters: maxToolIters,
      maxx_mode: maxxMode,
      agent,
      mode,
      execution_mode: executionMode,  // Iter 212m-58
      project_id: projectId,
      f12_payload: f12Payload,
    }),
    signal,
  });
  if (!res.ok || !res.body) {
    const txt = await res.text().catch(() => "");
    onError?.(`HTTP ${res.status}: ${txt || res.statusText}`);
    return;
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  // Iter 212m-57 — wrap the read loop in a try/catch so an AbortError
  // (raised when the caller cancels the AbortController, e.g. via the
  // stuck-thinking watchdog or the user clicking Stop) is handled
  // silently instead of bubbling as an unhandled rejection labelled
  // "BodyStreamBuffer was aborted" in the console.
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const frames = buf.split("\n\n");
      buf = frames.pop() || "";
      for (const frame of frames) {
        const line = frame.split("\n").find((l) => l.startsWith("data:"));
        if (!line) continue;
        try {
          const payload = JSON.parse(line.slice(5).trim());
          if (payload.meta) onMeta?.(payload);
          else if (payload.type === "mode") onMode?.(payload);
          else if (payload.type === "step") onStep?.(payload);   // Iter 212m-19
          else if (payload.type === "ops_redirect") onOpsRedirect?.(payload);
          else if (payload.type === "task_handoff") onTaskHandoff?.(payload);
          else if (payload.token) onToken?.(payload.token);
          else if (payload.thinking) onThinking?.(payload.elapsed_s || 0, payload.activity, payload.invocations || []);
          else if (payload.watchdog_pending) onWatchdogPending?.();
          else if (payload.watchdog) onWatchdog?.(payload.watchdog);
          else if (payload.done) onDone?.(payload);
          else if (payload.error) onError?.(payload.error);
        } catch {
          /* swallow malformed frame */
        }
      }
    }
  } catch (e) {
    // AbortError (signal.abort) and the related TypeError that some
    // browsers surface when the body stream is force-closed are not
    // real failures — they're the user / watchdog cancelling. Any
    // other error is a true network / parse failure → onError.
    const name = e?.name || "";
    const msg  = (e?.message || "").toLowerCase();
    const isAbort =
      name === "AbortError" ||
      msg.includes("aborted") ||
      msg.includes("body stream") ||
      msg.includes("bodystreambuffer");
    if (!isAbort) {
      onError?.(`Stream read failed: ${e?.message || e}`);
    }
    // Try to release the reader cleanly so the browser doesn't log
    // "ReadableStreamDefaultReader is still being read" warnings.
    try { reader.cancel(); } catch { /* ignore */ }
  }
}
