/**
 * useChatSession — session-level state: sessionId lifecycle,
 * history load, usage, turn persistence.
 *
 * Iter 140 — extracted from ChatPanel.jsx. Holds the network calls
 * that need a session_id (GET /chat/history, POST /chat/history,
 * GET /usage/me). Pure side-effects, no DOM ownership.
 *
 * Iter 280 P0 fix — the hook now ALSO owns `sessionId` itself and
 * persists it to localStorage. Dashboard.jsx has been destructuring
 * `sessionId` from this hook's return since Iter 140, but the hook
 * never actually returned one — it only accepted `sessionId` as a
 * parameter. That silent `undefined` meant `loadHistory` early-returned
 * on every fresh mount → chat history vanished on every browser
 * refresh. Fixed by generating + persisting a sessionId if the caller
 * doesn't pass one in.
 */
import { useState, useCallback, useEffect } from "react";
import { api } from "../lib/api";

const _SESSION_STORAGE_KEY = "aurem.chat.sessionId";

function _readOrCreateSessionId() {
  try {
    const existing = localStorage.getItem(_SESSION_STORAGE_KEY);
    if (existing && existing.length >= 8) return existing;
  } catch (_e) { /* SSR / private mode — fall through */ }
  const fresh = (typeof crypto !== "undefined" && crypto.randomUUID)
    ? crypto.randomUUID()
    : "sess_" + Math.random().toString(36).slice(2) + Date.now().toString(36);
  try { localStorage.setItem(_SESSION_STORAGE_KEY, fresh); } catch (_e) { /* */ }
  return fresh;
}

export function useChatSession({ sessionId: sessionIdProp, onTurnSaved } = {}) {
  const [ownSessionId] = useState(
    () => sessionIdProp || _readOrCreateSessionId()
  );
  const sessionId = sessionIdProp || ownSessionId;
  const refreshSessions = useCallback(() => {
    // Placeholder — Dashboard.jsx has always called this after a turn
    // completes, but the hook never implemented it. Currently a no-op;
    // wire up server-side session listing here if/when needed.
  }, []);

  const [loadingHistory, setLoadingHistory] = useState(false);
  const [usage, setUsage] = useState(null);

  const refreshUsage = useCallback(async () => {
    try {
      const r = await api.get("/usage/me");
      setUsage(r.data);
    } catch (_e) {
      /* usage endpoint may 401 pre-login — non-fatal */
    }
  }, []);

  const loadHistory = useCallback(
    async (setMessages) => {
      if (!sessionId) return;
      setLoadingHistory(true);
      try {
        const r = await api.get(`/chat/history?session_id=${sessionId}`);
        const turns = r.data?.turns || [];
        if (turns.length) {
          setMessages(
            turns.map((t) => ({
              role: t.role,
              content: t.content,
              provider: t.provider,
            })),
          );
        }
      } catch (_e) {
        /* 404 = new session, that's fine */
      }
      setLoadingHistory(false);
    },
    [sessionId],
  );

  const persistTurn = useCallback(
    async (userMsg, assistantMsg) => {
      if (!sessionId) return;
      try {
        await api.post("/chat/history", {
          session_id: sessionId,
          turns: [userMsg, assistantMsg],
        });
        if (onTurnSaved) onTurnSaved();
      } catch (_e) {
        /* persistence failure logged server-side */
      }
    },
    [sessionId, onTurnSaved],
  );

  useEffect(() => {
    refreshUsage();
  }, [refreshUsage]);

  return {
    sessionId,
    refreshSessions,
    loadingHistory,
    usage,
    refreshUsage,
    loadHistory,
    persistTurn,
  };
}
