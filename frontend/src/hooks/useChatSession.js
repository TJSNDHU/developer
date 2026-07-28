/**
 * useChatSession — session-level state: sessionId lifecycle, usage,
 * turn persistence.
 *
 * Iter 140 — extracted from ChatPanel.jsx. Holds the network calls
 * that need a session_id (POST /chat/history, GET /usage/me). Pure
 * side-effects, no DOM ownership.
 *
 * Iter 280 P0 fix — the hook now ALSO owns `sessionId` itself and
 * persists it to localStorage, generating one if the caller doesn't
 * pass one in.
 *
 * Iter 331 — dead-code prune: removed `loadHistory` (+ its
 * `loadingHistory` state) and the `refreshSessions` no-op placeholder.
 * Neither had a single caller in the codebase — ChatPanel owns its own
 * history loading + loadingHistory state, and server-side session
 * listing lives in Shell.jsx's SessionCtx.
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

  const [usage, setUsage] = useState(null);

  const refreshUsage = useCallback(async () => {
    try {
      const r = await api.get("/usage/me");
      setUsage(r.data);
    } catch (_e) {
      /* usage endpoint may 401 pre-login — non-fatal */
    }
  }, []);

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
    usage,
    refreshUsage,
    persistTurn,
  };
}
