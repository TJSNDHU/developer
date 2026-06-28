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
  Eye, EyeOff, Trash2, Network, ShieldCheck,
} from "lucide-react";
import { api, streamChat, API_BASE, getToken, getUser } from "../lib/api";
import { toast } from "./Toast";
import PreviewPanel from "./PreviewPanel";
import ModeSelector from "./ModeSelector";
import ThinkingHint from "./ThinkingHint";
import LiveTaskPopup from "./LiveTaskPopup";
import WarmStatusBar from "./WarmStatusBar";
import { useWarmStart } from "../hooks/useWarmStart";
import GraphPanel from "./GraphPanel";
import TemperatureBadge from "./TemperatureBadge";
import { useF12Errors, detectMode, F12Badge, ModePill } from "./ChatPanelF12";
import MessageBubble from "./MessageBubble";
import PostTaskScan from "./PostTaskScan";
import LiveStepFloatingCard from "./LiveStepFloatingCard";  // Iter 212m-19
import FounderOfferCard from "./FounderOfferCard";          // Iter 212m-30 PR-2
import SecurityScanDrawer from "./SecurityScanDrawer";      // Iter 212m-55 1-click scan
import { getScanSeverityCounts, onScanUpdated, setCachedScan } from "../lib/securityScanCache";  // Iter 212m-56
// Iter 212m-58 — Prompt / Loop execution-mode switcher + ancillary
// step bar and plan-approval card.
import LoopModeToggle, {
  EXEC_MODES, loadExecMode, saveExecMode,
} from "./LoopModeToggle";
import LoopStepBar from "./LoopStepBar";
import PlanApprovalCard from "./PlanApprovalCard";
// Iter 212m-65 — Phase D wiring: Self-heal indicator + paused-loop
// User Action card (powered by the real /loop/* SSE stream).
import { SelfHealIndicator, UserActionCard } from "./LoopActionCards";
import {
  startLoop, confirmLoop, pauseResponse, cancelLoop, streamLoopEvents,
} from "../lib/loopApi";
import { getChatBgTint } from "../utils/chatBgTint";        // Iter 212m-30 PR-2
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
    "I'm ORA — developers choice, by Aurem CTO — your sovereign engineering co-pilot. Ask me to plan a feature, write code, or debug an error. What are we shipping today?",
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
  // Iter 212m-19 — Live step floating card. Tracks the steps + model
  // + token estimate emitted during the current chat turn. Hidden
  // until the first step lands; auto-hides 3s after the orchestrator
  // signals `done`. The same `steps` data is mirrored into the
  // streaming message's `steps` field for the in-bubble cards.
  const [liveStepCard, setLiveStepCard] = useState(null);
  // Iter 131 — message-list toolbar state.
  // `hideOlder` collapses everything older than the last
  // HIDE_OLDER_THRESHOLD messages into a count badge (UI only — DB
  // is untouched). `clearingChat` blocks double-clicks on the
  // destructive Clear button.
  const [hideOlder, setHideOlder] = useState(false);
  // Iter 153 — review mode (swift / pro / maxx). Persisted across reloads.
  // Iter 212m-97 — also synced with the TopBar pills in Dashboard via
  // the `aurem:set-chat-mode` custom event so the two surfaces stay
  // in lockstep (previously the TopBar was a dummy).
  const [chatMode, setChatMode] = useState(() => {
    try { return localStorage.getItem("aurem_chat_mode") || "swift"; }
    catch { return "swift"; }
  });
  useEffect(() => {
    try { localStorage.setItem("aurem_chat_mode", chatMode); }
    catch { /* ignore */ }
    // Broadcast → TopBar
    try {
      window.dispatchEvent(new CustomEvent("aurem:chat-mode-changed", {
        detail: { mode: chatMode },
      }));
    } catch { /* CustomEvent unsupported */ }
  }, [chatMode]);

  // Listen ← TopBar pill clicks
  useEffect(() => {
    const onSet = (e) => {
      const m = e?.detail?.mode;
      if (m && ["swift", "pro", "maxx"].includes(m) && m !== chatMode) {
        setChatMode(m);
      }
    };
    window.addEventListener("aurem:set-chat-mode", onSet);
    return () => window.removeEventListener("aurem:set-chat-mode", onSet);
  }, [chatMode]);

  const [clearingChat, setClearingChat] = useState(false);
  // Iter 212m-30 PR-2 — Founder welcome chat-bg tint. The amber wash
  // decays each day for the first 72 h after signup; after that the
  // helper returns "transparent" so the visual hint stops naturally
  // without us needing a DB flag.
  const founderTint = useMemo(() => {
    const u = (typeof getUser === "function" && getUser()) || null;
    return getChatBgTint(u?.created_at);
  }, []);
  // Iter 148 — controls the "connect a repo" helper dialog. Triggered
  // by the no-repo warning pill above the composer and by clicking the
  // red GH status indicator in the toolbar.
  const [showRepoHelp, setShowRepoHelp] = useState(false);
  // Iter 154 — legacy `maxxMode` boolean is now derived from `chatMode`.
  // The standalone Maxx toggle button has been removed from the toolbar
  // (redundant with ModeSelector's Maxx pill). Backend payload still
  // expects `maxx_mode` so we keep the var alive — but it's a pure
  // derivation now, no setter, no localStorage.
  const maxxMode = chatMode === "maxx";
  // Iter 212m-86 — default preview CLOSED on every mount (regardless of
  // localStorage). User must click Preview tab to open. Prevents the
  // iframe from auto-bleeding into the dashboard right panel.
  const [previewOpen, setPreviewOpen] = useState(false);
  const [previewBlocks, setPreviewBlocks] = useState([]);
  // Iter 212m-9 — when the ShipDialog banner is clicked we want the
  // PreviewPanel to mount directly in deploy mode. Tracking it here
  // (paired with the panel key) forces a clean remount so the deploy
  // view is shown on first render.
  const [previewInitialMode, setPreviewInitialMode] = useState("preview");
  const openDeployTab = useCallback(() => {
    setPreviewInitialMode("deploy");
    setPreviewOpen(true);
    try { localStorage.setItem(PREVIEW_KEY, "1"); } catch { /* noop */ }
  }, []);
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
  // Iter 212m-43 — stuck-thinking auto-recovery watchdog. If the SSE
  // stream goes silent (no token / heartbeat / step / mode update)
  // for IDLE_TIMEOUT_MS, we abort the stream and silently retry the
  // turn once. If the retry also stalls, we surface a clean error
  // bubble with a retry button instead of hanging "thinking…" forever.
  const lastActivityRef = useRef(0);
  const idleTimerRef = useRef(null);
  const retryAttemptRef = useRef(0);
  const fileInputRef = useRef(null);
  const taRef = useRef(null);
  // Iter 146 — tracks whether the user has already sent a message in
  // the current session. Used to fire `aurem:chat-session-started`
  // exactly once so Shell.jsx can hide the sidebar after the first
  // send (not on every keystroke).
  const sessionStartedRef = useRef(false);
  // Reset the flag whenever the session changes (new chat, switched
  // project) so the sidebar reappears in the fresh session until the
  // user actually sends something.
  useEffect(() => {
    sessionStartedRef.current = false;
    try { window.dispatchEvent(new CustomEvent("aurem:chat-session-reset")); }
    catch { /* ignore */ }
  }, [sessionId]);

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

  // Iter 165 — Warm Start: trigger 4 background agents on project
  // select so the next chat turn already has pre-loaded context.
  const { status: warmStatus, progress: warmProgress } = useWarmStart(
    activeProject?.project_id
  );

  // Iter 165 — Codebase Graph drawer toggle. The drawer also dispatches
  // `ora-inject` events when a user clicks "Ask ORA about this file"
  // which this component picks up below to seed the composer input.
  const [graphOpen, setGraphOpen] = useState(false);
  // Iter 212m-55 — security scanner drawer state.
  const [scanOpen, setScanOpen] = useState(false);
  // Iter 212m-57 — visible status of the active SSE stream so we can
  // surface a "Slow response… / Reconnecting…" pill above the composer
  // when the stuck-thinking watchdog is approaching its 90s budget or
  // has already fired an auto-retry. Shape:
  //   { phase: 'idle' | 'slow' | 'reconnecting',
  //     silentFor: number,        // seconds of stream silence
  //     retryEtaSec: number|null  // seconds until the auto-retry fires
  //   }
  const [streamHealth, setStreamHealth] = useState({
    phase: "idle", silentFor: 0, retryEtaSec: null,
  });
  // ──────────────────────────────────────────────────────────────
  // Iter 212m-58 — Prompt / Loop execution mode.
  // ──────────────────────────────────────────────────────────────
  // `execMode` is persisted via the LoopModeToggle helpers and
  // controls a lot of downstream UX:
  //   • Send button text (Send vs Run loop)
  //   • Composer placeholder
  //   • Swift model availability (hidden in loop)
  //   • Shield badge label ("Auto" in loop)
  //   • Render of LoopStepBar + PlanApprovalCard
  //   • execution_mode body field on /chat/stream
  const [execMode, setExecMode] = useState(loadExecMode);
  // Loop pipeline state. `phase` drives the LoopStepBar and decides
  // whether to render the PlanApprovalCard. `retryCount` is reserved
  // for future verify-loop auto-retry UX (max 3).
  const [loopPhase, setLoopPhase] = useState("idle");
  const [loopRetryCount, setLoopRetryCount] = useState(0);
  // The pending plan message id — once the user approves, we continue
  // the same session with a `LOOP_PHASE:execute` follow-up.
  const pendingPlanRef = useRef(null);
  // ──────────────────────────────────────────────────────────────
  // Iter 212m-65 — Phase D: real LoopEngine wiring via /loop/* SSE.
  // ──────────────────────────────────────────────────────────────
  // `loopId` — the engine session id returned by /loop/start. Drives
  //            confirm/pause-response/cancel calls and the SSE stream.
  // `loopPlan` — the structured plan dict returned by /loop/start;
  //            rendered inside the PlanApprovalCard.
  // `selfHeal` — { visible, attempt, max, errorPreview } for the
  //            inline SelfHealIndicator strip.
  // `userAction` — { phase, message, errors } for the UserActionCard
  //            when the engine pauses with requires_user_action:true.
  // `loopAbortRef` — AbortController for the active SSE stream.
  const [loopId, setLoopId] = useState(null);
  const [loopPlan, setLoopPlan] = useState(null);
  const [selfHeal, setSelfHeal] = useState({ visible: false, attempt: 1, max: 3, errorPreview: "" });
  const [userAction, setUserAction] = useState(null);
  const [userActionBusy, setUserActionBusy] = useState(false);
  const loopAbortRef = useRef(null);
  // Iter 212m-58 — chatMode hard-pinned to Pro when loop is active
  // (Swift disabled per spec). We persist that nudge on toggle so a
  // user flipping back to Prompt mode keeps their last model pick.
  const lastPromptChatModeRef = useRef(null);
  // Iter 212m-56 — severity counts for Shield badge. Reads the shared
  // securityScanCache and re-renders on every drawer scan completion.
  const [scanCounts, setScanCounts] = useState(() =>
    getScanSeverityCounts(activeProject?.project_id),
  );
  useEffect(() => {
    setScanCounts(getScanSeverityCounts(activeProject?.project_id));
    const unsub = onScanUpdated((pid) => {
      if (pid === activeProject?.project_id) {
        setScanCounts(getScanSeverityCounts(pid));
      }
    });
    return unsub;
  }, [activeProject?.project_id]);
  useEffect(() => {
    const onInject = (e) => {
      const text = e?.detail?.text;
      if (typeof text === "string" && text.trim()) {
        setInput(text);
      }
    };
    window.addEventListener("ora-inject", onInject);
    return () => window.removeEventListener("ora-inject", onInject);
  }, []);

  // Iter 163 — chat toolbar (Show all / Clear chat) auto-hide on
  // typing, mirroring the top tabbar pattern. INDEPENDENT hot-zone:
  // hovering the very top edge of the chat-messages area brings the
  // toolbar back; the sidebar peek stays separate.
  const [toolbarHidden, setToolbarHidden] = useState(false);
  const toolbarPeekFromHoverRef = useRef(false);
  const isMobileRef = useRef(
    typeof window !== "undefined"
      && window.matchMedia("(max-width: 900px)").matches
  );
  useEffect(() => {
    const mq = window.matchMedia("(max-width: 900px)");
    const onChange = (e) => { isMobileRef.current = e.matches; };
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);
  useEffect(() => {
    const onStart = () => setToolbarHidden(true);
    const onReset = () => {
      setToolbarHidden(false);
      toolbarPeekFromHoverRef.current = false;
    };
    window.addEventListener("aurem:chat-session-started", onStart);
    window.addEventListener("aurem:chat-session-reset", onReset);
    return () => {
      window.removeEventListener("aurem:chat-session-started", onStart);
      window.removeEventListener("aurem:chat-session-reset", onReset);
    };
  }, []);
  const onToolbarHotZoneEnter = useCallback(() => {
    if (isMobileRef.current) return;
    toolbarPeekFromHoverRef.current = true;
    setToolbarHidden(false);
  }, []);
  const onToolbarMouseLeave = useCallback(() => {
    if (isMobileRef.current) return;
    if (toolbarPeekFromHoverRef.current) {
      toolbarPeekFromHoverRef.current = false;
      setToolbarHidden(true);
    }
  }, []);

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

  // Iter 154 — toggleMaxx removed; Maxx is now selected via ModeSelector.

  const togglePreview = useCallback(() => {
    setPreviewOpen((v) => {
      const next = !v;
      localStorage.setItem(PREVIEW_KEY, next ? "1" : "0");
      // Iter 145 — notify Dashboard so the top-right button label stays
      // in sync (button is the only visible entrypoint now).
      try {
        window.dispatchEvent(new CustomEvent("aurem:preview-state-changed", {
          detail: { open: next },
        }));
      } catch { /* ignore */ }
      return next;
    });
  }, []);

  // Iter 145 — listen for the top-right Preview toggle from Dashboard.
  useEffect(() => {
    const onToggle = (e) => {
      const desired = e?.detail?.open;
      setPreviewOpen((cur) => {
        const next = typeof desired === "boolean" ? desired : !cur;
        try { localStorage.setItem(PREVIEW_KEY, next ? "1" : "0"); }
        catch { /* ignore */ }
        return next;
      });
    };
    window.addEventListener("aurem:toggle-preview", onToggle);
    return () => window.removeEventListener("aurem:toggle-preview", onToggle);
  }, []);

  // Iter 212m-98 — Sidebar v2 wiring refs (latest-value pattern so the
  // event listeners below always read the current state).
  const sidebarWireRefs = useRef({});
  sidebarWireRefs.current.execMode = execMode;
  sidebarWireRefs.current.activeProject = activeProject;

  // Iter 145 — broadcast every previewOpen change (incl. auto-open
  // when a code reply lands or a project with preview_url is selected)
  // so Dashboard's top-right Preview/Hide button label always matches.
  useEffect(() => {
    try {
      window.dispatchEvent(new CustomEvent("aurem:preview-state-changed", {
        detail: { open: previewOpen },
      }));
    } catch { /* ignore */ }
  }, [previewOpen]);

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
    // Iter 212m-86 — REMOVED auto-open of preview on first code reply.
    // User reported the iframe bleeding into the dashboard right panel
    // on mount. Preview now opens ONLY when user clicks the Preview tab
    // (TopBar v2) or when ChatPanel's "Open preview" button fires
    // `aurem:toggle-preview`.
  }, [latestAssistant]);

  // Iter 169 — when the latest assistant turn shipped a task, fetch
  // the task's persisted `edits` and add each file as a preview block
  // ahead of any inline ```fenced``` code. This makes the </> Code
  // button on the right-hand panel show the ACTUAL files ORA pushed,
  // not just the live URL.
  useEffect(() => {
    const taskId = latestAssistant?.shipped_task_id;
    if (!taskId) return;
    let cancelled = false;
    (async () => {
      try {
        const r = await api.get(`/cto/tasks/${taskId}`);
        const edits = r?.data?.task?.edits || {};
        const fileBlocks = Object.entries(edits).map(([path, code]) => {
          const ext = (path.split(".").pop() || "").toLowerCase();
          const langMap = {
            py: "python", js: "javascript", jsx: "jsx", ts: "typescript",
            tsx: "tsx", html: "html", css: "css", json: "json",
            yml: "yaml", yaml: "yaml", md: "markdown",
            sh: "bash", sql: "sql", toml: "toml",
          };
          return {
            lang: langMap[ext] || "text",
            code: code || "",
            label: path,
          };
        });
        if (!cancelled && fileBlocks.length > 0) {
          // Merge with any inline-extracted blocks; shipped files first.
          setPreviewBlocks((prev) => {
            const inline = (prev || []).filter(
              (b) => !fileBlocks.some((f) => f.label === b.label)
            );
            return [...fileBlocks, ...inline];
          });
        }
      } catch {
        /* silently ignore — preview panel will just show inline blocks */
      }
    })();
    return () => { cancelled = true; };
  }, [latestAssistant?.shipped_task_id]);

  // Iter 184 — Fix B: background SSE listener for HTTP `/tasks/submit`
  // (or MCP) flows where <TaskLiveTape> may never mount inside a chat
  // bubble. We open the same /cto/tasks/{taskId}/stream the tape uses,
  // watch for the new `task_handoff` frame the worker emits right
  // before `done`, and dispatch the `ora-task-handoff` window event
  // so the floating popup latches on. The TaskLiveTape change in this
  // iter also dispatches the same event when the tape IS mounted, but
  // this listener guarantees coverage when it isn't.
  useEffect(() => {
    const taskId = latestAssistant?.shipped_task_id;
    if (!taskId) return;

    // Immediately surface the popup — same behaviour the chat-handoff
    // path has had since iter 114. The SSE stream below augments this
    // with handoff/done telemetry for any consumers wired off the
    // `ora-task-handoff` window event.
    setLivePopupTaskId(taskId);

    let aborted = false;
    const ctrl = new AbortController();

    (async () => {
      try {
        const token = getToken();
        const res = await fetch(
          `${API_BASE}/cto/tasks/${taskId}/stream`,
          {
            headers: token ? { Authorization: `Bearer ${token}` } : {},
            signal: ctrl.signal,
          },
        );
        if (!res.ok || !res.body) return;

        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buf = "";

        while (!aborted) {
          const { done: streamDone, value } = await reader.read();
          if (streamDone) break;
          buf += decoder.decode(value, { stream: true });
          const frames = buf.split("\n\n");
          buf = frames.pop() || "";
          for (const frame of frames) {
            const line = frame.split("\n").find((l) => l.startsWith("data:"));
            if (!line) continue;
            let p;
            try { p = JSON.parse(line.slice(5).trim()); }
            catch { continue; }
            if (!p || p.type === "ping") continue;

            if (p.type === "task_handoff") {
              try {
                window.dispatchEvent(
                  new CustomEvent("ora-task-handoff", {
                    detail: {
                      task_id: taskId,
                      sha: p.sha || "",
                      project_id: p.project_id || "",
                      source: p.source || "chatpanel_bg_stream",
                    },
                  }),
                );
              } catch { /* CustomEvent unsupported */ }
            }
            if (
              p.type === "done" || p.type === "fail" ||
              p.type === "failed" || p.type === "cancelled"
            ) {
              aborted = true;
              break;
            }
          }
        }
      } catch { /* aborted on cleanup or network glitch — both fine */ }
    })();

    return () => {
      aborted = true;
      try { ctrl.abort(); } catch { /* ignore */ }
    };
  }, [latestAssistant?.shipped_task_id]);

  // Iter 184 — window-event bridge: TaskLiveTape (mounted in chat
  // bubbles) and the background SSE listener above both dispatch
  // `ora-task-handoff` when the worker emits the handoff frame. We
  // latch the floating popup onto that task id so it stays visible
  // for the entire commit phase — covering chat-handoff,
  // ship-shortcut, HTTP /tasks/submit, and MCP-triggered tasks.
  useEffect(() => {
    const taskHandoffHandler = (e) => {
      const tid = e?.detail?.task_id;
      if (tid) setLivePopupTaskId(tid);
    };
    window.addEventListener("ora-task-handoff", taskHandoffHandler);
    return () => {
      window.removeEventListener("ora-task-handoff", taskHandoffHandler);
    };
  }, []);

  // Iter 212m-86 — REMOVED `auto-open preview when activeProject.preview_url`.
  // This was bleeding aurem.live iframe into the dashboard right panel on
  // mount even when the user just wanted to chat. Preview now opens only
  // via explicit user action (Preview tab / inline button).

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
            // Iter 161 — preserve review_mode across reload so the
            // agent chip stays accurate after a page refresh.
            reviewMode: t.review_mode || null,
            watchdog: t.watchdog,
            feedback: t.feedback,
            shipped_task_id: t.shipped_task_id,
          })));
          // Iter 212m-44 — the user has prior turns in this session
          // (reloaded mid-conversation), so the chrome (top tabs +
          // sidebar) should be auto-hidden as if they had just sent
          // a message. Without this, hitting refresh on an active
          // chat brought the topbar back even though the session
          // was clearly already in progress.
          if (!sessionStartedRef.current) {
            sessionStartedRef.current = true;
            try {
              window.dispatchEvent(new CustomEvent("aurem:chat-session-started", {
                detail: { session_id: sessionId, restored: true },
              }));
            } catch { /* ignore */ }
          }
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
    // Iter 212m-65 — also abort any active Loop Mode SSE stream so a
    // user Stop click reliably cancels mid-pipeline.
    if (loopAbortRef.current) {
      try { loopAbortRef.current.abort(); } catch { /* swallow */ }
      loopAbortRef.current = null;
    }
    // Iter 212m-43 — also kill any pending idle watchdog so it can't
    // fire an auto-retry after the user explicitly clicked Stop.
    if (idleTimerRef.current) {
      clearInterval(idleTimerRef.current);
      idleTimerRef.current = null;
    }
    retryAttemptRef.current = 0;
    setBusy(false);
    // Iter 212m-57 — clear the slow/reconnecting pill on Stop.
    setStreamHealth({ phase: "idle", silentFor: 0, retryEtaSec: null });
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

  async function send(e, opts = {}) {
    e?.preventDefault();
    // Iter 212m-58 — optional loop-phase override. Called with
    // `{ loopPhase: "execute", promptOverride: "..." }` from the
    // PlanApprovalCard approve handler so we can continue the same
    // session with the execute phase.
    const loopPhaseHint = opts.loopPhase || null;
    const promptOverride = opts.promptOverride || null;
    const text = promptOverride != null ? promptOverride : input.trim();
    // Pull ready attachments (uploading ones get skipped silently —
    // user can re-send if they were too slow). Also keep errored ones
    // (their stub markdown tells the LLM something was attempted).
    const readyAttachments = attachments.filter(
      (a) => a.status === "ready" || a.status === "error"
    );
    // Allow send when EITHER text OR attachments exist — previous gate
    // demanded text, which is why an image-only chat silently refused.
    if ((!text && !readyAttachments.length) || busy || !sessionId) return;
    if (promptOverride == null) setInput("");

    // ──────────────────────────────────────────────────────────────
    // Iter 212m-61 — /diagram chat command.
    // Bypass the normal SSE chat orchestration entirely and call the
    // dedicated /diagram/generate endpoint, then render a
    // <MermaidBlock> inside an assistant bubble.  All other slash
    // commands flow through the existing path.
    // ──────────────────────────────────────────────────────────────
    const slashMatch = text.match(/^\/diagram\b\s*(.*)$/is);
    if (slashMatch && promptOverride == null) {
      const dgPrompt = (slashMatch[1] || "").trim();
      if (!dgPrompt) {
        setInput(text);    // restore — they typed only `/diagram`
        return;
      }
      const userBubble = text;
      setMessages((m) => [
        ...m,
        { role: "user", content: userBubble },
        { role: "assistant", content: "", streaming: true,
          diagramPending: true },
      ]);
      setBusy(true);
      try {
        const r = await api.post("/diagram/generate", {
          prompt:  dgPrompt,
          repo_id: activeProject?.project_id || null,
        });
        const payload = r?.data || r;
        setMessages((m) => {
          const out = m.slice();
          for (let i = out.length - 1; i >= 0; i--) {
            if (out[i].role === "assistant" && out[i].diagramPending) {
              out[i] = {
                role: "assistant",
                streaming: false,
                content: "",
                diagram: {
                  code:  payload.mermaid_code,
                  title: payload.title || dgPrompt.slice(0, 80),
                  type:  payload.diagram_type,
                },
              };
              break;
            }
          }
          return out;
        });
      } catch (e) {
        const msg = e?.response?.data?.detail || e?.message || "Diagram failed";
        setMessages((m) => {
          const out = m.slice();
          for (let i = out.length - 1; i >= 0; i--) {
            if (out[i].role === "assistant" && out[i].diagramPending) {
              out[i] = {
                role: "assistant",
                streaming: false,
                content: `**Diagram failed**\n\n${msg}\n\nTry rephrasing or specify a type (e.g. \`/diagram sequence diagram of …\`).`,
              };
              break;
            }
          }
          return out;
        });
      } finally {
        setBusy(false);
      }
      return;
    }

    // ──────────────────────────────────────────────────────────────
    // Iter 212m-65 — Loop Mode fresh-turn fork.
    // In Loop mode, the FIRST user turn no longer streams through
    // `/chat/stream` with a `LOOP_PHASE:plan` suffix; it kicks off
    // the real backend LoopEngine via `POST /loop/start` and renders
    // the structured plan. Approval then triggers SSE-streamed
    // EXECUTE → VERIFY → SCAN → SHIP via runLoopAfterConfirm().
    // The legacy `LOOP_PHASE:execute` continuation through send() is
    // gone — handleApprovePlan calls confirmLoop directly.
    // ──────────────────────────────────────────────────────────────
    if (execMode === EXEC_MODES.LOOP && !opts.loopPhase) {
      await runLoopPlan(text, readyAttachments, opts);
      return;
    }
    // Iter 146 — once the user fires off the first message of this
    // session, broadcast `aurem:chat-session-started` so Shell.jsx
    // can hide the sidebar for the rest of the session. The peek hot
    // zone on the left edge remains the only way to bring it back.
    if (!sessionStartedRef.current) {
      sessionStartedRef.current = true;
      try {
        window.dispatchEvent(new CustomEvent("aurem:chat-session-started", {
          detail: { session_id: sessionId },
        }));
      } catch { /* ignore */ }
    }
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
    // Iter 212m-58 — Loop-mode phase prefix. The backend reads this
    // hint to decide whether the model should respond plan-only or
    // proceed with execution. First-turn loop sends get `:plan`; the
    // PlanApprovalCard approval path passes `loopPhase: "execute"`.
    const inLoop = execMode === EXEC_MODES.LOOP;
    const resolvedPhase = inLoop
      ? (loopPhaseHint || "plan")
      : null;
    const phasePrefix = resolvedPhase ? `LOOP_PHASE:${resolvedPhase}\n\n` : "";
    // Auto-augment prompt with active project context so the LLM stays scoped.
    const finalPrompt = activeProject
      ? `${phasePrefix}[Working on project: ${activeProject.name} — repo ${activeProject.github_owner}/${activeProject.github_repo}@${activeProject.branch}]\n\n${finalText}`
      : `${phasePrefix}${finalText}`;
    // Iter 212m-58 — drive the LoopStepBar from here. On a fresh loop
    // turn we set `plan_pending` (waits for the model to emit the
    // [PLAN_READY] marker, then the SSE onToken below flips it to
    // plan_approved/pending the user's button click); on the execute
    // continuation we jump straight to `executing`.
    if (inLoop) {
      if (resolvedPhase === "execute") {
        setLoopPhase("executing");
      } else {
        setLoopPhase("plan_pending");
      }
      setLoopRetryCount(0);
    } else {
      setLoopPhase("idle");
    }
    // Show what the user actually typed PLUS a small attachment summary
    // so the bubble doesn't dump 60KB of markdown on screen.
    const displayContent = readyAttachments.length
      ? `${text || ""}${text ? "\n\n" : ""}_📎 ${readyAttachments.length} attachment${
          readyAttachments.length > 1 ? "s" : ""}: ${
          readyAttachments.map((a) => a.name).join(", ")}_`
      : text;
    setMessages((m) => [
      ...m,
      ...(opts.skipUserBubble
        ? []
        : [{ role: "user", content: displayContent }]),
      { role: "assistant", content: "", streaming: true, maxxMode, councilRecalled: 0 },
    ]);
    setBusy(true);

    // Iter 42 — drain captured F12 errors at send time. The store self-clears
    // after flush() so we don't double-report old errors on subsequent sends.
    const f12Payload = (typeof window !== "undefined" && window.__auremF12)
      ? window.__auremF12.flush()
      : null;
    lastF12PayloadRef.current = f12Payload;

    // Iter 212m-19 — fresh floating-card session: clear any leftover
    // steps from the previous turn so the new turn starts at zero.
    setLiveStepCard({ steps: [], provider: null, tokens: 0, visible: true });

    // Iter 212m-43 — stuck-thinking auto-recovery. Wrap streamChat in
    // a runner that (a) bumps `lastActivityRef` on every SSE callback,
    // (b) ticks an idle watchdog every 5s, (c) aborts + silently
    // retries the turn once if 90s of total silence elapse, and
    // (d) surfaces a clean error bubble if the retry also stalls.
    const IDLE_TIMEOUT_MS = 90_000;
    const WATCHDOG_TICK_MS = 5_000;
    const MAX_RETRIES = 1;
    retryAttemptRef.current = 0;

    const clearIdleWatchdog = () => {
      if (idleTimerRef.current) {
        clearInterval(idleTimerRef.current);
        idleTimerRef.current = null;
      }
    };
    const bumpActivity = () => {
      lastActivityRef.current = Date.now();
      // Iter 212m-57 — any stream activity = back to healthy. Only
      // touch state if we were showing a "slow"/"reconnecting" pill,
      // to avoid pointless re-renders on every token.
      setStreamHealth((cur) =>
        cur.phase === "idle"
          ? cur
          : { phase: "idle", silentFor: 0, retryEtaSec: null }
      );
    };

    const runTurn = async () => {
      const ctrl = new AbortController();
      abortRef.current = ctrl;
      let providerSeen = "";
      lastActivityRef.current = Date.now();
      clearIdleWatchdog();
      idleTimerRef.current = setInterval(() => {
        const idleFor = Date.now() - lastActivityRef.current;
        // Iter 212m-57 — proactive UX: show a "Network slow" pill as
        // soon as we cross 30s of silence so the user knows we're
        // still trying. Pill auto-clears on next bumpActivity().
        if (idleFor >= 30_000 && idleFor < IDLE_TIMEOUT_MS) {
          setStreamHealth({
            phase: "slow",
            silentFor: Math.floor(idleFor / 1000),
            retryEtaSec: Math.max(0, Math.ceil((IDLE_TIMEOUT_MS - idleFor) / 1000)),
          });
        }
        if (idleFor < IDLE_TIMEOUT_MS) return;
        // Stream has gone silent — abort and recover.
        clearIdleWatchdog();
        try { ctrl.abort(); } catch { /* ignore */ }
        abortRef.current = null;
        if (retryAttemptRef.current < MAX_RETRIES) {
          retryAttemptRef.current += 1;
          // Iter 212m-57 — surface a "Reconnecting…" pill with a short
          // countdown so the wait between abort and retry is explained.
          setStreamHealth({
            phase: "reconnecting", silentFor: Math.floor(idleFor / 1000),
            retryEtaSec: 3,
          });
          // Reset the streaming bubble so the retry starts clean and
          // the user sees a subtle "retrying…" hint instead of a
          // dead bubble.
          setMessages((msgs) => {
            const copy = msgs.slice();
            const last = copy[copy.length - 1];
            if (last && last.role === "assistant" && last.streaming) {
              copy[copy.length - 1] = {
                ...last,
                content: "",
                activity: "Reconnecting… (auto-recovery)",
                progressPct: 0,
                seenActivities: [],
                invocations: [],
                steps: [],
              };
            }
            return copy;
          });
          setLiveStepCard({ steps: [], provider: null, tokens: 0, visible: true });
          // Fire the retry asynchronously so the current interval
          // tick can unwind cleanly.
          setTimeout(() => { runTurn().catch(() => {}); }, 50);
        } else {
          // Retry budget exhausted — fail the bubble gracefully.
          setLiveStepCard(null);
          setMessages((msgs) => {
            const copy = msgs.slice();
            const last = copy[copy.length - 1];
            if (last && last.role === "assistant") {
              copy[copy.length - 1] = {
                ...last,
                content: "⏳ ORA seemed to get stuck. The request was auto-cancelled after 90s of silence. Hit Send again to retry.",
                error: true,
                streaming: false,
              };
            }
            return copy;
          });
          setBusy(false);
        }
      }, WATCHDOG_TICK_MS);

      await streamChat({
      prompt: finalPrompt,
      projectId: activeProject?.project_id || null,
      sessionId,
      maxToolIters: 2,
      maxxMode,
      agent,                       // iter 38: selector value
      mode: chatMode,              // Iter 153: swift / pro / maxx review
      executionMode: execMode,     // Iter 212m-58: prompt / loop
      f12Payload,                  // iter 42: console/network/stack errors
      signal: ctrl.signal,
      onMode: (m) => {
        bumpActivity();
        // Backend now sends a full payload: {type:"mode", mode, confidence,
        // scores, needs_confirm}. Older flows still pass a bare string.
        if (typeof m === "string") {
          setServerMode(m);
        } else {
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
        }
        // Iter 141 — second real progress milestone. Mode classification
        // has finished, so the orchestrator is now committed to a route
        // (chat / code / debug). Jump to 25%.
        setMessages((msgs) => {
          const copy = msgs.slice();
          const last = copy[copy.length - 1];
          if (last && last.role === "assistant" && last.streaming) {
            copy[copy.length - 1] = {
              ...last,
              progressPct: Math.max(last.progressPct || 0, 25),
            };
          }
          return copy;
        });
      },
      onOpsRedirect: (m) => { bumpActivity(); setOpsRedirect(m); },
      // Iter 212m-78 — Council self-learning indicator. Emitted by
      // the backend BEFORE token streaming when the retriever
      // returned >=1 past similar (user, ORA-reply) pair. Pin the
      // count on the assistant bubble so it can render the
      // "📚 ORA recalled N similar past answers" caption above the
      // reply. Silent (no caption) on 0.
      onCouncil: (n) => {
        bumpActivity();
        if (!n || n <= 0) return;
        setMessages((msgs) => {
          const copy = msgs.slice();
          const last = copy[copy.length - 1];
          if (last && last.role === "assistant" && last.streaming) {
            copy[copy.length - 1] = { ...last, councilRecalled: n };
          }
          return copy;
        });
      },
      // Iter 51 — SSE Task Progress Streamer. Mode D→C (and any auto
      // handoff) emits this BEFORE content streams. Pin the task_id on
      // the streaming assistant bubble so the ShipStatusCard renders
      // inline and polls live progress — user never has to leave chat.
      onTaskHandoff: (p) => {
        bumpActivity();
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
        bumpActivity();
        // Iter 141 — first real progress milestone. The meta frame
        // confirms the server accepted the request and routed it to
        // the orchestrator. Anchor the bar at 15% so the user gets
        // immediate visual proof the backend is alive.
        if (m && m.provider) providerSeen = m.provider;
        // Iter 212m-19 — surface model name on the floating card the
        // moment the orchestrator returns its first meta frame.
        if (m && m.provider) {
          setLiveStepCard((cur) => cur ? { ...cur, provider: m.provider } : cur);
        }
        setMessages((msgs) => {
          const copy = msgs.slice();
          const last = copy[copy.length - 1];
          if (last && last.role === "assistant" && last.streaming) {
            copy[copy.length - 1] = {
              ...last,
              meta: m,
              progressPct: Math.max(last.progressPct || 0, 15),
              ...(typeof m?.temperature === "number" ? { temperature: m.temperature } : {}),
              ...(m?.mode ? { mode: m.mode } : {}),
              ...(typeof m?.thinking_s !== "undefined" ? { thinkingS: m.thinking_s } : {}),
              ...(typeof m?.tool_calls_run !== "undefined" ? { toolCallsRun: m.tool_calls_run } : {}),
            };
          }
          return copy;
        });
      },
      // Iter 35/36: server emits periodic {thinking:true, elapsed_s, activity}
      // frames during the tool-call loop so we can show a live counter +
      // a status label ("running 3 tools in parallel…").
      // Iter 212m-19 — Live step cards. Each SSE `{type:"step", text,
      // done}` from the orchestrator (Iter 212m-18) lands here. We
      // append to the streaming bubble's `steps` array AND fan it out
      // to the floating-card state so the right-rail progress card
      // can render in parallel.
      onStep: (s) => {
        bumpActivity();
        const stepObj = {
          text: s.text || "",
          done: !!s.done,
          ts:   Date.now(),
        };
        setMessages((msgs) => {
          const copy = msgs.slice();
          const last = copy[copy.length - 1];
          if (last && last.role === "assistant" && last.streaming) {
            const steps = [...(last.steps || []), stepObj];
            copy[copy.length - 1] = { ...last, steps };
          }
          return copy;
        });
        setLiveStepCard((cur) => {
          const steps = [...((cur && cur.steps) || []), stepObj];
          return {
            steps,
            provider: (cur && cur.provider) || null,
            tokens:   (cur && cur.tokens)   || 0,
            visible:  true,
          };
        });
      },
      onThinking: (elapsed, activity, invocations) => {
        bumpActivity();
        // Iter 167 — fan out file paths from tool invocations so the
        // KnowledgeGraph drawer can glow the nodes ORA is touching
        // right now. Read / search tools count as "live" too — they
        // tell the user what part of the repo the agent is inspecting.
        try {
          if (Array.isArray(invocations) && invocations.length) {
            const files = [];
            for (const inv of invocations) {
              const a = inv?.args || {};
              if (typeof a.path === "string") files.push(a.path);
              if (Array.isArray(a.paths)) {
                for (const p of a.paths) if (typeof p === "string") files.push(p);
              }
            }
            if (files.length) {
              window.dispatchEvent(new CustomEvent("ora-editing", {
                detail: { files: Array.from(new Set(files)).slice(0, 50) },
              }));
            }
          }
        } catch { /* ignore */ }

        setMessages((msgs) => {
          const copy = msgs.slice();
          const last = copy[copy.length - 1];
          if (last && last.role === "assistant" && last.streaming) {
            // Iter 141 — real progress. Each new activity advances
            // the bar by 5pp, capped at 50% (the "first-token"
            // milestone). If activity stays the same, the bar
            // doesn't jiggle.
            const seen = last.seenActivities || [];
            const isNewActivity = activity && !seen.includes(activity);
            const newSeen = isNewActivity ? [...seen, activity] : seen;
            const stepProgress = Math.min(50, 25 + newSeen.length * 5);
            copy[copy.length - 1] = {
              ...last,
              elapsedS: elapsed,
              seenActivities: newSeen,
              progressPct: Math.max(last.progressPct || 0, stepProgress),
              ...(activity ? { activity } : {}),
              // Iter 149 — live tool invocation chips shown beneath the
              // thinking bar so the user sees what AUREM is doing right
              // now (read_repo_file, search_repo, execute_bash, etc.).
              ...(Array.isArray(invocations) ? { invocations } : {}),
            };
          }
          return copy;
        });
      },
      onToken: (tok) => {
        bumpActivity();
        // Iter 212m-19 — rough live token count for the floating card
        // footer ("glm-5.2 · 1.2k tokens"). Counts WORDS as a proxy
        // since the backend doesn't emit a token count per chunk —
        // good enough for the visible "X.Xk tokens" display.
        setLiveStepCard((cur) => {
          if (!cur) return null;
          const added = (tok || "").length;
          return { ...cur, tokens: (cur.tokens || 0) + Math.max(1, Math.round(added / 4)) };
        });
        setMessages((msgs) => {
          const copy = msgs.slice();
          const last = copy[copy.length - 1];
          if (last && last.role === "assistant") {
            // Iter 141 — token-driven progress. Once the first token
            // lands we jump to 55% and then incrementally close on
            // 95% per chunk (asymptotic so we never overshoot before
            // onDone fires).
            const baseline = Math.max(last.progressPct || 0, 55);
            const gap = 95 - baseline;
            // Each token chunk closes ~5% of the remaining gap.
            const nextPct = Math.min(95, baseline + gap * 0.05);
            copy[copy.length - 1] = {
              ...last,
              content: (last.content || "") + tok,
              progressPct: nextPct,
            };
          }
          return copy;
        });
      },
      onWatchdogPending: () => {
        bumpActivity();
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
        bumpActivity();
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
        clearIdleWatchdog();
        // Iter 212m-19 — mark the floating card done so it can
        // auto-close 3s later, and finalise its model+token footer.
        setLiveStepCard((cur) => {
          if (!cur) return null;
          const steps = cur.steps && cur.steps.length
            ? (() => {
                const lastIdx = cur.steps.length - 1;
                // Only mutate if the tail isn't already a done frame.
                if (cur.steps[lastIdx]?.done) return cur.steps;
                const copy = cur.steps.slice();
                copy[lastIdx] = { ...copy[lastIdx], done: true };
                return copy;
              })()
            : cur.steps;
          return {
            ...cur,
            steps,
            provider: d.provider || cur.provider || null,
            // Rough token estimate from the streaming bubble.
            tokens: cur.tokens || 0,
            visible: true,
          };
        });
        setMessages((msgs) => {
          const copy = msgs.slice();
          const last = copy[copy.length - 1];
          if (last && last.role === "assistant") {
            copy[copy.length - 1] = {
              ...last, streaming: false,
              // Iter 141 — final progress milestone. Snap to 100%
              // so the bar briefly shows fully-green right before
              // disappearing (the streaming:false flip removes the
              // bar from MessageBubble's render path).
              progressPct: 100,
              provider: d.provider || providerSeen || "—",
              council: !!(d.council || d.provider === "mode-b-council"),
              // Iter 161 — surface the review mode (swift/pro/maxx) on
              // the assistant bubble so MessageBubble can render the
              // "Written by …" / "Reviewed by …" agent chip.
              reviewMode: d.review_mode || null,
              verifiedPaths: Array.isArray(d.verified_paths)
                ? d.verified_paths
                : [],
              // Iter 119 — citation chips from Tavily / Firecrawl /
              // fetch_url. Rendered as 🌐 chips below the message.
              webSources: Array.isArray(d.web_sources)
                ? d.web_sources
                : [],
              // Iter 212m-49 — provenance of the LLM hop that actually
              // served this turn. When `is_emergency` is true the
              // chat header surfaces a "⚡ free mode" pill so the
              // founder knows OpenRouter credits are exhausted and
              // Groq is filling in.
              llmProvenance: d.llm_provenance && typeof d.llm_provenance === "object"
                ? d.llm_provenance
                : null,
            };
          }
          return copy;
        });
        setBusy(false);
        abortRef.current = null;
        // Iter 212m-57 — clear any lingering "slow/reconnecting" pill.
        setStreamHealth({ phase: "idle", silentFor: 0, retryEtaSec: null });
        // Iter 212m-58 — Loop-mode phase transition on chat completion.
        //   • Plan turn done   → freeze on plan_pending; PlanApprovalCard
        //     is rendered until the user approves or cancels.
        //   • Execute turn done → auto-advance through verify (visual
        //     only in Phase A) → security (live scan via /security-scan)
        //     → ship/done.
        if (execMode === EXEC_MODES.LOOP) {
          const justSentPhase = resolvedPhase || "plan";
          if (justSentPhase === "plan") {
            // Detect the [PLAN_READY] marker in the last assistant
            // message; if present, we know ORA honoured the loop
            // contract and the card should render.
            const finalContent = (d?.content || "") + "";
            const ready =
              finalContent.includes("[PLAN_READY]") ||
              finalContent.includes("PLAN_READY");
            setLoopPhase(ready ? "plan_pending" : "plan_pending");
          } else if (justSentPhase === "execute") {
            // Brief verify flash (Phase B will swap this for real
            // ruff/eslint), then auto-trigger the security scan.
            setLoopPhase("verifying");
            setTimeout(() => setLoopPhase("security"), 500);
            const pid = activeProject?.project_id;
            if (pid) {
              api.post("/security-scan/run", { project_id: pid })
                .then((r) => {
                  const payload = r?.data || r;
                  setCachedScan(pid, payload);
                  // Auto-pause on critical findings per spec.
                  const crit = payload?.summary?.by_severity?.critical || 0;
                  if (crit > 0) {
                    setLoopPhase("error");
                  } else {
                    setLoopPhase("shipping");
                    setTimeout(() => setLoopPhase("done"), 600);
                    setTimeout(() => setLoopPhase("idle"), 4500);
                  }
                })
                .catch(() => { setLoopPhase("error"); });
            } else {
              setLoopPhase("done");
              setTimeout(() => setLoopPhase("idle"), 3000);
            }
          }
        }
        onTurnSaved?.();
        setTimeout(() => onTurnSaved?.(), 2800);
        refreshUsage();
        // Bug #3 — return cursor to the input after reply
        setTimeout(() => taRef.current?.focus(), 80);
      },
      onError: (err) => {
        clearIdleWatchdog();
        // Iter 212m-19 — hide the floating card on error so it
        // doesn't sit there with an in-progress ⏳ forever.
        setLiveStepCard(null);
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
        // Iter 212m-57 — clear pill on terminal error too.
        setStreamHealth({ phase: "idle", silentFor: 0, retryEtaSec: null });
        // Iter 212m-58 — surface loop bar in error state if we were in
        // a loop run. errorStep maps current phase → which segment to
        // mark red.
        if (execMode === EXEC_MODES.LOOP && loopPhase !== "idle") {
          setLoopPhase("error");
        }
      },
    });
    };
    await runTurn();
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

  // ──────────────────────────────────────────────────────────────
  // Iter 212m-65 — Loop-mode user actions (Phase D wiring).
  // ──────────────────────────────────────────────────────────────
  // runLoopPlan: kick off /loop/start, render plan inside an
  // assistant bubble. The user must then approve via PlanApprovalCard
  // before any code execution begins.
  async function runLoopPlan(userText, readyAttachments, opts) {
    if (busy || !sessionId) return;
    // Cancel any prior loop session (defensive — should be a no-op).
    if (loopAbortRef.current) {
      try { loopAbortRef.current.abort(); } catch { /* swallow */ }
      loopAbortRef.current = null;
    }
    setLoopId(null);
    setLoopPlan(null);
    setSelfHeal({ visible: false, attempt: 1, max: 3, errorPreview: "" });
    setUserAction(null);
    setLoopRetryCount(0);
    setLoopPhase("plan_pending");

    // Compose the user message (text + any attachment markdown).
    const attachmentBlock = (readyAttachments || [])
      .map((a) => a.markdown)
      .filter(Boolean)
      .join("\n\n");
    const userBody = userText || "(see attached files)";
    const composed = attachmentBlock
      ? `${attachmentBlock}\n\n${userBody}`
      : userBody;
    const displayContent = (readyAttachments || []).length
      ? `${userText || ""}${userText ? "\n\n" : ""}_📎 ${readyAttachments.length} attachment${
          readyAttachments.length > 1 ? "s" : ""}: ${
          readyAttachments.map((a) => a.name).join(", ")}_`
      : userText;

    setMessages((m) => [
      ...m,
      ...(opts?.skipUserBubble ? [] : [{ role: "user", content: displayContent }]),
      { role: "assistant", content: "", streaming: true, loopPending: true },
    ]);
    setBusy(true);
    try {
      const resp = await startLoop({
        projectId: activeProject?.project_id || null,
        userMessage: composed,
      });
      const plan = resp?.plan || {};
      const lid  = resp?.loop_id;
      setLoopId(lid);
      setLoopPlan(plan);
      // Replace the pending assistant bubble with a rendered plan.
      const planMd = formatPlanMarkdown(plan);
      setMessages((m) => {
        const out = m.slice();
        for (let i = out.length - 1; i >= 0; i--) {
          if (out[i].role === "assistant" && out[i].loopPending) {
            out[i] = {
              role: "assistant",
              streaming: false,
              content: planMd,
              loopPlan: true,
              loop_id: lid,
            };
            break;
          }
        }
        return out;
      });
    } catch (e) {
      const msg = e?.response?.data?.detail || e?.message || "Loop start failed";
      setMessages((m) => {
        const out = m.slice();
        for (let i = out.length - 1; i >= 0; i--) {
          if (out[i].role === "assistant" && out[i].loopPending) {
            out[i] = {
              role: "assistant",
              streaming: false,
              content: `**Loop failed to start**\n\n${msg}`,
              error: true,
            };
            break;
          }
        }
        return out;
      });
      setLoopPhase("error");
    } finally {
      setBusy(false);
    }
  }

  // Pretty-print the plan dict into a markdown bullet list.
  function formatPlanMarkdown(plan) {
    if (!plan || typeof plan !== "object") return "_(no plan returned)_";
    const title = plan.title || "Plan";
    const eta   = plan.estimated_time ? ` — _${plan.estimated_time}_` : "";
    const bullets = Array.isArray(plan.bullets) ? plan.bullets : [];
    const files = Array.isArray(plan.files_to_change) ? plan.files_to_change : [];
    let out = `### ${title}${eta}\n\n`;
    if (bullets.length) {
      bullets.forEach((b, i) => { out += `${i + 1}. ${b}\n`; });
      out += "\n";
    }
    if (files.length) {
      out += `**Files to change:**\n`;
      files.forEach((f) => { out += `- \`${f}\`\n`; });
    }
    return out.trim() || "_Empty plan_";
  }

  // Map a single SSE event from the engine into UI state updates.
  function handleLoopEvent(ev) {
    if (!ev) return;
    const state = ev.state || "";
    const phase = ev.phase || "";
    const requiresAction = !!ev.requires_user_action;
    const data = ev.data || {};

    // Drive the existing LoopStepBar phase enum.
    if (state === "executing")     setLoopPhase("executing");
    else if (state === "verifying") setLoopPhase("verifying");
    else if (state === "scanning")  setLoopPhase("security");
    else if (state === "shipping")  setLoopPhase("shipping");
    else if (state === "completed") setLoopPhase("done");
    else if (state === "failed")    setLoopPhase("error");
    else if (state === "aborted")   setLoopPhase("idle");

    // Self-heal indicator visibility.
    if (state === "self_healing") {
      const preview = Array.isArray(data.errors_preview)
        ? (data.errors_preview[0] || "")
        : "";
      const m = /attempt\s+(\d+)\b/i.exec(ev.message || "");
      const attempt = m ? parseInt(m[1], 10) : 1;
      setSelfHeal({ visible: true, attempt, max: 3, errorPreview: preview });
      setLoopRetryCount(attempt);
    } else if (selfHeal.visible && state !== "self_healing") {
      setSelfHeal((s) => ({ ...s, visible: false }));
    }

    // User Action Card (paused-for-user).
    if (state === "paused_for_user" && requiresAction) {
      const errors = Array.isArray(data.errors)
        ? data.errors.map((e) => typeof e === "string" ? e : JSON.stringify(e))
        : (Array.isArray(data.findings)
            ? data.findings.map((f) => `${f.severity}: ${f.path || ""} — ${f.title || f.message || ""}`)
            : []);
      setUserAction({
        phase,
        message: ev.message || "Loop paused — your input needed.",
        errors,
      });
    } else if (!requiresAction && state !== "paused_for_user") {
      // Clear any prior action card the moment the engine moves on.
      if (state === "executing" || state === "verifying" || state === "scanning"
          || state === "shipping" || state === "completed") {
        setUserAction(null);
      }
    }

    // Append meaningful progress as a single growing assistant bubble.
    appendLoopBubble(ev);
  }

  // Maintain a single trailing assistant bubble that grows with the
  // engine's live commentary. Each phase header becomes a new section.
  function appendLoopBubble(ev) {
    setMessages((m) => {
      const out = m.slice();
      let idx = -1;
      for (let i = out.length - 1; i >= 0; i--) {
        if (out[i].role === "assistant" && out[i].loopLive) { idx = i; break; }
        // Stop scanning before any plan/user bubble.
        if (out[i].role === "user") break;
      }
      const line = renderEventLine(ev);
      if (!line) return out;
      const terminal = ev.state === "completed" || ev.state === "failed" || ev.state === "aborted";
      if (idx === -1) {
        out.push({
          role: "assistant",
          streaming: !terminal,
          content: line,
          loopLive: true,
        });
      } else {
        out[idx] = {
          ...out[idx],
          content: out[idx].content + "\n" + line,
          streaming: !terminal,
        };
      }
      return out;
    });
  }

  function renderEventLine(ev) {
    const ph = (ev.phase || "").toUpperCase();
    const st = ev.state || "";
    const ms = ev.message || "";
    if (st === "completed") return `**Step 5 / 5 — Ship**  ${ms}`;
    if (st === "failed")    return `**Failed**  ${ms}`;
    if (st === "aborted")   return `**Aborted**  ${ms}`;
    if (ph === "EXECUTE")   return `**Step 2 / 5 — Execute**  ${ms}`;
    if (ph === "VERIFY")    return `**Step 3 / 5 — Verify**  ${ms}`;
    if (ph === "SCAN")      return `**Step 4 / 5 — Security**  ${ms}`;
    if (ph === "SHIP")      return `**Step 5 / 5 — Ship**  ${ms}`;
    if (ph === "SELF_HEAL") return `_↻ ${ms}_`;
    return ms ? `· ${ms}` : "";
  }

  function openLoopStream(lid) {
    if (loopAbortRef.current) {
      try { loopAbortRef.current.abort(); } catch { /* swallow */ }
    }
    loopAbortRef.current = streamLoopEvents(lid, {
      onEvent: handleLoopEvent,
      onTerminal: () => {
        loopAbortRef.current = null;
        setBusy(false);
        setSelfHeal((s) => ({ ...s, visible: false }));
      },
      onError: (err) => {
        // Surface a soft notice; engine state still persisted in Mongo.
        const msg = err?.message || "Loop stream interrupted";
        setMessages((m) => {
          const out = m.slice();
          for (let i = out.length - 1; i >= 0; i--) {
            if (out[i].role === "assistant" && out[i].loopLive) {
              out[i] = {
                ...out[i],
                content: out[i].content + `\n\n_⚠ ${msg}_`,
                streaming: false,
              };
              break;
            }
          }
          return out;
        });
        setBusy(false);
      },
    });
  }

  async function handlePauseAction(action, feedback) {
    if (!loopId) return;
    setUserActionBusy(true);
    try {
      await pauseResponse(loopId, action, feedback || "");
      if (action === "abort") {
        // Stream will emit `aborted`; clear the card now.
        setUserAction(null);
      } else {
        // Engine resumes; clear the card and let SSE drive new events.
        setUserAction(null);
        setBusy(true);
      }
    } catch (e) {
      toast(e?.response?.data?.detail || e?.message || "Pause-response failed");
    } finally {
      setUserActionBusy(false);
    }
  }

  // Approve: Phase D (Iter 212m-65) — call /loop/{id}/confirm with
  // approved:true and open the SSE stream. The engine drives the
  // pipeline; we just react to events.
  async function handleApprovePlan() {
    if (busy || !loopId) return;
    setLoopPhase("executing");
    setBusy(true);
    try {
      await confirmLoop(loopId, true, "");
      openLoopStream(loopId);
    } catch (e) {
      toast(e?.response?.data?.detail || e?.message || "Loop confirm failed");
      setLoopPhase("error");
      setBusy(false);
    }
  }
  async function handleCancelPlan() {
    if (loopId) {
      try { await confirmLoop(loopId, false, "User cancelled before execute"); }
      catch { /* engine already cleaned up */ }
    }
    if (loopAbortRef.current) {
      try { loopAbortRef.current.abort(); } catch { /* swallow */ }
      loopAbortRef.current = null;
    }
    setLoopPhase("idle");
    setLoopId(null);
    setLoopPlan(null);
    pendingPlanRef.current = null;
  }
  // Toggle handler — switch exec mode and, when entering loop, force
  // chatMode away from "swift" (loop disables swift per spec).
  function handleExecModeChange(m) {
    if (m === EXEC_MODES.LOOP && chatMode === "swift") {
      lastPromptChatModeRef.current = chatMode;
      setChatMode("pro");
    } else if (m === EXEC_MODES.PROMPT && lastPromptChatModeRef.current) {
      // Restore previous selection (only if user had swift before).
      setChatMode(lastPromptChatModeRef.current);
      lastPromptChatModeRef.current = null;
    }
    setExecMode(m);
  }

  // Iter 212m-98 — Sidebar v2 Tools wiring. Lives here so it has
  // access to `handleExecModeChange` and reads latest execMode via
  // the refs declared in the toggle-preview effect above.
  useEffect(() => {
    const openVanguard = () => {
      const ap = sidebarWireRefs.current.activeProject;
      if (ap?.project_id && ap?.github_owner && ap?.github_repo) {
        setScanOpen(true);
      } else {
        try {
          window.dispatchEvent(new CustomEvent("aurem:toast", {
            detail: {
              message: "Connect a GitHub repo to run Vanguard scan",
              kind: "warn",
            },
          }));
        } catch { /* no-op */ }
      }
    };
    const toggleLoop = () => {
      const cur = sidebarWireRefs.current.execMode;
      handleExecModeChange(cur === EXEC_MODES.LOOP ? EXEC_MODES.PROMPT : EXEC_MODES.LOOP);
    };
    window.addEventListener("aurem:open-vanguard", openVanguard);
    window.addEventListener("aurem:toggle-loop", toggleLoop);
    return () => {
      window.removeEventListener("aurem:open-vanguard", openVanguard);
      window.removeEventListener("aurem:toggle-loop", toggleLoop);
    };
  }, []);

  // Phase D (Iter 212m-65): plan card renders the structured plan the
  // backend LoopEngine returned. We no longer rely on the model's
  // [PLAN_READY] marker — the engine itself owns the plan phase.
  const showPlanCard =
    execMode === EXEC_MODES.LOOP &&
    loopPhase === "plan_pending" &&
    !!loopPlan &&
    !!loopId &&
    !busy;

  return (
    <div
      data-testid="chat-root"
      data-chat-mode={chatMode}
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
          transition: "flex 240ms cubic-bezier(0.4,0,0.2,1), background-color 600ms ease",
          position: "relative",   // Iter 212m-19 — anchor for floating card
          // Iter 212m-30 PR-2 — Founder welcome tint (first 72 h after
          // signup; "transparent" otherwise so no visual cost long-term).
          backgroundColor: founderTint,
        }}
      >
      {/* Iter 212m-49 — "⚡ free mode" pill. Surfaces when the most
          recent assistant turn was served by the Groq emergency
          fallback (i.e. OpenRouter paid AND every free-tier candidate
          failed). Anchored top-left of the chat pane so it doesn't
          collide with the live step floating card on top-right.
          Auto-hides as soon as a non-emergency turn lands. */}
      {(() => {
        const lastAsst = [...messages].reverse().find((m) => m.role === "assistant");
        const prov = lastAsst && lastAsst.llmProvenance;
        if (!prov || !prov.is_emergency) return null;
        return (
          <div
            data-testid="free-mode-pill"
            title={`Served by Groq emergency fallback (model: ${prov.model || "llama-3.3-70b-versatile"}). Recharge OpenRouter credits to restore primary models.`}
            style={{
              position: "absolute",
              top: 14,
              left: 16,
              zIndex: 30,
              display: "inline-flex",
              alignItems: "center",
              gap: 6,
              padding: "5px 10px 5px 8px",
              borderRadius: 999,
              fontSize: 11,
              fontWeight: 600,
              letterSpacing: "0.02em",
              color: "#fde68a",
              background: "rgba(234,179,8,0.12)",
              border: "1px solid rgba(234,179,8,0.45)",
              backdropFilter: "blur(8px)",
              WebkitBackdropFilter: "blur(8px)",
              cursor: "default",
              userSelect: "none",
              animation: "auremFreeModeFadeIn 220ms ease-out",
            }}
          >
            <span aria-hidden="true" style={{ fontSize: 13, lineHeight: 1 }}>⚡</span>
            <span>free mode</span>
            <style>{`
              @keyframes auremFreeModeFadeIn {
                from { opacity: 0; transform: translateY(-4px); }
                to   { opacity: 1; transform: translateY(0); }
              }
            `}</style>
          </div>
        );
      })()}
      {/* Iter 212m-19 — Live step floating card. Pinned top-right of
          the chat panel while ORA is processing; auto-closes 3s after
          the orchestrator emits ✅ Done. Mirrors the same step events
          rendered inline via <StepCards/> so the user can glance at
          either spot. */}
      {liveStepCard && liveStepCard.visible
        && Array.isArray(liveStepCard.steps)
        && liveStepCard.steps.length > 0 && (
        <LiveStepFloatingCard
          steps={liveStepCard.steps}
          provider={liveStepCard.provider}
          tokens={liveStepCard.tokens}
          onClose={() => setLiveStepCard(null)}
        />
      )}
      {/* Iter 165 — Warm Start status bar. Renders while the 4
          background agents are pre-loading project context after a
          project select. Auto-hides on ready/idle. */}
      <WarmStatusBar status={warmStatus} progress={warmProgress} />
      <div
        data-testid="chat-messages"
        style={{
          flex: 1, overflowY: "auto",
          padding: "24px 28px",
          paddingRight: livePopupTaskId ? 392 : 28,
          display: "flex", flexDirection: "column", gap: 20,
          transition: "padding-right 0.2s ease",
        }}
      >
        {/* Iter 131 — Clear ↑ toolbar. Sits at the top of the
            scrollable message list and only renders when there's
            at least one real (non-WELCOME) turn to act on.
            Iter 163 — auto-hides on typing; top hot-zone peek
            brings it back independently of the sidebar/topbar. */}
        {messages.length > 1 && toolbarHidden && (
          <div
            data-testid="chat-toolbar-hotzone"
            onMouseEnter={onToolbarHotZoneEnter}
            onClick={onToolbarHotZoneEnter}
            title="Show chat toolbar"
            style={{
              position: "sticky", top: -24, zIndex: 3,
              margin: "-24px -28px 0 -28px",
              height: 8,
              cursor: "pointer",
              background: "transparent",
            }}
          />
        )}
        {messages.length > 1 && (
          <div
            data-testid="chat-toolbar"
            data-typing-hidden={toolbarHidden ? "true" : "false"}
            onMouseLeave={onToolbarMouseLeave}
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
              transform: toolbarHidden ? "translateY(-120%)" : "translateY(0)",
              opacity: toolbarHidden ? 0 : 1,
              pointerEvents: toolbarHidden ? "none" : "auto",
              transition: "transform 260ms cubic-bezier(0.4, 0, 0.2, 1), opacity 200ms ease",
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
          // Iter 167 — task id of the last assistant turn (for the
          // post-task scanner banner). Computed inline so the banner
          // appears only beneath that bubble, never duplicated.
          const lastTaskId = (
            i === messages.length - 1 && m.role === "assistant"
              ? (m.shipped_task_id || null)
              : null
          );
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
              {/* Iter 212m-78 — Council recall caption.  Renders only
                  when the backend RAG retriever surfaced past
                  examples for this turn (council_recalled > 0). */}
              {m.role === "assistant"
                && (m.councilRecalled || 0) > 0 && (
                <div
                  data-testid={`council-recall-caption-${i}`}
                  style={{
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 6,
                    margin: "4px 0 2px 4px",
                    padding: "3px 9px",
                    fontSize: 11,
                    fontFamily:
                      "ui-monospace, SFMono-Regular, JetBrains Mono, monospace",
                    color: "rgba(245,158,11,0.95)",
                    background: "rgba(245,158,11,0.06)",
                    border: "1px solid rgba(245,158,11,0.25)",
                    borderRadius: 999,
                    letterSpacing: 0.2,
                  }}
                  title="ORA Council self-learning — pulled similar past Q&A as few-shot context for this reply"
                >
                  📚 ORA recalled {m.councilRecalled} similar past
                  answer{m.councilRecalled === 1 ? "" : "s"}
                </div>
              )}
              <MessageBubble
                idx={i}
                dbTurnIndex={dbTurnIndex}
                m={m}
                onRegenerate={regenerate}
                sessionId={sessionId}
                activeProject={activeProject}
                exhausted={exhausted}
                onTaskCompleted={triggerTaskFollowup}
                onOpenDeployTab={openDeployTab}
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
              {isLastAssistant && lastTaskId && activeProject?.project_id && (
                <PostTaskScan
                  taskId={lastTaskId}
                  projectId={activeProject.project_id}
                  onFixRequest={(prompt) => sendSuggestion(prompt)}
                />
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

      {/* Iter 212m-36 — composer status bar + token banner moved
          OUTSIDE the form so they sit ABOVE the founder-offer banner
          (per the user-locked layout: status updates → offer →
          composer, all flowing visually into each other). */}
      <TokenBanner usage={usage} />

      <div className="composer-status-bar" data-testid="composer-status-bar">
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
            setTimeout(() => {
              const form = taRef.current && taRef.current.form;
              if (form) form.requestSubmit();
            }, 50);
          }}
        />
      </div>

      {/* Iter 212m-58 — Prompt / Loop mode toggle (REMOVED in 212m-90 per
          founder spec; chat composer should be lean, only icon toolbar
          stays. Loop mode is still accessible via the icon button in
          the composer's right-hand toolbar). */}
      {/* <LoopModeToggle value={execMode} onChange={handleExecModeChange} /> */}

      {/* Iter 212m-58 — 5-step progress bar.  Renders only when the
          loop pipeline is active.  Wires into `loopPhase` set by
          send() and onDone above. */}
      <LoopStepBar
        phase={execMode === EXEC_MODES.LOOP ? loopPhase : "idle"}
        retryCount={loopRetryCount}
        errorStep={loopPhase === "error" ? 2 : 0}
      />

      {/* Iter 212m-58 — Plan approval card.  Renders the moment the
          plan-turn ends; user must click Approve before any code
          execution starts.  Cancel resets the loop. */}
      {showPlanCard && (
        <PlanApprovalCard
          onApprove={handleApprovePlan}
          onCancel={handleCancelPlan}
        />
      )}

      {/* Iter 212m-65 — Phase D wiring: live self-heal strip + paused
          user-action card driven by the /loop/{id}/stream SSE feed. */}
      <SelfHealIndicator
        visible={selfHeal.visible}
        attempt={selfHeal.attempt}
        max={selfHeal.max}
        errorPreview={selfHeal.errorPreview}
      />
      {userAction && (
        <UserActionCard
          phase={userAction.phase}
          message={userAction.message}
          errors={userAction.errors}
          busy={userActionBusy}
          onAction={handlePauseAction}
        />
      )}

      {/* Iter 212m-35 — Founder Offer attached to the TOP of the
          composer. Rounded top corners flow visually into the form
          below (which has a flat top edge here). Auto-hides when
          has_fully_claimed, sold-out, or >3 days since signup. */}
      <FounderOfferCard projectId={activeProject?.project_id} />

      {/* Iter 212m-57 — Stream health pill (slow / reconnecting). Sits
          directly above the composer so the user has clear feedback
          when the SSE stream stalls — previously the chat just looked
          frozen for up to 90s before silently auto-recovering. */}
      <StreamHealthPill state={streamHealth} />

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
          // Iter 212m-37 — match the founder banner's amber side
          // borders so the offer + composer read as one unified
          // container.  Bottom corners stay rounded; top stays flat
          // (the banner's rounded top corners cap the whole stack).
          borderLeft: "1px solid rgba(234,179,8,0.45)",
          borderRight: "1px solid rgba(234,179,8,0.45)",
          borderBottom: "1px solid rgba(234,179,8,0.45)",
          borderBottomLeftRadius: 12,
          borderBottomRightRadius: 12,
          // Iter 212m-54 — composer background now inherits from the
          // parent chat panel via `transparent`, so the seam between
          // chat-area and composer disappears in every mode (Swift /
          // Pro / Maxx / founder-tint). The amber side borders stay
          // since they belong to the founder offer banner stack.
          background: "transparent",
        }}
      >
        {/* Iter 212m-36 — TokenBanner + composer-status-bar moved
            outside the form (above the FounderOfferCard). The form
            now starts directly with the textarea wrapper. */}
          {/* Iter 158 — tier-aware thinking-state upsell pill.
              Appears 600ms into a chat-busy cycle, slides in with a
              soft amber glow, and converts dead wait time into a
              feature/upsell CTA. Founder tier renders nothing. */}
          <ThinkingHint busy={busy} />
          {/* Iter 148 — explicit warning when the active project has
              no GitHub repo wired up. Click opens a guided helper dialog
              with the exact 3 steps to connect one. The dialog drives
              both this pill and the toolbar's red GH-status indicator. */}
          {activeProject &&
            !(activeProject.github_owner && activeProject.github_repo) && (
            <button
              type="button"
              data-testid="no-repo-warning-pill"
              onClick={() => setShowRepoHelp(true)}
              title={`No GitHub repo connected to ${activeProject.name}`}
              style={{
                display: "inline-flex", alignItems: "center", gap: 6,
                fontSize: 10, fontFamily: "'JetBrains Mono', monospace",
                letterSpacing: "0.12em",
                color: "#ff8a8a",
                padding: "4px 10px",
                border: "1px solid rgba(239,68,68,0.4)",
                borderRadius: 999,
                background: "rgba(239,68,68,0.10)",
                cursor: "pointer",
                animation: "aurem-no-repo-blink 2.2s ease-in-out infinite",
              }}
            >
              ⚠ No repo linked — click to connect
            </button>
          )}

        {/* Iter 59 — Attachment pills. */}
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

        {/* Iter 147 — unified composer card: textarea + toolbar share
            one rounded surface so it reads as a single chat input. */}
        <div className="composer-card" data-testid="composer-card">
        <textarea
          ref={taRef}
          data-testid="chat-input"
          className="composer-input-bare"
          value={input}
          onChange={(e) => {
            const v = e.target.value;
            setInput(v);
            setDetectedMode(detectMode(v));
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
          placeholder={
            execMode === EXEC_MODES.LOOP
              ? "Describe the feature / fix — ORA plans → approves → ships."
              : "Ask ORA to build, fix, or scan..."
          }
          rows={Math.min(6, Math.max(2, input.split("\n").length))}
          autoFocus
          disabled={busy || exhausted}
        />

        {/* Toolbar — inside the same card as the textarea */}
        <div className="composer-toolbar">
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
            <span style={{ marginRight: 4 }} />
          )}
          <ToolButton
            testid="chat-attach-btn"
            title="Attach file — PDF, DOCX, XLSX, PPTX, images, code (max 25 MB)"
            onClick={() => fileInputRef.current?.click()}
            Icon={Paperclip}
            wide
          />
          {/* Iter 165 — Codebase Graph drawer toggle. Visible only when
              a real project is active so the toolbar stays clean on
              the home/scratch view. */}
          {activeProject?.project_id && activeProject.project_id !== "home" && (
            <ToolButton
              testid="graph-toggle-btn"
              title="Codebase graph — visualise file relationships"
              onClick={() => setGraphOpen((v) => !v)}
              Icon={Network}
              active={graphOpen}
              wide
            />
          )}
          {/* Iter 212m-55 — 1-click security scanner. Shield icon opens
              right-side drawer with vulnerability findings. Only shown
              when a real project with a connected GitHub repo is
              active (the scanner needs an owner/repo/PAT to read).
              Iter 212m-56 — red dot badge with critical+high count
              when the latest cached scan found findings. Same pattern
              as the GitHub status dot above. */}
          {activeProject?.project_id
            && activeProject.project_id !== "home"
            && activeProject?.github_owner
            && activeProject?.github_repo && (
            <span style={{ position: "relative", display: "inline-flex" }}>
              <ToolButton
                testid="chat-security-scan-btn"
                title={
                  execMode === EXEC_MODES.LOOP
                    ? "Auto — Shield runs automatically at Step 4 of every loop. Click to view findings."
                    : scanCounts && (scanCounts.critical + scanCounts.high) > 0
                      ? `${scanCounts.critical} critical • ${scanCounts.high} high vulnerabilities — click to view`
                      : "Run 1-click security scan on this repo"
                }
                onClick={() => setScanOpen((v) => !v)}
                Icon={ShieldCheck}
                active={scanOpen}
                wide
              />
              {/* Iter 212m-58 — In loop mode show an AUTO badge on the
                  shield so the user understands the scanner will fire
                  automatically.  Critical/high finding count badge
                  still wins if any exist. */}
              {execMode === EXEC_MODES.LOOP && !(scanCounts && (scanCounts.critical + scanCounts.high) > 0) && (
                <span
                  data-testid="chat-security-scan-auto-badge"
                  style={{
                    position: "absolute",
                    bottom: -4, right: -4,
                    padding: "0 5px", height: 12,
                    borderRadius: 999,
                    background: "linear-gradient(90deg, #a855f7, #6366f1)",
                    color: "#fff",
                    fontSize: 8.5, fontWeight: 800, letterSpacing: 0.4,
                    fontFamily: "'JetBrains Mono', monospace",
                    display: "inline-flex",
                    alignItems: "center", justifyContent: "center",
                    boxShadow: "0 0 6px rgba(168,85,247,0.7)",
                    pointerEvents: "none",
                  }}
                >
                  AUTO
                </span>
              )}
              {scanCounts && (scanCounts.critical + scanCounts.high) > 0 && (
                <span
                  data-testid="chat-security-scan-badge"
                  aria-label={`${scanCounts.critical + scanCounts.high} high-severity findings`}
                  style={{
                    position: "absolute",
                    top: -4, right: -4,
                    minWidth: 16, height: 16,
                    padding: "0 4px",
                    borderRadius: 999,
                    background: scanCounts.critical > 0 ? "#ef4444" : "#f97316",
                    color: "#0a0a0a",
                    fontSize: 9.5,
                    fontWeight: 700,
                    fontFamily: "'JetBrains Mono', monospace",
                    display: "inline-flex",
                    alignItems: "center", justifyContent: "center",
                    boxShadow: scanCounts.critical > 0
                      ? "0 0 6px rgba(239,68,68,0.7)"
                      : "0 0 6px rgba(249,115,22,0.7)",
                    pointerEvents: "none",
                  }}
                >
                  {scanCounts.critical + scanCounts.high > 99
                    ? "99+"
                    : scanCounts.critical + scanCounts.high}
                </span>
              )}
            </span>
          )}
          {/* Iter 212m-93 — REMOVED "Vanguard active" green pill from
              composer per founder spec (matches v0 lean look). The
              security guarantee is still surfaced via the bottom
              caption "ORA · Vanguard reviews every change before it
              ships." and via the Shield icon in the toolbar. */}
          {/* Iter 146 — passive GitHub status indicator.
              Green dot = active project has a connected repo (push works
              from the Projects page). Red dot = no repo configured.
              Click routes to Projects so the user can wire one up — we
              no longer expose AUREM-owned PAT pushes to end users. */}
          <button
            type="button"
            data-testid="chat-github-status"
            title={
              activeProject?.github_owner && activeProject?.github_repo
                ? `GitHub: connected — ${activeProject.github_owner}/${activeProject.github_repo}`
                : "GitHub: not connected — click to configure in Projects"
            }
            onClick={() => {
              // Iter 148 — open the in-place helper dialog instead of
              // navigating away. If a repo is already connected we go
              // straight to /projects so the user can manage it.
              const connected = !!(activeProject?.github_owner && activeProject?.github_repo);
              if (connected) window.location.href = "/projects";
              else setShowRepoHelp(true);
            }}
            style={{
              position: "relative",
              display: "inline-flex",
              alignItems: "center",
              justifyContent: "center",
              width: 38,
              height: 32,
              borderRadius: 8,
              background: "transparent",
              border: "1px solid var(--border, rgba(255,200,120,0.16))",
              cursor: "pointer",
              color: "var(--text-dim)",
            }}
          >
            <Github size={15} />
            <span
              aria-hidden="true"
              style={{
                position: "absolute",
                top: 4,
                right: 4,
                width: 6,
                height: 6,
                borderRadius: 999,
                background: (activeProject?.github_owner && activeProject?.github_repo)
                  ? "#22c55e"
                  : "#ef4444",
                boxShadow: (activeProject?.github_owner && activeProject?.github_repo)
                  ? "0 0 6px rgba(34,197,94,0.7)"
                  : "0 0 6px rgba(239,68,68,0.7)",
              }}
            />
          </button>
          {/* Iter 154 — legacy `chat-maxx-btn` removed. Maxx is now
              selected via the ModeSelector pill on the right; the
              standalone toggle was redundant and confused users. */}
          <span style={{ flex: 1 }} />
          {/* Iter 212m-97 — REMOVED in-composer <ModeSelector>. Swift/
              Pro/Maxx now lives ONLY in the TopBar (single source of
              truth). The two are kept in sync via the
              `aurem:set-chat-mode` ↔ `aurem:chat-mode-changed`
              custom-event bridge so the chat backend still receives
              the right `mode` payload. */}
          {/* Iter 145 — agent selector hidden. AUREM is default for
              everyone; ORA runs as a silent shadow-learner in the
              backend (see services/ora_learning.py). */}
          {false && agents.length > 1 && (
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
              <Send size={14} /> {execMode === EXEC_MODES.LOOP ? "Run loop" : "Send"}
            </button>
          )}
        </div>
        </div>

        {/* Iter 212m-93 — v0-spec composer footer caption. Replaces the
            old "Vanguard active" pill — same security message in a
            calmer place. */}
        <div data-testid="composer-footer-caption" style={{
          textAlign: "center", marginTop: 8,
          fontSize: 10, color: "var(--text-faint, #666)",
          fontFamily: "'JetBrains Mono', monospace",
          letterSpacing: "0.04em",
        }}>
          ORA · Vanguard reviews every change before it ships.
        </div>
      </form>

      {/* Iter 148 — Connect-repo helper dialog. Surfaces only when the
          user explicitly opens it (no-repo pill or red GH status icon).
          Shows the 3-step flow + a deep link straight to Projects. */}
      {showRepoHelp && (
        <RepoHelpDialog
          project={activeProject}
          onClose={() => setShowRepoHelp(false)}
          onOpenProjects={() => {
            setShowRepoHelp(false);
            window.location.href = "/projects";
          }}
        />
      )}

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
            key={`${activeProject?.project_id || "home"}::${previewInitialMode}`}
            initialViewMode={previewInitialMode}
            blocks={finalBlocks.length > 0 ? finalBlocks : [{
              lang: "text",
              code: activeProject
                ? `No preview URL set for "${activeProject.name}". Open Projects → Edit → "Live preview URL" to add one (e.g. https://yoursite.com).`
                : "No code blocks in the current chat yet. Ask AUREM to write some — Hint: ```html ... ``` or ```jsx ... ``` will render live here.",
            }]}
            onClose={() => {
              togglePreview();
              setPreviewInitialMode("preview");
            }}
            activeProject={activeProject}
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
      {/* Iter 165 — Codebase Graph drawer (right side). */}
      <GraphPanel
        projectId={activeProject?.project_id}
        open={graphOpen}
        onClose={() => setGraphOpen(false)}
      />
      {/* Iter 212m-55 — Security scan drawer (right side). */}
      <SecurityScanDrawer
        projectId={activeProject?.project_id}
        projectLabel={
          activeProject?.github_owner && activeProject?.github_repo
            ? `${activeProject.github_owner}/${activeProject.github_repo}`
            : activeProject?.name
        }
        open={scanOpen}
        onClose={() => setScanOpen(false)}
      />
    </div>
  );
}

