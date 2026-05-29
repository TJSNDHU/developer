/**
 * ChatPanel.jsx — Lightweight chat client that hits POST /chat/send.
 */
import React, { useState, useRef, useEffect } from "react";
import { Send, Bot, User, Loader2 } from "lucide-react";
import { api } from "../lib/api";

export default function ChatPanel() {
  const [messages, setMessages] = useState([
    {
      role: "assistant",
      content:
        "I'm AUREM CTO — your sovereign engineering co-pilot. Ask me to plan a feature, write code, or debug an error. What are we shipping today?",
    },
  ]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [sessionId] = useState(() => `s-${Math.random().toString(36).slice(2, 10)}`);
  const endRef = useRef(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, busy]);

  async function send(e) {
    e?.preventDefault();
    const text = input.trim();
    if (!text || busy) return;
    setInput("");
    setMessages((m) => [...m, { role: "user", content: text }]);
    setBusy(true);
    try {
      const r = await api.post("/chat/send", {
        prompt: text,
        session_id: sessionId,
        max_tool_iters: 2,
      });
      const reply = r.data?.content || "(no response)";
      const provider = r.data?.provider || "?";
      setMessages((m) => [...m, { role: "assistant", content: reply, provider }]);
    } catch (err) {
      const detail = err?.response?.data?.detail || err.message || "Request failed";
      setMessages((m) => [
        ...m,
        { role: "assistant", content: `⚠ ${detail}`, error: true },
      ]);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div data-testid="chat-panel" style={{
      display: "flex", flexDirection: "column",
      height: "calc(100vh - 200px)",
      minHeight: 480,
      background: "var(--panel)",
      border: "1px solid var(--border)",
      borderRadius: 6,
      overflow: "hidden",
    }}>
      <div data-testid="chat-messages" style={{
        flex: 1, overflowY: "auto", padding: "24px 28px",
        display: "flex", flexDirection: "column", gap: 20,
      }}>
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
            <div style={{
              width: 28, height: 28, borderRadius: 4,
              background: m.role === "user" ? "var(--accent-soft)" : "var(--panel-2)",
              border: "1px solid var(--border)",
              display: "flex", alignItems: "center", justifyContent: "center",
              color: m.role === "user" ? "var(--accent-2)" : "var(--text-dim)",
              flexShrink: 0,
            }}>
              {m.role === "user" ? <User size={14} /> : <Bot size={14} />}
            </div>
            <div style={{
              maxWidth: "80%",
              padding: "12px 16px",
              borderRadius: 4,
              background: m.role === "user"
                ? "rgba(255, 138, 42, 0.06)"
                : "var(--panel-2)",
              border: m.role === "user"
                ? "1px solid rgba(255,138,42,0.2)"
                : "1px solid var(--border)",
              fontSize: 14,
              lineHeight: 1.6,
              color: m.error ? "var(--danger)" : "var(--text)",
              whiteSpace: "pre-wrap",
              wordBreak: "break-word",
            }}>
              {m.content}
              {m.provider && (
                <div style={{
                  marginTop: 8, fontSize: 10,
                  fontFamily: "'JetBrains Mono', monospace",
                  color: "var(--text-faint)", letterSpacing: "0.1em",
                }}>
                  via {m.provider}
                </div>
              )}
            </div>
          </div>
        ))}
        {busy && (
          <div data-testid="chat-thinking" style={{
            display: "flex", alignItems: "center", gap: 8,
            color: "var(--text-dim)", fontSize: 13,
          }}>
            <Loader2 size={14} className="spin" style={{ animation: "spin 1s linear infinite" }} />
            thinking…
          </div>
        )}
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
        />
        <button
          type="submit"
          data-testid="chat-send"
          className="btn-primary"
          disabled={busy || !input.trim()}
        >
          <Send size={14} /> Send
        </button>
      </form>

      <style>{`@keyframes spin { from{transform:rotate(0)} to{transform:rotate(360deg)} }`}</style>
    </div>
  );
}
