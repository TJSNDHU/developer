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
  ShieldCheck, AlertTriangle, RefreshCw, Eye, Copy as CopyIcon,
  ThumbsUp, ThumbsDown, Undo2, ExternalLink,
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

const MAX_FILE_BYTES = 25 * 1024 * 1024; // 25 MB (server enforces same cap)
const TEXT_FAST_PATH_BYTES = 50 * 1024;  // <=50KB text → skip server roundtrip
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

// Detect a ```aurem-handoff fenced block — emitted by AUREM in HANDOFF MODE
// to signal "this is an executable CTO worker brief". We render a one-click
// Ship via CTO button when present.
function extractHandoffBrief(content) {
  if (!content) return null;
  const m = content.match(/```aurem-handoff\s*\n([\s\S]*?)```/);
  if (!m) return null;
  const brief = (m[1] || "").trim();
  return brief.length > 0 ? brief : null;
}

function estimateTokenCount(text) {
  if (!text) return 0;
  // ~1.3 tokens per word, rough heuristic — same model the backend deducts on.
  return Math.ceil(text.split(/\s+/).filter(Boolean).length * 1.3);
}

// THING 2 — Token-budget banner shown above the chat input.
//   pct_used >= 100%: red, "🚫 Tokens exhausted — upgrade …"
//   pct_used >=  80%: yellow, "⚠️ 80% tokens used — N remaining …"
//   below 80%:        nothing (returns null)
function TokenBanner({ usage }) {
  if (!usage) return null;
  const pct = usage.pct_used || 0;
  const exhausted = usage.is_exhausted || pct >= 100;
  if (!exhausted && pct < 80) return null;
  const remaining = Math.max(0, usage.remaining || 0);
  const used = usage.used || 0;
  const limit = usage.effective_limit || 0;

  const tone = exhausted
    ? { bg: "rgba(255,77,77,0.10)", border: "rgba(255,77,77,0.45)", color: "#ff8585", icon: "🚫" }
    : { bg: "rgba(255,196,0,0.10)", border: "rgba(255,196,0,0.45)", color: "#ffcf5c", icon: "⚠️" };

  return (
    <div
      data-testid="token-banner"
      data-state={exhausted ? "exhausted" : "warning"}
      style={{
        display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap",
        padding: "8px 12px",
        background: tone.bg,
        border: `1px solid ${tone.border}`,
        borderRadius: 8,
        fontSize: 12,
        color: tone.color,
        fontFamily: "'Jost', system-ui, sans-serif",
      }}
    >
      <span style={{ fontSize: 14 }}>{tone.icon}</span>
      <span style={{ flex: 1, minWidth: 0 }}>
        {exhausted ? (
          <>
            <b>Tokens exhausted.</b> {used.toLocaleString()} / {limit.toLocaleString()} used.
            Upgrade your plan to continue.
          </>
        ) : (
          <>
            <b>{Math.round(pct)}% tokens used</b> · {remaining.toLocaleString()} remaining.
            Upgrade to keep going.
          </>
        )}
      </span>
      <a
        href="/admin?tab=settings"
        data-testid="token-banner-upgrade"
        style={{
          padding: "5px 12px",
          background: tone.color,
          color: "#0a0a0e",
          fontWeight: 600,
          fontSize: 11,
          borderRadius: 6,
          textDecoration: "none",
          whiteSpace: "nowrap",
        }}
      >
        Upgrade →
      </a>
    </div>
  );
}

