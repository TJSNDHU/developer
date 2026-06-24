/**
 * ShipDialog.jsx — Inline "Ship via CTO" action row rendered below an
 * assistant message that contains an `aurem-handoff` brief.
 *
 * Renders one of:
 *   • Idle      — 🚀 Ship via CTO button (+ Maxx chip + ShipLintBadge)
 *   • Shipping  — disabled button with spinner
 *   • Shipped   — TaskProgressCard (live progress / commit / rollback)
 *   • Disabled  — italic hint (tokens exhausted / no active project)
 *   • Error     — red error text
 *
 * Purely presentational: parent (MessageBubble) owns the state machine
 * and passes callbacks.
 *
 * Iter 62: extracted from ChatPanel.jsx as part of the P1 split.
 */
import React from "react";
import { Loader2, Rocket } from "lucide-react";
import ShipLintBadge from "./ShipLintBadge";
import TaskProgressCard from "./TaskProgressCard";
import TaskLiveTape from "./TaskLiveTape";

export default function ShipDialog({
  idx,
  msg,
  handoffBrief,
  canShip,
  exhausted,
  shipState,
  taskInfo,
  activeProject,
  onShip,
  onRollback,
  onOpenDeployTab,
}) {
  if (!handoffBrief) return null;

  // Iter 212m-9 — "Code shipped — ready to go live?" reminder. Renders
  // whenever the shipped task is fully done so the user can jump
  // straight to the Deploy panel without hunting for the toolbar
  // button.
  const showDeployBanner = (
    shipState.status === "shipped"
    && taskInfo?.status === "done"
    && !!activeProject?.project_id
    && typeof onOpenDeployTab === "function"
  );

  return (
    <div data-testid={`ship-cto-row-${idx}`} style={{
      marginTop: 10, paddingLeft: 4,
      display: "flex", alignItems: "stretch", gap: 10, flexWrap: "wrap",
      flexDirection: "column",
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
      {!canShip ? (
        <div style={{ fontSize: 11, color: "var(--text-faint)", fontStyle: "italic" }}>
          {exhausted
            ? "🚫 Tokens exhausted — upgrade your plan to ship via CTO."
            : "Switch to a connected project to enable Ship via CTO."}
        </div>
      ) : shipState.status === "shipped" ? (
        <div style={{ flex: 1, minWidth: 0 }}>
          <TaskLiveTape taskId={shipState.taskId} />
          <TaskProgressCard
            taskId={shipState.taskId}
            task={taskInfo}
            project={activeProject}
            onRollback={onRollback}
          />
        </div>
      ) : (
        <>
          <button
            data-testid={`ship-cto-btn-${idx}`}
            onClick={onShip}
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
          {/* Iter 47 — Maxx mode chip + brief lint preview */}
          {msg.maxxMode && (
            <span
              data-testid={`ship-maxx-chip-${idx}`}
              title="Maxx mode ON — Claude reviews DeepSeek output before commit"
              style={{
                padding: "3px 8px", borderRadius: 4,
                fontSize: 10, fontWeight: 700,
                letterSpacing: "0.05em",
                fontFamily: "'JetBrains Mono', monospace",
                background: "var(--accent-soft)",
                color: "var(--accent-2)",
                border: "1px solid var(--border-strong)",
              }}
            >MAXX</span>
          )}
          <ShipLintBadge brief={handoffBrief} testidSuffix={idx} />
        </>
      )}
      {shipState.status === "error" && (
        <span style={{ fontSize: 11, color: "var(--danger)" }}>
          {shipState.error}
        </span>
      )}
      </div>

      {showDeployBanner && (
        <div
          data-testid={`ship-deploy-banner-${idx}`}
          style={{
            display: "flex", alignItems: "center", gap: 10,
            padding: "10px 14px",
            background: "linear-gradient(90deg, var(--accent-soft) 0%, transparent 100%)",
            border: "1px solid var(--accent-2)",
            borderRadius: 6,
            flexWrap: "wrap",
          }}
        >
          <Rocket size={14} color="var(--accent-2)" />
          <span style={{
            fontSize: 12, color: "var(--text)", fontWeight: 600,
            fontFamily: "'JetBrains Mono', monospace",
          }}>
            🚀 Code shipped — ready to go live?
          </span>
          <span style={{
            fontSize: 11, color: "var(--text-dim)", flex: 1, minWidth: 120,
          }}>
            Push the merged commit to your server with one click.
          </span>
          <button
            data-testid={`ship-deploy-banner-btn-${idx}`}
            onClick={onOpenDeployTab}
            style={{
              padding: "6px 14px", fontSize: 12, fontWeight: 600,
              background: "var(--accent-2)", color: "var(--bg)",
              border: "1px solid var(--accent-2)", borderRadius: 4,
              cursor: "pointer",
              fontFamily: "'JetBrains Mono', monospace",
              letterSpacing: "0.05em",
            }}
          >
            Open Deploy →
          </button>
        </div>
      )}
    </div>
  );
}
