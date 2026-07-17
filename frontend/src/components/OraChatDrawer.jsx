/**
 * components/OraChatDrawer.jsx — Iter 212m-238
 *
 * Right-side floating drawer available on any /admin/* page.
 * Reuses the same session/message model as the full-page /admin/ora-chat.
 *
 * Renders a compact chat surface: session picker, message list, input.
 * Streams responses via native EventSource + fetch (POST-body SSE).
 *
 * Zero third-party markdown lib — content is rendered as plain text
 * with monospace preserved. Fancy features (code blocks, regen)
 * live in Phase 2 per the spec.
 */
import React, { useCallback, useEffect, useRef, useState } from "react";
import { MessageSquare, X, Send, RefreshCw, Zap, AlertTriangle } from "lucide-react";
import { api } from "../lib/api";
import { getToken } from "../lib/api";

const BASE = `${process.env.REACT_APP_BACKEND_URL}/api/aurem-dev/ora-chat`;

export default function OraChatDrawer() {
  const [open, setOpen] = useState(false);
  const [sessionId, setSessionId] = useState(null);
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [budget, setBudget] = useState(null);
  const [stream, setStream] = useState({ route: null, model: null, buf: "", err: null });
  const listRef = useRef(null);
  const abortRef = useRef(null);

  // ── auto-create a session on first open ────────────────────────
  useEffect(() => {
    if (!open || sessionId) return;
    (async () => {
      try {
        const r = await api.post("/ora-chat/sessions", { title: "Quick chat" });
        setSessionId(r.data.session.session_id);
      } catch (e) {
        // Non-admin users get 403 — hide the drawer entirely on failure.
        setOpen(false);
      }
    })();
  }, [open, sessionId]);

  // ── keep budget snapshot fresh whenever drawer is open ─────────
  const refreshBudget = useCallback(async () => {
    try {
      const r = await api.get("/ora-chat/usage");
      setBudget(r.data.budget);
    } catch { /* ignore */ }
  }, []);
  useEffect(() => { if (open) refreshBudget(); }, [open, refreshBudget]);

  useEffect(() => {
    if (listRef.current) listRef.current.scrollTop = listRef.current.scrollHeight;
  }, [messages, stream.buf]);

  const send = async () => {
    const text = input.trim();
    if (!text || !sessionId || sending) return;
    setInput("");
    setMessages(m => [...m, { role: "user", content: text }]);
    setStream({ route: null, model: null, buf: "", err: null });
    setSending(true);

    try {
      const ctrl = new AbortController();
      abortRef.current = ctrl;
      const res = await fetch(`${BASE}/message`, {
        method:  "POST",
        headers: {
          "Content-Type":  "application/json",
          "Authorization": `Bearer ${getToken()}`,
          "Accept":        "text/event-stream",
        },
        body: JSON.stringify({ session_id: sessionId, content: text }),
        signal: ctrl.signal,
      });

      if (res.status === 402) {
        const body = await res.json().catch(() => ({}));
        setStream(s => ({ ...s, err: "budget" }));
        setBudget(body?.detail?.budget || null);
        setMessages(m => [...m, {
          role: "assistant",
          content: body?.detail?.message ||
                    "This month's ORA budget is used up. Resets on the 1st.",
          isError: "budget",
        }]);
        return;
      }
      if (res.status === 429) {
        setStream(s => ({ ...s, err: "rate_limited" }));
        setMessages(m => [...m, {
          role: "assistant",
          content: "Too many requests — try again in a minute.",
          isError: "rate_limited",
        }]);
        return;
      }
      if (!res.ok || !res.body) {
        setStream(s => ({ ...s, err: `http_${res.status}` }));
        return;
      }

      // Parse SSE stream manually so we can honor route/delta/error events.
      const reader = res.body.getReader();
      const dec    = new TextDecoder();
      let buffer   = "";
      let assistantBuf = "";
      let routeMeta = { route: null, model: null };
      let errored = null;

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += dec.decode(value, { stream: true });
        // sse-starlette emits `\r\n\r\n` between events; split defensively.
        const parts = buffer.split(/\r?\n\r?\n/);
        buffer = parts.pop() || "";
        for (const chunk of parts) {
          const lines = chunk.split(/\r?\n/);
          let evtType = "message";
          let dataStr = "";
          for (const ln of lines) {
            if (ln.startsWith("event:")) evtType = ln.slice(6).trim();
            else if (ln.startsWith("data:")) dataStr += ln.slice(5).trim();
          }
          if (!dataStr) continue;
          let obj = {};
          try { obj = JSON.parse(dataStr); } catch { continue; }
          if (evtType === "route" || obj.type === "route") {
            routeMeta = { route: obj.route, model: obj.model, temperature: obj.temperature };
            setStream(s => ({ ...s, ...routeMeta }));
          } else if (evtType === "delta" || obj.type === "delta") {
            assistantBuf += obj.content || "";
            setStream(s => ({ ...s, buf: assistantBuf }));
          } else if (evtType === "error" || obj.type === "error") {
            errored = obj.error || "stream_error";
            setStream(s => ({ ...s, err: errored }));
          } else if (evtType === "slash_result" || obj.type === "slash_result") {
            assistantBuf += `\n${JSON.stringify(obj.result?.value, null, 2)}\n`;
            setStream(s => ({ ...s, buf: assistantBuf }));
          } else if (evtType === "final" || obj.type === "final") {
            // Persist message to state; clear streaming buffer.
            setMessages(m => [...m, {
              role: "assistant", content: assistantBuf,
              route: routeMeta.route, model: routeMeta.model,
              temperature: routeMeta.temperature,
              cost_usd: obj.cost_usd, tokens_in: obj.input_tokens,
              tokens_out: obj.output_tokens,
              interrupted: !!errored,
            }]);
            setStream({ route: null, model: null, buf: "", err: null });
          }
        }
      }
      // Stream closed without a final event (network drop mid-stream).
      if (assistantBuf && stream.buf) {
        setMessages(m => [...m, {
          role: "assistant", content: assistantBuf,
          route: routeMeta.route, model: routeMeta.model,
          interrupted: true,
        }]);
        setStream({ route: null, model: null, buf: "", err: null });
      }
    } catch (e) {
      setStream(s => ({ ...s, err: e?.message || "stream_error" }));
    } finally {
      setSending(false);
      abortRef.current = null;
      refreshBudget();
    }
  };

  const overBudget = budget?.over_budget;

  return (
    <>
      {/* Floating trigger — bottom-right on every /admin/* page */}
      {!open && (
        <button
          type="button"
          data-testid="ora-chat-open"
          onClick={() => setOpen(true)}
          title="ORA Chat"
          style={{
            position: "fixed", right: 24, bottom: 24, zIndex: 100,
            width: 52, height: 52, borderRadius: 26,
            background: "#E07A5F", color: "#0a0a0a",
            border: "none", cursor: "pointer",
            display: "flex", alignItems: "center", justifyContent: "center",
            boxShadow: "0 8px 30px rgba(224,122,95,0.35)",
          }}
        >
          <MessageSquare size={22} strokeWidth={1.9} />
        </button>
      )}

      {open && (
        <div
          data-testid="ora-chat-drawer"
          style={{
            position: "fixed", right: 0, top: 0, bottom: 0,
            width: "min(440px, 96vw)", zIndex: 101,
            background: "#0f1113", color: "#e8e3d3",
            borderLeft: "1px solid rgba(255,255,255,0.06)",
            boxShadow: "-12px 0 40px rgba(0,0,0,0.4)",
            display: "flex", flexDirection: "column",
            fontFamily: "-apple-system, BlinkMacSystemFont, 'Inter', sans-serif",
          }}
        >
          {/* Header */}
          <div style={{
            padding: "14px 16px",
            borderBottom: "1px solid rgba(255,255,255,0.06)",
            display: "flex", alignItems: "center", gap: 10,
          }}>
            <MessageSquare size={16} color="#E07A5F" />
            <div style={{ fontSize: 13, fontWeight: 600, flex: 1 }}>ORA Chat</div>
            {budget && (
              <div
                data-testid="ora-chat-budget-pill"
                title={`This month: $${budget.spent_usd} of $${budget.cap_usd}`}
                style={{
                  fontSize: 10, padding: "3px 8px", borderRadius: 999,
                  background: overBudget ? "rgba(220,80,80,0.16)" : "rgba(224,122,95,0.14)",
                  color: overBudget ? "#f88" : "#f4a082",
                  fontFamily: "ui-monospace, monospace",
                }}
              >
                ${budget.spent_usd.toFixed(4)} / ${budget.cap_usd}
              </div>
            )}
            <button
              type="button"
              data-testid="ora-chat-close"
              onClick={() => setOpen(false)}
              aria-label="Close"
              style={{
                background: "transparent", border: "none",
                color: "#a39d8a", cursor: "pointer", padding: 6,
              }}
            >
              <X size={16} />
            </button>
          </div>

          {/* Message list */}
          <div
            ref={listRef}
            data-testid="ora-chat-messages"
            style={{
              flex: 1, overflow: "auto", padding: "16px",
              display: "flex", flexDirection: "column", gap: 12,
            }}
          >
            {messages.length === 0 && !stream.buf && (
              <div style={{ fontSize: 12, color: "#7a7466", lineHeight: 1.6 }}>
                Hi! Puchho jo bhi chahiye — general chat, research, ya slash-commands.
                <br /><br />
                Try: <code style={{ color: "#f4a082" }}>/users-today</code>, <code style={{ color: "#f4a082" }}>/help</code>
              </div>
            )}
            {messages.map((m, i) => (
              <MessageBubble key={i} msg={m} />
            ))}
            {stream.buf && (
              <MessageBubble
                msg={{ role: "assistant", content: stream.buf,
                        route: stream.route, model: stream.model,
                        streaming: true }}
              />
            )}
            {stream.err && stream.buf === "" && (
              <div
                data-testid="ora-chat-error"
                style={{
                  fontSize: 12, color: "#f88",
                  display: "flex", gap: 8, alignItems: "center",
                }}
              >
                <AlertTriangle size={13} /> Stream error: {stream.err}
              </div>
            )}
          </div>

          {/* Input */}
          {overBudget ? (
            <div
              data-testid="ora-chat-budget-locked"
              style={{
                borderTop: "1px solid rgba(255,255,255,0.06)",
                padding: "14px 16px",
                fontSize: 12, color: "#f4a082",
                background: "rgba(220,80,80,0.06)",
                lineHeight: 1.5,
              }}
            >
              <div style={{ fontWeight: 600, marginBottom: 4 }}>
                This month&apos;s ORA budget is used up.
              </div>
              Resets on the 1st. Raise <code>ORA_MONTHLY_BUDGET_USD</code> to unlock earlier.
            </div>
          ) : (
            <form
              onSubmit={(e) => { e.preventDefault(); send(); }}
              style={{
                borderTop: "1px solid rgba(255,255,255,0.06)",
                padding: "12px",
                display: "flex", gap: 8,
              }}
            >
              <input
                data-testid="ora-chat-input"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                placeholder="Ask ORA... (or /users-today)"
                disabled={sending}
                style={{
                  flex: 1, padding: "10px 12px",
                  background: "rgba(255,255,255,0.04)",
                  border: "1px solid rgba(255,255,255,0.08)",
                  borderRadius: 8, color: "#e8e3d3",
                  fontSize: 13,
                  outline: "none",
                }}
              />
              <button
                type="submit"
                data-testid="ora-chat-send"
                disabled={sending || !input.trim()}
                style={{
                  padding: "0 14px",
                  background: sending ? "#3a3428" : "#E07A5F",
                  color: "#0a0a0a",
                  border: "none", borderRadius: 8,
                  cursor: sending ? "wait" : "pointer",
                  display: "flex", alignItems: "center", justifyContent: "center",
                }}
              >
                {sending ? <RefreshCw size={14} className="spin" /> : <Send size={14} />}
              </button>
            </form>
          )}
        </div>
      )}
    </>
  );
}

