/**
 * ChatPanel.jsx — Streaming chat + session persistence.
 *
 * Toolbar:
 *  📎 Upload    — attach files (multi, <=50KB each) → injected as
 *                 [File: name]\n```...```\n blocks at end of input
 *  💾 GitHub    — open SaveToGithubDialog
 *  ⚡ Maxx      — toggle dual-engine mode (DeepSeek + Emergent watchdog)
 *  ➤ Send      — submits (Enter also submits)
 *
 * Props:
 *   sessionId   (required) — UUID of the active chat thread
 *   onTurnSaved (optional) — fired after persist, lets sidebar refresh
 */
import React, { useState, useRef, useEffect, useCallback, useMemo } from "react";
import {
  Send, Bot, User, Loader2, Square, Paperclip, Github, Zap,
  ShieldCheck, AlertTriangle, RefreshCw, Eye,
} from "lucide-react";
import { api, streamChat } from "../lib/api";
import { toast } from "./Toast";
import SaveToGithubDialog from "./SaveToGithubDialog";
import PreviewPanel from "./PreviewPanel";
import TemperatureBadge from "./TemperatureBadge";

const WELCOME = {
  role: "assistant",
  content:
    "I'm AUREM CTO — your sovereign engineering co-pilot. Ask me to plan a feature, write code, or debug an error. What are we shipping today?",
  provider: "system",
};

const MAX_FILE_BYTES = 50 * 1024; // 50 KB
const MAXX_KEY = "aurem_maxx_mode";
const PREVIEW_KEY = "aurem_preview_open";

const CODE_BLOCK_RE = /```(\w+)?\n([\s\S]*?)```/g;

// Detect HTML blob inside a message — either a fenced ```html block or a raw <html>/<div>
function extractInlineHTML(text) {
  if (!text) return null;
  const m1 = text.match(/```html\n([\s\S]*?)```/i);
  if (m1) return m1[1];
  const m2 = text.match(/<html[\s\S]*<\/html>/i);
  if (m2) return m2[0];
  const m3 = text.match(/<!doctype html[\s\S]*<\/html>/i);
  if (m3) return m3[0];
  return null;
}

function extractCodeBlocks(content) {
  if (!content) return [];
  const blocks = [];
  let m;
  CODE_BLOCK_RE.lastIndex = 0;
  while ((m = CODE_BLOCK_RE.exec(content)) !== null) {
    const lang = (m[1] || "text").toLowerCase();
    const code = m[2];
    if (code && code.trim()) blocks.push({ lang, code });
  }
  return blocks;
}

function estimateTokenCount(text) {
  if (!text) return 0;
  // ~1.3 tokens per word, rough heuristic — same model the backend deducts on.
  return Math.ceil(text.split(/\s+/).filter(Boolean).length * 1.3);
}

