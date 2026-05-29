/**
 * ChatPanel.jsx — Streaming chat with session persistence.
 *
 * Props:
 *   sessionId   (required)  — UUID of the active chat thread
 *   onTurnSaved (optional)  — called after the assistant turn is persisted,
 *                              so the sidebar can refresh its sessions list.
 */
import React, { useState, useRef, useEffect, useCallback } from "react";
import { Send, Bot, User, Loader2, Square } from "lucide-react";
import { api, streamChat } from "../lib/api";

const WELCOME = {
  role: "assistant",
  content:
    "I'm AUREM CTO — your sovereign engineering co-pilot. Ask me to plan a feature, write code, or debug an error. What are we shipping today?",
  provider: "system",
};

export default function ChatPanel({ sessionId, onTurnSaved }) {
  const [messages, setMessages] = useState([WELCOME]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const endRef = useRef(null);
  const abortRef = useRef(null);

  // Load saved history whenever the session changes
  useEffect(() => {
    if (!sessionId) return;
    let cancelled = false;
    setLoadingHistory(true);
    api
      .get(`/chat/history`, { params: { session_id: sessionId } })
      .then((r) => {
        if (cancelled) return;
        const turns = r.data?.messages || [];
        if (turns.length === 0) {
          setMessages([WELCOME]);
        } else {
          setMessages(turns.map((t) => ({
            role: t.role,
            content: t.content,
            provider: t.provider,
          })));
        }
      })
      .catch(() => !cancelled && setMessages([WELCOME]))
      .finally(() => !cancelled && setLoadingHistory(false));
    return () => {
      cancelled = true;
    };
  }, [sessionId]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, busy]);

  const stop = useCallback(() => {
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }
    setBusy(false);
  }, []);

  async function send(e) {
    e?.preventDefault();
    const text = input.trim();
    if (!text || busy || !sessionId) return;
    setInput("");
    // Push user message + empty assistant placeholder
    setMessages((m) => [
      ...m,
      { role: "user", content: text },
      { role: "assistant", content: "", streaming: true },
    ]);
    setBusy(true);

    const ctrl = new AbortController();
    abortRef.current = ctrl;
    let providerSeen = "";

    await streamChat({
      prompt: text,
      sessionId,
      maxToolIters: 2,
      signal: ctrl.signal,
      onMeta: (m) => {
        if (m.provider) providerSeen = m.provider;
      },
      onToken: (tok) => {
        setMessages((msgs) => {
          const copy = msgs.slice();
          const last = copy[copy.length - 1];
          if (last && last.role === "assistant") {
            copy[copy.length - 1] = {
              ...last,
              content: (last.content || "") + tok,
            };
          }
          return copy;
        });
      },
      onDone: (d) => {
        setMessages((msgs) => {
          const copy = msgs.slice();
          const last = copy[copy.length - 1];
          if (last && last.role === "assistant") {
            copy[copy.length - 1] = {
              ...last,
              streaming: false,
              provider: d.provider || providerSeen || "—",
            };
          }
          return copy;
        });
        setBusy(false);
        abortRef.current = null;
        onTurnSaved?.();
        // Background title generation runs ~1-2s after persist; refresh once more
        // so the sidebar picks up the title without forcing the user to reload.
        setTimeout(() => onTurnSaved?.(), 2800);
      },
      onError: (err) => {
        setMessages((msgs) => {
          const copy = msgs.slice();
          const last = copy[copy.length - 1];
          if (last && last.role === "assistant") {
            copy[copy.length - 1] = {
              ...last,
              content: `⚠ ${err}`,
              error: true,
              streaming: false,
            };
          }
          return copy;
        });
        setBusy(false);
        abortRef.current = null;
      },
    });
  }

  return (
    <div
      data-testid="chat-panel"
      style={{
        display: "flex",
        flexDirection: "column",
        height: "calc(100vh - 200px)",
        minHeight: 480,
        background: "var(--panel)",
        border: "1px solid var(--border)",
        borderRadius: 6,
        overflow: "hidden",
      }}
    >
      <div
        data-testid="chat-messages"
        style={{
          flex: 1,
          overflowY: "auto",
          padding: "24px 28px",
          display: "flex",
          flexDirection: "column",
          gap: 20,
        }}
      >
        {loadingHistory && (
          <div data-testid="chat-loading-history" style={{
            display: "flex", alignItems: "center", gap: 8,
            color: "var(--text-faint)", fontSize: 12,
          }}>
            <Loader2 size={12} style={{ animation: "spin 1s linear infinite" }} />
            loading history…
          </div>
        )}

        {messages.map((m, i) => (
          <div
            key={i}
            data-testid={`chat-msg-${m.role}-${i}`}
            style={{
              display: "flex",
              gap: 12,
              alignItems: "flex-start",
              flexDirection: m.role === "user" ? "row-reverse" : "row",
            }}
          >
            <div
              style={{
                width: 28,
                height: 28,
                borderRadius: 4,
                background:
                  m.role === "user" ? "var(--accent-soft)" : "var(--panel-2)",
                border: "1px solid var(--border)",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                color:
                  m.role === "user" ? "var(--accent-2)" : "var(--text-dim)",
                flexShrink: 0,
              }}
            >
              {m.role === "user" ? <User size={14} /> : <Bot size={14} />}
            </div>
            <div
              style={{
                maxWidth: "80%",
                padding: "12px 16px",
                borderRadius: 4,
                background:
                  m.role === "user"
                    ? "rgba(255, 138, 42, 0.06)"
                    : "var(--panel-2)",
                border:
                  m.role === "user"
                    ? "1px solid rgba(255,138,42,0.2)"
                    : "1px solid var(--border)",
                fontSize: 14,
                lineHeight: 1.6,
                color: m.error ? "var(--danger)" : "var(--text)",
                whiteSpace: "pre-wrap",
                wordBreak: "break-word",
              }}
            >
              {m.content}
              {m.streaming && !m.content && (
                <span
                  data-testid="chat-thinking"
                  style={{
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 8,
                    color: "var(--text-faint)",
                    fontStyle: "italic",
                    fontSize: 13,
                  }}
                >
                  <Loader2 size={12} style={{ animation: "spin 1s linear infinite" }} />
                  thinking…
                </span>
              )}
              {m.streaming && m.content && (
                <span
                  data-testid="chat-cursor"
                  style={{
                    display: "inline-block",
                    width: 7,
                    height: 14,
                    marginLeft: 2,
                    background: "var(--accent-2)",
                    verticalAlign: "middle",
                    animation: "blink 0.9s steps(1) infinite",
                  }}
                />
              )}
              {m.provider && m.provider !== "system" && !m.streaming && (
                <div
                  style={{
                    marginTop: 8,
                    fontSize: 10,
                    fontFamily: "'JetBrains Mono', monospace",
                    color: "var(--text-faint)",
                    letterSpacing: "0.1em",
                  }}
                >
                  via {m.provider}
                </div>
              )}
            </div>
          </div>
        ))}
        <div ref={endRef} />
      </div>

      <form
        onSubmit={send}
        style={{
          borderTop: "1px solid var(--border)",
          padding: 16,
          display: "flex",
          gap: 10,
          background: "var(--bg-elev)",
        }}
      >
        <input
          data-testid="chat-input"
          className="input"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask AUREM CTO to plan, build, debug…"
          autoFocus
          disabled={busy}
        />
        {busy ? (
          <button
            type="button"
            data-testid="chat-stop"
            className="btn-ghost"
            onClick={stop}
          >
            <Square size={13} /> Stop
          </button>
        ) : (
          <button
            type="submit"
            data-testid="chat-send"
            className="btn-primary"
            disabled={!input.trim() || !sessionId}
          >
            <Send size={14} /> Send
          </button>
        )}
      </form>

      <style>{`
        @keyframes spin { from { transform: rotate(0); } to { transform: rotate(360deg); } }
        @keyframes blink { 50% { opacity: 0; } }
      `}</style>
    </div>
  );
}
