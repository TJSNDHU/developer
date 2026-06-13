/**
 * useChatSession — session-level state: history load, usage,
 * turn persistence.
 *
 * Iter 140 — extracted from ChatPanel.jsx. Holds the network calls
 * that need a session_id (GET /chat/history, POST /chat/history,
 * GET /usage/me). Pure side-effects, no DOM ownership.
 */
import { useState, useCallback, useEffect } from "react";
import { api } from "../lib/api";

export function useChatSession({ sessionId, onTurnSaved } = {}) {
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
    loadingHistory,
    usage,
    refreshUsage,
    loadHistory,
    persistTurn,
  };
}
