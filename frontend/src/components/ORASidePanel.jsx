/**
 * ORASidePanel — sliding right-side panel that hosts the Ask-ORA
 * conversation. Mounted by FloatingORAButton.
 *
 * Capabilities:
 *   - SSE-streamed reply from /api/aurem-dev/chat/stream
 *   - Text-to-Voice playback of completed assistant turns (toggle)
 *   - Speech-to-Text dictation via Web Speech API (browser-native)
 *
 * Security: all network calls flow through useORAPanel which carries
 * the logged-in user's `aurem_token`. project_id is the user's own.
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
      setTimeout(() => inputRef.current?.focus(), 100);
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

  const handleSend = () => {
    if (!input.trim() || busy) return;
    onSend(input.trim());
    setInput("");
  };

  const handleKey = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  if (!open) return null;

  return (
    <>
      <div
        data-testid="ora-panel-backdrop"
        onClick={onClose}
        style={{
          position: "fixed", inset: 0,
          background: "rgba(0,0,0,0.3)",
          zIndex: 8000,
          backdropFilter: "blur(2px)",
        }}
      />

      <div
        data-testid="ora-panel"
        style={{
          position: "fixed",
          top: 0, right: 0, bottom: 0,
          width: "min(420px, 100vw)",
          background: "var(--panel, #0d1018)",
          borderLeft: "1px solid var(--border-strong, rgba(255,200,120,0.18))",
          zIndex: 8001,
          display: "flex",
          flexDirection: "column",
          boxShadow: "-8px 0 32px rgba(0,0,0,0.5)",
          animation: "ora-slide-in-right 0.2s ease-out",
        }}
      >
        {/* Header */}
        <div style={{
          display: "flex", alignItems: "center",
          justifyContent: "space-between",
          padding: "16px 20px",
          borderBottom: "1px solid var(--border)",
          flexShrink: 0,
        }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <div style={{
              width: 32, height: 32,
              borderRadius: "50%",
              background: "linear-gradient(135deg, #f59e0b, #d97706)",
              display: "flex", alignItems: "center",
              justifyContent: "center",
              fontSize: 14, fontWeight: 700, color: "#000",
            }}>O</div>
            <div>
              <div style={{
                fontSize: 14, fontWeight: 600,
                color: "var(--text, #e5e7eb)",
              }}>Ask ORA</div>
              {projectId && (
                <div
                  data-testid="ora-project-connected"
                  style={{
                    fontSize: 10,
                    color: "var(--text-faint, #6b7280)",
                    fontFamily: "'JetBrains Mono', monospace",
                  }}
                >
                  project connected ✓
                </div>
              )}
            </div>
          </div>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            {ttsSupported && (
              <button
                data-testid="ora-tts-toggle"
                onClick={() => {
                  if (voiceEnabled) stopVoice();
                  setVoiceEnabled((v) => !v);
                }}
                title={voiceEnabled ? "Voice off" : "Voice on"}
                style={{
                  background: voiceEnabled
                    ? "rgba(245,158,11,0.15)"
                    : "transparent",
                  border: "1px solid var(--border)",
                  borderRadius: 6, padding: "4px 8px",
                  cursor: "pointer",
                  color: voiceEnabled
                    ? "#f59e0b"
                    : "var(--text-faint)",
                }}
              >
                {voiceEnabled
                  ? <Volume2 size={14} />
                  : <VolumeX size={14} />}
              </button>
            )}
            <button
              data-testid="ora-close-btn"
              onClick={onClose}
              style={{
                background: "none", border: "none",
                color: "var(--text-faint)",
                cursor: "pointer", padding: 4,
              }}
            >
              <X size={18} />
            </button>
          </div>
        </div>

        {/* Messages */}
        <div
          data-testid="ora-messages"
          style={{
            flex: 1, overflowY: "auto",
            padding: "16px 20px",
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
                style={{
                  maxWidth: "85%",
                  padding: "10px 14px",
                  borderRadius: msg.role === "user"
                    ? "12px 12px 2px 12px"
                    : "12px 12px 12px 2px",
                  background: msg.role === "user"
                    ? "#f59e0b"
                    : "var(--bg-elev, rgba(255,255,255,0.04))",
                  color: msg.role === "user"
                    ? "#000"
                    : "var(--text, #e5e7eb)",
                  fontSize: 13,
                  lineHeight: 1.6,
                  border: msg.role === "assistant"
                    ? "1px solid var(--border)"
                    : "none",
                  opacity: msg.error ? 0.7 : 1,
                  whiteSpace: "pre-wrap",
                  wordBreak: "break-word",
                }}
              >
                {msg.content || (msg.streaming
                  ? <span style={{
                      opacity: 0.5,
                      fontFamily: "monospace",
                      fontSize: 11,
                    }}>thinking…</span>
                  : ""
                )}
                {msg.streaming && msg.content && (
                  <span style={{
                    display: "inline-block",
                    width: 6, height: 6,
                    borderRadius: "50%",
                    background: "#f59e0b",
                    marginLeft: 4,
                    animation: "ora-pulse 1s infinite",
                  }} />
                )}
              </div>
            </div>
          ))}
          <div ref={bottomRef} />
        </div>

        {/* Input */}
        <div style={{
          padding: "12px 16px",
          borderTop: "1px solid var(--border)",
          flexShrink: 0,
        }}>
          <div style={{ display: "flex", gap: 8, alignItems: "flex-end" }}>
            <textarea
              ref={inputRef}
              data-testid="ora-input"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKey}
              placeholder="Ask ORA anything about your project…"
              rows={1}
              disabled={busy}
              style={{
                flex: 1,
                background: "var(--bg-elev, rgba(255,255,255,0.04))",
                border: "1px solid var(--border)",
                borderRadius: 8,
                padding: "10px 12px",
                color: "var(--text, #e5e7eb)",
                fontSize: 13,
                resize: "none",
                fontFamily: "inherit",
                lineHeight: 1.5,
                outline: "none",
                maxHeight: 120,
                overflowY: "auto",
              }}
            />

            {sttSupported && (
              <button
                data-testid="ora-mic-btn"
                onClick={listening ? stopListening : startListening}
                disabled={busy}
                title={listening ? "Stop listening" : "Speak"}
                style={{
                  width: 36, height: 36,
                  borderRadius: 8,
                  border: "1px solid var(--border)",
                  background: listening
                    ? "rgba(239,68,68,0.15)"
                    : "var(--bg-elev, rgba(255,255,255,0.04))",
                  color: listening ? "#ef4444" : "var(--text-faint)",
                  cursor: "pointer",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  flexShrink: 0,
                }}
              >
                {listening ? <MicOff size={14} /> : <Mic size={14} />}
              </button>
            )}

            <button
              data-testid="ora-send-btn"
              onClick={busy ? onStop : handleSend}
              disabled={!busy && !input.trim()}
              style={{
                width: 36, height: 36,
                borderRadius: 8,
                border: "none",
                background: busy
                  ? "rgba(239,68,68,0.15)"
                  : (!input.trim() ? "rgba(245,158,11,0.4)" : "#f59e0b"),
                color: busy ? "#ef4444" : "#000",
                cursor: busy || input.trim() ? "pointer" : "not-allowed",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                flexShrink: 0,
                fontWeight: 600,
              }}
            >
              {busy ? <Square size={14} /> : <Send size={14} />}
            </button>
          </div>

          {projectId && (
            <div style={{
              fontSize: 9,
              color: "var(--text-faint, #6b7280)",
              marginTop: 6,
              fontFamily: "'JetBrains Mono', monospace",
            }}>
              tokens → your account · repo → your project only
            </div>
          )}
        </div>
      </div>

      <style>{`
        @keyframes ora-slide-in-right {
          from { transform: translateX(100%); opacity: 0; }
          to   { transform: translateX(0);    opacity: 1; }
        }
        @keyframes ora-pulse {
          0%, 100% { opacity: 1; }
          50%      { opacity: 0.3; }
        }
      `}</style>
    </>
  );
}
