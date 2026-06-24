/**
 * TaskProgressCard.jsx — Live commit / Ship task progress card.
 *
 * Renders one of three states for a CTO worker task:
 *   1. Running   — animated stage label + last 2 worker steps
 *   2. Failed    — red card with Retry button + truncated error text
 *   3. Success   — green card with commit SHA link, files changed,
 *                  "View diff" + "Rollback" actions.
 *
 * Props:
 *   taskId      (string)   — CTO task UUID
 *   task        (object?)  — polled task row {status, steps, commit_sha, …}
 *   project     (object?)  — {github_owner, github_repo, branch}
 *   onRollback  (fn)       — fired when user confirms rollback
 *
 * Iter 62: extracted from ChatPanel.jsx as part of the P1 split.
 */
import React, { useState } from "react";
import { Loader2, ExternalLink, Undo2 } from "lucide-react";
import { api } from "../lib/api";
import { toast } from "./Toast";

const STAGES = [
  { key: "pulling", label: "Cloning…",          icon: "📡" },
  { key: "reading", label: "Reading files…",    icon: "📄" },
  { key: "fixing",  label: "AI thinking…",      icon: "🧠" },
  { key: "pushing", label: "Writing & pushing…", icon: "🚀" },
  { key: "done",    label: "Pushed",             icon: "✅" },
];

function FailedCard({ taskId, task }) {
  const [retrying, setRetrying] = useState(false);
  async function retry() {
    if (retrying) return;
    setRetrying(true);
    try {
      await api.post(`/cto/tasks/${taskId}/retry`, {});
      toast({ message: "Re-queued", kind: "success" });
    } catch (e) {
      toast({
        message: e?.response?.data?.detail || "Retry failed",
        kind: "error",
      });
    } finally {
      setRetrying(false);
    }
  }
  // Iter 212m-12 — show the friendly translation when the backend
  // has populated it, falling back to the raw error otherwise. The
  // raw error is collapsed behind a "Show technical details"
  // toggle so non-technical founders aren't staring at stack
  // traces.
  const [showRaw, setShowRaw] = useState(false);
  const plain = task.error_plain || "";
  const steps = Array.isArray(task.error_steps) ? task.error_steps : [];
  const suggestion = task.error_suggestion || "";

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
      <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
        <span>❌ Task failed · <span style={{ opacity: 0.7 }}>{taskId}</span></span>
        <button
          data-testid={`ship-retry-${taskId}`}
          onClick={retry}
          disabled={retrying}
          className="btn-ghost"
          style={{
            padding: "2px 8px", fontSize: 10,
            borderColor: "rgba(255,107,107,0.5)",
            color: "var(--danger)",
          }}
        >
          {retrying ? "Re-queuing…" : "↻ Retry"}
        </button>
      </div>

      {plain && (
        <div
          data-testid={`ship-error-plain-${taskId}`}
          style={{ marginTop: 8, fontSize: 13, color: "var(--text)",
                   lineHeight: 1.55, fontFamily: "inherit" }}
        >
          {plain}
        </div>
      )}

      {steps.length > 0 && (
        <ol
          data-testid={`ship-error-steps-${taskId}`}
          style={{
            margin: "8px 0 4px",
            paddingLeft: 22,
            fontSize: 12,
            color: "var(--text-dim)",
            fontFamily: "inherit",
            lineHeight: 1.55,
          }}
        >
          {steps.map((s, i) => (
            <li key={i} style={{ marginBottom: 3 }}>{s}</li>
          ))}
        </ol>
      )}

      {suggestion && (
        <div
          data-testid={`ship-error-suggestion-${taskId}`}
          style={{
            marginTop: 6, padding: "6px 10px",
            background: "rgba(255,197,96,0.08)",
            border: "1px solid rgba(255,197,96,0.3)",
            borderRadius: 4,
            fontSize: 11, color: "var(--accent-2)",
            fontFamily: "inherit", lineHeight: 1.5,
          }}
        >
          💡 {suggestion}
        </div>
      )}

      {task.error && (
        <div style={{ marginTop: 8 }}>
          <button
            data-testid={`ship-show-raw-${taskId}`}
            onClick={() => setShowRaw((v) => !v)}
            className="btn-ghost"
            style={{
              padding: "2px 6px", fontSize: 10,
              color: "var(--text-faint)",
              borderColor: "var(--border)",
            }}
          >
            {showRaw ? "▾ Hide technical details" : "▸ Show technical details"}
          </button>
          {showRaw && (
            <pre
              data-testid={`ship-error-raw-${taskId}`}
              style={{
                marginTop: 6, padding: "8px 10px",
                background: "var(--bg)", color: "var(--text-faint)",
                border: "1px solid var(--border)", borderRadius: 4,
                fontSize: 10, lineHeight: 1.45,
                whiteSpace: "pre-wrap", wordBreak: "break-word",
                fontFamily: "'JetBrains Mono', monospace",
                maxHeight: 220, overflow: "auto",
              }}
            >
              {String(task.error).slice(0, 1200)}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}

export default function TaskProgressCard({ taskId, task, project, onRollback }) {
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
          <Loader2 size={12} style={{ animation: "spin 1s linear infinite", color: "var(--info)" }} />
          <span style={{ color: "var(--info)" }}>{current.icon} {current.label}</span>
          <span style={{ marginLeft: "auto", color: "var(--text-faint)", fontSize: 10 }}>{taskId}</span>
        </div>
        {(task?.steps || []).slice(-2).map((s, i) => (
          <div key={i} style={{
            marginTop: 4, fontSize: 10,
            color: s.status === "error" ? "var(--danger)" : "var(--text-faint)",
          }}>
            {s.step}
          </div>
        ))}
      </div>
    );
  }

  // Failed — own sub-component so hooks stay top-level
  if (status === "failed") {
    return <FailedCard taskId={taskId} task={task} />;
  }

  // Success
  const sha = task.commit_sha;
  const owner = project?.github_owner;
  const repo = project?.github_repo;
  const commitUrl = sha && owner && repo
    ? `https://github.com/${owner}/${repo}/commit/${sha}`
    : null;

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
