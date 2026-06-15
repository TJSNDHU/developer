/**
 * MessageBubble.jsx — Single chat message renderer.
 *
 * Owns:
 *   • Bubble layout (user / assistant), avatar, glass styling
 *   • Streaming cursor / activity / elapsed seconds
 *   • Inline HTML iframe preview (when assistant content has a fenced HTML block)
 *   • Hover-only action row (Copy / 👍 / 👎)
 *   • Maxx-mode footer chip
 *   • Ship-via-CTO dialog (ShipDialog) + auto-handoff progress (TaskProgressCard)
 *   • Watchdog status panel (WatchdogPanel)
 *
 * Iter 62: extracted from ChatPanel.jsx as part of the P1 split.
 */
import React, { useState, useEffect, useRef } from "react";
import {
  User, Bot, Loader2, ShieldCheck, AlertTriangle, RefreshCw,
  Copy as CopyIcon, ThumbsUp, ThumbsDown, Zap,
} from "lucide-react";
import { api } from "../lib/api";
import { toast } from "./Toast";
import ShipDialog from "./ShipDialog";
import TaskProgressCard from "./TaskProgressCard";
import TaskLiveTape from "./TaskLiveTape";
import TaskManagementPanel, { hasChecklist } from "./TaskManagementPanel";
import RenderedMessage from "./RenderedMessage";

// ---- Helpers (only used here) ----------------------------------------------

// Pull out HTML content from assistant message — either a ```html fence,
// a raw <html>…</html> blob, or a <!doctype html>…</html> doc. Returned
// markup is injected via iframe srcDoc with sandbox="allow-scripts".
function extractInlineHTML(text) {
  if (!text || typeof text !== "string") return null;
  const m1 = text.match(/```html\n([\s\S]*?)```/i);
  if (m1) return m1[1];
  const m2 = text.match(/<html[\s\S]*<\/html>/i);
  if (m2) return m2[0];
  const m3 = text.match(/<!doctype html[\s\S]*<\/html>/i);
  if (m3) return m3[0];
  return null;
}

// Detect a ```aurem-handoff fenced block — emitted by AUREM in HANDOFF MODE.
//
// Iter 84 + 85 tightening (defense-in-depth with the orchestrator prompt):
// the fence is ONLY for actionable code mutation work. We've seen the
// model leak it for follow-up reading instructions, permission-asking
// questions, vague advice without file paths, and most importantly
// fabricated citations (paths copied from semantic_search_repo that the
// model never actually opened). Each gate below targets a specific
// failure mode observed in production.
//
// Gates (in order, short-circuiting):
//   1. Length: 40 ≤ chars ≤ 1500. ≤ 12 non-empty lines.
//   2. Any '?' anywhere → reject.
//   3. Permission-asking phrases → reject.
//   4. Every non-empty line is a read-only verb / passive lookup → reject.
//   5. At least one mutation verb tied to file work (sharp list of 27).
//   6. At least one file-path token (slash + known extension).
//   7. Iter 85 — every file-path token in the fence MUST appear in the
//      `verifiedPaths` set provided by the backend (paths actually
//      `read_repo_file`'d this turn). Citations the model fabricated
//      from a semantic_search hit but never opened are rejected.
const MAX_BRIEF_CHARS = 1500;
const MAX_BRIEF_LINES = 12;

// Mutation verbs — sharp list of 27. Excludes soft verbs the model
// abuses in non-mutation senses (build / update / handle / expose /
// validate / render / configure / set up), AND excludes verbs that
// are usually conversational rather than file-changing
// (import / export / mount / swap / extract).
const MUTATION_VERBS = new RegExp(
  "\\b(create|add|fix|write|edit|rewrite|refactor|replace|implement" +
    "|scaffold|wire|install|patch|delete|remove|migrate|generate" +
    "|integrate|ship|introduce|inject|deprecate|rename|move|append" +
    "|prepend|register)\\b",
  "i",
);

