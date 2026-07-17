/**
 * pages/OraDirect.jsx — Iter 212m-242
 *
 * Light-cream themed public ORA Chat at /ora — matches BuildHome
 * aesthetic (cream background, white cards, warm accent).
 *
 * Interaction model:
 *   - Empty state → hero heading + big centered input card + suggestion pills
 *   - After first message → input slides to bottom-center; messages
 *     stream in the scrollable area above
 *
 * Auth: PIN pad (4 digits) → mints 7-day admin JWT via
 * /api/aurem-dev/ora-chat/pin-login. Reuses existing session /
 * streaming / budget / house-rules backend.
 */
import React, { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Lock, Settings, LogOut, ArrowUp, RefreshCw, Zap, Clock, Plus, Square } from "lucide-react";
import { api, setToken, getToken } from "../lib/api";
import OraChatHouseRulesPanel from "../components/OraChatHouseRulesPanel";

const BASE = `${process.env.REACT_APP_BACKEND_URL}/api/aurem-dev/ora-chat`;
const PIN_LENGTH = 4;

// Iter 212m-243 — Responsive width contract:
//   mobile  <768px  → 100% (edge-to-edge)
//   tablet  768-1023 → 70%  (15% margin each side)
//   desktop ≥1024   → 50%  (25% margin each side)
// Same widths applied to the messages list AND the input row.
function useContainerWidth() {
  const [w, setW] = useState(() =>
    typeof window !== "undefined" ? window.innerWidth : 1440);
  useEffect(() => {
    const on = () => setW(window.innerWidth);
    window.addEventListener("resize", on);
    return () => window.removeEventListener("resize", on);
  }, []);
  if (w < 768)   return { pct: "100%", maxW: "100%" };
  if (w < 1024)  return { pct: "70%",  maxW: "70%"  };
  return             { pct: "50%",  maxW: "50%"  };
}

// ── Palette (matches /app/frontend/src/pages/personal/BuildHome.jsx) ──
const PAL = {
  bg:         "#FAFAF5",
  card:       "#FFFFFF",
  text:       "#1C1C19",
  muted:      "#6B6B63",
  faint:      "#8B8B7D",
  chip:       "#F4F3EE",
  chipHover:  "#EDECE5",
  border:     "#E5E5DF",
  accent:     "#D56A4F",
  accentBg:   "rgba(224,122,95,0.10)",
  bubbleUser: "#EDECE5",
  bubbleAsst: "#FFFFFF",
};

// Iter 212m-250 — warm plaster-textured background photograph for
// both the PIN gate + the chat surface. Kept as a module-level const
// so the image URL is co-located with the palette. Soft cream tint
// on top (linear-gradient) preserves text readability without
// killing the texture.
const ORA_BG_IMG =
  "https://customer-assets-39nsmqrw.emergentagent.net/job_launch-pad-237/artifacts/bu3orhz1_Screenshot%202026-07-17%20002657.png";
const ORA_BG_STYLE = {
  backgroundImage:
    "linear-gradient(rgba(250,250,245,0.55), rgba(250,250,245,0.72)), " +
    `url("${ORA_BG_IMG}")`,
  backgroundSize: "cover",
  backgroundPosition: "center",
  backgroundRepeat: "no-repeat",
  backgroundAttachment: "fixed",
};

export default function OraDirect() {
  const [authorized, setAuthed] = useState(!!getToken());

  useEffect(() => {
    if (getToken()) {
      api.get("/auth/me").then(() => setAuthed(true))
                          .catch(() => setToken(null));
    }
  }, []);

  return authorized ? <ChatShell onLogout={() => { setToken(null); setAuthed(false); }} />
                     : <PinPad onSuccess={() => setAuthed(true)} />;
}

