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
  Send, Loader2, Square, Paperclip, Github, Zap,
  Eye,
} from "lucide-react";
import { api, streamChat } from "../lib/api";
import { toast } from "./Toast";
import SaveToGithubDialog from "./SaveToGithubDialog";
import PreviewPanel from "./PreviewPanel";
import TemperatureBadge from "./TemperatureBadge";
import { useF12Errors, detectMode, F12Badge, ModePill } from "./ChatPanelF12";
import MessageBubble from "./MessageBubble";

const WELCOME = {
  role: "assistant",
  content:
    "I'm AUREM CTO — your sovereign engineering co-pilot. Ask me to plan a feature, write code, or debug an error. What are we shipping today?",
  provider: "system",
};

const MAX_FILE_BYTES = 25 * 1024 * 1024; // 25 MB (server enforces same cap)
const TEXT_FAST_PATH_BYTES = 50 * 1024;  // <=50KB text → skip server roundtrip
const MAXX_KEY = "aurem_maxx_mode";
const AGENT_KEY = "aurem_chat_agent";
const PREVIEW_KEY = "aurem_preview_open";

const CODE_BLOCK_RE = /```(\w+)?\n([\s\S]*?)```/g;

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
  // Iter 38: agent selector. Persisted in localStorage so the user keeps
  // their pick across reloads. Default "auto" = our existing routing.
  const [agent, setAgent] = useState(
    () => localStorage.getItem(AGENT_KEY) || "auto"
  );
  const [agents, setAgents] = useState([
    { id: "auto", label: "AUREM", desc: "Auto-routes Claude/DeepSeek" },
  ]);
  useEffect(() => { localStorage.setItem(AGENT_KEY, agent); }, [agent]);

  // Iter 53 — post-commit wrap-up. When a Mode C task hits a terminal
  // status (done|failed), ask the backend to generate the closing
  // message ("what changed, did it resolve, how to verify") and append
  // it as a normal assistant message. The endpoint is idempotent, but
  // we also dedupe client-side via `followupFiredRef` so a rapid
  // re-poll never double-appends.
  const followupFiredRef = useRef(new Set());
  const triggerTaskFollowup = useCallback(async (taskId) => {
    if (!taskId || !sessionId) return;
    if (followupFiredRef.current.has(taskId)) return;
    followupFiredRef.current.add(taskId);
    try {
      const res = await api.post("/chat/task-followup", {
        session_id: sessionId, task_id: taskId,
      });
      const text = res?.data?.message;
      if (!text) return;
      setMessages((msgs) => {
        // Don't double-append if the same wrap-up is already in view
        // (e.g. user reloaded mid-task and history already has it).
        if (msgs.some((mm) => mm.kind === "task_followup"
                              && mm.task_id === taskId)) {
          return msgs;
        }
        return [
          ...msgs,
          {
            role: "assistant",
            content: text,
            provider: "ora",
            kind: "task_followup",
            task_id: taskId,
          },
        ];
      });
    } catch {
      // Silent failure — the ShipStatusCard already shows the user
      // commit details; the wrap-up is a bonus, not load-bearing.
      followupFiredRef.current.delete(taskId);
    }
  }, [sessionId]);
  useEffect(() => {
    // Load the list of agents this user can choose from. Founders see
    // ORA, regular users don't. Falls back silently on error.
    api.get("/chat/agents/list").then((r) => {
      if (r.data?.agents?.length) setAgents(r.data.agents);
    }).catch(() => {});
  }, []);

  // ora:prefill — fired by BrainDump's "Show diff →" buttons and any
  // other surface that wants to drop the user into chat with a primed
  // prompt. Listener lives here because ChatPanel owns the input state.
  useEffect(() => {
    const handler = (e) => {
      const msg = e?.detail?.message;
      if (typeof msg === "string" && msg.trim()) setInput(msg);
    };
    window.addEventListener("ora:prefill", handler);
    return () => window.removeEventListener("ora:prefill", handler);
  }, []);

  // Iter 42 — F12 error capture + mode classifier
  const f12 = useF12Errors();
  const [detectedMode, setDetectedMode] = useState(null);
  const [serverMode,   setServerMode]   = useState(null);
  // Pattern #4 follow-through — when the classifier returns
  // needs_confirm=true, we DON'T pause the stream (would require
  // round-trip protocol changes); instead we surface a non-blocking
  // banner so the user can rephrase next time if ORA picked the wrong
  // mode. Cleared automatically when a new prompt is sent.
  const [modeAmbiguous, setModeAmbiguous] = useState(null);
  // Iter 73 — ORA-detected ops request → deep-link to /admin/ops instead
  // of letting the model fabricate bash. Cleared on next prompt.
  const [opsRedirect, setOpsRedirect] = useState(null);
  const lastF12PayloadRef = useRef(null);
  const endRef = useRef(null);
  const abortRef = useRef(null);
  const fileInputRef = useRef(null);
  const taRef = useRef(null);

  // Attached files (separate from textarea content). Each:
  // {id, name, size, kind: "image"|"doc", status: "uploading"|"ready"|"error",
  //  markdown: string, error?: string}
  const [attachments, setAttachments] = useState([]);
  const [dragOver, setDragOver] = useState(false);

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

    // Track each attachment so the user sees a visible pill (with
    // remove "×" + size) and the body is sent only on submit. Previous
    // version dumped raw markdown into the textarea — for images that
    // failed conversion the textarea stayed blank and the user thought
    // "upload broken". Now every attachment gets a pill regardless of
    // whether parsing succeeded; failed parses still send the file
    // metadata so the LLM knows something was attached.
    const newAttachments = [];
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
      const isImage = f.type?.startsWith("image/")
        || ["png","jpg","jpeg","webp","gif","bmp"].includes(ext);

      // Optimistic pill so the user sees the file immediately
      const pillId = `att_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`;
      setAttachments((arr) => [...arr, {
        id: pillId,
        name: f.name,
        size: f.size,
        kind: isImage ? "image" : "doc",
        status: "uploading",
        markdown: "",
      }]);

      try {
        let mdBody;
        if (isSmallText) {
          const text = await f.text();
          mdBody = `[File: ${f.name}]\n\`\`\`${ext}\n${text}\n\`\`\``;
        } else {
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
          const kindLabel = d.kind === "image" ? "🖼️" : "📄";
          mdBody =
            `[${kindLabel} ${d.filename || f.name}` +
            ` · ${(d.original_size / 1024).toFixed(1)} KB` +
            ` → ${(d.md_size / 1024).toFixed(1)} KB${truncNote}]\n\n` +
            d.markdown;
        }
        // Patch the existing pill in place — status → ready, body filled.
        setAttachments((arr) => arr.map((a) =>
          a.id === pillId
            ? { ...a, status: "ready", markdown: mdBody }
            : a
        ));
        newAttachments.push({ name: f.name, ok: true });
      } catch (e) {
        const msg =
          e?.response?.data?.detail ||
          e?.message ||
          "Couldn't read file.";
        // Don't strip the pill — keep it visible so the user can
        // remove it manually and knows something went wrong. Also
        // send a tiny stub to the LLM so it knows an attachment was
        // attempted (this is the "fix from routes" — never silent).
        const stub = `[Attached but not parsable: ${f.name} — ${msg}]`;
        setAttachments((arr) => arr.map((a) =>
          a.id === pillId
            ? { ...a, status: "error", error: msg, markdown: stub }
            : a
        ));
        toast({ message: `${f.name}: ${msg}`, kind: "error" });
      }
    }
    if (newAttachments.filter((a) => a.ok).length) {
      toast({
        message: `Attached ${newAttachments.filter((a) => a.ok).length} file(s).`,
        kind: "success",
      });
    }
  }

  async function send(e) {
    e?.preventDefault();
    const text = input.trim();
    // Pull ready attachments (uploading ones get skipped silently —
    // user can re-send if they were too slow). Also keep errored ones
    // (their stub markdown tells the LLM something was attempted).
    const readyAttachments = attachments.filter(
      (a) => a.status === "ready" || a.status === "error"
    );
    // Allow send when EITHER text OR attachments exist — previous gate
    // demanded text, which is why an image-only chat silently refused.
    if ((!text && !readyAttachments.length) || busy || !sessionId) return;
    setInput("");
    // Clear any leftover ambiguous-mode banner from the previous turn —
    // a new prompt = new classification.
    setModeAmbiguous(null);
    setOpsRedirect(null);
    // Clear all attachments on send (uploading ones go too — UX rule:
    // hit Send → bubble shipped; what didn't make it can be re-attached).
    setAttachments([]);

    // Build the prompt: attachments first (so the LLM has context
    // before reading the user's question), then the user's text.
    const attachmentBlock = readyAttachments
      .map((a) => a.markdown)
      .filter(Boolean)
      .join("\n\n");
    const userBody = text || "(see attached files)";
    const bodyParts = [];
    if (attachmentBlock) bodyParts.push(attachmentBlock);
    bodyParts.push(userBody);
    const finalText = bodyParts.join("\n\n");
    // Auto-augment prompt with active project context so the LLM stays scoped.
    const finalPrompt = activeProject
      ? `[Working on project: ${activeProject.name} — repo ${activeProject.github_owner}/${activeProject.github_repo}@${activeProject.branch}]\n\n${finalText}`
      : finalText;
    // Show what the user actually typed PLUS a small attachment summary
    // so the bubble doesn't dump 60KB of markdown on screen.
    const displayContent = readyAttachments.length
      ? `${text || ""}${text ? "\n\n" : ""}_📎 ${readyAttachments.length} attachment${
          readyAttachments.length > 1 ? "s" : ""}: ${
          readyAttachments.map((a) => a.name).join(", ")}_`
      : text;
    setMessages((m) => [
      ...m,
      { role: "user", content: displayContent },
      { role: "assistant", content: "", streaming: true, maxxMode },
    ]);
    setBusy(true);

    const ctrl = new AbortController();
    abortRef.current = ctrl;
    let providerSeen = "";

    // Iter 42 — drain captured F12 errors at send time. The store self-clears
    // after flush() so we don't double-report old errors on subsequent sends.
    const f12Payload = (typeof window !== "undefined" && window.__auremF12)
      ? window.__auremF12.flush()
      : null;
    lastF12PayloadRef.current = f12Payload;

    await streamChat({
      prompt: finalPrompt,
      projectId: activeProject?.project_id || null,
      sessionId,
      maxToolIters: 2,
      maxxMode,
      agent,                       // iter 38: selector value
      f12Payload,                  // iter 42: console/network/stack errors
      signal: ctrl.signal,
      onMode: (m) => {
        // Backend now sends a full payload: {type:"mode", mode, confidence,
        // scores, needs_confirm}. Older flows still pass a bare string.
        if (typeof m === "string") {
          setServerMode(m);
          return;
        }
        setServerMode(m.mode);
        if (m.needs_confirm) {
          setModeAmbiguous({
            detected:   m.mode,
            confidence: m.confidence,
            scores:     m.scores,
          });
        } else {
          setModeAmbiguous(null);
        }
      },
      onOpsRedirect: (m) => setOpsRedirect(m),
      // Iter 51 — SSE Task Progress Streamer. Mode D→C (and any auto
      // handoff) emits this BEFORE content streams. Pin the task_id on
      // the streaming assistant bubble so the ShipStatusCard renders
      // inline and polls live progress — user never has to leave chat.
      onTaskHandoff: (p) => {
        setMessages((msgs) => {
          const copy = msgs.slice();
          const last = copy[copy.length - 1];
          if (last && last.role === "assistant") {
            copy[copy.length - 1] = {
              ...last,
              shipped_task_id: p.task_id,
              auto_handoff_project_id: p.project_id || null,
              auto_handoff_source: p.source || "auto",
            };
          }
          return copy;
        });
        // Fire a global hook so the right-side <PreviewPane /> can latch
        // onto the new task without prop-drilling through Shell.
        try {
          window.dispatchEvent(new CustomEvent("aurem:shipped", {
            detail: { task_id: p.task_id, project_id: p.project_id },
          }));
        } catch { /* ignore */ }
      },
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
              council: !!(d.council || d.provider === "mode-b-council"),
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
        className="glass-pane"
        style={{
          display: "flex", flexDirection: "column",
          flex: previewOpen ? "0 0 50%" : "1 1 auto",
          minWidth: 0,
          height: "100%",
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
              onTaskCompleted={triggerTaskFollowup}
            />
          );
        })}
        <div ref={endRef} />
      </div>

      {/* Ops redirect banner — when ORA detects "restart supervisor"
          / "free disk space" etc, link to the runbook page instead of
          letting the model fabricate bash commands. */}
      {opsRedirect && (
        <div data-testid="ops-redirect-banner" style={{
          margin: "0 16px 8px",
          padding: "10px 14px",
          background: "var(--accent-soft)",
          border: "1px solid var(--accent)",
          borderRadius: 6, fontSize: 12,
          display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap",
        }}>
          <span style={{ color: "var(--text-dim)", flex: 1, minWidth: 200 }}>
            {opsRedirect.reason || "This is a server operation that runs on your infra."}
          </span>
          <a
            data-testid="ops-redirect-link"
            href={opsRedirect.url || "/admin/ops"}
            target="_blank"
            rel="noreferrer"
            className="btn-primary"
            style={{ padding: "4px 12px", fontSize: 11,
                      textDecoration: "none" }}
          >
            Open Ops Recipes →
          </a>
          <button
            type="button"
            data-testid="ops-redirect-dismiss"
            onClick={() => setOpsRedirect(null)}
            className="btn-ghost"
            style={{ padding: "4px 10px", fontSize: 11 }}
          >
            dismiss
          </button>
        </div>
      )}

      {/* Ambiguous-mode disambiguation banner — non-blocking. Sits between
          the message list and the composer so it's the last thing the
          user sees before typing. Auto-cleared on next submit. */}
      {modeAmbiguous && (
        <div data-testid="mode-ambiguous-banner" style={{
          margin: "0 16px 8px",
          padding: "10px 14px",
          background: "var(--accent-soft)",
          border: "1px solid var(--border-strong)",
          borderRadius: 6, fontSize: 12,
          display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap",
        }}>
          <span style={{ color: "var(--text-dim)", flex: 1, minWidth: 200 }}>
            ORA picked{" "}
            <strong style={{ color: "var(--accent-2)" }}
                    data-testid="mode-ambiguous-detected">
              Mode {modeAmbiguous.detected}
            </strong>{" "}
            ({Math.round(modeAmbiguous.confidence * 100)}% confident). If
            that's wrong, cancel and rephrase — e.g. start with
            "debug …" for D, "add …" for C, "should I …" for B.
          </span>
          <button
            type="button"
            data-testid="mode-ambiguous-ok"
            onClick={() => setModeAmbiguous(null)}
            className="btn-primary"
            style={{ padding: "4px 12px", fontSize: 11 }}
          >
            Got it
          </button>
        </div>
      )}

      <form
        onSubmit={send}
        className="glass-composer"
        // Iter 59 — drag-and-drop attachment support directly on the
        // composer. dragOver state drives the visual cue.
        onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragOver(false);
          if (e.dataTransfer?.files?.length) handleFiles(e.dataTransfer.files);
        }}
        style={{
          padding: 14,
          display: "flex", flexDirection: "column", gap: 8,
          outline: dragOver ? "2px dashed var(--accent-2)" : "none",
          outlineOffset: -8,
          transition: "outline 120ms ease",
        }}
      >
        <TokenBanner usage={usage} />

        {/* Iter 59 — Attachment pills. Always visible while files are
            either uploading, ready, or errored. User can remove any pill
            with the × button. Failed pills stay visible (status="error")
            so the user knows what was attempted; their stub markdown is
            still sent to the LLM so the chat never silently drops the
            attempt. */}
        {attachments.length > 0 && (
          <div
            data-testid="chat-attachments-row"
            style={{
              display: "flex", flexWrap: "wrap", gap: 6,
              maxHeight: 120, overflowY: "auto",
            }}
          >
            {attachments.map((a) => {
              const colour =
                a.status === "uploading" ? "var(--text-faint)"
                : a.status === "error"   ? "var(--danger)"
                : "var(--accent-2)";
              const icon = a.kind === "image" ? "🖼️" : "📎";
              return (
                <span
                  key={a.id}
                  data-testid={`chat-attach-pill-${a.status}`}
                  title={a.error || `${a.name} · ${(a.size/1024).toFixed(1)} KB`}
                  style={{
                    display: "inline-flex", alignItems: "center", gap: 6,
                    padding: "4px 8px 4px 10px", fontSize: 11,
                    fontFamily: "'JetBrains Mono', monospace",
                    color: colour,
                    background: "rgba(255,255,255,0.04)",
                    border: `1px solid ${colour}55`,
                    borderRadius: 999,
                    maxWidth: 240,
                  }}
                >
                  <span>{icon}</span>
                  <span style={{
                    overflow: "hidden", textOverflow: "ellipsis",
                    whiteSpace: "nowrap", maxWidth: 160,
                  }}>{a.name}</span>
                  {a.status === "uploading"
                    ? <Loader2 size={11} className="spin" style={{ opacity: 0.6 }} />
                    : (
                      <button
                        type="button"
                        data-testid={`chat-attach-remove-${a.id}`}
                        onClick={() => setAttachments((arr) => arr.filter((x) => x.id !== a.id))}
                        style={{
                          background: "none", border: "none",
                          color: colour, cursor: "pointer",
                          padding: 0, lineHeight: 1, fontSize: 14,
                        }}
                        aria-label={`Remove ${a.name}`}
                      >×</button>
                    )}
                </span>
              );
            })}
          </div>
        )}

        {/* Iter 42 — Mode pill + F12 error badge above the input */}
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <ModePill mode={detectedMode || (serverMode ? { mode: serverMode, color: "#6b7280", label: "Mode " + serverMode } : null)} />
          <F12Badge
            errorCount={f12.errorCount}
            hasErrors={f12.hasErrors}
            onSendToORA={() => {
              const payload = f12.flush();
              const cc = payload?.console_errors?.length || 0;
              const nc = payload?.network_errors?.length || 0;
              const msg = `F12 errors captured (${cc} console, ${nc} network). Please diagnose.`;
              setInput(msg);
              lastF12PayloadRef.current = payload;
              // Trigger send on next tick
              setTimeout(() => {
                const form = taRef.current && taRef.current.form;
                if (form) form.requestSubmit();
              }, 50);
            }}
          />
        </div>
        {/* Input on top */}
        <textarea
          ref={taRef}
          data-testid="chat-input"
          className="input"
          value={input}
          onChange={(e) => {
            setInput(e.target.value);
            setDetectedMode(detectMode(e.target.value));
          }}
          onKeyDown={onKeyDown}
          onPaste={(e) => {
            // Iter 59 — paste-to-attach. If the clipboard contains any
            // image (Cmd-V from a screenshot tool), capture them as
            // attachments instead of letting them stringify into text.
            const items = e.clipboardData?.items || [];
            const files = [];
            for (const it of items) {
              if (it.kind === "file") {
                const f = it.getAsFile();
                if (f) files.push(f);
              }
            }
            if (files.length) {
              e.preventDefault();
              handleFiles(files);
            }
          }}
          placeholder="Ask AUREM CTO to plan, build, debug…  (Enter to send, Shift+Enter for newline. Drop / paste files anytime.)"
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
          {/* Iter 38: agent selector — appears only when >1 agent is
              available (i.e. founders see ORA, customers see nothing). */}
          {agents.length > 1 && (
            <select
              data-testid="chat-agent-select"
              value={agent}
              onChange={(e) => setAgent(e.target.value)}
              title="Pick which agent answers this chat"
              style={{
                fontSize: 11,
                fontFamily: "'JetBrains Mono', monospace",
                padding: "4px 8px",
                background: "var(--bg)",
                color: "var(--text)",
                border: "1px solid var(--border)",
                borderRadius: 6,
                cursor: "pointer",
              }}
            >
              {agents.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.label}
                </option>
              ))}
            </select>
          )}
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

