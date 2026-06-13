/**
 * useChatMessages — message list state + common transitions.
 *
 * Iter 140 — extracted from ChatPanel.jsx as part of the ChatPanel
 * split. Keeps every message-list mutation in one place so a future
 * refactor (or test) can target ONE module instead of grepping a
 * 1500-line component.
 */
import { useState, useCallback } from "react";

const WELCOME = {
  role: "assistant",
  content:
    "Hey — I'm ORA, your AI engineering co-pilot. Connect a GitHub repo in **Projects** and I'll read your codebase, plan changes, and ship commits directly. What are we building?",
  provider: "ora",
};

export function useChatMessages() {
  const [messages, setMessages] = useState([WELCOME]);

  const addMessage = useCallback((msg) => {
    setMessages((prev) => [...prev, msg]);
  }, []);

  const updateLastAssistant = useCallback((updater) => {
    setMessages((prev) => {
      const copy = [...prev];
      for (let i = copy.length - 1; i >= 0; i--) {
        if (copy[i].role === "assistant") {
          copy[i] =
            typeof updater === "function"
              ? updater(copy[i])
              : { ...copy[i], ...updater };
          break;
        }
      }
      return copy;
    });
  }, []);

  const finalizeStreaming = useCallback(() => {
    setMessages((prev) =>
      prev.map((m) => {
        if (m.role !== "assistant" || !m.streaming) return m;
        return { ...m, streaming: false };
      }),
    );
  }, []);

  const stopStreaming = useCallback(() => {
    setMessages((prev) =>
      prev.map((m) => {
        if (m.role !== "assistant" || !m.streaming) return m;
        return {
          ...m,
          streaming: false,
          stopped: true,
          content: m.content || "⏹ Stopped",
        };
      }),
    );
  }, []);

  const clearMessages = useCallback(() => {
    setMessages([WELCOME]);
  }, []);

  return {
    messages,
    setMessages,
    addMessage,
    updateLastAssistant,
    finalizeStreaming,
    stopStreaming,
    clearMessages,
    WELCOME,
  };
}
