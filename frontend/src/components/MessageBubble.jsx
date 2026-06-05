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
import React, { useState, useEffect } from "react";
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

  // Gate 7 — fabricated-citation guard. Iter 85.
  // If the backend tells us which files the model actually read this
  // turn, EVERY path inside the brief must be in that set. If the
  // backend omits the field (e.g. an older deployment) we don't enforce
  // — better to render a real Ship button than to over-block in a
  // version-skew scenario.
  if (Array.isArray(verifiedPaths) && verifiedPaths.length > 0) {
    const seen = new Set(verifiedPaths.map(_normalisePath));
    const briefPaths = brief.match(FILE_PATH_TOKEN_GLOBAL) || [];
    const fabricated = briefPaths
      .map(_normalisePath)
      .filter((p) => p && !seen.has(p));
    if (fabricated.length > 0) return null;
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

  // Iter 51 — when the server emits `task_handoff` mid-stream the parent
  // patches m.shipped_task_id but shipState was frozen at mount. Sync
  // when m.shipped_task_id changes so the poll loop actually fires.
  useEffect(() => {
    if (m.shipped_task_id && m.shipped_task_id !== shipState.taskId) {
      setShipState((s) => ({
        ...s, status: "shipped", taskId: m.shipped_task_id, error: null,
      }));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [m.shipped_task_id]);

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
        } else {
          // Iter 53 — fire the post-commit wrap-up once. Parent dedupes
          // via a ref + the backend endpoint is itself idempotent, so a
          // second fire is harmless if the effect re-runs.
          if (onTaskCompleted) onTaskCompleted(tid);
        }
      } catch {
        /* keep last known state */
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
  const handoffBrief = showActions
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
          {m.content}
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
                <div style={{
                  marginTop: 6, fontSize: 10,
                  color: "var(--text-faint)",
                  fontFamily: "'JetBrains Mono', monospace",
                }}>
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