// ────────────────────────────────────────────────────────────────────
// PIN pad (light theme)
// ────────────────────────────────────────────────────────────────────
function PinPad({ onSuccess }) {
  const [pin, setPin]   = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr]   = useState(null);

  const submit = async (p) => {
    if (p.length !== PIN_LENGTH || busy) return;
    setBusy(true); setErr(null);
    try {
      const r = await api.post("/ora-chat/pin-login", { pin: p });
      setToken(r.data.token);
      onSuccess();
    } catch (e) {
      const d = e?.response?.data?.detail || e?.response?.data;
      if (d?.error === "too_many_attempts") setErr(d.message);
      else if (d?.error === "invalid_pin") setErr(`Wrong PIN. ${d.attempts_remaining} left.`);
      else setErr("Login failed. Check connection.");
      setPin("");
    } finally { setBusy(false); }
  };
  const press = (d) => { if (pin.length >= PIN_LENGTH || busy) return;
                          const n = pin + d; setPin(n);
                          if (n.length === PIN_LENGTH) submit(n); };

  return (
    <div data-testid="ora-direct-pin"
         style={{ minHeight: "100dvh", background: PAL.bg, color: PAL.text,
                    ...ORA_BG_STYLE,
                    display: "flex", alignItems: "center", justifyContent: "center",
                    padding: "20px 20px 40px",
                    // iOS Safari cuts off content behind the bottom nav bar
                    // — dvh + safe-area padding keeps the pad fully tappable.
                    paddingBottom: "max(40px, env(safe-area-inset-bottom))",
                    WebkitTapHighlightColor: "transparent",
                    fontFamily: "-apple-system, BlinkMacSystemFont, 'Inter', sans-serif" }}>
      <div style={{ maxWidth: 380, width: "100%", textAlign: "center" }}>
        <div style={{ width: 56, height: 56, borderRadius: 14, background: PAL.accentBg,
                        border: `1px solid ${PAL.accent}33`, margin: "0 auto 22px",
                        display: "flex", alignItems: "center", justifyContent: "center" }}>
          <Lock size={22} color={PAL.accent} strokeWidth={1.8} />
        </div>
        <div style={{ fontSize: 22, fontWeight: 700, marginBottom: 6,
                        letterSpacing: -0.5 }}>ORA Chat</div>
        <div style={{ fontSize: 13, color: PAL.muted, marginBottom: 32 }}>
          Enter your 4-digit PIN
        </div>
        <div style={{ display: "flex", justifyContent: "center", gap: 14, marginBottom: 30 }}>
          {[0,1,2,3].map(i => (
            <div key={i} data-testid={`pin-dot-${i}`}
                 style={{ width: 14, height: 14, borderRadius: "50%",
                            background: pin.length > i ? PAL.accent : "transparent",
                            border: `2px solid ${pin.length > i ? PAL.accent : PAL.border}`,
                            transition: "all 0.1s" }} />
          ))}
        </div>
        {err && (
          <div data-testid="pin-error" style={{ fontSize: 12, color: "#c94a37",
                                                   marginBottom: 20, minHeight: 16 }}>{err}</div>
        )}
        <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 10,
                       maxWidth: 280, margin: "0 auto" }}>
          {["1","2","3","4","5","6","7","8","9"].map(d => (
            <PadKey key={d} label={d} testId={`pin-key-${d}`}
                     onClick={() => press(d)} disabled={busy} />
          ))}
          <PadKey label="C" testId="pin-key-clear" onClick={() => setPin("")} disabled={busy} muted />
          <PadKey label="0" testId="pin-key-0" onClick={() => press("0")} disabled={busy} />
          <PadKey label="⌫" testId="pin-key-back"
                   onClick={() => setPin(pin.slice(0, -1))} disabled={busy} muted />
        </div>
        <div style={{ marginTop: 26, fontSize: 10, color: PAL.faint }}>
          5 wrong attempts per hour · Session lasts 7 days
        </div>
      </div>
    </div>
  );
}
function PadKey({ label, onClick, disabled, muted, testId }) {
  // Iter 212m-244 — Mobile PIN reliability fix. Two problems seen on
  // iOS Safari + Chrome Android:
  //   (a) 300ms tap delay before click fires — touchAction:manipulation
  //       eliminates it.
  //   (b) The onMouseDown transform interfered with the click event
  //       being registered on some touch devices — switched to :active
  //       via inline CSS and dropped the mouse-only handlers.
  return (
    <button type="button" data-testid={testId} onClick={onClick} disabled={disabled}
            style={{ aspectRatio: "1 / 1", fontSize: 22,
                       fontWeight: 500, color: muted ? PAL.muted : PAL.text,
                       background: PAL.card,
                       border: `1px solid ${PAL.border}`, borderRadius: 12,
                       cursor: disabled ? "not-allowed" : "pointer",
                       transition: "background 120ms",
                       touchAction: "manipulation",
                       WebkitTapHighlightColor: "transparent",
                       userSelect: "none",
                       minHeight: 56,   // hits Apple/Google 44px+ tap-target guidance
                       minWidth: 56 }}
            onPointerDown={(e) => e.currentTarget.style.background = PAL.chip}
            onPointerUp={(e)   => e.currentTarget.style.background = PAL.card}
            onPointerLeave={(e)=> e.currentTarget.style.background = PAL.card}>
      {label}
    </button>
  );
}

