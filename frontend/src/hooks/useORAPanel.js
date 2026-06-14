/**
 * useORAPanel.js — state + streaming for the Floating ORA Panel.
 *
 * Security gates (HARD):
 *   - JWT is taken from THIS browser's localStorage only — the same
 *     `aurem_token` key used by lib/api.js so the panel inherits the
 *     logged-in user's identity. Never the admin token.
 *   - Active project_id is fetched from the user's own /cto/projects
 *     list (server-side already filters by user_id).
 *   - session_id is created ONCE per panel-open so multi-turn context
 *     works (spec-clarification: a fresh ID every send would erase the
 *     conversation each turn).
 *   - Tokens & usage stamping happen entirely server-side under the
 *     authenticated JWT → cost lands on the user's account.
 */
import { useState, useCallback, useRef } from "react";
import { api } from "../lib/api";

// Match lib/api.js: REACT_APP_BACKEND_URL is the canonical key in this
// codebase. VITE_API_URL is the local-dev fallback.
const API_BASE =
  (typeof process !== "undefined" && process.env && process.env.REACT_APP_BACKEND_URL) ||
  (typeof import.meta !== "undefined" && import.meta.env && import.meta.env.VITE_API_URL) ||
  "";

function readAuthToken() {
  // `aurem_token` is the canonical key (lib/api.js). The other names
  // are kept as best-effort fallbacks for any future migration so we
  // never accidentally fall back to an admin/global token.
  return (
    localStorage.getItem("aurem_token") ||
    localStorage.getItem("jwt") ||
    localStorage.getItem("token") ||
    localStorage.getItem("auth_token")
  );
}

export function useORAPanel() {
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState([{
    role: "assistant",
    content: "Hey — I'm ORA. Ask me anything about your project, or tell me what to fix.",
  }]);
  const [busy, setBusy] = useState(false);
  const [projectId, setProjectId] = useState(null);
  const abortRef = useRef(null);
  // Persist one session_id per panel-open so multi-turn context works.
  const sessionIdRef = useRef(null);

  // Load user's active project on open. Uses api.* so the request
  // automatically carries the logged-in user's Authorization header.
  const loadProject = useCallback(async () => {
    try {
      const r = await api.get("/cto/projects/list");
      const projects = r.data?.projects || [];
      if (projects.length > 0) {
        setProjectId(projects[0].project_id);
      }
    } catch (_) { /* silent — panel still works without a project */ }
  }, []);

  const openPanel = useCallback(() => {
    setOpen(true);
    sessionIdRef.current = `ora-panel-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    // Iter 151 — broadcast so Shell.jsx can shrink the main app
    // grid by `clamp(360px, 35vw, 680px)` and make room for the panel
    // instead of overlaying it on top of the composer.
    try {
      window.dispatchEvent(new CustomEvent("aurem:ora-panel-state", {
        detail: { open: true },
      }));
    } catch { /* ignore */ }
    loadProject();
  }, [loadProject]);

  const closePanel = useCallback(() => {
    setOpen(false);
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }
    try {
      window.dispatchEvent(new CustomEvent("aurem:ora-panel-state", {
        detail: { open: false },
      }));
    } catch { /* ignore */ }
  }, []);

  const sendMessage = useCallback(async (text) => {
    if (!text.trim() || busy) return;

    setMessages((prev) => [...prev, { role: "user", content: text }]);
    setBusy(true);
    setMessages((prev) => [...prev, {
      role: "assistant", content: "", streaming: true,
    }]);

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const token = readAuthToken();
      if (!token) {
        throw new Error("Not signed in — please log in and retry.");
      }

      // Iter 155 — `agent: "ora"` so the stream is routed straight to
      // the aurem.live ORA upstream (its own engine + system prompt),
      // not the AUREM orchestrator. No review-mode pill from the
      // panel; ORA picks its own model on the server side.
      const response = await fetch(
        `${API_BASE}/api/aurem-dev/chat/stream`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "Authorization": `Bearer ${token}`,
          },
          body: JSON.stringify({
            prompt: text,
            project_id: projectId || undefined,
            session_id: sessionIdRef.current,
            max_tool_iters: 4,
            agent: "ora",
          }),
          signal: controller.signal,
        },
      );

      if (!response.ok || !response.body) {
        throw new Error(`HTTP ${response.status}`);
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let assistantText = "";
      let buf = "";

      // SSE framing — lines may be split across chunks; buffer until
      // we see a real newline.
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        let idx;
        while ((idx = buf.indexOf("\n")) !== -1) {
          const line = buf.slice(0, idx);
          buf = buf.slice(idx + 1);
          if (!line.startsWith("data: ")) continue;
          try {
            const data = JSON.parse(line.slice(6));
            if (data.token) {
              assistantText += data.token;
              setMessages((prev) => {
                const copy = [...prev];
                const last = copy[copy.length - 1];
                if (last?.role === "assistant") {
                  copy[copy.length - 1] = {
                    ...last, content: assistantText, streaming: true,
                  };
                }
                return copy;
              });
            }
            if (data.done) {
              setMessages((prev) => {
                const copy = [...prev];
                const last = copy[copy.length - 1];
                if (last?.role === "assistant") {
                  copy[copy.length - 1] = { ...last, streaming: false };
                }
                return copy;
              });
            }
          } catch (_) { /* skip malformed frame */ }
        }
      }
    } catch (e) {
      if (e.name !== "AbortError") {
        setMessages((prev) => {
          const copy = [...prev];
          copy[copy.length - 1] = {
            role: "assistant",
            content: e.message || "Something went wrong. Please try again.",
            streaming: false,
            error: true,
          };
          return copy;
        });
      }
    } finally {
      setBusy(false);
      abortRef.current = null;
    }
  }, [busy, projectId]);

  const stopStream = useCallback(() => {
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }
    setBusy(false);
    setMessages((prev) => {
      const copy = [...prev];
      const last = copy[copy.length - 1];
      if (last?.streaming) {
        copy[copy.length - 1] = {
          ...last,
          streaming: false,
          stopped: true,
          content: last.content || "⏹ Stopped",
        };
      }
      return copy;
    });
  }, []);

  return {
    open, messages, busy, projectId,
    openPanel, closePanel, sendMessage, stopStream,
  };
}
