/**
 * ShipPendingCard.jsx — Iter 212m-111 (Manual Ship gate)
 *
 * Renders when the LoopEngine pauses at PAUSED_FOR_USER/phase=ship
 * with `data.kind="awaiting_ship"`. The user MUST click the
 * "Ship to GitHub" button to push the commit — no auto-ship.
 *
 * Props:
 *   pending  — { owner, repo, branch, files, file_count, commit_message }
 *   busy     — bool; disables both buttons while the API call is in-flight
 *   onConfirm(approved: boolean) — callback for Ship / Cancel
 */
import React, { useState } from "react";
import { GitCommit, Upload, X, FileText, ChevronDown, ChevronRight } from "lucide-react";

export default function ShipPendingCard({ pending, busy, onConfirm }) {
  const [expanded, setExpanded] = useState(false);
  if (!pending) return null;
  const { owner, repo, branch, files = [], file_count, commit_message } = pending;
  const totalFiles = file_count || files.length;
  const showFiles = expanded ? files : files.slice(0, 3);

  return (
    <div
      data-testid="ship-pending-card"
      role="region"
      aria-label="Ready to ship — manual confirmation required"
      style={{
        margin: "10px 12px",
        padding: 16,
        background: "linear-gradient(135deg, rgba(255,102,8,0.10), rgba(255,166,0,0.05))",
        border: "1px solid rgba(255,102,8,0.45)",
        borderRadius: 12,
        display: "flex", flexDirection: "column", gap: 12,
        fontFamily: "'JetBrains Mono', monospace",
        boxShadow: "0 0 32px -12px rgba(255,102,8,0.55)",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <GitCommit size={16} color="#FF6608" />
        <div style={{ flex: 1, minWidth: 0 }}>
          <strong style={{ fontSize: 12.5, color: "#FF6608", letterSpacing: 0.4 }}>
            Ready to ship · manual confirmation required
          </strong>
          <div style={{ fontSize: 10.5, color: "var(--text-faint, #888)", marginTop: 3 }}>
            {totalFiles} file{totalFiles === 1 ? "" : "s"} →{" "}
            <code style={{ color: "#fff" }}>{owner}/{repo}@{branch}</code>
          </div>
        </div>
      </div>

      {commit_message && (
        <div
          data-testid="ship-pending-commit-msg"
          style={{
            padding: "8px 12px",
            background: "rgba(0,0,0,0.30)",
            border: "1px solid rgba(255,255,255,0.06)",
            borderRadius: 6,
            fontSize: 11, color: "var(--text, #e8ecf3)",
            lineHeight: 1.5,
            whiteSpace: "pre-wrap", wordBreak: "break-word",
          }}
        >
          <div style={{ fontSize: 9, color: "#FF6608", letterSpacing: 0.5,
                        textTransform: "uppercase", marginBottom: 4 }}>
            Commit message
          </div>
          {commit_message}
        </div>
      )}

      {files.length > 0 && (
        <div>
          <button
            type="button"
            data-testid="ship-pending-files-toggle"
            onClick={() => setExpanded((v) => !v)}
            style={{
              display: "inline-flex", alignItems: "center", gap: 4,
              background: "none", border: "none", padding: 0,
              color: "var(--text-faint, #888)", fontSize: 10.5,
              cursor: "pointer", fontFamily: "inherit",
            }}
          >
            {expanded ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
            Files ({totalFiles})
          </button>
          <div style={{ marginTop: 6, display: "flex", flexDirection: "column", gap: 3 }}>
            {showFiles.map((f) => (
              <div
                key={f}
                style={{
                  display: "flex", alignItems: "center", gap: 6,
                  fontSize: 10.5, color: "var(--text-dim, #ccc)",
                  paddingLeft: 14,
                }}
              >
                <FileText size={10} style={{ flexShrink: 0, color: "#FF6608" }} />
                <span style={{
                  overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                }}>{f}</span>
              </div>
            ))}
            {!expanded && files.length > 3 && (
              <div style={{ fontSize: 10, color: "var(--text-faint, #888)", paddingLeft: 14 }}>
                + {files.length - 3} more
              </div>
            )}
          </div>
        </div>
      )}

      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <button
          type="button"
          data-testid="ship-to-github-btn"
          disabled={busy}
          onClick={() => onConfirm?.(true)}
          style={{
            flex: 1, minWidth: 180,
            display: "inline-flex", alignItems: "center", justifyContent: "center", gap: 8,
            padding: "10px 18px",
            background: "linear-gradient(135deg, #FF6608, #ff8a3d)",
            color: "#0a0a0a",
            border: "1px solid transparent",
            borderRadius: 8,
            fontSize: 12.5, fontWeight: 800,
            letterSpacing: 0.3,
            cursor: busy ? "not-allowed" : "pointer",
            opacity: busy ? 0.55 : 1,
            boxShadow: "0 8px 24px -10px rgba(255,102,8,0.7)",
            fontFamily: "inherit",
          }}
        >
          <Upload size={14} />
          {busy ? "Shipping…" : "Ship to GitHub"}
        </button>
        <button
          type="button"
          data-testid="ship-cancel-btn"
          disabled={busy}
          onClick={() => onConfirm?.(false)}
          style={{
            display: "inline-flex", alignItems: "center", gap: 6,
            padding: "10px 18px",
            background: "transparent",
            color: "var(--text-faint, #aaa)",
            border: "1px solid rgba(255,255,255,0.12)",
            borderRadius: 8,
            fontSize: 12, fontWeight: 600,
            cursor: busy ? "not-allowed" : "pointer",
            opacity: busy ? 0.55 : 1,
            fontFamily: "inherit",
          }}
        >
          <X size={12} /> Cancel
        </button>
      </div>

      <div style={{ fontSize: 9.5, color: "var(--text-faint, #777)", lineHeight: 1.5 }}>
        Manual ship only — nothing is pushed to your repository until
        you click <strong>Ship to GitHub</strong>.
      </div>
    </div>
  );
}
