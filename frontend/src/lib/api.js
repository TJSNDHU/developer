/**
 * api.js — Single source for backend URL + auth helpers.
 */
import axios from "axios";

// Backend base URL: REACT_APP_BACKEND_URL (preview) or VITE_API_URL (local)
const BACKEND =
  (typeof process !== "undefined" && process.env && process.env.REACT_APP_BACKEND_URL) ||
  (typeof import.meta !== "undefined" && import.meta.env && import.meta.env.VITE_API_URL) ||
  "";

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

// Health check (no /aurem-dev prefix — it's on /api/health)
export const healthApi = axios.create({
  baseURL: `${BACKEND}/api`,
  timeout: 10000,
});

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
                                    onMeta, onToken, onWatchdog, onWatchdogPending,
                                    onThinking, onDone, onError, signal }) {
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
      project_id: projectId,
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
        else if (payload.token) onToken?.(payload.token);
        else if (payload.thinking) onThinking?.(payload.elapsed_s || 0, payload.activity);
        else if (payload.watchdog_pending) onWatchdogPending?.();
        else if (payload.watchdog) onWatchdog?.(payload.watchdog);
        else if (payload.done) onDone?.(payload);
        else if (payload.error) onError?.(payload.error);
      } catch {
        /* swallow malformed frame */
      }
    }
  }
}