function ToolButton({ testid, title, onClick, Icon, active, className, wide }) {
  // Iter 154 — `wide` lifts the button from 34×34 → 42×34 so the two
  // remaining toolbar buttons (Attach + GitHub) read clearer now that
  // the Maxx toggle has been retired.
  const w = wide ? 42 : 34;
  return (
    <button
      type="button"
      data-testid={testid}
      title={title}
      onClick={onClick}
      className={className}
      style={{
        width: w, height: 34, borderRadius: wide ? 8 : 4,
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
      <Icon size={wide ? 15 : 14} />
    </button>
  );
}

/**
 * StreamHealthPill — Iter 212m-57.
 * Tiny inline status pill that sits above the composer when the SSE
 * chat stream is stalling. Driven by `streamHealth` state in
 * ChatPanel:
 *   • phase === 'slow'         → amber dot + "Slow response… {n}s of
 *                                 silence — will auto-retry in {m}s"
 *   • phase === 'reconnecting' → red dot + "Reconnecting…"
 *   • phase === 'idle'         → renders nothing (null)
 * No close button — auto-clears on next token / done / error / Stop.
 */
function StreamHealthPill({ state }) {
  if (!state || state.phase === "idle") return null;
  const isReconnect = state.phase === "reconnecting";
  const color = isReconnect ? "#ef4444" : "#f59e0b";
  const label = isReconnect
    ? `Reconnecting after ${state.silentFor}s of silence…`
    : `Slow response — ${state.silentFor}s silent` +
      (state.retryEtaSec != null ? `, auto-retry in ${state.retryEtaSec}s` : "");
  return (
    <div
      data-testid="chat-stream-health-pill"
      data-stream-phase={state.phase}
      role="status"
      aria-live="polite"
      style={{
        display: "flex", alignItems: "center", gap: 8,
        padding: "6px 12px",
        margin: "6px 12px 0",
        borderRadius: 999,
        background: isReconnect
          ? "rgba(239,68,68,0.10)"
          : "rgba(245,158,11,0.10)",
        border: `1px solid ${isReconnect
          ? "rgba(239,68,68,0.45)"
          : "rgba(245,158,11,0.45)"}`,
        color, fontSize: 11.5,
        fontFamily: "'JetBrains Mono', monospace",
        animation: isReconnect ? "pillPulse 1.2s ease-in-out infinite" : "none",
      }}
    >
      <span
        style={{
          width: 8, height: 8, borderRadius: "50%",
          background: color,
          boxShadow: `0 0 6px ${color}`,
          flexShrink: 0,
        }}
      />
      <span style={{ flex: 1, minWidth: 0 }}>{label}</span>
      <style>{`
        @keyframes pillPulse {
          0%, 100% { opacity: 1; }
          50%      { opacity: 0.55; }
        }
      `}</style>
    </div>
  );
}

/**
 * RepoHelpDialog — Iter 148.
 * Lightweight modal that explains exactly how to wire a GitHub repo to
 * the currently active project. We surface it instead of pushing the
 * user out to `/projects` blind so they understand the *why* before
 * being asked to enter owner/repo. Keeps shipping unblocked.
 */
function RepoHelpDialog({ project, onClose, onOpenProjects }) {
  return (
    <div
      data-testid="repo-help-overlay"
      onClick={onClose}
      style={{
        position: "fixed", inset: 0, zIndex: 1000,
        background: "rgba(8, 11, 18, 0.62)",
        backdropFilter: "blur(8px)",
        display: "flex", alignItems: "center", justifyContent: "center",
        padding: 24,
      }}
    >
      <div
        data-testid="repo-help-dialog"
        onClick={(e) => e.stopPropagation()}
        style={{
          width: "min(520px, 100%)",
          background: "linear-gradient(180deg, rgba(20,24,34,0.95), rgba(13,16,24,0.95))",
          border: "1px solid rgba(255,138,42,0.28)",
          borderRadius: 14,
          padding: "24px 26px",
          boxShadow: "0 20px 60px rgba(0,0,0,0.55), 0 0 0 1px rgba(255,255,255,0.02) inset",
          color: "var(--text)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 14 }}>
          <span
            style={{
              width: 32, height: 32, borderRadius: 8,
              background: "rgba(239,68,68,0.16)",
              border: "1px solid rgba(239,68,68,0.5)",
              display: "inline-flex", alignItems: "center", justifyContent: "center",
              fontSize: 16,
            }}
          >⚠</span>
          <div style={{ flex: 1 }}>
            <div style={{
              fontSize: 14, fontWeight: 600, letterSpacing: "0.02em",
            }}>No GitHub repo connected</div>
            <div style={{ fontSize: 11, color: "var(--text-dim)", marginTop: 2 }}>
              {project?.name
                ? `Project "${project.name}" is not linked to any repository yet.`
                : "Select a project first, then link a repository."}
            </div>
          </div>
        </div>

        <div style={{
          fontSize: 12, color: "var(--text-dim)", lineHeight: 1.55,
          marginBottom: 14,
        }}>
          Once linked, AUREM can commit code changes directly to your
          GitHub repository on every successful task. Without a repo,
          shipped work stays inside the chat session only.
        </div>

        <div style={{
          background: "rgba(255,255,255,0.02)",
          border: "1px solid var(--border)",
          borderRadius: 10,
          padding: "14px 16px",
          marginBottom: 16,
        }}>
          <div style={{
            fontSize: 10, fontFamily: "'JetBrains Mono', monospace",
            letterSpacing: "0.18em", color: "var(--accent-2)",
            marginBottom: 10,
          }}>HOW TO CONNECT — 3 STEPS</div>
          <ol style={{ margin: 0, paddingLeft: 18, fontSize: 13, lineHeight: 1.7 }}>
            <li>Open the <strong>Projects</strong> page from the sidebar (or click the button below).</li>
            <li>Click <strong>Edit</strong> on the project card you want to connect.</li>
            <li>Fill in <code style={{
              background: "rgba(255,255,255,0.06)", padding: "1px 6px",
              borderRadius: 4, fontSize: 11,
            }}>github_owner</code> and <code style={{
              background: "rgba(255,255,255,0.06)", padding: "1px 6px",
              borderRadius: 4, fontSize: 11,
            }}>github_repo</code> with your repository details, then save.</li>
          </ol>
        </div>

        <div style={{
          fontSize: 11, color: "var(--text-faint)", marginBottom: 16,
          padding: "8px 12px", background: "rgba(255,197,96,0.06)",
          border: "1px solid rgba(255,197,96,0.2)", borderRadius: 8,
        }}>
          💡 Tip: the repo must already exist on GitHub and your AUREM
          installation must have push access. New repo?{" "}
          <a
            href="https://github.com/new"
            target="_blank"
            rel="noopener noreferrer"
            style={{ color: "var(--accent-2)", textDecoration: "underline" }}
          >Create one here</a>.
        </div>

        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
          <button
            type="button"
            data-testid="repo-help-cancel"
            onClick={onClose}
            className="btn-ghost"
            style={{ fontSize: 12 }}
          >
            Later
          </button>
          <button
            type="button"
            data-testid="repo-help-open-projects"
            onClick={onOpenProjects}
            className="btn-primary"
            style={{ fontSize: 12, gap: 6 }}
          >
            Open Projects →
          </button>
        </div>
      </div>
    </div>
  );
}