export default function ChatPanel({ sessionId, onTurnSaved, activeProject }) {
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
  const [usage, setUsage] = useState(null);  // {used, effective_limit, remaining, pct_used, is_exhausted}
  const endRef = useRef(null);
  const abortRef = useRef(null);
  const fileInputRef = useRef(null);
  const taRef = useRef(null);

  // Load token usage on mount + every time a turn is saved (so the banner
  // reflects fresh consumption right after a chat reply / CTO task).
  const refreshUsage = useCallback(async () => {
    try {
      const r = await api.get("/usage/me");
      setUsage(r.data);
    } catch (_) { /* non-fatal */ }
  }, []);
  useEffect(() => { refreshUsage(); }, [refreshUsage]);

  const exhausted = !!usage?.is_exhausted;

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

  // Auto-open preview when a project with a preview_url is selected
  useEffect(() => {
    if (!activeProject?.preview_url) return;
    if (localStorage.getItem(PREVIEW_KEY) === "0") return; // user explicitly closed
    setPreviewOpen(true);
  }, [activeProject?.preview_url, activeProject?.project_id]);

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
            feedback: t.feedback,
            shipped_task_id: t.shipped_task_id,
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

    // Plain-text extensions we can safely read in the browser without a
    // server roundtrip (saves a request for small snippets).
    const TEXT_EXTS = new Set([
      "txt", "md", "markdown", "log", "csv", "tsv",
      "js", "jsx", "ts", "tsx", "py", "rb", "go", "rs",
      "java", "c", "cc", "cpp", "h", "hpp", "cs", "kt", "swift",
      "html", "htm", "css", "scss", "less",
      "json", "yaml", "yml", "toml", "xml", "ini", "env", "sh", "bash",
      "sql", "vue", "svelte", "lua", "php",
    ]);

    const chunks = [];
    for (const f of files) {
      if (f.size > MAX_FILE_BYTES) {
        toast({
          message: `${f.name} exceeds 25 MB — skipped.`,
          kind: "error",
        });
        continue;
      }

      const ext = (f.name.split(".").pop() || "").toLowerCase();
      const isSmallText = TEXT_EXTS.has(ext) && f.size <= TEXT_FAST_PATH_BYTES;

      try {
        if (isSmallText) {
          // Fast path — read in browser
          const text = await f.text();
          chunks.push(`[File: ${f.name}]\n\`\`\`${ext}\n${text}\n\`\`\``);
        } else {
          // Server path — push file through MarkItDown
          toast({
            message: `Converting ${f.name} via MarkItDown…`,
            kind: "info",
            duration: 1500,
          });
          const form = new FormData();
          form.append("file", f);
          const r = await api.post("/upload/convert", form, {
            headers: { "Content-Type": "multipart/form-data" },
            timeout: 90000,
          });
          const d = r.data || {};
          const truncNote = d.truncated
            ? " *(server truncated — large file)*"
            : "";
          chunks.push(
            `[File: ${d.filename || f.name}` +
            ` · ${(d.original_size / 1024).toFixed(1)} KB` +
            ` → ${(d.md_size / 1024).toFixed(1)} KB markdown${truncNote}]\n\n` +
            d.markdown
          );
        }
      } catch (e) {
        const msg =
          e?.response?.data?.detail ||
          e?.message ||
          "Couldn't read file.";
        toast({ message: `${f.name}: ${msg}`, kind: "error" });
      }
    }
    if (chunks.length) {
      setInput((prev) => (prev ? prev + "\n\n" : "") + chunks.join("\n\n"));
      toast({
        message: `Attached ${chunks.length} file(s).`,
        kind: "success",
      });
    }
  }

  async function send(e) {
    e?.preventDefault();
    const text = input.trim();
    if (!text || busy || !sessionId) return;
    setInput("");
    // Auto-augment prompt with active project context so the LLM stays scoped.
    const finalPrompt = activeProject
      ? `[Working on project: ${activeProject.name} — repo ${activeProject.github_owner}/${activeProject.github_repo}@${activeProject.branch}]\n\n${text}`
      : text;
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
      prompt: finalPrompt,
      projectId: activeProject?.project_id || null,
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
                thinkingS: m.thinking_s,
                toolCallsRun: m.tool_calls_run,
              };
            }
            return copy;
          });
        }
      },
      // Iter 35/36: server emits periodic {thinking:true, elapsed_s, activity}
      // frames during the tool-call loop so we can show a live counter +
      // a status label ("running 3 tools in parallel…").
      onThinking: (elapsed, activity) => {
        setMessages((msgs) => {
          const copy = msgs.slice();
          const last = copy[copy.length - 1];
          if (last && last.role === "assistant" && last.streaming) {
            copy[copy.length - 1] = {
              ...last,
              elapsedS: elapsed,
              ...(activity ? { activity } : {}),
            };
          }
          return copy;
        });
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
        refreshUsage();
        // Bug #3 — return cursor to the input after reply
        setTimeout(() => taRef.current?.focus(), 80);
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
        height: "100%",
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
          height: "100%",
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

        {messages.map((m, i) => {
          // The DB `turns` array does NOT contain the front-end-only
          // WELCOME / system messages. So when we ship a turn and tell
          // the backend "this is turn N", N must be computed from the
          // user/assistant pairs only — not from the rendered position.
          // Otherwise on first refresh the shipped_task_id lands on a
          // sparse / nonexistent index and the button reappears.
          //                                              — Iter 34 fix
          const dbTurnIndex = messages
            .slice(0, i + 1)
            .filter((mm) => mm.provider !== "system")
            .length - 1;
          return (
            <MessageBubble
              key={i}
              idx={i}
              dbTurnIndex={dbTurnIndex}
              m={m}
              onRegenerate={regenerate}
              sessionId={sessionId}
              activeProject={activeProject}
              exhausted={exhausted}
            />
          );
        })}
        <div ref={endRef} />
      </div>

      <form
        onSubmit={send}
        style={{
          borderTop: "1px solid var(--border)",
          padding: 14, background: "var(--bg-elev)",
          display: "flex", flexDirection: "column", gap: 8,
        }}
      >
        <TokenBanner usage={usage} />
        {/* Input on top */}
        <textarea
          ref={taRef}
          data-testid="chat-input"
          className="input"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder="Ask AUREM CTO to plan, build, debug…  (Enter to send, Shift+Enter for newline)"
          rows={Math.min(6, Math.max(2, input.split("\n").length))}
          autoFocus
          disabled={busy || exhausted}
          style={{ resize: "none", width: "100%", fontFamily: "'Jost', system-ui, sans-serif" }}
        />

        {/* Toolbar + Send BELOW input */}
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <input
            ref={fileInputRef}
            type="file"
            multiple
            data-testid="chat-file-input"
            style={{ display: "none" }}
            onChange={(e) => {
              handleFiles(e.target.files);
              e.target.value = "";
            }}
          />
          {activeProject && (
            <span
              data-testid="chat-project-pill"
              title={`Pinned to ${activeProject.name} — ${activeProject.github_owner}/${activeProject.github_repo}`}
              style={{
                display: "inline-flex", alignItems: "center", gap: 6,
                padding: "4px 10px", fontSize: 10,
                fontFamily: "'JetBrains Mono', monospace",
                letterSpacing: "0.08em",
                color: "var(--accent-2)",
                background: "var(--accent-soft)",
                border: "1px solid var(--accent)",
                borderRadius: 999,
                marginRight: 4,
              }}
            >
              ▸ {activeProject.name}
            </span>
          )}
          <ToolButton
            testid="chat-attach-btn"
            title="Attach file — PDF, DOCX, XLSX, PPTX, images, code (max 25 MB)"
            onClick={() => fileInputRef.current?.click()}
            Icon={Paperclip}
          />
          <ToolButton
            testid="chat-github-btn"
            title={activeProject
              ? `Save to ${activeProject.github_owner}/${activeProject.github_repo}`
              : "Save to GitHub"}
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
          <ToolButton
            testid="chat-preview-btn"
            title={previewOpen ? "Hide live preview" : "Show live preview"}
            onClick={togglePreview}
            Icon={Eye}
            active={previewOpen}
          />
          <span style={{ flex: 1 }} />
          {maxxMode && (
            <span
              data-testid="maxx-active-pill"
              style={{
                fontSize: 10, fontFamily: "'JetBrains Mono', monospace",
                letterSpacing: "0.16em", color: "var(--accent-2)",
                padding: "4px 10px", border: "1px solid var(--accent)",
                borderRadius: 999, background: "var(--accent-soft)",
                boxShadow: "0 0 12px -2px var(--accent)",
              }}
            >
              ⚡ MAXX
            </span>
          )}
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
              disabled={!input.trim() || !sessionId || exhausted}
              title={exhausted ? "Tokens exhausted — upgrade your plan" : undefined}
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
        activeProject={activeProject}
      />

      <style>{`
        @keyframes spin { from { transform: rotate(0); } to { transform: rotate(360deg); } }
        @keyframes blink { 50% { opacity: 0; } }
      `}</style>
      </div>

      {previewOpen && (() => {
        const liveBlock = activeProject?.preview_url
          ? [{ lang: "live_url", code: activeProject.preview_url, label: "Live Site" }]
          : [];
        const finalBlocks = [...liveBlock, ...previewBlocks];
        return (
          <PreviewPanel
            blocks={finalBlocks.length > 0 ? finalBlocks : [{
              lang: "text",
              code: activeProject
                ? `No preview URL set for "${activeProject.name}". Open Projects → Edit → "Live preview URL" to add one (e.g. https://yoursite.com).`
                : "No code blocks in the current chat yet. Ask AUREM to write some — Hint: ```html ... ``` or ```jsx ... ``` will render live here.",
            }]}
            onClose={togglePreview}
          />
        );
      })()}
    </div>
  );
}

