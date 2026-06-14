/**
 * ORASidePanel — slides in from the right and mirrors the main chat
 * inbox's theme (glass panel, accent-2 user bubble, btn-primary send).
 *
 * Iter 150 changes:
 *   - Width now 35% of viewport (bounded 360–680px) per spec
 *   - Slower slide-in (520ms cubic-bezier) for a calmer reveal
 *   - All colors use the same CSS variables as ChatPanel so light/dark
 *     themes and brand changes propagate automatically
 *   - Hidden on mobile (FAB itself doesn't mount on small screens)
 *
 * Security gates unchanged: all network calls flow through useORAPanel
 * which carries the logged-in user's `aurem_token`.
 */
import React, { useEffect, useRef, useState } from "react";
import { X, Send, Square, Volume2, VolumeX, Mic, MicOff } from "lucide-react";
import { useTextToVoice } from "../hooks/useTextToVoice";

export default function ORASidePanel({
  open, messages, busy, projectId,
  onClose, onSend, onStop,
}) {
  const [input, setInput] = useState("");
  const [voiceEnabled, setVoiceEnabled] = useState(false);
  const [listening, setListening] = useState(false);
  const bottomRef = useRef(null);
  const inputRef = useRef(null);
  const recognitionRef = useRef(null);
  const lastSpokenRef = useRef("");
  const { speak, stop: stopVoice, supported: ttsSupported } = useTextToVoice();

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  useEffect(() => {
    if (open) {
      setTimeout(() => inputRef.current?.focus(), 120);
    }
  }, [open]);

  // Speak each NEW assistant turn (no double-speak on re-render).
  useEffect(() => {
    if (!voiceEnabled) return;
    const last = messages[messages.length - 1];
    if (!last || last.role !== "assistant" || last.streaming) return;
    if (!last.content || lastSpokenRef.current === last.content) return;
    lastSpokenRef.current = last.content;
    speak(last.content);
  }, [messages, voiceEnabled, speak]);

  const sttSupported =
    typeof window !== "undefined" &&
    ("SpeechRecognition" in window || "webkitSpeechRecognition" in window);

  const startListening = () => {
    if (!sttSupported) return;
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    const recognition = new SR();
    recognition.continuous = false;
    recognition.interimResults = false;
    recognition.lang = "en-US";
    recognition.onresult = (e) => {
      const transcript = e.results[0][0].transcript;
      setInput((prev) => (prev ? prev + " " + transcript : transcript));
      setListening(false);
    };
    recognition.onerror = () => setListening(false);
    recognition.onend = () => setListening(false);
    recognitionRef.current = recognition;
    try { recognition.start(); setListening(true); }
    catch (_) { setListening(false); }
  };

  const stopListening = () => {
    try { recognitionRef.current?.stop(); } catch (_) { /* ignore */ }
    setListening(false);
  };

  const handleSend = (modeArg) => {
    if (!input.trim() || busy) return;
    onSend(input.trim(), modeArg || chatMode);
    setInput("");
  };

  const handleKey = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend(chatMode);
    }
  };

  if (!open) return null;

  return (
    <>
      {/* Iter 151 — split-screen, no backdrop. Shell shrinks the main
          app width so the panel sits beside the existing chat instead
          of covering it. Composer stays fully usable. */}
      <div
        data-testid="ora-panel"
        className="glass-sidebar"
        style={{
          position: "fixed",
          top: 0, right: 0, bottom: 0,
          // 35% of viewport, clamped so it never gets unusably narrow
          // on small desktops or comically wide on ultrawides.
          width: "clamp(360px, 35vw, 680px)",
          background: "var(--panel)",
          borderLeft: "1px solid var(--border-strong)",
          zIndex: 8001,
          display: "flex",
          flexDirection: "column",
          boxShadow: "-12px 0 48px rgba(0,0,0,0.55)",
          animation: "ora-slide-in-right 520ms cubic-bezier(0.22, 1, 0.36, 1)",
        }}
      >
        {/* Header — matches ChatPanel header aesthetic. */}
        <div style={{
          display: "flex", alignItems: "center",
          justifyContent: "space-between",
          padding: "14px 18px",
          borderBottom: "1px solid var(--border)",
          flexShrink: 0,
        }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <div>
              <div className="serif" style={{
                fontSize: 15, color: "var(--text)", lineHeight: 1,
              }}>Ask ORA</div>
              {projectId && (
                <div
                  data-testid="ora-project-connected"
                  style={{
                    fontSize: 10,
                    color: "var(--text-faint)",
                    fontFamily: "'JetBrains Mono', monospace",
                    letterSpacing: "0.08em",
                    marginTop: 2,
                  }}
                >
                  PROJECT CONNECTED ✓
                </div>
              )}
            </div>
          </div>
          <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
            {ttsSupported && (
              <button
                data-testid="ora-tts-toggle"
                onClick={() => {
                  if (voiceEnabled) stopVoice();
                  setVoiceEnabled((v) => !v);
                }}
                title={voiceEnabled ? "Voice off" : "Voice on"}
                style={{
                  width: 30, height: 30, borderRadius: 6,
                  background: voiceEnabled ? "var(--accent-soft)" : "transparent",
                  border: `1px solid ${voiceEnabled ? "var(--accent)" : "var(--border)"}`,
                  color: voiceEnabled ? "var(--accent-2)" : "var(--text-dim)",
                  cursor: "pointer",
                  display: "inline-flex",
                  alignItems: "center",
                  justifyContent: "center",
                  transition: "color 120ms, border-color 120ms, background 120ms",
                }}
              >
                {voiceEnabled ? <Volume2 size={14} /> : <VolumeX size={14} />}
              </button>
            )}
            <button
              data-testid="ora-close-btn"
              onClick={onClose}
              title="Close"
              style={{
                width: 30, height: 30, borderRadius: 6,
                background: "transparent",
                border: "1px solid var(--border)",
                color: "var(--text-dim)",
                cursor: "pointer",
                display: "inline-flex", alignItems: "center", justifyContent: "center",
              }}
            >
              <X size={14} />
            </button>
          </div>
        </div>

        {/* Messages */}
        <div
          data-testid="ora-messages"
          style={{
            flex: 1, overflowY: "auto",
            padding: "18px 18px 8px",
            display: "flex", flexDirection: "column", gap: 12,
          }}
        >
          {messages.map((msg, i) => (
            <div key={i} style={{
              display: "flex",
              justifyContent: msg.role === "user" ? "flex-end" : "flex-start",
            }}>
              <div
                data-testid={`ora-msg-${msg.role}`}
                className={msg.role === "user" ? "glass-bubble-user" : "glass-bubble-assistant"}
                style={{
                  maxWidth: "88%",
                  padding: "10px 14px",
                  borderRadius: msg.role === "user"
                    ? "12px 12px 2px 12px"
                    : "12px 12px 12px 2px",
                  background: msg.role === "user"
                    ? "var(--accent-soft)"
                    : "var(--panel-2, rgba(255,255,255,0.03))",
                  color: msg.role === "user" ? "var(--accent-2)" : "var(--text)",
                  border: `1px solid ${msg.role === "user" ? "var(--accent)" : "var(--border)"}`,
                  fontSize: 13,
                  lineHeight: 1.55,
                  opacity: msg.error ? 0.75 : 1,
                  whiteSpace: "pre-wrap",
                  wordBreak: "break-word",
                  boxShadow: msg.role === "user"
                    ? "0 0 14px -4px var(--accent)"
                    : "none",
                }}
              >
                {msg.content || (msg.streaming
                  ? <span style={{
                      opacity: 0.55,
                      fontFamily: "'JetBrains Mono', monospace",
                      fontSize: 11,
                      letterSpacing: "0.06em",
                    }}>thinking…</span>
                  : ""
                )}
                {msg.streaming && msg.content && (
                  <span style={{
                    display: "inline-block",
                    width: 6, height: 6,
                    borderRadius: "50%",
                    background: "var(--accent-2)",
                    marginLeft: 4,
                    animation: "ora-pulse 1s infinite",
                  }} />
                )}
              </div>
            </div>
          ))}
          <div ref={bottomRef} />
        </div>

        {/* Composer — uses the same composer-card aesthetic as ChatPanel. */}
        <div style={{
          padding: "10px 14px 14px",
          borderTop: "1px solid var(--border)",
          flexShrink: 0,
          background: "rgba(13, 16, 24, 0.32)",
          backdropFilter: "blur(12px)",
          WebkitBackdropFilter: "blur(12px)",
        }}>
          <div className="composer-card">
            <textarea
              ref={inputRef}
              data-testid="ora-input"
              className="composer-input-bare"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKey}
              placeholder="Ask ORA anything — ~6s simple · ~20-30s multi-file"
              rows={1}
              disabled={busy}
              style={{ maxHeight: 120, overflowY: "auto" }}
            />
            <div className="composer-toolbar">
              {sttSupported && (
                <button
                  type="button"
                  data-testid="ora-mic-btn"
                  onClick={listening ? stopListening : startListening}
                  disabled={busy}
                  title={listening ? "Stop listening" : "Speak"}
                  style={{
                    width: 30, height: 30, borderRadius: 6,
                    border: `1px solid ${listening ? "rgba(239,68,68,0.6)" : "var(--border)"}`,
                    background: listening ? "rgba(239,68,68,0.10)" : "transparent",
                    color: listening ? "#ff8a8a" : "var(--text-dim)",
                    cursor: "pointer",
                    display: "inline-flex",
                    alignItems: "center", justifyContent: "center",
                  }}
                >
                  {listening ? <MicOff size={14} /> : <Mic size={14} />}
                </button>
              )}
              <span style={{ flex: 1 }} />
              <ModeSelector value={chatMode} onChange={setChatMode} />
              {busy ? (
                <button
                  type="button"
                  data-testid="ora-send-btn"
                  onClick={onStop}
                  className="btn-ghost"
                  style={{ fontSize: 12, gap: 6 }}
                >
                  <Square size={13} /> Stop
                </button>
              ) : (
                <button
                  type="button"
                  data-testid="ora-send-btn"
                  onClick={() => handleSend(chatMode)}
                  className="btn-primary"
                  disabled={!input.trim()}
                  style={{ fontSize: 12, gap: 6 }}
                >
                  <Send size={13} /> Send
                </button>
              )}
            </div>
          </div>

          {projectId && (
            <div style={{
              fontSize: 9,
              color: "var(--text-faint)",
              marginTop: 6,
              fontFamily: "'JetBrains Mono', monospace",
              letterSpacing: "0.08em",
              textAlign: "center",
            }}>
              tokens → your account · repo → your project only
            </div>
          )}
        </div>
      </div>

      <style>{`
        @keyframes ora-slide-in-right {
          from { transform: translateX(110%); opacity: 0; }
          to   { transform: translateX(0);    opacity: 1; }
        }
        @keyframes ora-fade-in {
          from { opacity: 0; }
          to   { opacity: 1; }
        }
        @keyframes ora-pulse {
          0%, 100% { opacity: 1; }
          50%      { opacity: 0.3; }
        }
      `}</style>
    </>
  );
}
