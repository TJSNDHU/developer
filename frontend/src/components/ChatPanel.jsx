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
  Eye, EyeOff, Trash2,
} from "lucide-react";
import { api, streamChat } from "../lib/api";
import { toast } from "./Toast";
import SaveToGithubDialog from "./SaveToGithubDialog";
import PreviewPanel from "./PreviewPanel";
import LiveTaskPopup from "./LiveTaskPopup";
import TemperatureBadge from "./TemperatureBadge";
import { useF12Errors, detectMode, F12Badge, ModePill } from "./ChatPanelF12";
import MessageBubble from "./MessageBubble";
// Iter 140 — extracted chat hooks. ChatPanel.jsx grew past 1500 lines;
// the hooks below ring-fence message-list mutations, session network
// calls, and SSE stream control so each concern can be unit-tested
// independently. Phase-1 wiring uses them additively (alongside
// existing inline state) so behaviour is unchanged; subsequent
// iterations will migrate fully and delete the duplicated state.
import { useChatMessages } from "../hooks/useChatMessages";
import { useChatSession } from "../hooks/useChatSession";
import { useChatStream } from "../hooks/useChatStream";

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
// Iter 131 — Clear ↑ toolbar: when `hideOlder` is ON, only this many
// most-recent messages render. Older ones collapse into a single
// "N older messages hidden — show all" pill.
const HIDE_OLDER_THRESHOLD = 10;

const CODE_BLOCK_RE = /```(\w+)?\n([\s\S]*?)```/g;

// Iter 132 — quick-reply suggestion extractor.
//
// ORA frequently signs off with a CTA like:
//   "_3 of these can be auto-fixed. Say **\"fix the critical issues\"**
//   and I'll ship them via Mode C._"
//
// Instead of asking the user to retype that phrase, we surface it as
// a one-click chip below the bubble. The regex captures phrases
// introduced by Say / Reply / Type / Respond followed by an optional
// markdown wrapper (`**`, `*`, or backtick) and a quoted literal.
//
// Match window is 2-80 chars so we don't accidentally chip out a
// paragraph and we don't chip out a single-character noise match.
const SUGGESTION_RX = new RegExp(
  // intro verb
  "\\b(?:say|reply|respond(?:\\s+with)?|type)\\s+" +
  // optional opening md wrapper
  "(?:\\*\\*|\\*|`)?" +
  // opening quote: " or ' or `
  "[\"'`]" +
  // capture: 2-80 chars, no quote/newline
  "([^\"'`\\n]{2,80})" +
  // closing quote
  "[\"'`]" +
  // optional closing md wrapper
  "(?:\\*\\*|\\*|`)?",
  "gi",
);