function ActionBtn({ testid, title, onClick, Icon, active, color }) {
  return (
    <button
      type="button" data-testid={testid} title={title} onClick={onClick}
      style={{
        background: "none",
        border: "1px solid var(--border)",
        color: active ? (color || "var(--accent-2)") : "var(--text-faint)",
        cursor: "pointer", padding: "4px 6px",
        borderRadius: 4, display: "inline-flex",
        transition: "color 120ms, border-color 120ms",
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.color = color || "var(--accent-2)";
        e.currentTarget.style.borderColor = "var(--border-strong)";
      }}
      onMouseLeave={(e) => {
        if (!active) {
          e.currentTarget.style.color = "var(--text-faint)";
          e.currentTarget.style.borderColor = "var(--border)";
        }
      }}
    >
      <Icon size={11} />
    </button>
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

function ShipStatusCard({ taskId, task, project, onRollback }) {
  // Status sequence we show to the user (mapped from raw worker status)
  const STAGES = [
    { key: "pulling", label: "Cloning…", icon: "📡" },
    { key: "reading", label: "Reading files…", icon: "📄" },
    { key: "fixing",  label: "AI thinking…", icon: "🧠" },
    { key: "pushing", label: "Writing & pushing…", icon: "🚀" },
    { key: "done",    label: "Pushed",            icon: "✅" },
  ];
  const status = task?.status || "queued";
  const rbStatus = task?.rollback_status;
  const rbRunning = rbStatus === "queued" || rbStatus === "running";

  // While running
  if (!task || (status !== "done" && status !== "failed")) {
    const stageIdx = STAGES.findIndex((s) => s.key === status);
    const current = stageIdx >= 0 ? STAGES[stageIdx] : { icon: "⏳", label: status };
    return (
      <div data-testid={`ship-status-${taskId}`} style={{
        padding: "10px 12px",
        background: "var(--panel-2)",
        border: "1px solid var(--border)",
        borderRadius: 4,
        fontSize: 12, color: "var(--text-dim)",
        fontFamily: "'JetBrains Mono', monospace",
        minWidth: 260,
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <Loader2 size={12} style={{ animation: "spin 1s linear infinite", color: "var(--accent-2)" }} />
          <span>{current.icon} {current.label}</span>
          <span style={{ marginLeft: "auto", color: "var(--text-faint)", fontSize: 10 }}>{taskId}</span>
        </div>
        {(task?.steps || []).slice(-2).map((s, i) => (
          <div key={i} style={{ marginTop: 4, fontSize: 10, color: s.status === "error" ? "var(--danger)" : "var(--text-faint)" }}>
            {s.step}
          </div>
        ))}
      </div>
    );
  }

  // Failed
  if (status === "failed") {
    return (
      <div data-testid={`ship-status-${taskId}`} style={{
        padding: "10px 12px",
        background: "rgba(255,107,107,0.06)",
        border: "1px solid rgba(255,107,107,0.3)",
        borderRadius: 4,
        fontSize: 12, color: "var(--danger)",
        fontFamily: "'JetBrains Mono', monospace",
        maxWidth: 460,
      }}>
        <div>❌ Task failed · <span style={{ opacity: 0.7 }}>{taskId}</span></div>
        {task.error && (
          <div style={{ marginTop: 6, fontSize: 11, color: "var(--text-dim)", whiteSpace: "pre-wrap" }}>
            {String(task.error).slice(0, 240)}
          </div>
        )}
      </div>
    );
  }

  // Success
  const sha = task.commit_sha;
  const owner = project?.github_owner;
  const repo = project?.github_repo;
  const commitUrl = sha && owner && repo
    ? `https://github.com/${owner}/${repo}/commit/${sha}`
    : null;

  // Extract changed file paths from the worker's steps (lines starting with "💾")
  const files = (task.steps || [])
    .filter((s) => (s.step || "").startsWith("💾"))
    .map((s) => s.step.replace(/^💾\s*/, ""));

  const reverted = !!task.rollback_sha;

  return (
    <div data-testid={`ship-status-${taskId}`} style={{
      padding: "12px 14px",
      background: reverted ? "var(--panel-2)" : "rgba(0, 230, 118, 0.05)",
      border: `1px solid ${reverted ? "var(--border)" : "rgba(0,230,118,0.3)"}`,
      borderRadius: 4,
      fontSize: 12,
      maxWidth: 460,
    }}>
      <div style={{
        display: "flex", alignItems: "center", gap: 8,
        fontFamily: "'JetBrains Mono', monospace",
        color: reverted ? "var(--text-dim)" : "var(--ok)",
        fontWeight: 600,
      }}>
        {reverted ? "↩︎ Reverted" : "✅ Pushed"}
        {sha && (commitUrl ? (
          <a href={commitUrl} target="_blank" rel="noreferrer"
             data-testid={`ship-commit-link-${taskId}`}
             style={{ color: "var(--accent-2)", textDecoration: "none" }}
             title="View commit on GitHub">
            {sha} <ExternalLink size={10} style={{ display: "inline" }} />
          </a>
        ) : <span>{sha}</span>)}
        {task.rollback_sha && (
          <span style={{ color: "var(--text-faint)", marginLeft: 4 }}>
            → {task.rollback_sha}
          </span>
        )}
      </div>

      {task.result && (
        <div style={{ marginTop: 6, color: "var(--text)" }}>{task.result}</div>
      )}

      {files.length > 0 && (
        <div style={{ marginTop: 8, fontSize: 11, color: "var(--text-dim)" }}>
          <div style={{ marginBottom: 2, color: "var(--text-faint)", letterSpacing: "0.05em" }}>
            FILES CHANGED
          </div>
          {files.slice(0, 4).map((f, i) => (
            <div key={i} style={{ fontFamily: "'JetBrains Mono', monospace" }}>
              • {f}
            </div>
          ))}
          {files.length > 4 && (
            <div style={{ color: "var(--text-faint)" }}>+ {files.length - 4} more</div>
          )}
        </div>
      )}

      <div style={{ display: "flex", gap: 8, marginTop: 10, flexWrap: "wrap" }}>
        {commitUrl && (
          <a href={commitUrl} target="_blank" rel="noreferrer"
             className="btn-ghost"
             style={{ padding: "5px 10px", fontSize: 11, textDecoration: "none" }}>
            <ExternalLink size={11} /> View diff
          </a>
        )}
        {!reverted && !rbRunning && (
          <button
            data-testid={`ship-rollback-${taskId}`}
            onClick={onRollback}
            className="btn-ghost"
            style={{
              padding: "5px 10px", fontSize: 11,
              borderColor: "rgba(255,107,107,0.3)",
              color: "var(--danger)",
            }}
          >
            <Undo2 size={11} /> Rollback
          </button>
        )}
        {rbRunning && (
          <span style={{ fontSize: 11, color: "var(--accent-2)",
                         display: "inline-flex", alignItems: "center", gap: 6 }}>
            <Loader2 size={11} style={{ animation: "spin 1s linear infinite" }} /> reverting…
          </span>
        )}
      </div>
    </div>
  );
}


function MessageBubble({ idx, dbTurnIndex, m, onRegenerate, sessionId, activeProject, exhausted }) {
  const [copied, setCopied] = useState(false);
  const [vote, setVote] = useState(m.feedback?.vote || null);
  const [hover, setHover] = useState(false);
  const [shipState, setShipState] = useState({
    status: m.shipped_task_id ? "shipped" : "idle",  // restore from history
    taskId: m.shipped_task_id || null,
    error: null,
  });
  // Live task progress (polled while shipped task is in-flight)
  const [taskInfo, setTaskInfo] = useState(null);

  // Poll the CTO task while it's in progress, until done/failed
  useEffect(() => {
    const tid = shipState.taskId;
    if (!tid) return;
    let cancelled = false;
    const TERMINAL = new Set(["done", "failed"]);
    async function tick() {
      try {
        const r = await api.get(`/cto/tasks/${tid}`);
        const t = r.data?.task || null;
        if (cancelled || !t) return;
        setTaskInfo(t);
        if (!TERMINAL.has(t.status)) {
          setTimeout(tick, 2000);
        }
      } catch {
        /* keep last known state */
      }
    }
    tick();
    return () => { cancelled = true; };
  }, [shipState.taskId]);

  async function rollbackShipped() {
    const tid = shipState.taskId;
    if (!tid) return;
    const ok1 = window.confirm(
      "Rollback this commit?\nA new revert commit will be pushed " +
      "(history preserved, no force-push)."
    );
    if (!ok1) return;
    const ok2 = window.confirm("Are you sure? This pushes to your repo right now.");
    if (!ok2) return;
    try {
      await api.post(`/cto/tasks/${tid}/rollback`, { confirm: "ROLLBACK" });
      toast({ message: "Rollback queued", kind: "info" });
    } catch (e) {
      toast({
        message: e?.response?.data?.detail || "Rollback failed to start",
        kind: "error",
      });
    }
  }

  function copyText() {
    if (!m.content) return;
    navigator.clipboard.writeText(m.content);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }

  async function sendVote(v) {
    if (!sessionId) return;
    const next = vote === v ? null : v; // toggle off if same
    setVote(next);
    if (!next) return;
    try {
      await api.post("/chat/feedback", {
        session_id: sessionId, turn_index: idx, vote: next,
      });
      toast({ message: next === "up" ? "Thanks — noted 👍" : "Got it — we'll do better", kind: "info", duration: 1800 });
    } catch {
      /* ignore */
    }
  }

  const showActions = m.role === "assistant" && !m.streaming && m.provider !== "system" && !m.error;
  const showUserCopy = m.role === "user" && !!m.content;
  // Detect ```aurem-handoff fence → render one-click Ship via CTO button
  const handoffBrief = showActions ? extractHandoffBrief(m.content) : null;
  const canShip = !!(handoffBrief && activeProject?.project_id && !exhausted);

  async function shipViaCTO() {
    if (!canShip || shipState.status === "shipping" || shipState.status === "shipped") return;
    // One UI confirm is enough; the brief is already shown inline above.
    const ok = window.confirm(
      `Ship via CTO will:\n\n` +
      `1. Clone ${activeProject.github_owner}/${activeProject.github_repo}@${activeProject.branch}\n` +
      `2. Apply the AI-generated changes\n` +
      `3. git commit + push to your repo\n\n` +
      `Rollback is available once the task completes if anything looks wrong.\n\n` +
      `Proceed?`
    );
    if (!ok) return;
    setShipState({ status: "shipping", taskId: null, error: null });
    try {
      const r = await api.post("/cto/tasks/submit", {
        project_id: activeProject.project_id,
        task: handoffBrief,
        files: [],
        context: `from chat session ${sessionId}, turn ${idx}`,
      });
      const taskId = r.data?.task_id || null;
      setShipState({ status: "shipped", taskId, error: null });
      // Persist on the turn so refresh/rejoin doesn't show the button again
      if (taskId && sessionId) {
        try {
          await api.post("/chat/turn/shipped", {
            session_id: sessionId,
            // Iter 34: use DB index (skips WELCOME / system messages) —
            // not the rendered messages array position. Falls back to
            // idx for safety on legacy history payloads.
            turn_index: typeof dbTurnIndex === "number" ? dbTurnIndex : idx,
            task_id: taskId,
          });
        } catch { /* non-fatal */ }
      }
      toast({
        message: taskId ? `Task queued — ${taskId}` : "Task queued",
        kind: "success", duration: 3000,
      });
    } catch (e) {
      const msg = e?.response?.data?.detail || e?.message || "Submit failed";
      setShipState({ status: "error", taskId: null, error: msg });
      toast({ message: msg, kind: "error" });
    }
  }

  return (
    <div
      data-testid={`chat-msg-${m.role}-${idx}`}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
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
      <div style={{ maxWidth: "80%", position: "relative" }}>
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
          {/* Floating copy button for USER bubbles — appears on hover only */}
          {showUserCopy && (
            <button
              data-testid={`copy-user-${idx}`}
              onClick={copyText}
              title={copied ? "Copied!" : "Copy"}
              aria-label="Copy message"
              style={{
                position: "absolute",
                top: 6, left: 6,             // bubble is right-aligned for user, so left edge is "outside-inner"
                width: 24, height: 24,
                display: "inline-flex",
                alignItems: "center", justifyContent: "center",
                background: "var(--panel-2)",
                border: "1px solid var(--border)",
                borderRadius: 4,
                color: copied ? "var(--ok)" : "var(--text-dim)",
                cursor: "pointer",
                opacity: hover ? 1 : 0,
                transition: "opacity 0.15s ease",
                pointerEvents: hover ? "auto" : "none",
                padding: 0,
              }}
            >
              <CopyIcon size={12} />
            </button>
          )}
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
              fontFamily: "'JetBrains Mono', monospace",
            }}>
              <Loader2 size={12} style={{ animation: "spin 1s linear infinite" }} />
              {/* iter 35/36: live elapsed + activity label so the user
                  always sees WHAT AUREM is doing, not just THAT it is */}
              <span>
                {m.activity || "thinking"}
                {typeof m.elapsedS === "number"
                  ? ` · ${m.elapsedS.toFixed(1)}s`
                  : "…"}
              </span>
            </span>
          )}
          {m.streaming && m.content && (
            <>
              <span data-testid="chat-cursor" style={{
                display: "inline-block", width: 7, height: 14,
                marginLeft: 2, background: "var(--accent-2)",
                verticalAlign: "middle",
                animation: "blink 0.9s steps(1) infinite",
              }} />
              {typeof m.elapsedS === "number" && m.elapsedS > 1.5 && (
                <div style={{ marginTop: 6, fontSize: 10,
                              color: "var(--text-faint)",
                              fontFamily: "'JetBrains Mono', monospace" }}>
                  · {m.elapsedS.toFixed(1)}s
                </div>
              )}
            </>
          )}
          {m.provider && m.provider !== "system" && !m.streaming && m.maxxMode && (
            <div style={{
              marginTop: 8, fontSize: 10,
              fontFamily: "'JetBrains Mono', monospace",
              color: "var(--text-faint)", letterSpacing: "0.1em",
              display: "inline-flex", alignItems: "center", gap: 6,
            }}>
              <Zap size={10} style={{ color: "var(--accent-2)" }} /> maxx
            </div>
          )}
        </div>

        {/* Action row for assistant bubbles — copy / 👍 / 👎 — visible on hover */}
        {showActions && (
          <div data-testid={`msg-actions-${idx}`} style={{
            display: "flex", gap: 4, marginTop: 6,
            paddingLeft: 4,
            opacity: hover ? 1 : 0,
            transition: "opacity 0.15s ease",
            pointerEvents: hover ? "auto" : "none",
          }}>
            <ActionBtn testid={`copy-${idx}`} title={copied ? "Copied!" : "Copy"} onClick={copyText} Icon={CopyIcon} active={copied} />
            <ActionBtn testid={`thumbs-up-${idx}`} title="Good reply" onClick={() => sendVote("up")} Icon={ThumbsUp} active={vote === "up"} color="var(--ok)" />
            <ActionBtn testid={`thumbs-down-${idx}`} title="Bad reply — we'll refine" onClick={() => sendVote("down")} Icon={ThumbsDown} active={vote === "down"} color="var(--danger)" />
          </div>
        )}

        {/* Ship via CTO button — only when an aurem-handoff brief is present */}
        {handoffBrief && (
          <div data-testid={`ship-cto-row-${idx}`} style={{
            marginTop: 10, paddingLeft: 4,
            display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap",
          }}>
            {!canShip ? (
              <div style={{ fontSize: 11, color: "var(--text-faint)", fontStyle: "italic" }}>
                {exhausted
                  ? "🚫 Tokens exhausted — upgrade your plan to ship via CTO."
                  : "Switch to a connected project to enable Ship via CTO."}
              </div>
            ) : shipState.status === "shipped" ? (
              <ShipStatusCard
                taskId={shipState.taskId}
                task={taskInfo}
                project={activeProject}
                onRollback={rollbackShipped}
              />
            ) : (
              <button
                data-testid={`ship-cto-btn-${idx}`}
                onClick={shipViaCTO}
                disabled={shipState.status === "shipping"}
                style={{
                  display: "inline-flex", alignItems: "center", gap: 8,
                  padding: "8px 14px",
                  background: shipState.status === "shipping"
                    ? "var(--panel-2)"
                    : "var(--accent-2)",
                  color: shipState.status === "shipping"
                    ? "var(--text-dim)"
                    : "var(--bg)",
                  border: "1px solid var(--accent-2)",
                  borderRadius: 4,
                  fontSize: 12, fontWeight: 600,
                  fontFamily: "'JetBrains Mono', monospace",
                  letterSpacing: "0.05em",
                  cursor: shipState.status === "shipping" ? "wait" : "pointer",
                }}
                title={`Ship to ${activeProject.github_owner}/${activeProject.github_repo}@${activeProject.branch}`}
              >
                {shipState.status === "shipping"
                  ? (<><Loader2 size={12} style={{ animation: "spin 1s linear infinite" }} /> shipping…</>)
                  : (<>🚀 Ship via CTO</>)}
              </button>
            )}
            {shipState.status === "error" && (
              <span style={{ fontSize: 11, color: "var(--danger)" }}>
                {shipState.error}
              </span>
            )}
          </div>
        )}

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
