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
import { api } from "../lib/api";

// Iter 186 — keywords that trigger an auto-draft of a support email
// in parallel with the user's normal Ask Advisor message. Kept short
// and case-insensitive; matches anywhere in the prompt.
const SUPPORT_KEYWORDS = [
  "not working", "broken", "error", "bug",
  "issue", "problem", "help", "support",
  "failed", "cant", "can't", "doesn't work",
];

const isSupportRequest = (text) =>
  SUPPORT_KEYWORDS.some((k) => (text || "").toLowerCase().includes(k));

export default function ORASidePanel({
  open, messages, busy, projectId,
  onClose, onSend, onStop,
}) {
  const [input, setInput] = useState("");
  // Iter 155 — ModeSelector removed from the ORA panel per user feedback:
  // ORA picks its own engine via the aurem.live API, the Swift/Pro/Maxx
  // pills here were just confusing duplication of the main AUREM chat.
  // `onSend` no longer receives a mode argument from this panel.
  const [voiceEnabled, setVoiceEnabled] = useState(false);
  const [listening, setListening] = useState(false);
  // Iter 186 — support-email drafting state. `supportDraft` holds the
  // backend response ({subject, to, body}); the preview card renders
  // when it's non-null and `supportSent` is false.
  // Iter 187 — added a confirmation gate ("Did this fix your
  // issue?") so we only draft an email when the Advisor's reply
  // didn't actually solve the problem. `pendingIssueText` survives
  // across the confirm step so we can ship the full LLM analysis as
  // `advisor_analysis` to the backend draft endpoint.
  const [supportDraft, setSupportDraft] = useState(null);
  const [supportLoading, setSupportLoading] = useState(false);
  const [supportSent, setSupportSent] = useState(false);
  const [showSupportConfirm, setShowSupportConfirm] = useState(false);
  const [pendingIssueText, setPendingIssueText] = useState("");
  const [advisorAnalysis, setAdvisorAnalysis] = useState("");
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

  // Iter 187 — after the Advisor finishes replying to a support-flagged
  // prompt, capture the reply as `advisorAnalysis` and surface the
  // "Did this fix your issue?" confirmation. The 2 s delay lets the
  // user read at least the first line before the buttons appear so
  // the confirm feels like a follow-up rather than an interruption.
  useEffect(() => {
    if (!pendingIssueText) return;
    const last = messages[messages.length - 1];
    if (!last || last.role !== "assistant" || last.streaming) return;
    if (!last.content) return;
    setAdvisorAnalysis(last.content);
    const t = setTimeout(() => setShowSupportConfirm(true), 2000);
    return () => clearTimeout(t);
  }, [messages, pendingIssueText]);

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

  // Iter 187 — draft a support email AFTER the user confirms that
  // the Advisor's fix didn't resolve the issue. Pulls the latest
  // assistant reply text out of `advisorAnalysis` so the backend can
  // tell the LLM "this fix didn't work" and write an escalation-tone
  // email. Also ships the browser UA + current page URL so support
  // can reproduce environment-specific issues.
  async function handleSupportEmail() {
    const txt = (pendingIssueText || "").trim();
    if (!txt) return;
    setSupportLoading(true);
    setSupportDraft(null);
    setSupportSent(false);
    setShowSupportConfirm(false);
    try {
      const r = await api.post("/chat/ora/draft-support-email", {
        issue: txt,
        project_id: projectId || null,
        advisor_analysis: advisorAnalysis || "",
        user_agent: (typeof navigator !== "undefined" && navigator.userAgent) || "",
        page_url: (typeof window !== "undefined" && window.location?.href) || "",
      });
      if (r.data?.ok) setSupportDraft(r.data);
    } catch (e) {
      // Silent failure — the regular Ask Advisor reply still lands in
      // the message list, so the user isn't blocked. Logged for ops.
      // eslint-disable-next-line no-console
      console.error("Support draft failed", e);
    } finally {
      setSupportLoading(false);
    }
  }

  function dismissSupportConfirm() {
    setShowSupportConfirm(false);
    setPendingIssueText("");
    setAdvisorAnalysis("");
  }

  function sendSupportEmail() {
    if (!supportDraft) return;
    try {
      const subject = encodeURIComponent(supportDraft.subject || "");
      const emailBody = encodeURIComponent(supportDraft.body || "");
      const to = encodeURIComponent(supportDraft.to || "");
      // mailto: opens the user's mail client with everything filled in.
      // We use window.open so it works across browsers without
      // navigating away from the panel.
      window.open(`mailto:${to}?subject=${subject}&body=${emailBody}`);
      setSupportSent(true);
      // Auto-clear the card after 3 s so a follow-up draft can take
      // its place without manual dismissal. Iter 187 — also clear
      // the pending issue + advisor analysis so the next support
      // request starts from a clean slate.
      setTimeout(() => {
        setSupportDraft(null);
        setSupportSent(false);
        setPendingIssueText("");
        setAdvisorAnalysis("");
      }, 3000);
    } catch (e) {
      // eslint-disable-next-line no-console
      console.error("Send failed", e);
    }
  }

  const handleSend = () => {
    if (!input.trim() || busy) return;
    const txt = input.trim();
    onSend(txt);
    setInput("");
    // Iter 187 — instead of drafting an email in parallel with the
    // Advisor's reply (Iter 186 behaviour), gate it behind a "Did
    // this fix your issue?" confirmation. We stash the user's
    // prompt as `pendingIssueText` so the draft endpoint can ship it
    // with the Advisor's analysis later. The confirm card itself is
    // surfaced by the useEffect below once the assistant settles.
    if (isSupportRequest(txt)) {
      setPendingIssueText(txt);
      setAdvisorAnalysis("");
      setShowSupportConfirm(false);
    }
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
              }}>Ask Advisor</div>
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

        {/* Iter 187 — "Did this fix your issue?" confirmation card.
            Shows 2 s after the Advisor finishes a reply to a
            support-flagged prompt. Yes clears the pending state;
            No drafts the escalation email with the Advisor's
            analysis included. */}
        {showSupportConfirm && !supportDraft && !supportLoading && (
          <div
            data-testid="ora-support-confirm"
            style={{
              margin: "0 14px 8px",
              padding: "12px 14px",
              background: "rgba(245,158,11,0.06)",
              border: "1px solid rgba(245,158,11,0.2)",
              borderRadius: 10,
            }}
          >
            <div style={{
              fontSize: 12,
              color: "#f8fafc",
              marginBottom: 10,
              fontFamily: "'JetBrains Mono', monospace",
            }}>
              Did the above fix resolve your issue?
            </div>
            <div style={{ display: "flex", gap: 8 }}>
              <button
                data-testid="ora-support-confirm-yes"
                onClick={dismissSupportConfirm}
                style={{
                  flex: 1,
                  padding: "8px 0",
                  background: "rgba(34,197,94,0.1)",
                  border: "1px solid rgba(34,197,94,0.3)",
                  borderRadius: 7,
                  color: "#22c55e",
                  fontSize: 12,
                  cursor: "pointer",
                  fontFamily: "'JetBrains Mono', monospace",
                }}
              >
                ✅ Yes, fixed!
              </button>
              <button
                data-testid="ora-support-confirm-no"
                onClick={handleSupportEmail}
                style={{
                  flex: 1,
                  padding: "8px 0",
                  background: "rgba(239,68,68,0.1)",
                  border: "1px solid rgba(239,68,68,0.3)",
                  borderRadius: 7,
                  color: "#f87171",
                  fontSize: 12,
                  cursor: "pointer",
                  fontFamily: "'JetBrains Mono', monospace",
                }}
              >
                ❌ No, contact support
              </button>
            </div>
          </div>
        )}

        {supportLoading && (
          <div
            data-testid="ora-support-loading"
            style={{
              padding: "10px 14px",
              margin: "0 14px 8px",
              background: "rgba(245,158,11,0.06)",
              border: "1px solid rgba(245,158,11,0.15)",
              borderRadius: 8,
              fontSize: 12,
              color: "#f59e0b",
              fontFamily: "'JetBrains Mono', monospace",
            }}
          >
            Drafting support email…
          </div>
        )}

        {supportDraft && !supportSent && (
          <div
            data-testid="ora-support-draft"
            style={{
              margin: "0 14px 8px",
              background: "#0f172a",
              border: "1px solid rgba(245,158,11,0.2)",
              borderRadius: 10,
              overflow: "hidden",
            }}
          >
            <div style={{
              padding: "10px 14px",
              borderBottom: "1px solid rgba(255,255,255,0.06)",
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
            }}>
              <span style={{
                fontSize: 12, fontWeight: 600,
                color: "#f59e0b",
                fontFamily: "'JetBrains Mono', monospace",
              }}>
                Support Email Draft
              </span>
              <button
                data-testid="ora-support-dismiss"
                onClick={() => setSupportDraft(null)}
                style={{
                  background: "none", border: "none",
                  color: "#64748b", cursor: "pointer",
                  fontSize: 16, lineHeight: 1,
                }}
              >×</button>
            </div>

            <div style={{ padding: "12px 14px" }}>
              <div style={{
                fontSize: 10, color: "#64748b",
                marginBottom: 4,
                fontFamily: "'JetBrains Mono', monospace",
              }}>
                To: {supportDraft.to}
              </div>
              <div style={{
                fontSize: 10, color: "#64748b",
                marginBottom: 10,
                fontFamily: "'JetBrains Mono', monospace",
                wordBreak: "break-word",
              }}>
                Subject: {supportDraft.subject}
              </div>
              <div
                data-testid="ora-support-body"
                style={{
                  fontSize: 12, color: "#94a3b8",
                  lineHeight: 1.6,
                  background: "rgba(0,0,0,0.2)",
                  borderRadius: 6,
                  padding: "10px 12px",
                  maxHeight: 160,
                  overflowY: "auto",
                  fontFamily: "'JetBrains Mono', monospace",
                  whiteSpace: "pre-wrap",
                }}
              >
                {supportDraft.body}
              </div>
            </div>

            <div style={{
              padding: "10px 14px",
              borderTop: "1px solid rgba(255,255,255,0.06)",
              display: "flex",
              gap: 8,
            }}>
              <button
                data-testid="ora-support-send"
                onClick={sendSupportEmail}
                style={{
                  flex: 1,
                  padding: "9px 0",
                  background: "#f59e0b",
                  color: "#0a0e1a",
                  border: "none",
                  borderRadius: 7,
                  fontSize: 12,
                  fontWeight: 600,
                  cursor: "pointer",
                  fontFamily: "'JetBrains Mono', monospace",
                }}
              >
                Send Email
              </button>
              <button
                data-testid="ora-support-discard"
                onClick={() => setSupportDraft(null)}
                style={{
                  padding: "9px 16px",
                  background: "transparent",
                  color: "#64748b",
                  border: "1px solid rgba(255,255,255,0.08)",
                  borderRadius: 7,
                  fontSize: 12,
                  cursor: "pointer",
                }}
              >
                Discard
              </button>
            </div>
          </div>
        )}

        {supportSent && (
          <div
            data-testid="ora-support-sent"
            style={{
              margin: "0 14px 8px",
              padding: "10px 14px",
              background: "rgba(34,197,94,0.08)",
              border: "1px solid rgba(34,197,94,0.2)",
              borderRadius: 8,
              fontSize: 12,
              color: "#22c55e",
              fontFamily: "'JetBrains Mono', monospace",
              textAlign: "center",
            }}
          >
            Email opened in your mail app
          </div>
        )}

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
              placeholder="Ask Advisor anything — ~6s simple · ~20-30s multi-file"
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
              {/* Iter 155 — ModeSelector removed. ORA uses its own
                  upstream engine (aurem.live), so the Swift/Pro/Maxx
                  pills don't apply here. */}
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
                  onClick={handleSend}
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