// ────────────────────────────────────────────────────────────────────
// Chat shell (post-auth) — BuildHome-inspired aesthetic
// ────────────────────────────────────────────────────────────────────
const SUGGESTIONS = [
  "Aaj ka top AI news",
  "Explain quantum computing simply",
  "Draft a launch tweet for AUREM",
  "/users-today",
];

function ChatShell({ onLogout }) {
  const containerW = useContainerWidth();
  const [sessionId, setSessionId] = useState(null);
  const [messages, setMessages]   = useState([]);
  const [input, setInput]         = useState("");
  const [sending, setSending]     = useState(false);
  const [budget, setBudget]       = useState(null);
  const [stream, setStream]       = useState({ buf: "", route: null, model: null });
  const [showRules, setShowRules] = useState(false);
  const [showPicker, setShowPicker] = useState(false);
  const [recentSessions, setRecent] = useState([]);
  const listRef = useRef(null);
  const abortRef = useRef(null);

  // Bootstrap
  useEffect(() => {
    (async () => {
      try {
        const r = await api.get("/ora-chat/sessions");
        const rows = r.data.sessions || [];
        setRecent(rows);
        if (rows.length === 0) {
          const c = await api.post("/ora-chat/sessions", { title: "Quick chat" });
          setSessionId(c.data.session.session_id);
        } else {
          setShowPicker(true);
        }
      } catch { /* token invalid — fall through */ }
    })();
  }, []);

  const refreshBudget = async () => {
    try { const r = await api.get("/ora-chat/usage"); setBudget(r.data.budget); }
    catch { /* ignore */ }
  };
  useEffect(() => { refreshBudget(); }, []);

  useEffect(() => {
    if (listRef.current) listRef.current.scrollTop = listRef.current.scrollHeight;
  }, [messages, stream.buf]);

  const startNewSession = async () => {
    try {
      const c = await api.post("/ora-chat/sessions", { title: "Quick chat" });
      setSessionId(c.data.session.session_id);
      setMessages([]); setShowPicker(false);
    } catch { /* ignore */ }
  };
  const openSession = async (sid) => {
    try {
      const r = await api.get(`/ora-chat/sessions/${sid}`);
      const s = r.data.session;
      setMessages((s.messages || []).map(m => ({
        role: m.role, content: m.content, route: m.route, model: m.model,
      })));
      setSessionId(sid); setShowPicker(false);
    } catch { /* ignore */ }
  };

  const send = async () => {
    const text = input.trim();
    if (!text || !sessionId || sending) return;
    setInput("");
    setMessages(m => [...m, { role: "user", content: text }]);
    setStream({ buf: "", route: null, model: null });
    setSending(true);
    // Iter 212m-246 — AbortController lets the Stop button interrupt
    // an in-flight SSE stream cleanly.
    const controller = new AbortController();
    abortRef.current = controller;
    let clientTz = "";
    try { clientTz = Intl.DateTimeFormat().resolvedOptions().timeZone || ""; } catch { /* */ }
    try {
      const res = await fetch(`${BASE}/message`, {
        method: "POST",
        signal: controller.signal,
        headers: {
          "Content-Type": "application/json",
          "Authorization": `Bearer ${getToken()}`,
          "Accept": "text/event-stream",
          ...(clientTz ? { "X-Client-TZ": clientTz } : {}),
        },
        body: JSON.stringify({ session_id: sessionId, content: text }),
      });
      if (res.status === 402) {
        const b = await res.json().catch(() => ({}));
        setMessages(m => [...m, { role: "assistant",
                                     content: b?.detail?.message || "Budget paused.",
                                     isError: true }]);
        return;
      }
      if (!res.ok || !res.body) {
        setMessages(m => [...m, { role: "assistant",
                                     content: `Error ${res.status}. Try again.`,
                                     isError: true }]);
        return;
      }
      const reader = res.body.getReader();
      const dec = new TextDecoder();
      let buffer = "", buf = "", routeMeta = {};
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += dec.decode(value, { stream: true });
        const parts = buffer.split(/\r?\n\r?\n/);
        buffer = parts.pop() || "";
        for (const chunk of parts) {
          let evtType = "message", dataStr = "";
          for (const ln of chunk.split(/\r?\n/)) {
            if (ln.startsWith("event:")) evtType = ln.slice(6).trim();
            else if (ln.startsWith("data:")) dataStr += ln.slice(5).trim();
          }
          if (!dataStr) continue;
          let obj = {}; try { obj = JSON.parse(dataStr); } catch { continue; }
          if (evtType === "route" || obj.type === "route") {
            routeMeta = { route: obj.route, model: obj.model, temperature: obj.temperature,
                           sources: obj.sources, sources_fired: obj.sources_fired,
                           downgraded: obj.downgraded };
            setStream(s => ({ ...s, ...routeMeta }));
          } else if (evtType === "delta" || obj.type === "delta") {
            buf += obj.content || "";
            setStream(s => ({ ...s, buf }));
          } else if (evtType === "slash_result" || obj.type === "slash_result") {
            buf += `\n${JSON.stringify(obj.result?.value, null, 2)}\n`;
            setStream(s => ({ ...s, buf }));
          } else if (evtType === "final" || obj.type === "final") {
            setMessages(m => [...m, { role: "assistant", content: buf,
                                         ...routeMeta,
                                         cost_usd: obj.cost_usd }]);
            setStream({ buf: "", route: null, model: null });
          }
        }
      }
    } catch (e) {
      if (e.name === "AbortError") {
        // User pressed Stop — preserve partial buffer as an
        // interrupted assistant turn instead of erroring.
        setMessages(m => (stream.buf
          ? [...m, { role: "assistant", content: stream.buf,
                       interrupted: true, ...stream }]
          : m));
      } else {
        setMessages(m => [...m, { role: "assistant",
                                     content: `Network error: ${e.message}`,
                                     isError: true }]);
      }
    } finally { setSending(false); abortRef.current = null; refreshBudget(); }
  };

  const stop = () => {
    if (abortRef.current) {
      abortRef.current.abort();
    }
  };

  const hasChat = messages.length > 0 || stream.buf;

  return (
    <div style={{ minHeight: "100vh", height: "100vh",
                    background: PAL.bg, color: PAL.text,
                    ...ORA_BG_STYLE,
                    display: "flex", flexDirection: "column",
                    fontFamily: "-apple-system, BlinkMacSystemFont, 'Inter', sans-serif" }}>
      {/* Header */}
      <header style={{ display: "flex", alignItems: "center",
                          padding: "16px 32px",
                          borderBottom: `1px solid ${PAL.border}`,
                          background: PAL.card }}>
        <div style={{ fontWeight: 700, letterSpacing: 2, fontSize: 15 }}>AUREM</div>
        <div style={{ flex: 1 }} />
        {budget && (
          <div data-testid="ora-budget-pill"
               title={`Today: $${budget.day_spent_usd} · Month: $${budget.month_spent_usd}`}
               style={{ fontSize: 11, padding: "4px 10px", borderRadius: 999,
                          background: PAL.chip, color: PAL.muted,
                          fontFamily: "ui-monospace, monospace", marginRight: 12 }}>
            ${(budget.day_spent_usd || 0).toFixed(4)} / ${budget.day_cap_usd}
          </div>
        )}
        <IconBtn testId="ora-history" onClick={() => setShowPicker(true)}><Clock size={16} /></IconBtn>
        <IconBtn testId="ora-settings" onClick={() => setShowRules(true)}><Settings size={16} /></IconBtn>
        <IconBtn testId="ora-logout" onClick={onLogout}><LogOut size={16} /></IconBtn>
      </header>

      {/* Main area */}
      <div style={{ flex: 1, display: "flex", flexDirection: "column",
                     overflow: "hidden", position: "relative" }}>
        {!hasChat && (
          // ── EMPTY STATE — centered hero ─────────────────────────
          <div style={{ flex: 1, display: "flex", flexDirection: "column",
                          alignItems: "center", justifyContent: "center",
                          padding: "20px", overflow: "auto" }}>
            <div style={{ maxWidth: containerW.maxW, width: containerW.pct,
                            textAlign: "center" }}>
              <div style={{ display: "inline-block", padding: "5px 12px",
                              background: PAL.accentBg, color: PAL.accent,
                              borderRadius: 999, fontSize: 12, fontWeight: 500,
                              marginBottom: 24 }}>
                <Zap size={12} style={{ display: "inline", marginRight: 4, verticalAlign: -1 }} />
                ORA Chat
              </div>
              <h1 style={{ fontSize: "clamp(32px, 5vw, 52px)",
                             fontWeight: 700, lineHeight: 1.1,
                             margin: "0 0 20px", letterSpacing: -1 }}>
                How can I help?
              </h1>
              <p style={{ fontSize: 15, color: PAL.muted, marginBottom: 40,
                            maxWidth: 480, marginLeft: "auto", marginRight: "auto" }}>
                General chat, deep research, or type a <code style={{ color: PAL.accent }}>/</code> for
                deterministic slash-commands.
              </p>
              <InputCard input={input} setInput={setInput} onSend={send}
                          sending={sending} onStop={stop} large />
              <div style={{ marginTop: 32, display: "flex", flexWrap: "wrap",
                              gap: 10, justifyContent: "center" }}>
                {SUGGESTIONS.map(s => (
                  <button key={s} data-testid={`suggestion-${s}`}
                          onClick={() => { setInput(s); }}
                          style={{ padding: "8px 16px", borderRadius: 999,
                                     background: PAL.chip, border: "none",
                                     color: PAL.muted, fontSize: 13,
                                     cursor: "pointer", fontFamily: "inherit" }}
                          onMouseEnter={(e) => e.currentTarget.style.background = PAL.chipHover}
                          onMouseLeave={(e) => e.currentTarget.style.background = PAL.chip}>
                    {s}
                  </button>
                ))}
              </div>
            </div>
          </div>
        )}

        {hasChat && (
          // ── CHAT STATE — messages scroll, input pinned bottom ────
          <>
            <div ref={listRef} data-testid="ora-messages"
                 style={{ flex: 1, overflow: "auto",
                            // Iter 212m-247 — bottom pad must clear the
                            // widened 5-row input (form ~120 px + wrapper
                            // ~40 px + gradient fade + safe-area). Was
                            // 140 px for the old 3-row input which hid
                            // ~60 px of the latest reply behind the
                            // input panel.
                            padding: "24px 20px min(240px, 32vh)" }}>
              <div style={{ maxWidth: containerW.maxW, width: containerW.pct,
                              margin: "0 auto",
                              display: "flex", flexDirection: "column", gap: 16 }}>
                {messages.map((m, i) => <Bubble key={i} m={m} />)}
                {sending && !stream.buf && <ThinkingDots />}
                {stream.buf && (
                  <Bubble m={{ role: "assistant", content: stream.buf,
                                 ...stream, streaming: true }} />
                )}
              </div>
            </div>
            <div style={{ position: "absolute", bottom: 0, left: 0, right: 0,
                            padding: "16px 20px 20px",
                            background: `linear-gradient(to top, ${PAL.bg} 60%, transparent)` }}>
              <div style={{ maxWidth: containerW.maxW, width: containerW.pct,
                              margin: "0 auto" }}>
                <InputCard input={input} setInput={setInput} onSend={send}
                            sending={sending} onStop={stop} />
              </div>
            </div>
          </>
        )}
      </div>

      {/* Sessions picker overlay */}
      {showPicker && (
        <PickerModal recent={recentSessions} onClose={() => setShowPicker(false)}
                     onNew={startNewSession} onOpen={openSession} />
      )}
      {/* House rules panel */}
      {showRules && (
        <OraChatHouseRulesPanel onClose={() => setShowRules(false)} />
      )}
    </div>
  );
}