export default function ChatPanel({ sessionId, onTurnSaved }) {
  const [messages, setMessages] = useState([WELCOME]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [showGithub, setShowGithub] = useState(false);
  const [maxxMode, setMaxxMode] = useState(
    () => localStorage.getItem(MAXX_KEY) === "1"
  );
  const [previewOpen, setPreviewOpen] = useState(
    () => localStorage.getItem(PREVIEW_KEY) === "1"
  );
  const [previewBlocks, setPreviewBlocks] = useState([]);
  const endRef = useRef(null);
  const abortRef = useRef(null);
  const fileInputRef = useRef(null);
  const taRef = useRef(null);

  const toggleMaxx = useCallback(() => {
    setMaxxMode((v) => {
      const next = !v;
      localStorage.setItem(MAXX_KEY, next ? "1" : "0");
      toast({
        message: next
          ? "Maxx mode ON — Emergent watchdog will review every reply."
          : "Maxx mode OFF — single-engine DeepSeek.",
        kind: next ? "warn" : "info",
      });
      return next;
    });
  }, []);

  const togglePreview = useCallback(() => {
    setPreviewOpen((v) => {
      const next = !v;
      localStorage.setItem(PREVIEW_KEY, next ? "1" : "0");
      return next;
    });
  }, []);

  // Auto-extract code blocks from the latest *completed* assistant reply
  const latestAssistant = useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      const m = messages[i];
      if (m.role === "assistant" && !m.streaming && m.provider !== "system") {
        return m;
      }
    }
    return null;
  }, [messages]);

  useEffect(() => {
    if (!latestAssistant) return;
    const blocks = extractCodeBlocks(latestAssistant.content);
    if (blocks.length === 0) return;
    setPreviewBlocks(blocks);
    // Auto-open the panel on first code reply (don't override if user closed it manually mid-session)
    if (!localStorage.getItem(PREVIEW_KEY)) {
      setPreviewOpen(true);
      localStorage.setItem(PREVIEW_KEY, "1");
    }
  }, [latestAssistant]);

  // Load history on session change
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
            watchdog: t.watchdog,
          })));
        }
      })
      .catch(() => !cancelled && setMessages([WELCOME]))
      .finally(() => !cancelled && setLoadingHistory(false));
    return () => { cancelled = true; };
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

  async function handleFiles(fileList) {
    const files = Array.from(fileList || []);
    if (!files.length) return;
    const chunks = [];
    for (const f of files) {
      if (f.size > MAX_FILE_BYTES) {
        toast({ message: `${f.name} exceeds 50 KB — skipped.`, kind: "error" });
        continue;
      }
      try {
        const text = await f.text();
        const ext = (f.name.split(".").pop() || "").toLowerCase();
        chunks.push(`[File: ${f.name}]\n\`\`\`${ext}\n${text}\n\`\`\``);
      } catch (e) {
        toast({ message: `Couldn't read ${f.name}: ${e.message}`, kind: "error" });
      }
    }
    if (chunks.length) {
      setInput((prev) => (prev ? prev + "\n\n" : "") + chunks.join("\n\n"));
      toast({ message: `Attached ${chunks.length} file(s).`, kind: "success" });
    }
  }

  async function send(e) {
    e?.preventDefault();
    const text = input.trim();
    if (!text || busy || !sessionId) return;
    setInput("");
    setMessages((m) => [
      ...m,
      { role: "user", content: text },
      { role: "assistant", content: "", streaming: true, maxxMode },
    ]);
    setBusy(true);

    const ctrl = new AbortController();
    abortRef.current = ctrl;
    let providerSeen = "";

    await streamChat({
      prompt: text,
      sessionId,
      maxToolIters: 2,
      maxxMode,
      signal: ctrl.signal,
      onMeta: (m) => {
        if (m.provider) providerSeen = m.provider;
        if (typeof m.temperature === "number" || m.mode) {
          setMessages((msgs) => {
            const copy = msgs.slice();
            const last = copy[copy.length - 1];
            if (last && last.role === "assistant") {
              copy[copy.length - 1] = {
                ...last,
                temperature: m.temperature,
                mode: m.mode,
              };
            }
            return copy;
          });
        }
      },
      onToken: (tok) => {
        setMessages((msgs) => {
          const copy = msgs.slice();
          const last = copy[copy.length - 1];
          if (last && last.role === "assistant") {
            copy[copy.length - 1] = { ...last, content: (last.content || "") + tok };
          }
          return copy;
        });
      },
      onWatchdogPending: () => {
        setMessages((msgs) => {
          const copy = msgs.slice();
          const last = copy[copy.length - 1];
          if (last && last.role === "assistant") {
            copy[copy.length - 1] = { ...last, watchdogPending: true };
          }
          return copy;
        });
      },
      onWatchdog: (wd) => {
        setMessages((msgs) => {
          const copy = msgs.slice();
          const last = copy[copy.length - 1];
          if (last && last.role === "assistant") {
            copy[copy.length - 1] = {
              ...last, watchdog: wd, watchdogPending: false,
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
              ...last, streaming: false,
              provider: d.provider || providerSeen || "—",
            };
          }
          return copy;
        });
        setBusy(false);
        abortRef.current = null;
        onTurnSaved?.();
        setTimeout(() => onTurnSaved?.(), 2800);
      },
      onError: (err) => {
        setMessages((msgs) => {
          const copy = msgs.slice();
          const last = copy[copy.length - 1];
          if (last && last.role === "assistant") {
            copy[copy.length - 1] = {
              ...last, content: `⚠ ${err}`, error: true, streaming: false,
            };
          }
          return copy;
        });
        setBusy(false);
        abortRef.current = null;
      },
    });
  }

  function regenerate() {
    // Walk backwards to find the last user message
    const lastUser = [...messages].reverse().find((m) => m.role === "user");
    if (!lastUser) return;
    setInput(lastUser.content);
    setTimeout(() => taRef.current?.focus(), 50);
  }

  function onKeyDown(e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  }

  return (
    <div
      data-testid="chat-root"
      style={{
        display: "flex",
        height: "100vh",
        width: "100%",
        overflow: "hidden",
      }}
    >
      <div
        data-testid="chat-panel"
        style={{
          display: "flex", flexDirection: "column",
          flex: previewOpen ? "0 0 50%" : "1 1 auto",
          minWidth: 0,
          height: "100vh",
          background: "var(--panel)",
          borderLeft: "1px solid var(--border)",
          overflow: "hidden",
          transition: "flex 240ms cubic-bezier(0.4,0,0.2,1)",
        }}
      >
      <div
        data-testid="chat-messages"
        style={{
          flex: 1, overflowY: "auto", padding: "24px 28px",
          display: "flex", flexDirection: "column", gap: 20,
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
          <MessageBubble key={i} idx={i} m={m} onRegenerate={regenerate} />
        ))}
        <div ref={endRef} />
      </div>

      <form
        onSubmit={send}
        style={{
          borderTop: "1px solid var(--border)",
          padding: 14, background: "var(--bg-elev)",
          display: "flex", flexDirection: "column", gap: 10,
        }}
      >
        {/* Toolbar */}
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <input
            ref={fileInputRef}
            type="file"
            multiple
            data-testid="chat-file-input"
            style={{ display: "none" }}
            onChange={(e) => {
              handleFiles(e.target.files);
              e.target.value = ""; // allow re-select same file
            }}
          />
          <ToolButton
            testid="chat-attach-btn"
            title="Attach file (max 50 KB each)"
            onClick={() => fileInputRef.current?.click()}
            Icon={Paperclip}
          />
          <ToolButton
            testid="chat-github-btn"
            title="Save to GitHub"
            onClick={() => setShowGithub(true)}
            Icon={Github}
          />
          <ToolButton
            testid="chat-maxx-btn"
            title={maxxMode ? "Maxx mode ON (Emergent watchdog)" : "Maxx mode OFF"}
            onClick={toggleMaxx}
            Icon={Zap}
            active={maxxMode}
          />
          <span style={{ flex: 1 }} />
          {maxxMode && (
            <span
              data-testid="maxx-active-pill"
              style={{
                fontSize: 10, fontFamily: "'JetBrains Mono', monospace",
                letterSpacing: "0.16em", color: "var(--accent-2)",
                padding: "4px 10px", border: "1px solid var(--accent)",
                borderRadius: 999,
                background: "var(--accent-soft)",
                boxShadow: "0 0 12px -2px var(--accent)",
              }}
            >
              ⚡ MAXX
            </span>
          )}
        </div>

        {/* Input + Send/Stop */}
        <div style={{ display: "flex", gap: 10, alignItems: "flex-end" }}>
          <textarea
            ref={taRef}
            data-testid="chat-input"
            className="input"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder="Ask AUREM CTO to plan, build, debug…  (Enter to send, Shift+Enter for newline)"
            rows={Math.min(6, Math.max(1, input.split("\n").length))}
            autoFocus
            disabled={busy}
            style={{ resize: "none", flex: 1, fontFamily: "'Jost', system-ui, sans-serif" }}
          />
          {busy ? (
            <button
              type="button" data-testid="chat-stop"
              className="btn-ghost" onClick={stop}
            >
              <Square size={13} /> Stop
            </button>
          ) : (
            <button
              type="submit" data-testid="chat-send"
              className="btn-primary"
              disabled={!input.trim() || !sessionId}
            >
              <Send size={14} /> Send
            </button>
          )}
        </div>
      </form>

      <SaveToGithubDialog
        open={showGithub}
        onClose={() => setShowGithub(false)}
        sessionId={sessionId}
      />

      <style>{`
        @keyframes spin { from { transform: rotate(0); } to { transform: rotate(360deg); } }
        @keyframes blink { 50% { opacity: 0; } }
      `}</style>
      </div>

      {previewOpen && (
        <PreviewPanel
          blocks={previewBlocks.length > 0 ? previewBlocks : [{
            lang: "text",
            code: "No code blocks in the current chat yet. Ask AUREM to write some — Hint: ```html ... ``` or ```jsx ... ``` will render live here.",
          }]}
          onClose={togglePreview}
        />
      )}
    </div>
  );
}

