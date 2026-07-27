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
import { api, streamChat, API_BASE, getToken, getUser, isAdminOrFounder } from "../lib/api";
import { toast, dismissToast } from "./Toast";
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
import IntentTierIndicator from "./IntentTierIndicator";
import LoopStepBar from "./LoopStepBar";
import LoopStatusChip from "./LoopStatusChip";        // Iter 309 · Batch-2 aftermath — sticky loop-status chip
import AgentStatusBar from "./AgentStatusBar";
import LoopLiveFeed from "./LoopLiveFeed";
import PlanApprovalCard from "./PlanApprovalCard";
// Iter 212m-65 — Phase D wiring: Self-heal indicator + paused-loop
// User Action card (powered by the real /loop/* SSE stream).
import { SelfHealIndicator, UserActionCard } from "./LoopActionCards";
import ShipPendingCard from "./ShipPendingCard";
import {
  startLoop, confirmLoop, pauseResponse, cancelLoop, streamLoopEvents,
  confirmShip,
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

// Iter 212m-153 — leaf components + pure helpers extracted from
// ChatPanel.jsx into focused files.  Behaviour is identical; the
// imports below replace in-file definitions that previously lived
// at the top + bottom of this file (~700 LOC).
import TokenBanner       from "./chat/TokenBanner";
import ToolButton        from "./chat/ToolButton";
import StreamHealthPill  from "./chat/StreamHealthPill";
import RepoHelpDialog    from "./chat/RepoHelpDialog";
import ScanStatusStrip, { markScanJustCompleted } from "./ScanStatusStrip";        // Iter 212m-190 · Session 3
import SlashCommandMenu, { matchSlashCommands } from "./SlashCommandMenu";         // Iter 212m-190 · Session 3
import {
  isLoopUnlockedSync,
  extractSuggestions,
  extractCodeBlocks,
  estimateTokenCount,
} from "../utils/chatTextUtils";

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

// Iter 212m-153 — `CODE_BLOCK_RE`, `SUGGESTION_RX`, `isLoopUnlockedSync`,
// `extractSuggestions`, `extractCodeBlocks`, `estimateTokenCount`, and
// the `<TokenBanner>` component were extracted into focused modules.
// Their imports live at the top of this file alongside the chat hooks.

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
  // Iter 212m-190 · Session 3 — Chat-native scan commands. Slash-menu
  // is a controlled popover triggered by inputs starting with "/", and
  // ScanStatusStrip surfaces scan lifecycle events above the composer.
  const [slashOpen, setSlashOpen] = useState(false);
  const [slashIdx, setSlashIdx]   = useState(0);
  const [scanState, setScanState] = useState("idle");
  // Iter 212m-149 — Intent Gateway last-known tier for the indicator.
  // Updated when an SSE `intent` frame arrives during a chat turn.
  const [lastIntentTier, setLastIntentTier] = useState(null);
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
  //
  // Iter 212m-130 — Loop Mode is temporarily locked to founder /
  // admin / unlimited accounts. Non-founder users see the
  // "LOOP · SOON" pill (Lock icon) and clicking it fires a toast
  // instead of toggling. We also force any stale localStorage
  // `loop` value back to `prompt` on mount so the chat doesn't
  // silently send `execution_mode:"loop"` for non-founders.
  const isLoopUnlocked = useMemo(() => {
    const u = (typeof getUser === "function" && getUser()) || null;
    return !!(u && (u.is_admin || u.is_unlimited || u.tier === "founder"));
  }, []);
  const [execMode, setExecMode] = useState(() => {
    const m = loadExecMode();
    if (m === EXEC_MODES.LOOP && !isLoopUnlockedSync()) {
      try { saveExecMode(EXEC_MODES.PROMPT); } catch { /* ignore */ }
      return EXEC_MODES.PROMPT;
    }
    return m;
  });
  // Loop pipeline state. `phase` drives the LoopStepBar and decides
  // whether to render the PlanApprovalCard. `retryCount` is reserved
  // for future verify-loop auto-retry UX (max 3).
  const [loopPhase, setLoopPhase] = useState("idle");
  const [loopRetryCount, setLoopRetryCount] = useState(0);

  // ── Iter 309 · Live Narration state ────────────────────────────────
  // `loopStepTones` — derived from real backend narration events with
  // `data.type === "narration"`. Maps narration_step → the LATEST
  // tone we've seen for that step, so LoopStepBar's ECG strip can
  // render active/success/danger correctly.
  //
  // Rule (matches `foldNarrations` in LoopLiveFeed): later events on
  // the same step OVERWRITE earlier ones, so a pending → success
  // transition correctly resolves the strip to green. `null` /
  // missing = future step (untouched).
  //
  // On SSE reconnect + gap replay (Item 6), events are re-delivered
  // in order via `handleLoopEvent`, and this state is rebuilt from
  // the replayed events — so a resolved-green step will NOT flicker
  // back to "active" after a reconnect (same guarantee foldNarrations
  // provides in the feed).
  const [loopStepTones, setLoopStepTones] = useState({});
  // Iter 288 — the actual phase that failed. Was previously hardcoded
  // to EXECUTE (step 2) inside the LoopStepBar props, which meant a
  // ship-time or verify-time failure would incorrectly paint EXECUTE
  // red. Now we remember the phase from the failed SSE frame.
  const [loopErrorPhase, setLoopErrorPhase] = useState(null);
  // The pending plan message id — once the user approves, we continue
  // the same session with a `LOOP_PHASE:execute` follow-up.
  const pendingPlanRef = useRef(null);

  // Iter 212m-130 — "Coming Soon" toast for non-founders who click
  // the locked LoopModeToggle pill. The toggle fires the global
  // event; we surface a friendly explanation here instead of
  // silently no-op'ing the click.
  useEffect(() => {
    if (isLoopUnlocked) return undefined;
    const onLocked = () => {
      try {
        toast(
          "Loop Mode — coming soon. We're polishing the Plan → Execute → "
          + "Verify → Scan → Ship pipeline. It will unlock for all "
          + "developers shortly.",
          "info",
        );
      } catch { /* ignore */ }
    };
    window.addEventListener("aurem:loop-coming-soon", onLocked);
    return () => window.removeEventListener("aurem:loop-coming-soon", onLocked);
  }, [isLoopUnlocked]);
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
  // Iter 275 — dedicated live-feed panel state. `loopFeedEvent` is
  // the LAST raw SSE event; `LoopLiveFeed` maintains its own ring
  // buffer downstream. `loopTerminal` flips true on completed/
  // failed/aborted so the pulsing indicator turns steady.
  const [loopFeedEvent, setLoopFeedEvent] = useState(null);
  const [loopTerminal, setLoopTerminal] = useState(false);
  // Iter 288 — synchronous guard against late/out-of-order SSE frames
  // (heartbeats or per-file "executing" events from parallel tasks
  // whose queue.put() awaited across `_fail`'s _emit). State updates
  // are async, so once we've decided the loop is terminal we ALSO
  // flip this ref immediately — every subsequent handleLoopEvent
  // consults it synchronously before mutating loopPhase / feed.
  const loopTerminalRef = useRef(false);
  // Iter 212m-111 — Manual Ship gate. Populated when the engine emits
  // a paused_for_user event with data.kind === "awaiting_ship".
  // `{owner, repo, branch, files, file_count, commit_message}`.
  const [shipPending, setShipPending] = useState(null);
  const [shipBusy, setShipBusy] = useState(false);
  const loopAbortRef = useRef(null);
  // Iter 316 · Fix A — handle for the /loop/active fallback-poll
  // interval so we can clearInterval when SSE wins the race OR when
  // the plan is absorbed OR when the loop is cancelled.
  const loopFallbackPollRef = useRef(null);

  // Iter 284 — visible queue indicator.  When a user submits during
  // busy state, the message auto-queues via the 409 flow.  This
  // counter surfaces above the composer as "N queued" so the queue
  // is discoverable + auditable.
  const [queuedCount, setQueuedCount] = useState(0);

  // Iter 212m-117 — Rehydrate paused-Ship state on mount. If the user
  // had a Loop paused at the manual Ship gate and refreshed the
  // browser, this re-populates the ShipPendingCard so they can resume
  // without losing the work. PAT is already scrubbed server-side.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const projectId = activeProject?.project_id;
        if (!projectId) return;
        const r = await api.get(`/loop/active?project_id=${encodeURIComponent(projectId)}`);
        const active = (r?.data || r)?.active;
        if (cancelled || !active) return;
        if (active.state === "paused_for_user" && active.phase === "ship"
            && active.ship_pending) {
          // ── Iter 320 · Bug 4 — mirror the Iter 316 Fix B pattern ──
          // Previously this branch only set loopId + shipPending and
          // dropped the SSE bind + loopPhase remap, so on reload the
          // stepper stayed pinned to its initial phase (EXECUTE-orange
          // was the observed live symptom) and any subsequent state
          // transitions never reached this tab. Now: same shape as
          // the awaiting_confirmation branch — setLoopPhase +
          // openLoopStream, so the stepper paints SHIP and confirm-
          // ship / integrity_guard_rejected frames land here.
          setLoopId(active.loop_id);
          setLoopPhase("ship");
          openLoopStream(active.loop_id);
          setShipPending({
            owner:          active.ship_pending.owner,
            repo:           active.ship_pending.repo,
            branch:         active.ship_pending.branch,
            files:          Object.keys(active.ship_pending.files || {}),
            file_count:     Object.keys(active.ship_pending.files || {}).length,
            commit_message: active.ship_pending.commit_message,
            message:        "Loop resumed — ready to ship.",
          });
          // eslint-disable-next-line no-console
          console.debug("[iter320] hydrate — ship-gate branch bound SSE",
            "loop_id=", active.loop_id);
        } else if (active.state === "awaiting_confirmation" && active.plan) {
          // Iter 316 · Fix B — hydrate path was missing SSE bind +
          // loopPhase remap. On browser reload into an awaiting-
          // confirmation loop, we set loopId+loopPlan but forgot to
          // (a) set loopPhase="plan_pending" (showPlanCard gate) and
          // (b) openLoopStream so ship-gate / execute-phase events
          // land after the user approves. Result before the fix:
          // approval card never rendered post-reload, exact same
          // symptom as the founder-reported simple-task stall.
          setLoopId(active.loop_id);
          setLoopPlan(active.plan);
          setLoopPhase("plan_pending");
          openLoopStream(active.loop_id);
          // eslint-disable-next-line no-console
          console.debug("[iter316] hydrate — awaiting_confirmation branch bound SSE",
            "loop_id=", active.loop_id);
        } else if (active.state === "paused_for_user") {
          // Iter 308 v2 — Reaper-rescued execute/verify/scan sessions
          // land here (state=paused_for_user, phase=execute|verify|...).
          // Prior code only handled the ship-gate variant above, so
          // a rescued loop was invisible after refresh — user saw
          // the "waiting for plan approval" placeholder forever even
          // though the loop had been paused by the reaper. Now we
          // hydrate loopId + reconnect SSE so the paused_for_user
          // event lands, LoopStepBar paints the correct step, and
          // the LoopLiveFeed placeholder switches to "Paused —
          // waiting for your input…".
          setLoopId(active.loop_id);
          setLoopPhase("paused_for_user");
          openLoopStream(active.loop_id);
        } else if (["executing", "verifying", "scanning", "shipping",
                    "self_healing"].includes(active.state)) {
          // Iter 212m-177 — P1-7: loop is MID-RUN (user refreshed while
          // the engine works). Reconnect the SSE stream so the ship
          // gate / completion still reaches this tab.
          setLoopId(active.loop_id);
          openLoopStream(active.loop_id);
        }
      } catch (e) {
        // Best-effort hydrate; never block initial render.
        console.debug("loop/active hydrate skipped:", e?.message);
      }
    })();
    return () => { cancelled = true; };
  }, [activeProject?.project_id]);
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

  // Iter 212m-106 — Top-tab "Graph" wiring. Was a no-op per the
  // original "feature window WIP" note; now dispatches via the
  // existing `setGraphOpen` flag so the GraphPanel drawer opens
  // on the user's currently-active project. The same event is
  // also fired by the sidebar Codebase Graph link.
  useEffect(() => {
    const open = () => setGraphOpen(true);
    const close = () => setGraphOpen(false);
    const toggle = (e) => {
      const d = e?.detail || {};
      if (d.open === true) open();
      else if (d.open === false) close();
      else setGraphOpen((v) => !v);
    };
    window.addEventListener("aurem:toggle-graph", toggle);
    return () => window.removeEventListener("aurem:toggle-graph", toggle);
  }, []);

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
    // Iter 283 — also send an explicit /loop/{id}/cancel to the
    // backend. Aborting the SSE controller alone was enough for
    // an actively-streaming loop (server detects the disconnect
    // and cleans up), but for a `paused_for_user` loop at a gate
    // (e.g. SHIP-approval), the engine is idle — there's no
    // stream to abort, so the loop stayed alive server-side after
    // Stop.  Fire cancelLoop() unconditionally when we have a
    // loopId; the endpoint is idempotent and returns 404-tolerant.
    if (loopId) {
      cancelLoop(loopId).catch((err) => {
        console.debug("[loop-stop] cancelLoop failed:", err);
      });
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
  }, [loopId]);

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
    // Iter 280 P0 — additionally allow send DURING an active loop when
    // execMode=LOOP + text is present, so the Iter 279 queue-next flow
    // (409 → confirm dialog offering Queue vs Cancel-restart) actually
    // becomes reachable. Previously the `busy` gate here made the whole
    // feature unreachable — the send call never fired while a loop ran.
    const isLoopQueueAttempt = (
      busy && execMode === EXEC_MODES.LOOP && !!text
    );
    if (
      (!text && !readyAttachments.length)
      || (busy && !isLoopQueueAttempt)
      || !sessionId
    ) return;
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
      // Iter 212m-149 — Intent Gateway result frame.  Pin the tier
      // dot in the composer to the gateway's authoritative answer
      // for this turn.
      onIntent: (intent) => {
        if (intent && intent.tier) setLastIntentTier(intent.tier);
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
              // Iter 212m-171 — Scope Badge: repo the reply was scoped to.
              repo_owner: d.repo_owner || null,
              repo_name:  d.repo_name  || null,
              branch:     d.branch     || null,
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
    // Iter 212m-190 · Session 3 — Slash-menu key handling. When the
    // menu is open the arrow keys navigate matches, Enter/Tab picks
    // the highlighted command (fires the scan), Escape closes it.
    if (slashOpen) {
      const matches = matchSlashCommands(input);
      if (matches.length > 0) {
        if (e.key === "ArrowDown") {
          e.preventDefault();
          setSlashIdx((i) => Math.min(i + 1, matches.length - 1));
          return;
        }
        if (e.key === "ArrowUp") {
          e.preventDefault();
          setSlashIdx((i) => Math.max(i - 1, 0));
          return;
        }
        if (e.key === "Escape") {
          setSlashOpen(false);
          return;
        }
        if (e.key === "Enter" || e.key === "Tab") {
          e.preventDefault();
          runSlashCommand(matches[slashIdx] || matches[0]);
          return;
        }
      }
    }
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  }

  // Iter 212m-190 · Session 3 — Slash-command scan dispatcher. Maps
  // each command to a category slice of the existing
  // `/codebase-health/scan` endpoint (single source of truth). While
  // the scan runs the strip shows an in-progress spinner; on success
  // we stash a session-scoped summary via `markScanJustCompleted` so
  // the strip surfaces the critical/high totals (or nothing at all
  // when clean).
  async function runSlashCommand(cmd) {
    if (!cmd) return;
    setSlashOpen(false);
    setInput("");
    const projectId = activeProject?.project_id;
    if (!projectId) {
      toast({ message: "Connect a repo first — /scan needs a project", kind: "warn" });
      return;
    }
    await _executeSlashScan(cmd, projectId);
  }

  // Iter 212m-217 — Rate-limit aware scan executor.
  //
  // Backend `_gh_get` (routers/security_scan.py) surfaces GitHub API
  // rate-limits as a structured 429:
  //   { error: "github_rate_limited",
  //     message: "…",
  //     retry_after_seconds: N,
  //     github_message: "…" }
  //
  // And the per-user scan quota limiter (codebase_health.scan) uses
  // the same shape with `error: "scan_rate_limited"`.  Both cases now
  // render a persistent countdown toast that auto-retries when the
  // timer hits zero, with a Cancel button to bail out.  Other errors
  // (401 bad PAT, 404 repo not found, 502 upstream) surface as a
  // one-shot error toast with the actual server detail so the user
  // has an actionable string to act on instead of a silent failure.
  async function _executeSlashScan(cmd, projectId, opts = {}) {
    setScanState("in_progress");
    try {
      const r = await api.post("/codebase-health/scan", {
        project_id: projectId,
        categories: cmd.categories || null,
      });
      // Success — clear any lingering rate-limit toast from the retry
      // path and record the summary for the strip.
      if (opts.retryToastId != null) {
        try { dismissToast(opts.retryToastId); } catch { /* ignore */ }
      }
      const summary = r.data?.summary || {};
      const bySev   = summary.by_severity || {};
      markScanJustCompleted({
        critical:    bySev.critical || 0,
        high:        bySev.high || 0,
        projectId,
        projectName: activeProject?.github_repo || projectId,
      });
    } catch (e) {
      const status = e?.response?.status;
      const detail = e?.response?.data?.detail;
      const isRateLimited =
        status === 429 &&
        typeof detail === "object" && detail &&
        typeof detail.retry_after_seconds === "number" &&
        detail.retry_after_seconds > 0;
      if (isRateLimited) {
        // Cap runaway retries in case the server keeps rate-limiting
        // us back-to-back.  Three cycles is enough to survive a brief
        // secondary rate limit but avoids infinite toast loops.
        const attempt = (opts.attempt || 0) + 1;
        if (attempt > 3) {
          toast({
            message: `Rate limit still active after 3 retries — try again later.`,
            kind: "error",
            duration: 6000,
          });
          return;
        }
        // Cap the countdown at 300 s so we don't render an hour-long
        // timer even if GitHub asks for one.
        const retrySecs = Math.min(300, Math.max(1, detail.retry_after_seconds));
        const isGh = detail.error === "github_rate_limited";
        const label = isGh
          ? "GitHub API rate limit hit"
          : `Scan quota reached (${detail.category || "scan"})`;
        // Stable id so the toast updates in place instead of stacking.
        const toastId = opts.retryToastId || `scan-rate-${projectId}`;
        let cancelled = false;
        toast({
          id:         toastId,
          message:    `${label} — retrying automatically…`,
          kind:       "warn",
          persistent: true,
          countdown:  retrySecs,
          onExpire:   () => {
            if (cancelled) return;
            _executeSlashScan(cmd, projectId, {
              attempt,
              retryToastId: toastId,
            });
          },
          actions: [
            {
              label:   "Cancel",
              onClick: () => {
                cancelled = true;
                setScanState("idle");
              },
            },
            {
              label:   "Retry now",
              primary: true,
              onClick: () => {
                cancelled = true;
                _executeSlashScan(cmd, projectId, {
                  attempt,
                  retryToastId: toastId,
                });
              },
            },
          ],
        });
        // We stay in "in_progress" only while the timer runs. Reset
        // now so the input strip doesn't lock; the retry will flip
        // it back on.
        setScanState("idle");
        return;
      }
      // Non-rate-limit failure — surface the real reason.
      let msg = detail;
      if (typeof msg !== "string") {
        msg = msg?.message
          || (() => { try { return JSON.stringify(msg); } catch { return String(msg); } })();
      }
      toast({
        message: `Scan failed — ${msg || "network error"}`,
        kind:    "error",
        duration: 6000,
      });
    } finally {
      // Only clear if we're not mid-countdown (rate-limit early return
      // has already reset it).
      setScanState((s) => (s === "in_progress" ? "idle" : s));
    }
  }

  // ──────────────────────────────────────────────────────────────
  // Iter 212m-65 — Loop-mode user actions (Phase D wiring).
  // ──────────────────────────────────────────────────────────────
  // runLoopPlan: kick off /loop/start, render plan inside an
  // assistant bubble. The user must then approve via PlanApprovalCard
  // before any code execution begins.
  async function runLoopPlan(userText, readyAttachments, opts) {
    // Iter 281 — Step 0 fix: removed the `busy` early-return.
    // Rationale: send() at line 1130 deliberately allows a LOOP-mode
    // busy re-entry so the Iter 279 queue-next flow (409
    // loop_already_running → Queue/Cancel-restart dialog) can trigger.
    // If we early-returned here, that dialog was unreachable and the
    // prompt vanished silently. The 409 path itself is idempotent.
    if (!sessionId) return;
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
    // Iter 309 · Reset per-step tones on a fresh loop kick-off so
    // stale success/danger colours from the previous run don't leak
    // into the new ECG strip.
    setLoopStepTones({});

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
      // ── Iter 324 · Fix A — purge stale `loopPending` bubbles ──
      // Live evidence (founder screenshot): a "**Generating plan…**"
      // bubble with a 337s counter persisted alongside the real
      // plan bubble because a previous loop-start attempt died
      // before its bubble could be replaced (session reload,
      // failed HTTP, aborted loop). Strip every stale pending
      // bubble BEFORE inserting the new one so at most ONE
      // "Generating plan…" placeholder exists at any moment.
      ...m.filter((row) => !(row.role === "assistant" && row.loopPending)),
      ...(opts?.skipUserBubble ? [] : [{ role: "user", content: displayContent }]),
      { role: "assistant", content: "", streaming: true, loopPending: true },
    ]);
    setBusy(true);
    try {
      const resp = await startLoop({
        projectId: activeProject?.project_id || null,
        userMessage: composed,
      });
      const lid  = resp?.loop_id;
      setLoopId(lid);

      // ── Iter 312 · Class 1 companion (frontend) ─────────────────
      // With LOOP_START_ASYNC=true (default), /loop/start now returns
      // {plan:null, async_start:true} immediately — the plan blob
      // arrives later via SSE `{state:'awaiting_confirmation',
      // phase:'plan', data:{plan}}`. Bind the stream NOW so we don't
      // miss that emission, and keep the "generating plan…" pending
      // bubble in place. handleLoopEvent will swap it for the
      // formatted plan the moment the engine emits.
      if (resp?.async_start === true || !resp?.plan) {
        if (lid) openLoopStream(lid);
        // eslint-disable-next-line no-console
        console.debug("[iter316] async-start branch — openLoopStream fired at", new Date().toISOString(), "loop_id=", lid);
        // ── Iter 316 · Fix A — proactive /loop/active fallback poll ─
        // Belt-and-braces reconciliation. If the SSE plan-ready event
        // fails to land (multi-worker race, dropped stream, whatever),
        // this poll picks it up from Mongo's loop_sessions.context.plan
        // and drives the same handleLoopEvent path. Whichever wins,
        // wins — the OTHER will no-op harmlessly.
        //
        // Console logs (Fix C) tell us on every real run which path
        // actually delivered the plan (SSE / poll / neither), so we
        // can retire this fallback once SSE is proven reliable OR
        // keep it as permanent belt-and-braces if the race is real.
        if (lid && !loopFallbackPollRef.current) {
          const pollStartedAt = Date.now();
          const timer = setInterval(async () => {
            try {
              const { getActiveLoop } = await import("../lib/loopApi");
              const activeResp = await getActiveLoop(activeProject?.project_id || null);
              const active = activeResp?.active;
              if (active?.loop_id === lid
                  && active?.state === "awaiting_confirmation"
                  && active?.plan) {
                const elapsedMs = Date.now() - pollStartedAt;
                // eslint-disable-next-line no-console
                console.debug("[iter316] FALLBACK-POLL delivered plan at",
                  new Date().toISOString(),
                  "elapsedMs=", elapsedMs,
                  "(SSE path did NOT deliver first — investigate)");
                // Synthesize the same shape SSE would have delivered
                // so handleLoopEvent's plan-absorption block fires
                // identically to the SSE path. Zero code duplication.
                handleLoopEvent({
                  loop_id: lid,
                  state:   "awaiting_confirmation",
                  phase:   "plan",
                  data:    { plan: active.plan },
                  message: "Plan ready — awaiting your approval. (via /loop/active fallback)",
                  requires_user_action: true,
                });
                clearInterval(timer);
                loopFallbackPollRef.current = null;
              }
            } catch { /* poll failure is silent — SSE may still succeed */ }
          }, 3000);
          loopFallbackPollRef.current = timer;
        }
        setMessages((m) => {
          const out = m.slice();
          for (let i = out.length - 1; i >= 0; i--) {
            if (out[i].role === "assistant" && out[i].loopPending) {
              out[i] = {
                ...out[i],
                content: "**Generating plan…**\n\n"
                       + "The engine is consulting Council + Parliament. "
                       + "The plan will appear here as soon as it's ready.",
              };
              break;
            }
          }
          return out;
        });
      } else {
        // Legacy sync path (LOOP_START_ASYNC=false) — plan blob is
        // inline in the response, render it immediately.
        const plan = resp.plan || {};
        setLoopPlan(plan);
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
      }
    } catch (e) {
      // Iter 212m-176 — FastAPI can return `detail` as an object/array
      // (422 validation, structured 409 lock info). Template-string on
      // an object renders "[object Object]" — normalise to text.
      const detail = e?.response?.data?.detail;
      // ── Iter 279 + 284 — queue-next handling ─────────────────
      // If the 409 says another loop is running, silently QUEUE the
      // new prompt (no OS-native window.confirm, no in-app modal —
      // matches the pattern users expect from other agentic apps).
      // A "N queued" chip renders above the composer so the queue
      // is visible + dismissible.
      if (e?.response?.status === 409
          && detail?.error === "loop_already_running"
          && detail?.existing_loop_id) {
        const existingId = detail.existing_loop_id;
        const choice = true;   // Iter 284 — always queue by default.
        setMessages((m) => {
          const out = m.slice();
          for (let i = out.length - 1; i >= 0; i--) {
            if (out[i].role === "assistant" && out[i].loopPending) {
              out[i] = {
                role: "assistant", streaming: false,
                content: choice
                  ? `⏳ **Queued.** Will start when loop \`${existingId.slice(0,12)}…\` finishes.`
                  : `⏹ **Cancelling current loop, then starting yours…**`,
              };
              break;
            }
          }
          return out;
        });
        setBusy(false);
        if (choice) {
          setQueuedCount((n) => n + 1);   // Iter 284 — visible chip.
          // Queue path — poll the existing loop until terminal, then
          // recursively call this function with the same composed
          // message. Cheap 3s polling; auto-stops once terminal.
          const iv = setInterval(async () => {
            try {
              const st = await api.get(`/loop/${existingId}/status`);
              const s  = st?.data?.state || "";
              if (["completed","failed","aborted","done"].includes(s)) {
                clearInterval(iv);
                setQueuedCount((n) => Math.max(0, n - 1));
                // Fire the queued message with a small delay so the
                // acquire_loop_lock ghost-sweep sees the new terminal
                // state before we retry.
                setTimeout(() => runLoopPlan(composed), 1500);
              }
            } catch {
              /* keep polling — transient errors are fine */
            }
          }, 3000);
        } else {
          // Cancel-and-restart path.
          try {
            await cancelLoop(existingId);
            // Small wait so cancel's DB writes land + ghost sweep is
            // guaranteed to see state=aborted on the next acquire.
            await new Promise((r) => setTimeout(r, 800));
            runLoopPlan(composed);
          } catch (err) {
            setMessages((m) => m.concat([{
              role: "assistant", streaming: false,
              content: `**Cancel-and-restart failed:** ${err?.message || err}`,
              error: true,
            }]));
          }
        }
        return;
      }
      let msg = detail ?? e?.message ?? "Loop start failed";
      if (typeof msg !== "string") {
        msg = msg?.message
          || (() => { try { return JSON.stringify(msg); } catch { return String(msg); } })();
      }

      // ── Iter 312 · Class 3 — Timeout-recovery reconciliation ──────
      // If axios raised a client-side timeout (60s cap in api.js),
      // the backend session was almost certainly created (lock write
      // happens BEFORE any long LLM/Council work in /loop/start).
      // Poll /loop/active to confirm — if we find our own session in
      // a non-terminal state, bind SSE to it and show a "still
      // working" indicator instead of a hard failure.
      const isTimeout =
        e?.code === "ECONNABORTED" ||
        (typeof e?.message === "string" && /timeout of \d+ms exceeded/i.test(e.message));
      if (isTimeout) {
        try {
          const { getActiveLoop } = await import("../lib/loopApi");
          const activeResp = await getActiveLoop(activeProject?.project_id || null);
          const active = activeResp?.active;
          if (active?.loop_id) {
            // Recovery path — the loop DID start server-side, HTTP
            // just took too long to return. Bind ChatPanel to it and
            // let SSE take over from here (the plan blob will arrive
            // via a phase-transition event once planning finishes).
            setLoopId(active.loop_id);
            // Iter 312 · Class 3 — remap phase for gate compatibility:
            // active.state='awaiting_confirmation' + phase='plan' means
            // the plan is ALREADY ready → jump straight to plan_pending
            // so PlanApprovalCard renders. Otherwise stay in planning.
            if (active.state === "awaiting_confirmation" && active.phase === "plan") {
              setLoopPhase("plan_pending");
            } else {
              setLoopPhase(String(active.phase || active.state || "planning").toLowerCase());
            }
            // If /loop/active already carries the plan (engine finished
            // planning during the axios timeout window), absorb it now
            // so PlanApprovalCard has data on first render — no waiting
            // for the SSE re-emit.
            if (active.plan) {
              setLoopPlan(active.plan);
            }
            // CRITICAL: bind the SSE stream so subsequent phase events
            // (plan_ready emission if plan not yet arrived, execute /
            // verify / ship transitions after approve) land here. The
            // previous Class 3 patch mutated local state only and left
            // the stream unbound — that's why the founder never saw
            // the approval card during the last attempt.
            openLoopStream(active.loop_id);
            setMessages((m) => {
              const out = m.slice();
              for (let i = out.length - 1; i >= 0; i--) {
                if (out[i].role === "assistant" && out[i].loopPending) {
                  // If plan is already available, render it now.
                  if (active.plan) {
                    out[i] = {
                      role: "assistant",
                      streaming: false,
                      content: formatPlanMarkdown(active.plan),
                      loopPlan: true,
                      loop_id: active.loop_id,
                    };
                  } else {
                    out[i] = {
                      role: "assistant",
                      streaming: true,
                      content: "**Plan taking longer than expected — still working…**\n\n" +
                               "The initial HTTP request timed out but the loop is running " +
                               `server-side (loop \`${active.loop_id.slice(-8)}\`). ` +
                               "The plan will appear here as soon as it's ready.",
                      loopPending: true,
                    };
                  }
                  break;
                }
              }
              return out;
            });
            return;   // recovery successful, do NOT render the failure
          }
        } catch (_recoveryErr) {
          // Recovery poll itself failed — fall through to the
          // original failure rendering below. Not adding a second
          // error message; the original one is truthful in this case.
        }
      }

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
    // Iter 312 · Class 3 companion — support BOTH the legacy plan
    // schema (bullets + files_to_change) and the engine's actual
    // AWAITING_CONFIRMATION emission schema (description + steps +
    // files-of-objects with path/action/reason). The prior formatter
    // silently swallowed the SSE-delivered plan because it only read
    // the legacy keys, leaving the recovery bubble showing a title
    // with no body.
    const description = typeof plan.description === "string"
      ? plan.description.trim()
      : "";
    const bullets = Array.isArray(plan.bullets) ? plan.bullets : [];
    const steps   = Array.isArray(plan.steps)   ? plan.steps   : [];
    const filesLegacy = Array.isArray(plan.files_to_change) ? plan.files_to_change : [];
    const filesNew    = Array.isArray(plan.files) ? plan.files : [];

    let out = `### ${title}${eta}\n\n`;
    if (description) {
      out += `${description}\n\n`;
    }
    // Prefer the new `steps` key, fall back to legacy `bullets`.
    const stepList = steps.length ? steps : bullets;
    if (stepList.length) {
      stepList.forEach((s, i) => {
        const line = typeof s === "string" ? s : (s?.text || s?.title || JSON.stringify(s));
        out += `${i + 1}. ${line}\n`;
      });
      out += "\n";
    }
    // Files section — support both string list (legacy) and object list.
    const filesToRender = filesNew.length ? filesNew : filesLegacy;
    if (filesToRender.length) {
      out += `**Files to change:**\n`;
      filesToRender.forEach((f) => {
        if (typeof f === "string") {
          out += `- \`${f}\`\n`;
        } else if (f && typeof f === "object") {
          const path = f.path || f.file || "";
          const action = f.action ? ` _(${f.action})_` : "";
          const reason = f.reason ? ` — ${f.reason}` : "";
          out += `- \`${path}\`${action}${reason}\n`;
        }
      });
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

    // Iter 288 — terminal guard. Once we've seen ANY terminal event
    // (completed/failed/aborted), later SSE frames are dropped for
    // state-driving purposes so a late "executing" heartbeat cannot
    // flip loopPhase back off "error"/"done"/"idle" and re-arm the
    // "Agent is running…" bar or the heartbeat gap fallback. We still
    // let the terminal frame ITSELF pass through this function.
    const isTerminalFrame = (state === "completed"
                          || state === "failed"
                          || state === "aborted"
                          || state === "expired");
    if (loopTerminalRef.current && !isTerminalFrame) {
      // eslint-disable-next-line no-console
      console.debug("[loop-sse] DROP non-terminal frame after terminal:",
        "state=", state, "phase=", phase);
      return;
    }
    if (isTerminalFrame) {
      loopTerminalRef.current = true;
    }

    // Iter 275 — mirror the raw event into the LoopLiveFeed panel.
    // This is the same event object the rest of this handler
    // consumes; the panel just needs the trailing sequence.
    // Iter 280 P0 — trace the mirror so we can prove the panel's
    // input prop is actually being updated (or not).
    // eslint-disable-next-line no-console
    console.debug("[loop-sse] → setLoopFeedEvent",
      "phase=", ev.phase, "sub=", ev.data?.sub_step);
    setLoopFeedEvent(ev);

    // ── Iter 309 · Live Narration — fold into loopStepTones ─────
    // For any narration event, update the step's tone. This drives
    // the LoopStepBar ECG strip (active vs resolved) in real time.
    // Zero client-side simulation — the moment the backend emits a
    // pending narration, the ECG animates; the moment it emits a
    // success/danger narration for the same step, the ECG flatlines.
    if (data && data.type === "narration" && data.narration_step) {
      const step = String(data.narration_step);
      const tone = String(data.tone || "pending");
      setLoopStepTones((prev) => ({ ...prev, [step]: tone }));
    }

    // Drive the existing LoopStepBar phase enum.
    // Iter 308 v2 — EVERY backend LoopState.value is now mapped so
    // the step bar renders correctly regardless of the engine's
    // exact state. The prior gap left self_healing / paused_for_user /
    // expired as noops → loopPhase stayed on the last known
    // running-state value, which the user perceived as "frozen".
    // aborted now maps to `aborted` (a terminal state) not idle so
    // the visual doesn't lie about a still-live loop.
    if      (state === "idle")                  setLoopPhase("idle");
    else if (state === "planning")              setLoopPhase("planning");
    else if (state === "awaiting_confirmation") {
      // Iter 312 · Class 3 companion — when the async-start engine
      // finishes its plan phase, the engine emits
      // {state:'awaiting_confirmation', phase:'plan', data:{plan}}.
      // The PlanApprovalCard is gated on loopPhase === 'plan_pending'
      // (see showPlanCard below), so route the plan-confirmation
      // variant to 'plan_pending' instead of the raw state value.
      // Non-plan awaiting_confirmation variants (e.g., ship gate) keep
      // the raw state — those have their own dedicated cards.
      if (phase === "plan") setLoopPhase("plan_pending");
      else                  setLoopPhase("awaiting_confirmation");
    }
    else if (state === "executing")             setLoopPhase("executing");
    else if (state === "self_healing")          setLoopPhase("self_healing");
    else if (state === "paused_for_user")       setLoopPhase("paused_for_user");
    else if (state === "verifying")             setLoopPhase("verifying");
    else if (state === "scanning")              setLoopPhase("scanning");
    else if (state === "shipping")              setLoopPhase("shipping");
    else if (state === "completed")             setLoopPhase("completed");
    else if (state === "failed")                setLoopPhase("failed");
    else if (state === "aborted")               setLoopPhase("aborted");
    else if (state === "expired")               setLoopPhase("expired");

    // ── Iter 312 · Class 3 companion — absorb plan blob from SSE ─
    // In the async-start world, the plan no longer arrives in the
    // /loop/start HTTP response body — it arrives here, on the
    // engine's first AWAITING_CONFIRMATION emission (loop_engine.py
    // ~line 625: `data={"plan": plan}`). Capture it so
    // PlanApprovalCard has something to render, and replace the
    // "still working" pending bubble with a formatted plan markdown
    // preview (same UX as the pre-Iter-312 sync path had inline).
    if (state === "awaiting_confirmation" && phase === "plan" && data && data.plan) {
      // ── Iter 316 · Fix C — telemetry ────────────────────────────
      // Log WHICH path delivered the plan-ready event so we can
      // retroactively see (from the browser console) whether SSE
      // is working reliably in prod or whether Fix A's fallback
      // poll is doing the real work. Message-suffix tag comes from
      // the synthetic event Fix A crafts.
      const viaFallback = /via \/loop\/active fallback/.test(ev.message || "");
      // eslint-disable-next-line no-console
      console.debug("[iter316] PLAN-READY absorbed at",
        new Date().toISOString(),
        "path=", viaFallback ? "FALLBACK-POLL" : "SSE",
        "loop_id=", ev.loop_id);
      // Cancel the fallback poll if it's still running — SSE won
      // (or we're firing from the fallback itself and this is the
      // last iteration). Idempotent.
      if (loopFallbackPollRef.current) {
        try { clearInterval(loopFallbackPollRef.current); } catch { /* swallow */ }
        loopFallbackPollRef.current = null;
      }
      setLoopPlan(data.plan);
      const planMd = formatPlanMarkdown(data.plan);
      const lid = ev.loop_id || null;
      setMessages((m) => {
        // ── Iter 324 · Fix A2 — plan lands → replace the FIRST
        // (most recent) pending bubble, then purge any OTHER
        // stale pending bubbles left over from earlier failed
        // attempts. Prevents the "Generating plan… 337s" ghost
        // from lingering next to the real plan.
        let replaced = false;
        const out = [];
        for (let i = m.length - 1; i >= 0; i--) {
          const row = m[i];
          if (!replaced && row.role === "assistant"
              && (row.loopPending || row.loopLive)) {
            out.unshift({
              role: "assistant",
              streaming: false,
              content: planMd,
              loopPlan: true,
              loop_id: lid,
            });
            replaced = true;
          } else if (row.role === "assistant"
                     && (row.loopPending || row.loopLive)) {
            // stale pending / live bubble left from a prior
            // aborted attempt — drop it entirely.
            continue;
          } else {
            out.unshift(row);
          }
        }
        return out;
      });
    }

    // Iter 288 — the terminal-frame's own `phase` field tells us WHERE
    // the loop died. Remember it so LoopStepBar can paint the right
    // step red instead of hard-coding EXECUTE.
    if (state === "failed" && phase) {
      setLoopErrorPhase(phase);
      setBusy(false);           // stop the "Agent is running…" bar immediately
      setLoopTerminal(true);    // stop the heartbeat / gap-fallback line
    }

    // Iter 212m-106 — Ship modal wiring. The engine emits the final
    // ship event with state="completed", phase="ship", and `data`
    // carrying the REAL commit_sha + html_url + files_changed from
    // GitHub's API response. Forward this to the dashboard's
    // ShipConfirmModal so the user sees the commit confirmation
    // (or a failure card if the push failed).
    if (state === "completed" && phase === "ship" && data && data.commit_sha) {
      try {
        window.dispatchEvent(new CustomEvent("aurem:open-ship-modal", {
          detail: {
            kind: "shipped",
            commit_sha:  data.commit_sha,
            full_sha:    data.full_sha,
            html_url:    data.html_url,
            files:       data.files_changed || [],
            scan:        data.scan_results || null,
            commit_msg:  data.commit_message,
          },
        }));
      } catch { /* event dispatch never throws but be defensive */ }
    } else if (state === "failed" && phase === "ship") {
      try {
        window.dispatchEvent(new CustomEvent("aurem:open-ship-modal", {
          detail: {
            kind: "failed",
            error: data?.error || ev.message || "Ship failed",
          },
        }));
      } catch { /* noop */ }
    }

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
      // Iter 212m-111 — Manual Ship gate. Awaiting-ship pauses get a
      // dedicated ShipPendingCard with a green "Ship to GitHub" button
      // — NOT the generic retry/skip/abort UserActionCard.
      if (data && data.kind === "awaiting_ship") {
        setShipPending({
          owner:           data.owner,
          repo:            data.repo,
          branch:          data.branch,
          files:           data.files || [],
          file_count:      data.file_count || (data.files || []).length,
          commit_message:  data.commit_message || "",
          message:         ev.message || "Ready to ship.",
        });
        setUserAction(null);
      } else {
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
      }
    } else if (!requiresAction && state !== "paused_for_user") {
      // Clear any prior action card the moment the engine moves on.
      if (state === "executing" || state === "verifying" || state === "scanning"
          || state === "shipping" || state === "completed") {
        setUserAction(null);
        // Iter 212m-111 — also clear the ship-pending card once the
        // engine resumes (user clicked Ship → engine moves to
        // SHIPPING → COMPLETED, or user cancelled → ABORTED).
        if (state === "shipping" || state === "completed") {
          setShipPending(null);
        }
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
    // Iter 278 — heartbeat frames belong ONLY in the composer-adjacent
    // LoopLiveFeed panel (transient "still-alive" indicator). Keeping
    // them out of the growing chat bubble prevents 10-20 "still waiting…"
    // lines from cluttering permanent scroll history.
    const sub = (ev.data && ev.data.sub_step) || "";
    if (sub === "heartbeat" || ev.data?.keepalive === true) return null;
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
    // Iter 275 — reset the live-feed on every fresh open so a
    // reconnect / new run doesn't inherit the previous run's ring.
    setLoopFeedEvent(null);
    setLoopTerminal(false);
    // Iter 288 — clear the terminal guard on every new stream so a
    // second run in the same session actually accepts events again.
    loopTerminalRef.current = false;
    setLoopErrorPhase(null);
    // Iter 280 P0 — SSE event-chain tracing. Explicit console.debug
    // at every stage so a real (human-driven) browser session can
    // confirm whether events actually reach the frontend, and if so
    // whether they carry the phase/state/message fields the panels
    // expect. Turned on unconditionally — logs are cheap, silence
    // was expensive when the LoopLiveFeed silently stayed empty.
    // eslint-disable-next-line no-console
    console.debug("[loop-sse] openLoopStream() called for loop_id=", lid);
    loopAbortRef.current = streamLoopEvents(lid, {
      onEvent: (ev) => {
        // eslint-disable-next-line no-console
        console.debug("[loop-sse] onEvent →",
          "phase=", ev?.phase,
          "state=", ev?.state,
          "sub=", ev?.data?.sub_step,
          "keepalive=", !!ev?.data?.keepalive,
          "msg=", (ev?.message || "").slice(0, 120),
          "full=", ev);
        // Iter 316 · Fix C — narrow-flag plan-ready frames on SSE
        // so the console log stream is greppable for "did SSE
        // actually deliver the plan?" without wading through every
        // heartbeat/narration line.
        if (ev?.state === "awaiting_confirmation" && ev?.phase === "plan"
            && ev?.data?.plan) {
          // eslint-disable-next-line no-console
          console.debug("[iter316] SSE PLAN-READY FRAME arrived at",
            new Date().toISOString(),
            "loop_id=", ev.loop_id);
        }
        handleLoopEvent(ev);
      },
      onTerminal: () => {
        // eslint-disable-next-line no-console
        console.debug("[loop-sse] onTerminal (stream closed)");
        setLoopTerminal(true);
        loopAbortRef.current = null;
        setBusy(false);
        setSelfHeal((s) => ({ ...s, visible: false }));
      },
      onError: (err) => {
        // eslint-disable-next-line no-console
        console.warn("[loop-sse] onError →", err);
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

  // Iter 212m-111 — Manual Ship gate handlers.
  async function handleShipConfirm() {
    if (!loopId || shipBusy) return;
    setShipBusy(true);
    try {
      await confirmShip(loopId, true);
      // SSE will deliver SHIPPING → COMPLETED. Clear the card now to
      // give immediate feedback; the LoopStepBar shows progress.
      setShipPending(null);
      setBusy(true);
    } catch (e) {
      toast(e?.response?.data?.detail || e?.message || "Ship failed to start");
    } finally {
      setShipBusy(false);
    }
  }
  async function handleShipCancel() {
    if (!loopId || shipBusy) return;
    setShipBusy(true);
    try {
      await confirmShip(loopId, false);
      setShipPending(null);
      setLoopPhase("idle");
    } catch (e) {
      toast(e?.response?.data?.detail || e?.message || "Cancel failed");
    } finally {
      setShipBusy(false);
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
    // Iter 316 · Fix A — cancel fallback poll on user cancel.
    if (loopFallbackPollRef.current) {
      try { clearInterval(loopFallbackPollRef.current); } catch { /* swallow */ }
      loopFallbackPollRef.current = null;
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
  // Iter 289 — the PlanApprovalCard MUST NOT render for a loop that
  // has already reached a terminal state (failed / done / idle from
  // abort). Same class of bug as iter288's stepper/heartbeat: dead
  // loops must LOOK dead, and an approve-button on a failed loop
  // used to POST /confirm and return 499 (bug flagged by
  // bug_testing_agent in iter288). loopTerminalRef is the shared
  // synchronous guard; loopPhase covers the React-render pass.
  const showPlanCard =
    execMode === EXEC_MODES.LOOP &&
    loopPhase === "plan_pending" &&
    !!loopPlan &&
    !!loopId &&
    !busy &&
    !loopTerminal &&
    !loopTerminalRef.current &&
    loopPhase !== "error" &&
    loopPhase !== "done";

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
          minHeight: 0,
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
      {/* Iter 309 · Batch-2 aftermath — Persistent loop-status chip.
          Sticky at the top of the chat pane so it never scrolls out
          of view during a long-running loop. Reads /loop/active from
          the backend (10s poll + on-focus refresh) so client-side
          state resets can never make it lie about whether a loop is
          running. Distinct outlined-red "Stop" button with 4s
          click-again-to-confirm — matches ChatGPT/Claude/Cursor's
          converged pattern for long-running task cancellation. */}
      <LoopStatusChip
        projectId={activeProject?.project_id || null}
        // Iter 309 · Item E — chip-wins reconciliation. When
        // /loop/active reports a phase different from SSE-derived
        // loopPhase (usually after a reconnect gap), backend truth
        // wins. This is intentional and NOT redundant with the SSE
        // handler — SSE can lag or drop; the poll is authoritative.
        onPhaseUpdate={(chipPhase, chipState) => {
          // Iter 312 · Class 3 companion — apply the same plan-variant
          // remap used in handleLoopEvent so the chip's poll doesn't
          // clobber loopPhase='plan_pending' with the raw 'plan'
          // (which would suppress PlanApprovalCard's showPlanCard
          // gate immediately after timeout recovery). If the backend
          // reports awaiting_confirmation+phase=plan, canonicalise to
          // plan_pending on the client.
          let normalised = chipPhase;
          if (chipState === "awaiting_confirmation" && chipPhase === "plan") {
            normalised = "plan_pending";
          }
          if (normalised && normalised !== loopPhase) {
            setLoopPhase(normalised);
          }
        }}
      />

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
          project select. Auto-hides on ready/idle.
          Iter 212m-108 — also hide once any chat content is on
          screen (history loaded OR user already sent a message).
          Previously the skeleton bars stayed visible above real
          messages because warm-start polling didn't always reach
          "ready" within the 60s cap, leaving the user staring at
          shimmer bars on top of working content. */}
      {messages.length === 0 && (
        <WarmStatusBar status={warmStatus} progress={warmProgress} />
      )}
      <div
        data-testid="chat-messages"
        style={{
          flex: 1, overflowY: "auto",
          minHeight: 0,
          // Iter 212m-134 / 212m-140 — Padding lives in index.css (CSS
          // container queries adapt 17.25% / 24px / 12px gutters as the
          // chat panel shrinks). Only the live-popup right override
          // stays inline because it's a JS-driven runtime state, not
          // a layout-driven state — the popup overlaps the right edge
          // and needs 392 px regardless of width.
          ...(livePopupTaskId ? { paddingRight: 392 } : {}),
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
          composer, all flowing visually into each other).

          Iter 324 · Fix B — the F12 status bar previously rendered
          BETWEEN the chat scroll and the LoopStepBar, which put a
          "1 console error / SEND TO ORA" chip visually STRANDED at
          the bottom-left with no anchor. Founder screenshot marked
          it as misplaced. Fix: keep TokenBanner here (it's a wallet
          notice that logically belongs above operations) but MOVE
          the composer-status-bar (F12Badge + ModePill) further
          down, past the LoopStepBar, so it hugs the composer input
          on the same row block. Rendered below just before the
          composer form opens.
      */}
      <TokenBanner usage={usage} />

      {/* Iter 212m-163 — Loop mode pill is now rendered INSIDE the
          composer toolbar (next to the IntentTierIndicator), driven
          by `isLoopUnlocked` so founder/admin/unlimited see the
          unlocked pill and everyone else sees the locked "Loop · soon"
          variant.  The actual JSX lives in the composer-toolbar block
          below (search the toolbar for the LoopModeToggle tag). */}

      {/* Iter 212m-58 — 5-step progress bar.  Renders only when the
          loop pipeline is active.  Wires into `loopPhase` set by
          send() and onDone above.

          Iter 212m-201 — Founder request: bar should only appear
          when LOOP mode is toggled ON.  In prompt mode we hide it
          entirely so the chat column stays clean, and let the
          composer grow taller (3-line min) to give more room for
          the user's question. */}
      {execMode === EXEC_MODES.LOOP && (
        <LoopStepBar
          phase={loopPhase}
          retryCount={loopRetryCount}
          // Iter 288 — paint the ACTUAL failed step red, not always
          // step 2 (EXECUTE). loopErrorPhase is set from the failed
          // SSE frame's `phase` field inside handleLoopEvent. When
          // absent (loop still running or aborted with no phase), we
          // pass 0 so LoopStepBar renders nothing as errored.
          errorStep={loopPhase === "error"
            ? ({plan:1, execute:2, verify:3, security:4, scan:4, ship:5}[
                (loopErrorPhase || "").toLowerCase()] || 2)
            : 0}
          // Iter 309 · Live Narration — real per-step tones sourced
          // from backend narration events. Drives the ECG strip's
          // active/success/danger variants. Empty object = every step
          // uses legacy phase-based fallback (backward compat with
          // loops from stale backends that don't emit narration).
          stepTones={loopStepTones}
        />
      )}

      {/* Iter 212m-58 — Plan approval card.  Renders the moment the
          plan-turn ends; user must click Approve before any code
          execution starts.  Cancel resets the loop. */}
      {showPlanCard && (
        <div className="chat-inline-card">
          <PlanApprovalCard
            onApprove={handleApprovePlan}
            onCancel={handleCancelPlan}
          />
        </div>
      )}

      {/* Iter 212m-65 — Phase D wiring: live self-heal strip + paused
          user-action card driven by the /loop/{id}/stream SSE feed. */}
      {/* Iter 275 — compact live-feed panel: last 4-5 real SSE events
          from loop_engine.py phase transitions. Fallback line during
          real silences is contextual (uses last known phase), not
          canned. Hidden until the first event lands. */}
      {loopId && (
        <div className="chat-inline-card">
          <LoopLiveFeed
            loopId={loopId}
            event={loopFeedEvent}
            terminal={loopTerminal}
            phase={loopPhase}
          />
        </div>
      )}
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
      {shipPending && (
        <ShipPendingCard
          pending={shipPending}
          busy={shipBusy}
          onConfirm={(approved) => approved ? handleShipConfirm() : handleShipCancel()}
        />
      )}

      {/* Iter 324 · Fix B — composer-status-bar RELOCATED below the
          operational surface (LoopStepBar / LoopLiveFeed / SelfHeal /
          UserAction / ShipPending) so the F12 chip + ModePill visually
          hug the composer input instead of floating orphaned in the
          upper-left corner (founder screenshot Marker #2). Empty state
          collapses via `.composer-status-bar:empty { display:none }`
          so idle sessions don't get a blank spacer row. */}
      <div className="composer-status-bar" data-testid="composer-status-bar">
        <ModePill mode={detectedMode || (serverMode ? { mode: serverMode, color: "#6b7280", label: "Mode " + serverMode } : null)} />
        <F12Badge
          errorCount={f12.errorCount}
          hasErrors={f12.hasErrors}
          onCopyPayload={() => {
            const payload = f12.flush();
            try {
              navigator.clipboard?.writeText(JSON.stringify(payload, null, 2));
            } catch { /* ignore */ }
          }}
          onSendToORA={() => {
            const payload = f12.flush();
            const cc = payload?.console_errors?.length || 0;
            const nc = payload?.network_errors?.length || 0;
            const msg = `F12 errors captured (${cc} console, ${nc} network). Please diagnose.`;
            const ok = window.confirm(
              `Send the captured F12 errors to ORA for analysis?\n\n` +
              `${cc} console error(s), ${nc} network error(s)\n\n` +
              `OK → send to ORA\nCancel → copy payload to clipboard instead`
            );
            if (!ok) {
              try {
                navigator.clipboard?.writeText(JSON.stringify(payload, null, 2));
              } catch { /* ignore */ }
              return;
            }
            setInput(msg);
            lastF12PayloadRef.current = payload;
            setTimeout(() => {
              const form = taRef.current && taRef.current.form;
              if (form) form.requestSubmit();
            }, 50);
          }}
        />
      </div>

      {/* Iter 212m-35 — Founder Offer attached to the TOP of the
          composer. Rounded top corners flow visually into the form
          below (which has a flat top edge here). Auto-hides when
          has_fully_claimed, sold-out, or >3 days since signup. */}
      <FounderOfferCard projectId={activeProject?.project_id} />

      {/* Iter 212m-57 — Stream health pill (slow / reconnecting). Sits
          directly above the composer so the user has clear feedback
          when the SSE stream stalls — previously the chat just looked
          frozen for up to 90s before silently auto-recovering.
          Iter 212m-103 — `Retry now` button aborts the in-flight
          controller; existing AbortError → retry path picks it up. */}
      <StreamHealthPill
        state={streamHealth}
        onRetry={() => {
          try { abortRef.current?.abort(); } catch { /* ignore */ }
        }}
      />

      {/* Iter 212m-190 · Session 3 — Chat-native scan strip. Sits
          just above the composer form so scan lifecycle events
          (in-progress, just-completed, backlog reminder) surface
          without blocking the input. `projectId=null` hides it. */}
      <ScanStatusStrip
        projectId={activeProject?.project_id}
        scanState={scanState}
        projectName={activeProject?.github_repo || ""}
        onReviewFindings={() => window.location.assign("/codebase-health")}
      />

      {/* Iter 284/288 — "N queued · Agent is running" caption row.
          Iter 295 — extracted to AgentStatusBar.jsx so it's testable
          in isolation. Behaviour identical: renders only while
          `busy` is true, disappears the instant it flips false. */}
      {/* Iter 309 · Item D — Hide AgentStatusBar during an active
          loop. LoopStatusChip (sticky top-of-pane) already conveys
          "agent is running" + the phase, and LoopStepBar's active-step
          ECG pulse is a third redundant "still working" signal. Three
          pulsing dots at once was a UX regression per the audit.
          Guard: hide when a loop is active. Kept fully unchanged for
          non-loop chat turns (its actual purpose). */}
      {(() => {
        const isLoopActive =
          execMode === EXEC_MODES.LOOP &&
          loopPhase &&
          loopPhase !== "idle" &&
          loopPhase !== "completed" &&
          loopPhase !== "failed" &&
          loopPhase !== "aborted" &&
          loopPhase !== "expired";
        if (isLoopActive) return null;
        return <AgentStatusBar busy={busy} queuedCount={queuedCount} />;
      })()}

      <form
        data-testid="chat-form"
        onSubmit={send}
        className="glass-composer"
        // Iter 284 — outline turns amber when the agent is running so
        // the composer visually pairs with the running-agent caption
        // above.  Matches the reference UI pattern where the whole
        // composer shell tints during work.
        data-agent-running={busy ? "true" : undefined}
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
          // Iter 212m-135 / 212m-140 — composer padding lives in
          // index.css (same container queries as messages). Only
          // top/bottom 14 px is structural and stays inline alongside
          // the form's display/border styling.
          display: "flex", flexDirection: "column", gap: 8,
          outline: dragOver ? "2px dashed var(--accent-2)" : "none",
          outlineOffset: -8,
          transition: "outline 120ms ease",
          // Iter 212m-197 — Founder request (option B): remove the
          // entire composer box outline so it blends with the chat
          // pane. The previous amber side/bottom borders (from
          // 212m-37) were intended to visually fuse with the
          // FounderOfferCard above; without a top edge on that card,
          // the ring looked disconnected on wide viewports. Inner
          // `.composer-card` still has its own 1px outline so the
          // textarea stays visually contained.
          border: "none",
          // Iter 212m-54 — composer background inherits from parent
          // chat panel via `transparent`.
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
        <div className="composer-card" data-testid="composer-card"
             style={{ position: "relative" }}>
        {/* Iter 212m-190 · Session 3 — Slash-command popover. Absolute
            positioned above the textarea; visible while `slashOpen`
            AND the input still starts with a slash pattern. */}
        {slashOpen && (
          <SlashCommandMenu
            matches={matchSlashCommands(input)}
            selectedIndex={slashIdx}
            onPick={(cmd) => runSlashCommand(cmd)}
          />
        )}
        <textarea
          ref={taRef}
          data-testid="chat-input"
          className="composer-input-bare"
          value={input}
          onChange={(e) => {
            const v = e.target.value;
            setInput(v);
            setDetectedMode(detectMode(v));
            // Iter 212m-190 · Session 3 — Slash-menu detection. Opens
            // the popover the moment the user types a leading "/",
            // filters as they keep typing, closes once the prefix is
            // gone or the input contains a newline.
            const isSlashy = v.startsWith("/") && !v.includes("\n") && matchSlashCommands(v).length > 0;
            setSlashOpen(isSlashy);
            if (!isSlashy) setSlashIdx(0);
            // Iter 212m-123 — Per founder spec: TopBar / focus chrome
            // hides ONLY once the user has actually TYPED something.
            // Pure focus (click into the input) no longer triggers
            // hide — the previous `onFocus` dispatch was removed.
            if (v.length > 0) {
              try { window.dispatchEvent(new CustomEvent("aurem:chat-focus")); }
              catch { /* ignore */ }
            }
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
          placeholder="Ask ORA to build, fix, or scan… (type / for scan commands)"
          // Iter 212m-201 — Composer height depends on execMode:
          //   • Prompt mode  → min 3 rows (extra breathing room for
          //     longer natural-language asks now that LoopStepBar is
          //     hidden).
          //   • Loop mode    → min 2 rows (LoopStepBar sits above and
          //     consumes vertical space, so keep composer compact).
          // Still auto-grows up to 6 rows based on \n count.
          rows={Math.min(
            6,
            Math.max(
              execMode === EXEC_MODES.LOOP ? 2 : 3,
              input.split("\n").length
            )
          )}
          autoFocus
          // Iter 280 P0 fix — allow typing during an active loop so
          // the queue-next feature (Iter 279) is actually reachable.
          // The submit handler still gates network I/O via `busy`;
          // this only unlocks typing + the 409 → queue confirm path.
          // `exhausted` (token depletion) still hard-locks the input.
          disabled={exhausted}
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
          {/* Iter 212m-162 — Security Scan composer button removed.
              The scanner is now surfaced as a "Coming soon" card inside
              /tools (Developer tools) — see ToolsPage.jsx. Removing
              the composer affordance keeps the chat header lean and
              consolidates all upcoming security/health tooling on
              one page. */}
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
                : "GitHub: not connected — click to configure"
            }
            onClick={() => {
              // Iter 212m-199 — Founder request: clicking the connected
              // GitHub toggle used to `window.location.href = "/projects"`
              // which threw users into the legacy Projects page (with the
              // old sidebar). That felt like a regression back to the V1
              // interface. When the repo is already connected we now
              // treat this toggle as a passive status indicator — the
              // tooltip already exposes owner/repo, and the sidebar
              // handles switching. Only the disconnected state still
              // opens the in-place RepoHelpDialog.
              const connected = !!(activeProject?.github_owner && activeProject?.github_repo);
              if (!connected) setShowRepoHelp(true);
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
              // Iter 212m-199 — connected state is passive → default
              // cursor so the button doesn't over-promise interaction.
              cursor: (activeProject?.github_owner && activeProject?.github_repo)
                ? "default"
                : "pointer",
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
          {/* Iter 212m-195 — Vanguard caption pulled INTO the toolbar
              row as the middle flex-1 element. Previously it lived
              as a separate row underneath (`composer-footer-caption`)
              which made the composer visually two-tier. User wanted
              a single bottom row with icons on left, security line
              centered, mode toggles + send on right — this span is
              exactly that middle piece. Retains original testid so
              existing tests keep passing. */}
          <span
            data-testid="composer-footer-caption"
            style={{
              flex: 1, textAlign: "center", padding: "0 12px",
              fontSize: 10, color: "var(--text-faint, #666)",
              fontFamily: "'JetBrains Mono', monospace",
              letterSpacing: "0.04em",
              userSelect: "none",
            }}>
            ORA · Vanguard reviews every change before it ships.
          </span>
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
          {/* Iter 212m-149 → 212m-163 — Intent Tier Indicator + Loop
              toggle BOTH live here.  The Intent Gateway still
              auto-routes every message (casual / query / agentic /
              clarify); the Loop pill is a manual override for
              founder/admin/unlimited users who want to force the full
              Plan → Execute → Verify → Ship pipeline regardless of
              the Gateway's tier pick.  Free / paid non-admin users
              see a locked "Loop · soon" variant so the surface stays
              consistent without giving them the engine. */}
          <IntentTierIndicator liveText={input} lastTier={lastIntentTier} />
          <LoopModeToggle
            value={execMode}
            onChange={handleExecModeChange}
            locked={!isLoopUnlocked}
          />

          {busy ? (
            <>
              {/* Iter 284 — queue-next affordance.
                  When the loop is running AND the user has typed a new
                  prompt in LOOP mode, show a visible send button that
                  fires the exact same send() handler.  Previously only
                  the stop button rendered here, so the queue-next
                  feature (Iter 279) was only reachable via keyboard
                  Enter — undiscoverable. */}
              {execMode === EXEC_MODES.LOOP && input.trim() && sessionId && !exhausted && (
                <button
                  type="button" data-testid="chat-queue-send"
                  onClick={() => send()}
                  title="Queue this message — runs when the current loop finishes"
                  style={{
                    display: "inline-flex", alignItems: "center",
                    justifyContent: "center",
                    width: 38, height: 38, borderRadius: "50%",
                    background: "rgba(255,102,8,0.9)",
                    border: "1px solid rgba(255,102,8,0.9)",
                    color: "#0A0A0A", cursor: "pointer",
                    boxShadow: "0 0 20px -6px rgba(255,102,8,0.7)",
                    transition: "background 140ms, transform 100ms",
                    marginRight: 6,
                  }}
                >
                  <Send size={15} strokeWidth={2.5} style={{ pointerEvents: "none" }} />
                </button>
              )}
              <button
                type="button" data-testid="chat-stop"
                onClick={stop}
                title="Stop streaming"
                style={{
                  display: "inline-flex", alignItems: "center", justifyContent: "center",
                  width: 38, height: 38, borderRadius: "50%",
                  background: "rgba(255,102,8,0.18)",
                  border: "1px solid rgba(255,102,8,0.45)",
                  color: "#FF6608",
                  cursor: "pointer",
                  transition: "background 140ms, transform 100ms",
                }}
              >
                <Square size={14} strokeWidth={2.5} />
              </button>
            </>
          ) : (
            <button
              type="button" data-testid="chat-send"
              // Iter 212m-132 — Bug fix: mouse click was sometimes
              // not firing because the `disabled` attribute could be
              // stale during the click frame (browser respects DOM
              // disabled, but React's render hadn't flipped it to
              // `false` yet after the user typed the last char +
              // immediately clicked).  Enter worked because the
              // onKeyDown bypassed this gate entirely.  Fix:
              //   1. ALWAYS keep the button clickable — the send()
              //      function already gates on the same conditions
              //      (`!text && !readyAttachments.length || busy ||
              //       !sessionId` at line ~1228).  Visual
              //      "disabled" treatment stays via inline styles.
              //   2. Drop the redundant e.preventDefault() and
              //      e.stopPropagation() — button has type="button"
              //      so it has no default-submit action to prevent.
              //   3. Add `onPointerDown` as a redundant earlier-fire
              //      handler in case the click event gets eaten by
              //      a transient overlay (defence in depth).
              //   4. Explicit `pointer-events:auto` + relative
              //      z-index:5 to guarantee no ancestor with
              //      `pointer-events:none` swallows the click.
              //   5. Use `aria-disabled` for SR users instead of
              //      the native `disabled` attribute.
              aria-disabled={!input.trim() || !sessionId || exhausted}
              onPointerDown={(e) => {
                // Native pointer event fires BEFORE click — if any
                // overlay is at z-index above this button, this is
                // our last reliable hook to capture the press.
                // We do NOT send() here (it would double-fire with
                // the synthetic onClick); we just guarantee focus
                // moves to the button so React's onClick will land.
                if (e.currentTarget) e.currentTarget.focus();
              }}
              onClick={() => {
                // Iter 212m-132 — call send() with no event arg
                // (matches the Enter-key path at onKeyDown so both
                // entrypoints take the IDENTICAL code path inside
                // send()).  send() already handles all gating.
                send();
              }}
              title={
                exhausted
                  ? "Tokens exhausted — upgrade your plan"
                  : execMode === EXEC_MODES.LOOP
                    ? "Run loop"
                    : "Send"
              }
              style={{
                display: "inline-flex", alignItems: "center", justifyContent: "center",
                width: 38, height: 38, borderRadius: "50%",
                background: (!input.trim() || !sessionId || exhausted)
                  ? "rgba(255,102,8,0.25)"
                  : "#FF6608",
                border: "none",
                color: (!input.trim() || !sessionId || exhausted) ? "#7A3B0B" : "#0A0A0A",
                cursor: (!input.trim() || !sessionId || exhausted) ? "not-allowed" : "pointer",
                opacity: (!input.trim() || !sessionId || exhausted) ? 0.55 : 1,
                boxShadow: (!input.trim() || !sessionId || exhausted)
                  ? "none"
                  : "0 0 20px -6px rgba(255,102,8,0.7)",
                transition: "background 140ms, opacity 140ms, transform 100ms",
                // Iter 212m-132 — guarantee click reachability.
                pointerEvents: "auto",
                position: "relative",
                zIndex: 5,
              }}
            >
              <Send size={15} strokeWidth={2.5} style={{ pointerEvents: "none" }} />
            </button>
          )}
        </div>
        {/* Iter 212m-195 — old separate footer caption row removed;
            the caption now lives INSIDE the toolbar between left icons
            and right toggles (flex-1 span above), matching the v2
            single-bottom-row layout. */}
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
        repoOwner={activeProject?.github_owner}
        repoName={activeProject?.github_repo}
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