function IconBtn({ children, onClick, testId }) {
  return (
    <button type="button" onClick={onClick} data-testid={testId}
            style={{ padding: 8, marginLeft: 4, background: "transparent",
                       border: "none", color: PAL.muted, cursor: "pointer",
                       borderRadius: 8, display: "flex", alignItems: "center" }}
            onMouseEnter={(e) => e.currentTarget.style.background = PAL.chip}
            onMouseLeave={(e) => e.currentTarget.style.background = "transparent"}>
      {children}
    </button>
  );
}


// Iter 212m-246 — colored 3-dot pulse shown between "user just sent"
// and "first delta arrives" so the UI never feels frozen. Each dot
// uses a different accent color and staggers its bounce so the row
// reads as a purposeful "thinking" cue, not a spinner.
function ThinkingDots() {
  return (
    <div data-testid="ora-thinking-dots"
         style={{ alignSelf: "flex-start",
                    padding: "12px 16px",
                    borderRadius: 14,
                    background: PAL.bubbleAsst,
                    border: `1px solid ${PAL.border}`,
                    display: "flex", gap: 6, alignItems: "center" }}>
      <style>{`
        @keyframes ora-pulse {
          0%, 80%, 100% { transform: translateY(0);   opacity: 0.35; }
          40%           { transform: translateY(-4px); opacity: 1; }
        }
      `}</style>
      {[
        { c: "#D56A4F", d: "0ms"   }, // terracotta
        { c: "#81B29A", d: "160ms" }, // moss
        { c: "#3B82F6", d: "320ms" }, // blue
      ].map((dot, i) => (
        <span key={i}
              style={{ width: 8, height: 8, borderRadius: 999,
                         background: dot.c, display: "inline-block",
                         animation: `ora-pulse 1.2s ease-in-out ${dot.d} infinite` }} />
      ))}
      <span style={{ fontSize: 11, color: PAL.faint, marginLeft: 4,
                       fontStyle: "italic" }}>thinking…</span>
    </div>
  );
}