// Lines whose ACTION is read-only. Catches active read verbs AND
// passive lookups ("is located at", "can be found in", "appears to
// live at", "may reference"). Anchored to line start after optional
// list bullet / number so a mid-sentence "the bug is located at line 80
// of auth.py" inside a real ship brief is NOT mis-rejected.
const READ_ONLY_LINE = new RegExp(
  "^\\s*[-*•]?\\s*\\d*[.)]?\\s*" +
    "(read|inspect|check|review|examine|see how|see if|look at" +
    "|look into|take a look|have a look|glance at|investigate|verify" +
    "|confirm|explore|browse|find|search|locate|identify|understand" +
    "|may (be|reference|include|contain)" +
    "|might (be|reference|include|contain)" +
    "|could (be|include|contain)" +
    "|appears to|seems to" +
    "|is (located|defined|found|present) (in|at)" +
    "|can be found|lives (in|at))\\b",
  "i",
);

// Permission-asking phrases ORA leaks instead of just doing the work.
const PERMISSION_PHRASES = new RegExp(
  "\\b(would you like|should i\\b|shall i\\b|want me to|do you want" +
    "|let me know|if you'?d like|confirm if|tell me which" +
    "|which (one|file)|do you prefer|happy to (read|check|look)" +
    "|i can (read|check|look))\\b",
  "i",
);

// A real file-path token = at least one '/' AND a known extension.
// Filenames alone (no slash) do NOT qualify.
const FILE_PATH_TOKEN = new RegExp(
  "\\b[\\w.\\-/@]+/[\\w.\\-]+" +
    "\\.(py|pyi|js|jsx|ts|tsx|md|mdx|json|ya?ml|css|scss|sass|html?" +
    "|env|toml|sh|sql|graphql|prisma|svelte|vue|rs|go|java|kt|swift)\\b",
  "i",
);

// Pull EVERY file-path token out of the brief — used by Gate 7 to
// cross-check against `verifiedPaths`. The global flag matters here.
const FILE_PATH_TOKEN_GLOBAL = new RegExp(FILE_PATH_TOKEN.source, "gi");

function _normalisePath(p) {
  if (!p) return "";
  // Leading "./" and "/" are harmless; backend stores plain repo paths.
  return String(p).replace(/^\.?\/+/, "").trim();
}

function extractHandoffBrief(content, verifiedPaths) {
  if (!content || typeof content !== "string") return null;
  const m = content.match(/```aurem-handoff\s*\n([\s\S]*?)```/);
  if (!m) return null;
  const brief = m[1].trim();

  // Gate 1 — length / line caps.
  if (brief.length < 40) return null;
  if (brief.length > MAX_BRIEF_CHARS) return null;
  const lines = brief
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter(Boolean);
  if (lines.length === 0 || lines.length > MAX_BRIEF_LINES) return null;

  // Gate 2 — any question mark anywhere kills the brief.
  if (/\?/.test(brief)) return null;

  // Gate 3 — explicit permission-asking phrases.
  if (PERMISSION_PHRASES.test(brief)) return null;

  // Gate 4 — every non-empty line is read-only / passive lookup.
  const allReadOnly = lines.every((l) => READ_ONLY_LINE.test(l));
  if (allReadOnly) return null;

  // Gate 5 — at least one mutation verb tied to file work.
  if (!MUTATION_VERBS.test(brief)) return null;

  // Gate 6 — at least one concrete file-path token.
  if (!FILE_PATH_TOKEN.test(brief)) return null;

  // Gate 7 — fabricated-citation guard. Iter 85 (refined Iter 86).
  // The relaxed contract:
  //   • If `verifiedPaths` is empty/absent → skip the gate (version-skew).
  //   • Otherwise, the brief must contain AT LEAST ONE path that IS
  //     in verifiedPaths. That proves the model actually opened a
  //     real file this turn. The remaining paths may be new files
  //     the worker will CREATE (e.g. `backend/tests/test_foo.py`) —
  //     those can't be in verifiedPaths because they don't exist on
  //     disk yet. The original "ALL paths must match" rule killed
  //     this legitimate new-file-creation case.
  if (Array.isArray(verifiedPaths) && verifiedPaths.length > 0) {
    const seen = new Set(verifiedPaths.map(_normalisePath));
    const briefPaths = (brief.match(FILE_PATH_TOKEN_GLOBAL) || [])
      .map(_normalisePath)
      .filter(Boolean);
    if (briefPaths.length > 0) {
      const matched = briefPaths.filter((p) => seen.has(p));
      if (matched.length === 0) {
        // Every path in the brief was fabricated → fence is junk.
        return null;
      }
    }
  }

  return brief;
}

