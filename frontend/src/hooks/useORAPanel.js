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
import { api, getUser } from "../lib/api";
import { getActiveProjectId, setActiveProjectId } from "../components/TabBar";

// Match lib/api.js: REACT_APP_BACKEND_URL is the canonical key in this
// codebase. VITE_API_URL is the local-dev fallback.
const API_BASE =
  (typeof process !== "undefined" && process.env && process.env.REACT_APP_BACKEND_URL) ||
  (typeof import.meta !== "undefined" && import.meta.env && import.meta.env.VITE_API_URL) ||
  "";

// Iter 164 — Personalised welcome. Replaces the generic "hey, I am ORA"
// line with a project-aware greeting that addresses the user by first
// name. Plain text only (no emoji in body) so SpeechSynthesis reads
// naturally; a single 👋 sits in the headline where TTS pauses anyway.
const WELCOME = `Welcome to AUREM CTO, {name}! 👋

Your AI Advisor is ready. I've already loaded your project context and I'm here to help you ship faster, debug smarter, and make better technical decisions.

What are we building today?`;

function buildWelcomeMessage() {
  const user = getUser();
  const firstName = (user?.name || "").split(" ")[0] || "Developer";
  return WELCOME.replace("{name}", firstName);
}

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
    // Iter 164 — personalised "Ask Advisor" welcome (was the legacy
    // "hey, I am ORA" line). buildWelcomeMessage() reads the cached
    // user from localStorage so the greeting addresses the user by
    // first name. Plain words only — TTS reads it naturally.
    content: buildWelcomeMessage(),
  }]);
  const [busy, setBusy] = useState(false);
  const [projectId, setProjectId] = useState(null);
  const abortRef = useRef(null);
  // Persist one session_id per panel-open so multi-turn context works.
  const sessionIdRef = useRef(null);

  // Load user's active project on open. Uses api.* so the request
  // automatically carries the logged-in user's Authorization header.
  //
  // Iter 212m-190 — PROJECT CONTEXT BUG FIX. Previously this hook always
  // grabbed `projects[0]` from the list, ignoring the user's actually
  // selected project (stored in localStorage as `aurem_active_project`).
  // Result: main chat showed "Project: automation / Repo: TJSNDHU/Aurem"
  // while the Advisor claimed "No repo connected" because it was pointing
  // at a different project entirely.
  //
  // New rules:
  //   1. Prefer the localStorage active project id (TabBar's source of truth).
  //   2. Verify it still exists in the fetched list — if not (deleted),
  //      auto-heal by falling back to the first *wired* project and
  //      persist that as the new active id.
  //   3. If nothing is set at all, auto-activate the first wired project
  //      so the Advisor is never operating on a null context when the
  //      user actually has connected repos.
  const loadProject = useCallback(async () => {
    try {
      const r = await api.get("/cto/projects/list");
      const projects = r.data?.projects || [];
      if (projects.length === 0) return;

      const wired = projects.filter(
        (p) => p.github_owner && p.github_repo,
      );
      const savedId = getActiveProjectId();
      const savedStillExists = savedId && projects.some((p) => p.project_id === savedId);

      let chosen = null;
      if (savedStillExists) {
        chosen = savedId;
      } else if (wired.length > 0) {
        chosen = wired[0].project_id;
        // Persist so TabBar + main chat also converge on the same id.
        setActiveProjectId(chosen);
      } else if (projects.length > 0) {
        chosen = projects[0].project_id;
      }
      if (chosen) setProjectId(chosen);
    } catch (_) { /* silent — panel still works without a project */ }
  }, []);

  const openPanel = useCallback(() => {
    setOpen(true);
    sessionIdRef.current = `ora-panel-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    // Iter 212m-190 — synchronously seed projectId from localStorage
    // BEFORE the async /cto/projects/list fetch resolves. This guarantees
    // the very first message from the user goes with a real project_id
    // even if they open the panel and immediately hit Send.
    const savedId = getActiveProjectId();
    if (savedId) setProjectId(savedId);
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