function InputCard({ input, setInput, onSend, sending, onStop, large = false }) {
  // Iter 212m-246 — widened to 5 lines (was 3) so long prompts and
  // multi-line context paste-ins stay comfortably readable without
  // shrinking the message stream area. Auto-grows past 5 up to 12
  // lines. Send button becomes a Stop button while streaming.
  return (
    <form onSubmit={(e) => { e.preventDefault(); if (!sending) onSend(); }}
          style={{ background: PAL.card,
                     border: `1px solid ${PAL.border}`,
                     borderRadius: 16,
                     padding: "14px 16px",
                     boxShadow: "0 4px 20px rgba(0,0,0,0.04)",
                     display: "flex", alignItems: "flex-end", gap: 10 }}>
      <textarea
        data-testid="ora-input"
        value={input}
        onChange={(e) => setInput(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); if (!sending) onSend(); } }}
        placeholder={large ? "Ask ORA... (or /repo-tree, /find, /read)" : "Message ORA..."}
        rows={5}
        style={{ flex: 1, border: "none", outline: "none",
                   fontFamily: "inherit", fontSize: 15,
                   color: PAL.text, background: "transparent",
                   resize: "none", padding: "4px 4px",
                   lineHeight: 1.55,
                   minHeight: `calc(1.55em * 5)`,
                   maxHeight: `calc(1.55em * 12)`,
                   overflowY: "auto",
                   opacity: sending ? 0.65 : 1 }}
        disabled={sending}
      />
      {sending ? (
        <button type="button" onClick={onStop} data-testid="ora-stop"
                title="Stop generating"
                style={{ width: 38, height: 38, borderRadius: 999,
                           background: "#E5E5DF", color: PAL.text,
                           border: "none", cursor: "pointer",
                           display: "flex", alignItems: "center", justifyContent: "center",
                           flexShrink: 0, transition: "background 120ms" }}
                onMouseEnter={(e) => e.currentTarget.style.background = "#D8D8D0"}
                onMouseLeave={(e) => e.currentTarget.style.background = "#E5E5DF"}>
          <Square size={14} fill={PAL.text} />
        </button>
      ) : (
        <button type="submit" data-testid="ora-send" disabled={!input.trim()}
                style={{ width: 38, height: 38, borderRadius: 999,
                           background: input.trim() ? PAL.accent : PAL.chip,
                           color: input.trim() ? "#fff" : PAL.faint,
                           border: "none", cursor: "pointer",
                           display: "flex", alignItems: "center", justifyContent: "center",
                           flexShrink: 0, transition: "background 120ms",
                           touchAction: "manipulation" }}>
          <ArrowUp size={16} />
        </button>
      )}
    </form>
  );
}