// ---- Sub-components --------------------------------------------------------

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

// ---- Main component --------------------------------------------------------

export default function MessageBubble({
  idx, dbTurnIndex, m, onRegenerate, sessionId,
  activeProject, exhausted, onTaskCompleted,
}) {
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

  // Iter 135 — Client-side fallback elapsed counter.
  // Backend emits `tick` SSE frames every 0.5-0.6s with the canonical
  // elapsed_s (see chat.py `_ticker` / `_maybe_ship_shortcut`). Before
  // the FIRST tick lands the bubble used to show "thinking…" with no
  // number, which made users think the system was hung. We now run a
  // local 100ms timer the moment `m.streaming` flips on. Display logic
  // uses `Math.max(local, backend)` so backend ticks remain
  // authoritative and the counter is always monotonic — never goes
  // backwards when backend updates jump ahead of local.
  const streamStartRef = useRef(null);
  const [localElapsedS, setLocalElapsedS] = useState(0);
  useEffect(() => {
    if (!m.streaming) {
      streamStartRef.current = null;
      setLocalElapsedS(0);
      return undefined;
    }
    if (streamStartRef.current === null) {
      streamStartRef.current = Date.now();
    }
    const id = setInterval(() => {
      if (streamStartRef.current !== null) {
        setLocalElapsedS((Date.now() - streamStartRef.current) / 1000);
      }
    }, 100);
    return () => clearInterval(id);
  }, [m.streaming]);

  const displayElapsedS = Math.max(
    typeof m.elapsedS === "number" ? m.elapsedS : 0,
    localElapsedS,
  );

  // Iter 51 — when the server emits `task_handoff` mid-stream the parent
  // patches m.shipped_task_id but shipState was frozen at mount. Sync
  // when m.shipped_task_id changes so the poll loop actually fires.
  useEffect(() => {
    if (m.shipped_task_id && m.shipped_task_id !== shipState.taskId) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setShipState((s) => ({
        ...s, status: "shipped", taskId: m.shipped_task_id, error: null,
      }));
    }
  }, [m.shipped_task_id, shipState.taskId]);

  // Poll the CTO task while it's in progress, until done/failed
  // Iter 151 — Production logs showed runaway polling: 50+ GETs to
  // /cto/tasks/{id} in seconds across 4 stuck task IDs. Root causes:
  //   (a) TERMINAL set was too narrow — `error`, `blocked`, `rejected`,
  //       `cancelled` never stopped the loop;
  //   (b) the API call itself swallowed errors silently, so a 4xx/5xx
  //       just re-fired tick() every 2s forever;
  //   (c) no maximum-attempt safety cap.
  // We now (i) recognise all known terminal states, (ii) stop on any
  // hard error from the API, (iii) hard-cap at 30 minutes (900 polls).
  useEffect(() => {
    const tid = shipState.taskId;
    if (!tid) return;
    let cancelled = false;
    let attempts = 0;
    const MAX_ATTEMPTS = 900;          // 900 * 2s = 30 minutes
    const TERMINAL = new Set([
      "done", "failed", "error", "blocked", "rejected",
      "cancelled", "canceled", "completed", "timed_out",
    ]);
    async function tick() {
      if (cancelled) return;
      attempts += 1;
      if (attempts > MAX_ATTEMPTS) {
        // Give up — task is either stuck or backend is unreachable.
        // Either way, stop hammering the API.
        return;
      }
      try {
        const r = await api.get(`/cto/tasks/${tid}`);
        const t = r.data?.task || null;
        if (cancelled) return;
        if (!t) {
          // Task not found — terminal. Stop polling.
          return;
        }
        setTaskInfo(t);
        if (!TERMINAL.has(t.status)) {
          setTimeout(tick, 2000);
        } else {
          // Iter 53 — fire the post-commit wrap-up once. Parent dedupes
          // via a ref + the backend endpoint is itself idempotent, so a
          // second fire is harmless if the effect re-runs.
          if (onTaskCompleted) onTaskCompleted(tid);
        }
      } catch (err) {
        // 4xx → permission / missing task; 5xx → backend down. Either
        // way, hammering the same endpoint won't help. Stop.
        const code = err?.response?.status;
        if (code && code >= 400 && code < 500) {
          // Hard stop — task is inaccessible to us. No retry.
          return;
        }
        // 5xx or network — back off and try once more, then give up.
        if (attempts < 3) {
          setTimeout(tick, 5000);
        }
      }
    }
    tick();
    return () => { cancelled = true; };
  }, [shipState.taskId, onTaskCompleted]);

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
      toast({
        message: next === "up" ? "Thanks — noted 👍" : "Got it — we'll do better",
        kind: "info", duration: 1800,
      });
    } catch {
      /* ignore */
    }
  }

  const showActions = m.role === "assistant" && !m.streaming && m.provider !== "system" && !m.error;
  const showUserCopy = m.role === "user" && !!m.content;
  // Detect ```aurem-handoff fence → render one-click Ship via CTO button
  // Iter 89: once a turn has been shipped (m.shipped_task_id present
  // from /chat/history), suppress the handoff brief entirely so the
  // Ship button can NEVER come back. The raw `​```aurem-handoff` fence
  // stays in m.content (we don't mutate past messages), but render
  // path B (line ~629) takes over and shows TaskLiveTape inline.
  //
  // This is the user-reported fix: previously the button reappeared
  // after refresh / re-login because extractHandoffBrief ran against
  // the raw content and didn't know the turn had already shipped.
  const handoffBrief = showActions && !m.shipped_task_id
    ? extractHandoffBrief(m.content, m.verifiedPaths)
    : null;
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
        maxx_mode: !!m.maxxMode,   // iter 47: per-message Maxx flag flows to backend
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
        <div
          className={
            "glass-bubble " +
            (m.role === "user" ? "glass-bubble-user" : "glass-bubble-assistant")
          }
          style={{
            padding: "12px 16px",
            fontSize: 14, lineHeight: 1.6,
            color: m.error ? "var(--danger)" : "var(--text)",
            whiteSpace: "pre-wrap", wordBreak: "break-word",
          }}
        >
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
          {/* Iter 148 — Monaco-powered code rendering for fenced blocks.
              User-typed messages don't get parsed (preserves raw text);
              assistant replies get full syntax highlighting + copy. */}
          {m.role === "assistant" ? (
            <RenderedMessage text={m.content} />
          ) : (
            m.content
          )}
          {/* Multi-file checklist parsed from ORA's message + DB-backed plan (pairs with Gap 3 + structural multi-file contract). */}
          {m.role === "assistant" && (hasChecklist(m.content) || m.shipped_task_id) && (
            <TaskManagementPanel text={m.content} taskId={m.shipped_task_id} />
          )}
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
            <div data-testid="chat-thinking-wrap" style={{
              display: "inline-flex", flexDirection: "column",
              gap: 6, minWidth: 220,
            }}>
              {/* Iter 136/141 — Top-of-bubble progress line.
                 Iter 141 replaces the asymptotic time-based dummy
                 with REAL milestone progress driven by SSE events:
                 meta=15%, mode=25%, thinking activity=30-50%,
                 first token=55%, tokens stream toward 95%, done=100%.
                 We fall back to the time curve ONLY if `progressPct`
                 isn't set yet (very first ~50ms before the meta
                 frame lands) so the bar never sits at 0. */}
              {(() => {
                const real = typeof m.progressPct === "number" ? m.progressPct : null;
                const t = displayElapsedS;
                const fallback = Math.min(15, (1 - Math.exp(-t / 2)) * 15);
                const pctRaw = real !== null ? real : fallback;
                const pct = Math.max(0, Math.min(100, pctRaw)) / 100;
                // Yellow (255,197,96) → Green (109,212,161)
                const r = Math.round(255 + (109 - 255) * pct);
                const g = Math.round(197 + (212 - 197) * pct);
                const b = Math.round(96 + (161 - 96) * pct);
                const colour = `rgb(${r}, ${g}, ${b})`;
                return (
                  <div
                    data-testid="chat-thinking-progress"
                    aria-label={`generating reply — ${Math.round(pct * 100)}%`}
                    style={{
                      width: "100%",
                      height: 3,
                      background: "rgba(255,255,255,0.06)",
                      borderRadius: 999,
                      overflow: "hidden",
                    }}
                  >
                    <div
                      style={{
                        width: `${pct * 100}%`,
                        height: "100%",
                        background: colour,
                        boxShadow: `0 0 6px ${colour}`,
                        transition: "width 350ms cubic-bezier(0.25, 0.46, 0.45, 0.94), background-color 350ms linear, box-shadow 350ms linear",
                      }}
                    />
                  </div>
                );
              })()}
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
                  {` · ${displayElapsedS.toFixed(1)}s`}
                </span>
              </span>
              {/* Iter 149 — Live tool invocation chips. Surfaces the
                  raw tool-call activity from the orchestrator (e.g.
                  "read_repo_file ✓", "search_repo …") so the user knows
                  EXACTLY what AUREM is touching while it streams.
                  Hidden when no tools have been called this turn. */}
              {Array.isArray(m.invocations) && m.invocations.length > 0 && (
                <div
                  data-testid="chat-invocations"
                  style={{
                    marginTop: 8,
                    display: "flex", flexWrap: "wrap", gap: 6,
                  }}
                >
                  {m.invocations.slice(-6).map((inv, i) => {
                    const name = inv.tool || inv.name || "tool";
                    const done = inv.status === "ok" || inv.ok === true || inv.completed;
                    const failed = inv.status === "error" || inv.ok === false;
                    return (
                      <span
                        key={i}
                        data-testid={`chat-invocation-${i}`}
                        title={inv.summary || inv.detail || name}
                        style={{
                          padding: "2px 8px",
                          fontSize: 10,
                          fontFamily: "'JetBrains Mono', monospace",
                          letterSpacing: "0.04em",
                          borderRadius: 999,
                          border: "1px solid " + (
                            failed ? "rgba(255,107,107,0.4)"
                              : done ? "rgba(109,212,161,0.3)"
                              : "var(--border)"
                          ),
                          background: failed ? "rgba(255,107,107,0.06)"
                            : done ? "rgba(109,212,161,0.06)"
                            : "rgba(255,255,255,0.03)",
                          color: failed ? "var(--danger)"
                            : done ? "var(--ok)"
                            : "var(--text-faint)",
                        }}
                      >
                        {failed ? "✗ " : done ? "✓ " : "▸ "}
                        {name}
                      </span>
                    );
                  })}
                </div>
              )}
            </div>
          )}
          {m.streaming && m.content && (
            <>
              <span data-testid="chat-cursor" style={{
                display: "inline-block", width: 7, height: 14,
                marginLeft: 2, background: "var(--accent-2)",
                verticalAlign: "middle",
                animation: "blink 0.9s steps(1) infinite",
              }} />
              {displayElapsedS > 1.5 && (
                <div style={{
                  marginTop: 6, fontSize: 10,
                  color: "var(--text-faint)",
                  fontFamily: "'JetBrains Mono', monospace",
                }}>
                  · {displayElapsedS.toFixed(1)}s
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
          {/* Iter 161 — Agent chip. Shows which engine ACTUALLY wrote
              the reply (and if it was reviewed). Mapping:
                review_mode=swift → DeepSeek wrote, Claude diff-reviewed
                review_mode=pro   → DeepSeek wrote, Claude full-reviewed
                review_mode=maxx  → Claude wrote (no review tail)
                else (auto/chat) → fall back to raw provider name      */}
          {!m.streaming && m.provider && m.provider !== "system"
              && !m.council && (
            (() => {
              const rm = (m.reviewMode || "").toLowerCase();
              const prov = (m.provider || "").toLowerCase();
              let writer = null;
              let reviewer = null;
              if (rm === "maxx" || prov.includes("claude")) {
                writer = "Claude";
              } else if (rm === "swift" || rm === "pro") {
                writer = "DeepSeek";
                reviewer = "Claude";
              } else if (prov.includes("deepseek")) {
                writer = "DeepSeek";
              }
              if (!writer) return null;
              return (
                <div
                  data-testid={`agent-chip-${idx}`}
                  data-review-mode={rm || "auto"}
                  style={{
                    marginTop: 8, fontSize: 10,
                    fontFamily: "'JetBrains Mono', monospace",
                    color: "var(--text-faint)",
                    letterSpacing: "0.1em",
                    display: "inline-flex", alignItems: "center", gap: 6,
                    padding: "2px 8px", borderRadius: 999,
                    background: "rgba(255, 138, 42, 0.05)",
                    border: "1px solid rgba(255, 138, 42, 0.20)",
                  }}
                >
                  <span style={{ color: "var(--accent-2)" }}>{writer}</span>
                  <span>wrote</span>
                  {reviewer && (
                    <>
                      <span style={{ opacity: 0.5 }}>·</span>
                      <span style={{ color: "var(--accent-2)" }}>{reviewer}</span>
                      <span>reviewed</span>
                    </>
                  )}
                </div>
              );
            })()
          )}
          {!m.streaming && (m.council || m.provider === "mode-b-council") && (
            <div data-testid={`council-badge-${idx}`} style={{
              marginTop: 8, fontSize: 10,
              fontFamily: "'JetBrains Mono', monospace",
              color: "var(--accent-2)", letterSpacing: "0.1em",
              display: "inline-flex", alignItems: "center", gap: 6,
              padding: "2px 8px",
              border: "1px solid var(--accent-2)",
              borderRadius: 999,
              opacity: 0.85,
            }}>
              · 5-adviser council · chairman verdict
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

        {/* Ship via CTO row — only when an aurem-handoff brief is present */}
        <ShipDialog
          idx={idx}
          msg={m}
          handoffBrief={handoffBrief}
          canShip={canShip}
          exhausted={exhausted}
          shipState={shipState}
          taskInfo={taskInfo}
          activeProject={activeProject}
          onShip={shipViaCTO}
          onRollback={rollbackShipped}
        />

        {/* Iter 51 — Auto-handoff (Mode D→C, etc.) progress card.
            When the server fires `task_handoff` with no aurem-handoff
            fence, render TaskProgressCard inline so the user sees live
            worker progress in the same chat bubble. */}
        {m.role === "assistant"
          && m.shipped_task_id
          && !handoffBrief
          && !m.streaming && (
          <div data-testid={`auto-handoff-row-${idx}`} style={{
            marginTop: 10, paddingLeft: 4,
          }}>
            <TaskLiveTape
              taskId={shipState.taskId || m.shipped_task_id}
            />
            <TaskProgressCard
              taskId={shipState.taskId || m.shipped_task_id}
              task={taskInfo}
              project={activeProject}
              onRollback={rollbackShipped}
            />
          </div>
        )}

        {/* Iter 119 — Web citation chips (Tavily / Firecrawl / fetch_url).
            Shows the user where ORA actually fetched external info from.
            One-click verifiable; no chip means no web read this turn. */}
        {m.role === "assistant"
          && !m.streaming
          && Array.isArray(m.webSources)
          && m.webSources.length > 0 && (
          <div data-testid={`citation-chips-${idx}`} style={{
            marginTop: 10, display: "flex", flexWrap: "wrap", gap: 6,
            alignItems: "center", paddingLeft: 4,
          }}>
            <span style={{
              fontSize: 10, color: "var(--text-faint)",
              letterSpacing: ".06em", textTransform: "uppercase",
              marginRight: 4,
            }}>
              Web sources
            </span>
            {m.webSources.map((src, ci) => {
              let domain = "";
              try { domain = new URL(src.url).hostname.replace(/^www\./, ""); }
              catch { domain = src.url; }
              return (
                <a
                  key={ci}
                  data-testid={`citation-chip-${idx}-${ci}`}
                  href={src.url}
                  target="_blank"
                  rel="noopener noreferrer nofollow"
                  title={src.title || src.url}
                  style={{
                    display: "inline-flex", alignItems: "center", gap: 4,
                    padding: "3px 8px",
                    background: "var(--panel-2)",
                    border: "1px solid var(--border)",
                    borderRadius: 999,
                    fontSize: 11, lineHeight: 1.4,
                    color: "var(--text-dim)",
                    textDecoration: "none",
                    fontFamily: "ui-monospace, Menlo, monospace",
                    transition: "border-color .12s, color .12s",
                  }}
                  onMouseEnter={(e) => {
                    e.currentTarget.style.borderColor = "var(--accent)";
                    e.currentTarget.style.color = "var(--text)";
                  }}
                  onMouseLeave={(e) => {
                    e.currentTarget.style.borderColor = "var(--border)";
                    e.currentTarget.style.color = "var(--text-dim)";
                  }}
                >
                  <span style={{ fontSize: 10 }}>🌐</span>
                  <span>{domain}</span>
                </a>
              );
            })}
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
