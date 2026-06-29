/**
 * PersistentFixBar.jsx — Iter 212m-148
 *
 * Thin (44 px) bar pinned to the bottom of the viewport. Shows
 * progress of the current Bulk Fix job and stays visible across
 * route changes, panel toggles, and backdrop dismissals.
 *
 * Founder spec:
 *   - 44 px tall, full-width, fixed bottom-0, highest z-index below
 *     critical modals (we use 1290 — drawer is 1301).
 *   - Layout L→R: pulse dot · label+sub · count badge · chevron.
 *   - States: running (amber), done (green), error (red).
 *   - 2 px animated progress track at bottom of bar.
 *   - Click bar → togglePanel(); never closes the SSE.
 *   - "Dismiss" only available in terminal states (done/error).
 *   - Bar hidden when status==='idle' OR user dismissed terminal.
 */
import React from "react";
import { ChevronUp, ChevronDown, X } from "lucide-react";
import { useFixJob } from "./FixJobContext";

function basename(p) {
  if (!p) return "";
  const i = p.lastIndexOf("/");
  return i >= 0 ? p.slice(i + 1) : p;
}

export default function PersistentFixBar() {
  const {
    status, total, completed, failed, remaining,
    activeRow, terminal, panelVisible, dismissed,
    togglePanel, dismiss,
  } = useFixJob();

  if (status === "idle" || dismissed) return null;

  const tone = status === "done"
    ? { dot: "#86efac", bar: "#22c55e", label: "#86efac", bg: "rgba(34,197,94,0.07)", border: "rgba(34,197,94,0.30)" }
    : status === "error"
      ? { dot: "#fca5a5", bar: "#ef4444", label: "#fca5a5", bg: "rgba(239,68,68,0.07)", border: "rgba(239,68,68,0.30)" }
      : { dot: "#fdba74", bar: "#fb923c", label: "#fde68a", bg: "rgba(251,146,60,0.06)", border: "rgba(251,146,60,0.30)" };

  const labelMain = status === "done"
    ? "Fix complete"
    : status === "error"
      ? "Fix stopped"
      : "Fixing codebase";
  const labelSub = (() => {
    if (status === "done") {
      const ok = completed - failed;
      return `${ok} fixed${failed > 0 ? `, ${failed} failed` : ""}`;
    }
    if (status === "error") {
      const activeFile = activeRow?.file ? basename(activeRow.file) : "";
      return activeFile
        ? `${activeFile} failed`
        : (terminal?.message?.slice(0, 80) || "Worker error");
    }
    const fileLabel = activeRow?.file ? basename(activeRow.file) : "preparing…";
    return `${completed + 1} of ${total} · ${fileLabel}`;
  })();
  const countBadge = status === "done"
    ? `${completed - failed} done`
    : status === "error"
      ? `${failed} failed`
      : `${remaining} left`;

  const pct = total ? Math.min(100, Math.round((completed / total) * 100)) : 0;
  const showDismiss = status === "done" || status === "error";

  return (
    <div
      data-testid="persistent-fix-bar"
      data-status={status}
      style={{
        position: "fixed", bottom: 0, left: 0, right: 0,
        height: 44, zIndex: 1290,
        background: "rgba(13,16,24,0.96)",
        backdropFilter: "blur(12px)",
        borderTop: `1px solid ${tone.border}`,
        display: "flex", alignItems: "stretch",
        animation: "fixBarSlideUp 240ms ease-out",
        color: "#e8ecf3",
      }}
    >
      <style>{`
        @keyframes fixBarSlideUp {
          from { transform: translateY(100%); opacity: 0.4; }
          to   { transform: translateY(0); opacity: 1; }
        }
        @keyframes barPulseDot {
          0%, 100% { opacity: 0.55; transform: scale(1); }
          50%      { opacity: 1; transform: scale(1.15); }
        }
        @keyframes barProgressShine {
          0%   { background-position: -200px 0; }
          100% { background-position: 200px 0; }
        }
      `}</style>

      <button
        type="button"
        data-testid="persistent-fix-bar-toggle"
        onClick={togglePanel}
        style={{
          flex: 1, minWidth: 0,
          display: "flex", alignItems: "center", gap: 12,
          padding: "0 18px",
          background: "transparent", border: 0,
          cursor: "pointer", color: "inherit",
          textAlign: "left",
        }}
      >
        <span
          data-testid="persistent-fix-bar-dot"
          style={{
            width: 9, height: 9, borderRadius: 999,
            background: tone.dot, flexShrink: 0,
            animation: status === "running" ? "barPulseDot 1.2s infinite" : "none",
          }}
        />
        <div style={{ flex: 1, minWidth: 0, display: "flex", alignItems: "center", gap: 12, fontSize: 12 }}>
          <span
            data-testid="persistent-fix-bar-label"
            style={{ fontWeight: 700, color: tone.label, letterSpacing: "0.02em" }}
          >{labelMain}</span>
          <span style={{
            color: "#94a3b8",
            fontFamily: "'JetBrains Mono', monospace",
            overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
          }}>· {labelSub}</span>
        </div>
        <span
          data-testid="persistent-fix-bar-badge"
          style={{
            padding: "3px 10px", borderRadius: 999, fontSize: 10,
            fontWeight: 700, letterSpacing: "0.05em",
            background: tone.bg, border: `1px solid ${tone.border}`,
            color: tone.label, fontFamily: "'JetBrains Mono', monospace",
            flexShrink: 0,
          }}
        >{countBadge}</span>
        <span style={{ display: "inline-flex", alignItems: "center",
                       color: "#94a3b8", flexShrink: 0 }}>
          {panelVisible
            ? <ChevronDown size={14} />
            : <ChevronUp   size={14} />}
        </span>
      </button>

      {showDismiss && (
        <button
          type="button"
          data-testid="persistent-fix-bar-dismiss"
          onClick={(e) => { e.stopPropagation(); dismiss(); }}
          title="Dismiss"
          style={{
            display: "flex", alignItems: "center", justifyContent: "center",
            width: 44, background: "transparent",
            border: 0, borderLeft: "1px solid rgba(255,255,255,0.06)",
            color: "#64748b", cursor: "pointer",
          }}
        ><X size={14} /></button>
      )}

      {/* 2 px progress track */}
      <div
        data-testid="persistent-fix-bar-progress"
        style={{
          position: "absolute", left: 0, right: 0, bottom: 0,
          height: 2, background: "rgba(255,255,255,0.05)",
          overflow: "hidden",
        }}>
        <div
          style={{
            width: `${pct}%`, height: "100%",
            background: status === "running"
              ? `linear-gradient(90deg, ${tone.bar}, ${tone.dot}, ${tone.bar})`
              : tone.bar,
            backgroundSize: "200px 100%",
            animation: status === "running"
              ? "barProgressShine 1.6s linear infinite" : "none",
            transition: "width 320ms ease-out",
          }} />
      </div>
    </div>
  );
}