function Bubble({ m }) {
  const isUser = m.role === "user";
  return (
    <div data-testid={`ora-msg-${m.role}`}
         style={{ alignSelf: isUser ? "flex-end" : "flex-start",
                    maxWidth: "85%",
                    padding: "12px 16px",
                    borderRadius: 14,
                    background: m.isError ? "#fdeeea"
                                   : isUser ? PAL.bubbleUser : PAL.bubbleAsst,
                    border: isUser ? "none" : `1px solid ${PAL.border}`,
                    color: PAL.text, fontSize: 14, lineHeight: 1.6,
                    whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
      {m.content}
      {(m.route || m.streaming) && (
        <div style={{ marginTop: 6, fontSize: 10, color: PAL.faint,
                        display: "flex", gap: 6, alignItems: "center" }}>
          {m.route && (
            <span data-testid="ora-route-badge" style={{ padding: "1px 6px", borderRadius: 4,
                             background: PAL.chip,
                             fontFamily: "ui-monospace, monospace" }}>
              {m.route}
              {m.route === "deep" && m.sources && ` · ${m.sources}`}
              {m.downgraded && ` · downgraded`}
              {m.temperature !== undefined && ` · t=${m.temperature}`}
            </span>
          )}
          {m.streaming && <span>streaming…</span>}
        </div>
      )}
    </div>
  );
}

function PickerModal({ recent, onClose, onNew, onOpen }) {
  return (
    <div data-testid="ora-picker"
         onClick={onClose}
         style={{ position: "fixed", inset: 0, zIndex: 200,
                    background: "rgba(28,28,25,0.4)",
                    display: "flex", alignItems: "center", justifyContent: "center",
                    padding: 20 }}>
      <div onClick={(e) => e.stopPropagation()}
           style={{ background: PAL.card, borderRadius: 16,
                      border: `1px solid ${PAL.border}`,
                      maxWidth: 460, width: "100%", maxHeight: "80vh",
                      overflow: "auto", padding: 20 }}>
        <div style={{ display: "flex", justifyContent: "space-between",
                        alignItems: "center", marginBottom: 16 }}>
          <div style={{ fontSize: 15, fontWeight: 600 }}>Recent sessions</div>
          <button data-testid="ora-picker-new" onClick={onNew}
                  style={{ background: PAL.accent, color: "#fff",
                             border: "none", borderRadius: 8,
                             padding: "8px 14px", fontSize: 12, fontWeight: 600,
                             cursor: "pointer", display: "flex", gap: 4,
                             alignItems: "center" }}>
            <Plus size={12} /> New chat
          </button>
        </div>
        {recent.length === 0 && (
          <div style={{ fontSize: 13, color: PAL.muted }}>No previous sessions.</div>
        )}
        {recent.map(s => (
          <button key={s.session_id} data-testid={`ora-picker-${s.session_id}`}
                  onClick={() => onOpen(s.session_id)}
                  style={{ display: "block", width: "100%", textAlign: "left",
                             padding: "12px 14px", marginBottom: 8,
                             background: PAL.chip,
                             border: `1px solid ${PAL.border}`,
                             borderRadius: 10, cursor: "pointer",
                             color: PAL.text, fontFamily: "inherit" }}>
            <div style={{ fontSize: 13, fontWeight: 500 }}>
              {s.title || "Untitled"}
            </div>
            <div style={{ fontSize: 11, color: PAL.faint, marginTop: 3 }}>
              {s.message_count || 0} messages · {new Date(s.updated_at * 1000).toLocaleString()}
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}