function MessageBubble({ msg }) {
  const isUser = msg.role === "user";
  return (
    <div
      data-testid={`ora-chat-msg-${msg.role}`}
      style={{
        alignSelf: isUser ? "flex-end" : "flex-start",
        maxWidth: "85%",
        padding: "10px 12px",
        borderRadius: 10,
        background: isUser
          ? "rgba(224,122,95,0.14)"
          : msg.isError
            ? "rgba(220,80,80,0.10)"
            : "rgba(255,255,255,0.04)",
        border: msg.isError
          ? "1px solid rgba(220,80,80,0.3)"
          : "1px solid rgba(255,255,255,0.06)",
        fontSize: 13, lineHeight: 1.55,
        whiteSpace: "pre-wrap",
        wordBreak: "break-word",
      }}
    >
      {msg.content}
      {(msg.route || msg.streaming || msg.interrupted) && (
        <div style={{
          marginTop: 6, fontSize: 10,
          color: msg.interrupted ? "#f88" : "#7a7466",
          display: "flex", gap: 6, alignItems: "center",
          flexWrap: "wrap",
        }}>
          {msg.route && (
            <span style={{
              padding: "1px 6px", borderRadius: 4,
              background: "rgba(255,255,255,0.04)",
              fontFamily: "ui-monospace, monospace",
            }}>
              <Zap size={9} /> {msg.route}
              {msg.temperature !== undefined && ` · t=${msg.temperature}`}
            </span>
          )}
          {msg.streaming && <span>streaming…</span>}
          {msg.interrupted && (
            <span data-testid="ora-chat-interrupted">
              ⚠ Response interrupted — try again
            </span>
          )}
        </div>
      )}
    </div>
  );
}
