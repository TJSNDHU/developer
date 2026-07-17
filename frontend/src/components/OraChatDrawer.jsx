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
import { MessageSquare, X, Send, RefreshCw, Zap, AlertTriangle,
         Settings, Clock, Plus } from "lucide-react";
import { api } from "../lib/api";
import { getToken } from "../lib/api";
import OraChatHouseRulesPanel from "./OraChatHouseRulesPanel";

const BASE = `${process.env.REACT_APP_BACKEND_URL}/api/aurem-dev/ora-chat`;

export default function OraChatDrawer({ forceOpen = false, fullscreen = false } = {}) {
  const [open, setOpen] = useState(forceOpen);
  const [sessionId, setSessionId] = useState(null);
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [budget, setBudget] = useState(null);
  const [stream, setStream] = useState({ route: null, model: null, buf: "", err: null });
  const [showRules, setShowRules] = useState(false);
  const [recentSessions, setRecentSessions] = useState([]);
  const [showPicker, setShowPicker] = useState(false);
  const listRef = useRef(null);
  const abortRef = useRef(null);

  // ── on first open: fetch recent sessions instead of auto-creating.
  //    Empty list → auto-create a fresh one. Non-empty → show picker.
  useEffect(() => {
    if (!open || sessionId) return;
    (async () => {
      try {
        const r = await api.get("/ora-chat/sessions");
        const rows = r.data.sessions || [];
        setRecentSessions(rows);
        if (rows.length === 0) {
          const c = await api.post("/ora-chat/sessions", { title: "Quick chat" });
          setSessionId(c.data.session.session_id);
        } else {
          setShowPicker(true);
        }
      } catch (e) {
        setOpen(false);
      }
    })();
  }, [open, sessionId]);

  const startNewSession = async () => {
    try {
      const c = await api.post("/ora-chat/sessions", { title: "Quick chat" });
      setSessionId(c.data.session.session_id);
      setMessages([]);
      setShowPicker(false);
    } catch { /* ignore */ }
  };

  const openExistingSession = async (sid) => {
    try {
      const r = await api.get(`/ora-chat/sessions/${sid}`);
      const s = r.data.session;
      const msgs = (s.messages || []).map(m => ({
        role: m.role, content: m.content,
        route: m.route, model: m.model, temperature: m.temperature,
      }));
      setMessages(msgs);
      setSessionId(sid);
      setShowPicker(false);
    } catch { /* ignore */ }
  };

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
      // Iter 212m-240 — auto-detect browser timezone (IANA name) and
      // pass to backend so the runtime-context block uses the user's
      // real local time, no manual config anywhere.
      let clientTz = "";
      try {
        clientTz = Intl.DateTimeFormat().resolvedOptions().timeZone || "";
      } catch { /* very old browser — backend falls back to env default */ }
      const res = await fetch(`${BASE}/message`, {
        method:  "POST",
        headers: {
          "Content-Type":  "application/json",
          "Authorization": `Bearer ${getToken()}`,
          "Accept":        "text/event-stream",
          ...(clientTz ? { "X-Client-TZ": clientTz } : {}),
        },
        body: JSON.stringify({ session_id: sessionId, content: text }),
        signal: ctrl.signal,
      });

      if (res.status === 402) {
        const body = await res.json().catch(() => ({}));
        setStream(s => ({ ...s, err: "spike" }));
        setBudget(body?.detail?.budget || null);
        setMessages(m => [...m, {
          role: "assistant",
          content: body?.detail?.message ||
                    "Daily spend spike detected — chat is paused.",
          isError: "spike",
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
            routeMeta = { route: obj.route, model: obj.model, temperature: obj.temperature,
                           sources: obj.sources, sources_fired: obj.sources_fired,
                           downgraded: obj.downgraded };
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
              sources: routeMeta.sources || obj.sources,
              sources_fired: routeMeta.sources_fired || obj.sources_fired,
              downgraded: routeMeta.downgraded || obj.downgraded,
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

  const overBudget = budget?.mode === "spike_hard_stop";
  const economyMode = budget?.mode === "economy";
  const warningMode = budget?.mode === "warning";

  return (
    <>
      {/* Floating trigger — bottom-right on every /admin/* page.
          Hidden in fullscreen mode (already inside a full page). */}
      {!open && !fullscreen && (
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
            position: "fixed",
            right: 0, top: 0, bottom: 0,
            left: fullscreen ? 0 : "auto",
            width: fullscreen ? "100vw" : "min(440px, 96vw)",
            zIndex: 101,
            background: "#0f1113", color: "#e8e3d3",
            borderLeft: fullscreen ? "none" : "1px solid rgba(255,255,255,0.06)",
            boxShadow: fullscreen ? "none" : "-12px 0 40px rgba(0,0,0,0.4)",
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
                title={`Today: $${budget.day_spent_usd} / $${budget.day_cap_usd}  ·  Month: $${budget.month_spent_usd} / $${budget.month_cap_usd}`}
                style={{
                  fontSize: 10, padding: "3px 8px", borderRadius: 999,
                  background: overBudget
                    ? "rgba(220,80,80,0.16)"
                    : economyMode
                      ? "rgba(220,150,80,0.14)"
                      : "rgba(224,122,95,0.14)",
                  color: overBudget ? "#f88"
                          : economyMode ? "#f4c082" : "#f4a082",
                  fontFamily: "ui-monospace, monospace",
                }}
              >
                {economyMode ? "econ · " : ""}
                ${(budget.day_spent_usd || 0).toFixed(4)} / ${budget.day_cap_usd}
              </div>
            )}
            <button
              type="button"
              data-testid="ora-chat-history-btn"
              onClick={() => setShowPicker(true)}
              title="Recent sessions"
              style={{
                background: "transparent", border: "none",
                color: "#a39d8a", cursor: "pointer", padding: 6,
              }}
            >
              <Clock size={15} />
            </button>
            <button
              type="button"
              data-testid="ora-chat-rules-btn"
              onClick={() => setShowRules(true)}
              title="House rules"
              style={{
                background: "transparent", border: "none",
                color: "#a39d8a", cursor: "pointer", padding: 6,
              }}
            >
              <Settings size={15} />
            </button>
            <button
              type="button"
              data-testid="ora-chat-close"
              onClick={() => { if (!fullscreen) setOpen(false); }}
              disabled={fullscreen}
              aria-label="Close"
              style={{
                background: "transparent", border: "none",
                color: fullscreen ? "#3a362d" : "#a39d8a",
                cursor: fullscreen ? "default" : "pointer", padding: 6,
                visibility: fullscreen ? "hidden" : "visible",
              }}
            >
              <X size={16} />
            </button>
          </div>

          {/* Warning/economy banner */}
          {(warningMode || economyMode) && (
            <div data-testid="ora-chat-budget-banner"
                 style={{
                   padding: "8px 16px", fontSize: 11,
                   background: economyMode
                     ? "rgba(220,150,80,0.08)"
                     : "rgba(224,122,95,0.06)",
                   color: economyMode ? "#f4c082" : "#f4a082",
                   borderBottom: "1px solid rgba(255,255,255,0.04)",
                 }}>
              {economyMode
                ? "Budget mode active — using economy model (GLM-5.2). Full routing resumes tomorrow."
                : "~70% of today's ORA budget used."}
            </div>
          )}

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
                fontSize: 12, color: "#f88",
                background: "rgba(220,80,80,0.06)",
                lineHeight: 1.5,
              }}
            >
              <div style={{ fontWeight: 600, marginBottom: 4 }}>
                Daily spend spike detected.
              </div>
              Today: ${budget?.day_spent_usd} · Spike cap: ${budget?.spike_cap_usd}.
              Raise <code>ORA_DAILY_SPIKE_USD</code> to override.
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

      {/* Recent-sessions picker (Iter 212m-239) */}
      {open && showPicker && (
        <div
          data-testid="ora-chat-picker"
          style={{
            position: "fixed", inset: 0, zIndex: 102,
            background: "rgba(0,0,0,0.55)",
            display: "flex", alignItems: "center", justifyContent: "flex-end",
          }}
          onClick={() => setShowPicker(false)}
        >
          <div onClick={(e) => e.stopPropagation()}
               style={{
                 width: "min(440px, 96vw)",
                 marginRight: "min(440px, 96vw)",
                 maxHeight: "80vh", overflow: "auto",
                 background: "#0f1113",
                 border: "1px solid rgba(255,255,255,0.06)",
                 borderRadius: 12, padding: 18,
                 color: "#e8e3d3",
                 fontFamily: "-apple-system, BlinkMacSystemFont, 'Inter', sans-serif",
               }}>
            <div style={{ display: "flex", justifyContent: "space-between",
                            alignItems: "center", marginBottom: 12 }}>
              <div style={{ fontSize: 13, fontWeight: 600 }}>Recent sessions</div>
              <button data-testid="ora-picker-new"
                      onClick={startNewSession}
                      style={{ background: "#E07A5F", color: "#0a0a0a",
                                border: "none", borderRadius: 6,
                                padding: "6px 10px", fontSize: 11,
                                fontWeight: 600, cursor: "pointer",
                                display: "flex", gap: 4, alignItems: "center" }}>
                <Plus size={11} /> New chat
              </button>
            </div>
            {recentSessions.length === 0 && (
              <div style={{ fontSize: 12, color: "#7a7466" }}>
                No previous sessions.
              </div>
            )}
            {recentSessions.map(s => (
              <button
                key={s.session_id}
                data-testid={`ora-picker-${s.session_id}`}
                onClick={() => openExistingSession(s.session_id)}
                style={{
                  display: "block", width: "100%", textAlign: "left",
                  padding: "10px 12px", marginBottom: 6,
                  background: "rgba(255,255,255,0.03)",
                  border: "1px solid rgba(255,255,255,0.06)",
                  borderRadius: 6, cursor: "pointer",
                  color: "#e8e3d3",
                }}>
                <div style={{ fontSize: 12, fontWeight: 500 }}>
                  {s.title || "Untitled"}
                </div>
                <div style={{ fontSize: 10, color: "#7a7466", marginTop: 3 }}>
                  {s.message_count || 0} messages · {new Date(s.updated_at * 1000).toLocaleString()}
                </div>
              </button>
            ))}
          </div>
        </div>
      )}

      {/* House rules panel */}
      {open && showRules && (
        <OraChatHouseRulesPanel onClose={() => setShowRules(false)} />
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
            <span data-testid="ora-drawer-route-badge" style={{
              padding: "1px 6px", borderRadius: 4,
              background: "rgba(255,255,255,0.04)",
              fontFamily: "ui-monospace, monospace",
            }}>
              <Zap size={9} /> {msg.route}
              {msg.route === "deep" && msg.sources && ` · ${msg.sources}`}
              {msg.downgraded && ` · downgraded`}
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