function ToolButton({ testid, title, onClick, Icon, active }) {
  return (
    <button
      type="button"
      data-testid={testid}
      title={title}
      onClick={onClick}
      style={{
        width: 34, height: 34, borderRadius: 4,
        background: active ? "var(--accent-soft)" : "transparent",
        border: `1px solid ${active ? "var(--accent)" : "var(--border)"}`,
        color: active ? "var(--accent-2)" : "var(--text-dim)",
        cursor: "pointer",
        display: "flex", alignItems: "center", justifyContent: "center",
        transition: "color 120ms, border-color 120ms, background 120ms, box-shadow 220ms",
        boxShadow: active ? "0 0 14px -3px var(--accent)" : "none",
      }}
      onMouseEnter={(e) => {
        if (!active) {
          e.currentTarget.style.color = "var(--accent-2)";
          e.currentTarget.style.borderColor = "var(--border-strong)";
        }
      }}
      onMouseLeave={(e) => {
        if (!active) {
          e.currentTarget.style.color = "var(--text-dim)";
          e.currentTarget.style.borderColor = "var(--border)";
        }
      }}
    >
      <Icon size={14} />
    </button>
  );
}

function MessageBubble({ idx, m, onRegenerate }) {
  return (
    <div
      data-testid={`chat-msg-${m.role}-${idx}`}
      style={{
        display: "flex", gap: 12, alignItems: "flex-start",
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
      <div style={{ maxWidth: "80%" }}>
        <div style={{
          padding: "12px 16px",
          borderRadius: 4,
          background: m.role === "user" ? "rgba(255, 138, 42, 0.06)" : "var(--panel-2)",
          border: m.role === "user"
            ? "1px solid rgba(255,138,42,0.2)"
            : "1px solid var(--border)",
          fontSize: 14, lineHeight: 1.6,
          color: m.error ? "var(--danger)" : "var(--text)",
          whiteSpace: "pre-wrap", wordBreak: "break-word",
        }}>
          {m.content}
          {/* Inline HTML preview directly inside the bubble (separate from side PreviewPanel) */}
          {m.role === "assistant" && !m.streaming && (() => {
            const html = extractInlineHTML(m.content);
            return html ? (
              <iframe
                data-testid={`inline-html-${idx}`}
                srcDoc={html}
                sandbox="allow-scripts"
                title="inline-preview"
                style={{
                  display: "block",
                  width: "100%",
                  height: 360,
                  marginTop: 12,
                  border: "1px solid var(--border)",
                  borderRadius: 4,
                  background: "white",
                }}
              />
            ) : null;
          })()}
          {m.streaming && !m.content && (
            <span data-testid="chat-thinking" style={{
              display: "inline-flex", alignItems: "center", gap: 8,
              color: "var(--text-faint)", fontStyle: "italic", fontSize: 13,
            }}>
              <Loader2 size={12} style={{ animation: "spin 1s linear infinite" }} />
              thinking…
            </span>
          )}
          {m.streaming && m.content && (
            <span data-testid="chat-cursor" style={{
              display: "inline-block", width: 7, height: 14,
              marginLeft: 2, background: "var(--accent-2)",
              verticalAlign: "middle",
              animation: "blink 0.9s steps(1) infinite",
            }} />
          )}
          {m.provider && m.provider !== "system" && !m.streaming && (
            <div style={{
              marginTop: 8, fontSize: 10,
              fontFamily: "'JetBrains Mono', monospace",
              color: "var(--text-faint)", letterSpacing: "0.1em",
              display: "inline-flex", alignItems: "center", gap: 6,
              flexWrap: "wrap",
            }}>
              via {m.provider}
              {m.maxxMode && <Zap size={10} style={{ color: "var(--accent-2)" }} />}
              <span data-testid={`token-cost-${idx}`} style={{
                opacity: 0.7,
              }}>
                · ~{estimateTokenCount(m.content)} tokens
              </span>
              {typeof m.temperature === "number" && (
                <TemperatureBadge temperature={m.temperature} mode={m.mode} />
              )}
            </div>
          )}
        </div>

        {/* Watchdog pending */}
        {m.role === "assistant" && m.watchdogPending && (
          <div data-testid={`watchdog-pending-${idx}`} style={{
            marginTop: 8, fontSize: 11,
            color: "var(--text-faint)",
            display: "flex", alignItems: "center", gap: 6,
          }}>
            <Loader2 size={11} style={{ animation: "spin 1s linear infinite" }} />
            Watchdog reviewing…
          </div>
        )}

        {/* Watchdog result */}
        {m.role === "assistant" && m.watchdog && m.watchdog.ok && (
          <WatchdogPanel idx={idx} wd={m.watchdog} onRegenerate={onRegenerate} />
        )}
        {m.role === "assistant" && m.watchdog && !m.watchdog.ok && (
          <div data-testid={`watchdog-error-${idx}`} style={{
            marginTop: 8, fontSize: 11, color: "var(--text-faint)",
            fontStyle: "italic",
          }}>
            Watchdog skipped: {m.watchdog.error || "unavailable"}
          </div>
        )}
      </div>
    </div>
  );
}

function WatchdogPanel({ idx, wd, onRegenerate }) {
  const [open, setOpen] = useState(!wd.passed);
  const score = wd.score ?? "?";
  let pill = { bg: "rgba(255,107,107,0.1)", color: "var(--danger)", border: "rgba(255,107,107,0.4)" };
  if (typeof wd.score === "number") {
    if (wd.score >= 8) pill = { bg: "rgba(109,212,161,0.1)", color: "var(--ok)", border: "rgba(109,212,161,0.4)" };
    else if (wd.score >= 7) pill = { bg: "rgba(255,197,96,0.1)", color: "var(--accent-2)", border: "rgba(255,197,96,0.4)" };
  }

  return (
    <div data-testid={`watchdog-${idx}`} style={{
      marginTop: 10,
      border: `1px solid ${pill.border}`,
      borderRadius: 4, background: pill.bg,
      padding: "10px 12px", fontSize: 12,
    }}>
      <div
        onClick={() => setOpen((v) => !v)}
        style={{
          display: "flex", alignItems: "center", gap: 8,
          cursor: "pointer", userSelect: "none",
        }}
      >
        {wd.passed
          ? <ShieldCheck size={13} style={{ color: pill.color }} />
          : <AlertTriangle size={13} style={{ color: pill.color }} />}
        <span style={{ color: pill.color, fontWeight: 600 }}>
          Watchdog · {wd.passed ? "passed" : "flagged"}
        </span>
        <span data-testid={`watchdog-score-${idx}`} style={{
          marginLeft: 4,
          padding: "2px 8px", borderRadius: 999,
          background: "rgba(0,0,0,0.25)",
          color: pill.color, fontWeight: 600,
          fontFamily: "'JetBrains Mono', monospace",
          fontSize: 10,
        }}>
          {score}/10
        </span>
        <span style={{ flex: 1 }} />
        <span style={{ color: "var(--text-faint)", fontSize: 10 }}>
          {open ? "click to hide" : "click to expand"}
        </span>
      </div>
      {open && (
        <div style={{ marginTop: 10, color: "var(--text-dim)", lineHeight: 1.6 }}>
          {wd.verdict && (
            <div style={{ marginBottom: 8, color: "var(--text)" }}>
              <em>{wd.verdict}</em>
            </div>
          )}
          {Array.isArray(wd.issues) && wd.issues.length > 0 && (
            <ul style={{ margin: 0, paddingLeft: 18, fontSize: 11 }}>
              {wd.issues.map((iss, i) => (
                <li key={i} style={{ marginBottom: 2 }}>{iss}</li>
              ))}
            </ul>
          )}
          {!wd.passed && (
            <button
              data-testid={`watchdog-regen-${idx}`}
              type="button"
              onClick={onRegenerate}
              className="btn-ghost"
              style={{ marginTop: 10, fontSize: 11, padding: "6px 10px" }}
            >
              <RefreshCw size={11} /> Regenerate
            </button>
          )}
        </div>
      )}
    </div>
  );
}