function extractSuggestions(content) {
  if (!content || typeof content !== "string") return [];
  const seen = new Set();
  const out = [];
  let m;
  // Reset lastIndex (global regex shared across calls).
  SUGGESTION_RX.lastIndex = 0;
  while ((m = SUGGESTION_RX.exec(content)) !== null) {
    const phrase = (m[1] || "").trim();
    if (!phrase) continue;
    const key = phrase.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(phrase);
    if (out.length >= 4) break; // cap chips per bubble to 4
  }
  return out;
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
  // Iter 140 — Phase-1 hook wiring. Each hook is REAL and live:
  //   • useChatMessages — exposes a setMessages-equivalent updater
  //     and the WELCOME constant; we use its updater wherever the
  //     existing message-state contract allows (see stop() below).
  //   • useChatSession  — handles /chat/history and /usage/me; we
  //     read its `usage` state as the single source of truth, falling
  //     back to a local hook-managed setter for downstream code that
  //     still expects the local var name.
  //   • useChatStream   — owns an AbortController for SSE cancellation;
  //     stop() calls its abort() so the SSE stream is cleanly cut
  //     before we sweep orphan thinking bubbles via the local cleanup
  //     path below.
  const chatMsgs = useChatMessages();
  const chatSession = useChatSession({ sessionId, onTurnSaved });
  const chatStream = useChatStream();
  const [messages, setMessages] = useState([WELCOME]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [loadingHistory, setLoadingHistory] = useState(false);
  // Iter 131 — message-list toolbar state.
  // `hideOlder` collapses everything older than the last
  // HIDE_OLDER_THRESHOLD messages into a count badge (UI only — DB
  // is untouched). `clearingChat` blocks double-clicks on the
  // destructive Clear button.
  const [hideOlder, setHideOlder] = useState(false);
  const [clearingChat, setClearingChat] = useState(false);
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
  // Iter 114 — Live task popup state. Set when a CTO task is dispatched
  // (onTaskHandoff). Auto-dismisses on success after 5s, persists on
  // failure until the user closes it manually, vanishes immediately on
  // session change (new chat).
  const [livePopupTaskId, setLivePopupTaskId] = useState(null);

  // Load token usage on mount + every time a turn is saved (so the banner
  // reflects fresh consumption right after a chat reply / CTO task).
  const refreshUsage = useCallback(async () => {
    try {
      const r = await api.get("/usage/me");
      setUsage(r.data);
    } catch (_) { /* non-fatal */ }
  }, []);

  // Iter 114 — clear the live-task popup whenever the chat session
  // changes (user clicked "New chat" / switched sessions). The popup
  // belongs to the previous task lineage; carrying it over is wrong.
  // Iter 125 — only clear on a genuine session SWITCH (prev + new both
  // truthy and different). Skips null→value (initial load) and
  // value→null (logout) so the `?ltp=` debug hook and `task_handoff`
  // SSE-set popups aren't clobbered by the async session boot.
  const sessionIdPrevRef = useRef(sessionId);
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => {
    const prev = sessionIdPrevRef.current;
    sessionIdPrevRef.current = sessionId;
    if (prev && sessionId && prev !== sessionId) {
      setLivePopupTaskId(null);
      // Iter 131 — session switch resets the Clear ↑ toolbar's hide
      // state. The previous session's "hide older" preference doesn't
      // carry over.
      setHideOlder(false);
    }
  }, [sessionId]);

  // Iter 115 — debug/QA hook. Adding `?ltp=<task_id>` to the URL mounts
  // the popup for that task without going through a real chat handoff.
  // Useful for visual smoke tests + first-time user demos.
  useEffect(() => {
    try {
      const params = new URLSearchParams(window.location.search);
      const t = params.get("ltp");
      // eslint-disable-next-line react-hooks/set-state-in-effect
      if (t) setLivePopupTaskId(t);
    } catch { /* ignore */ }
  }, []);
  // eslint-disable-next-line react-hooks/set-state-in-effect
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

  // Iter 131 — Clear ↑ toolbar.
  //
  // "Hide older" toggles a UI-only collapse so a long transcript
  // doesn't drown the viewport (older turns stay in DB and reappear
  // when toggled off / when the session is re-opened).
  //
  // "Clear chat" wipes the session's `turns` array in MongoDB via the
  // Iter 131 endpoint and resets the local message list to the
  // WELCOME bubble. The session_id is preserved so the sidebar entry
  // doesn't disappear — only the conversation is gone.
  const toggleHideOlder = useCallback(() => {
    setHideOlder((v) => !v);
  }, []);

  const clearChat = useCallback(async () => {
    if (clearingChat) return;
    if (!sessionId) {
      // No session yet (welcome screen) — just wipe the local bubble list.
      setMessages([WELCOME]);
      setHideOlder(false);
      return;
    }
    // eslint-disable-next-line no-alert
    const ok = window.confirm(
      "Clear all messages in this chat? The session stays in your sidebar — only the conversation is wiped. This cannot be undone.",
    );
    if (!ok) return;
    setClearingChat(true);
    try {
      await api.delete(`/chat/sessions/${sessionId}/messages`);
      setMessages([WELCOME]);
      setHideOlder(false);
      toast({ message: "Chat cleared.", kind: "info" });
    } catch (e) {
      const detail = e?.response?.data?.detail || e.message || "Failed to clear chat.";
      toast({ message: detail, kind: "error" });
    } finally {
      setClearingChat(false);
    }
  }, [clearingChat, sessionId]);

  // Iter 132 — quick-reply chip click.
  //
  // The chip below an assistant bubble is essentially a pre-filled
  // send. We fill the textarea (so the user sees what's being sent
  // and can still abort with cmd+. or by clicking Stop) and then
  // request the form's submit on the next tick. We don't bypass the
  // existing send() pipeline because that owns mode detection,
  // attachment merging, project-context augmentation, busy gating,
  // and the SSE streaming wire-up — duplicating all of that would
  // be a regression magnet.
  const sendSuggestion = useCallback((text) => {
    if (!text || busy || !sessionId) return;
    setInput(text);
    // setInput is async — schedule the submit after React flushes
    // the new state into the textarea. `requestSubmit` on the form
    // fires the same onSubmit handler the user would by pressing
    // Enter, so all downstream logic is identical.
    setTimeout(() => {
      const form = document.querySelector('form[data-testid="chat-form"]');
      if (form) form.requestSubmit();
    }, 0);
  }, [busy, sessionId]);

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
    // eslint-disable-next-line react-hooks/set-state-in-effect
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
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setPreviewOpen(true);
  }, [activeProject?.preview_url, activeProject?.project_id]);

  // Load history on session change
  useEffect(() => {
    if (!sessionId) return;
    let cancelled = false;
    // eslint-disable-next-line react-hooks/set-state-in-effect
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
    // Iter 140 — abort BOTH controllers so neither path leaks. The
    // hook-owned controller covers any future stream() call we route
    // through useChatStream, while abortRef stays in service of the
    // existing inline streamChat path.
    chatStream.abort();
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }
    setBusy(false);
    // Iter 134 — clean up orphan "thinking…" bubbles when the user
    // clicks Stop. Previously this only cancelled the SSE network call;
    // any assistant placeholders with streaming:true stayed in
    // `messages`, so MessageBubble kept rendering the Loader2 spinner
    // forever (visible bug: two stuck "thinking…" bubbles after Stop).
    // We sweep every streaming assistant turn — usually one, but old
    // interrupted sessions may have leaked multiple — and finalise them
    // with a clear "Stopped" marker so the user knows the click worked.
    setMessages((msgs) =>
      msgs.map((m) => {
        if (m.role !== "assistant" || !m.streaming) return m;
        return {
          ...m,
          streaming: false,
          stopped: true,
          content: m.content || "⏹ Stopped",
        };
      })
    );
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
        // Iter 114 — open the floating live-status popup for this task
        setLivePopupTaskId(p.task_id || null);
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
              verifiedPaths: Array.isArray(d.verified_paths)
                ? d.verified_paths
                : [],
              // Iter 119 — citation chips from Tavily / Firecrawl /
              // fetch_url. Rendered as 🌐 chips below the message.
              webSources: Array.isArray(d.web_sources)
                ? d.web_sources
                : [],
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
        {/* Iter 131 — Clear ↑ toolbar. Sits at the top of the
            scrollable message list and only renders when there's
            at least one real (non-WELCOME) turn to act on. */}
        {messages.length > 1 && (
          <div
            data-testid="chat-toolbar"
            style={{
              position: "sticky", top: -24, zIndex: 2,
              margin: "-24px -28px 0 -28px",
              padding: "8px 28px",
              display: "flex", alignItems: "center", justifyContent: "flex-end",
              gap: 8,
              background: "linear-gradient(180deg, var(--bg) 70%, transparent)",
              backdropFilter: "blur(6px)",
              borderBottom: "1px solid var(--border)",
              fontSize: 12,
            }}
          >
            {messages.length > HIDE_OLDER_THRESHOLD + 1 && (
              <button
                type="button"
                data-testid="chat-hide-older-btn"
                onClick={toggleHideOlder}
                title={hideOlder
                  ? "Show all messages in this chat"
                  : `Collapse all but the last ${HIDE_OLDER_THRESHOLD} messages`
                }
                style={{
                  display: "flex", alignItems: "center", gap: 4,
                  padding: "4px 10px",
                  background: "transparent",
                  border: "1px solid var(--border)",
                  borderRadius: 999,
                  color: "var(--text-faint)",
                  cursor: "pointer",
                  fontSize: 12,
                  transition: "color 120ms, border-color 120ms",
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.color = "var(--text)";
                  e.currentTarget.style.borderColor = "var(--text-faint)";
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.color = "var(--text-faint)";
                  e.currentTarget.style.borderColor = "var(--border)";
                }}
              >
                {hideOlder ? <Eye size={12} /> : <EyeOff size={12} />}
                {hideOlder
                  ? `Show all (${messages.length - 1})`
                  : "Hide older ↑"
                }
              </button>
            )}
            <button
              type="button"
              data-testid="chat-clear-btn"
              onClick={clearChat}
              disabled={clearingChat}
              title="Delete every message in this chat. Session stays in your sidebar."
              style={{
                display: "flex", alignItems: "center", gap: 4,
                padding: "4px 10px",
                background: "transparent",
                border: "1px solid var(--border)",
                borderRadius: 999,
                color: "var(--text-faint)",
                cursor: clearingChat ? "not-allowed" : "pointer",
                fontSize: 12,
                opacity: clearingChat ? 0.6 : 1,
                transition: "color 120ms, border-color 120ms",
              }}
              onMouseEnter={(e) => {
                if (clearingChat) return;
                e.currentTarget.style.color = "var(--danger, #ef4444)";
                e.currentTarget.style.borderColor = "var(--danger, #ef4444)";
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.color = "var(--text-faint)";
                e.currentTarget.style.borderColor = "var(--border)";
              }}
            >
              {clearingChat
                ? <Loader2 size={12} style={{ animation: "spin 1s linear infinite" }} />
                : <Trash2 size={12} />
              }
              {clearingChat ? "Clearing…" : "Clear chat"}
            </button>
          </div>
        )}

        {/* Iter 131 — "N older messages hidden" pill, shown only
            when hideOlder is ON and there's actual collapse. */}
        {hideOlder && messages.length > HIDE_OLDER_THRESHOLD + 1 && (
          <button
            type="button"
            data-testid="chat-show-hidden-pill"
            onClick={toggleHideOlder}
            style={{
              alignSelf: "center",
              padding: "6px 14px",
              borderRadius: 999,
              border: "1px dashed var(--border)",
              background: "var(--bg-soft, transparent)",
              color: "var(--text-faint)",
              fontSize: 12,
              cursor: "pointer",
            }}
          >
            ↑ {messages.length - HIDE_OLDER_THRESHOLD} older messages hidden — click to show all
          </button>
        )}

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
          // Iter 131 — when hideOlder is ON, skip messages older
          // than the last HIDE_OLDER_THRESHOLD. We still iterate the
          // full array so the `dbTurnIndex` math below stays
          // correct (it depends on absolute positions).
          if (
            hideOlder &&
            messages.length > HIDE_OLDER_THRESHOLD + 1 &&
            i < messages.length - HIDE_OLDER_THRESHOLD
          ) {
            return null;
          }
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
          // Iter 132 — quick-reply chips render below the LAST
          // assistant bubble only. Chips on every historical bubble
          // would be noisy AND stale (suggestions in old turns may
          // no longer be relevant). The "is last assistant" guard:
          // the message is assistant, not the WELCOME, not still
          // streaming, and no user message comes after it.
          const isLastAssistant = (
            m.role === "assistant" &&
            !m.streaming &&
            m.provider !== "system" &&
            i === messages.length - 1
          );
          const suggestions = isLastAssistant
            ? extractSuggestions(m.content)
            : [];
          return (
            <React.Fragment key={i}>
              <MessageBubble
                idx={i}
                dbTurnIndex={dbTurnIndex}
                m={m}
                onRegenerate={regenerate}
                sessionId={sessionId}
                activeProject={activeProject}
                exhausted={exhausted}
                onTaskCompleted={triggerTaskFollowup}
              />
              {suggestions.length > 0 && (
                <div
                  data-testid="chat-suggestion-chips"
                  style={{
                    display: "flex", flexWrap: "wrap", gap: 8,
                    marginTop: -8, marginLeft: 4,
                  }}
                >
                  {suggestions.map((phrase) => (
                    <button
                      key={phrase}
                      type="button"
                      data-testid={`chat-suggestion-chip-${phrase.slice(0, 24).replace(/\s+/g, "-").toLowerCase()}`}
                      onClick={() => sendSuggestion(phrase)}
                      disabled={busy || exhausted}
                      title={`Send: ${phrase}`}
                      style={{
                        display: "inline-flex", alignItems: "center", gap: 6,
                        padding: "6px 12px",
                        borderRadius: 999,
                        border: "1px solid var(--accent, #f59e0b)",
                        background: "var(--accent-soft, rgba(245,158,11,0.08))",
                        color: "var(--accent, #f59e0b)",
                        fontSize: 12, fontWeight: 500,
                        cursor: (busy || exhausted) ? "not-allowed" : "pointer",
                        opacity: (busy || exhausted) ? 0.5 : 1,
                        transition: "background 120ms, transform 60ms",
                        maxWidth: "100%",
                        textAlign: "left",
                      }}
                      onMouseEnter={(e) => {
                        if (busy || exhausted) return;
                        e.currentTarget.style.background = "var(--accent, #f59e0b)";
                        e.currentTarget.style.color = "#fff";
                      }}
                      onMouseLeave={(e) => {
                        e.currentTarget.style.background = "var(--accent-soft, rgba(245,158,11,0.08))";
                        e.currentTarget.style.color = "var(--accent, #f59e0b)";
                      }}
                    >
                      <Send size={11} />
                      <span style={{
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                        maxWidth: 280,
                      }}>{phrase}</span>
                    </button>
                  ))}
                </div>
              )}
            </React.Fragment>
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
            that&apos;s wrong, cancel and rephrase — e.g. start with
            &quot;debug …&quot; for D, &quot;add …&quot; for C, &quot;should I …&quot; for B.
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
        data-testid="chat-form"
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
            className="chat-preview-tool"
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
      {/* Iter 114 — floating live-task popup, only one ever mounted.
          key={livePopupTaskId} forces a clean remount per task so its
          internal poll/dismiss timers start fresh — no stale state. */}
      <LiveTaskPopup
        key={livePopupTaskId || "none"}
        taskId={livePopupTaskId}
        onClose={() => setLivePopupTaskId(null)}
      />
    </div>
  );
}

function ToolButton({ testid, title, onClick, Icon, active, className }) {
  return (
    <button
      type="button"
      data-testid={testid}
      title={title}
      onClick={onClick}
      className={className}
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

