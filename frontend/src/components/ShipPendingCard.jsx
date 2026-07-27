/**
 * ShipPendingCard.jsx — Iter 212m-111 (Manual Ship gate)
 * Iter 328 · Deploy 2 — enriched with per-file diff + integrity pill
 *
 * Renders when the LoopEngine pauses at PAUSED_FOR_USER/phase=ship
 * with `data.kind="awaiting_ship"`. The user MUST click the
 * "Ship to GitHub" button to push the commit — no auto-ship.
 *
 * Iter 328 additions (safety fix — pre-Loop-mode users had these
 * gates via ShipConfirmModal, which is now legacy):
 *   1. Per-file +additions / −deletions chips inline with each file
 *      path (data source: `pending.files_diff`, computed backend-side
 *      via services/loop_ship_diff.compute_files_diff).
 *   2. Integrity guard verdict pill above the file list (data source:
 *      `pending.integrity_verdict` — "clean" when Iter 318's pre-ship
 *      guard cleared Rules 1/2/3, "unknown" if the guard didn't run).
 *   No mocked data — if the backend omits these fields (e.g. an
 *   in-flight loop that paused BEFORE Iter 328 shipped), the pills
 *   simply don't render. Never fake a verdict.
 *
 * Props:
 *   pending  — {
 *     owner, repo, branch, files, file_count, commit_message,
 *     files_diff?:        [{ path, additions, deletions, is_new,
 *                            delta_bytes, diff_source }],
 *     integrity_verdict?: "clean" | "unknown",
 *   }
 *   busy     — bool; disables both buttons while the API call is in-flight
 *   onConfirm(approved: boolean) — callback for Ship / Cancel
 */
import React, { useState } from "react";
import {
  GitCommit, Upload, X, FileText, ChevronDown, ChevronRight,
  ShieldCheck, ShieldAlert,
} from "lucide-react";

// Build a fast lookup map: path -> diff row.
function _buildDiffMap(files_diff) {
  const m = new Map();
  if (Array.isArray(files_diff)) {
    for (const row of files_diff) {
      if (row && row.path) m.set(row.path, row);
    }
  }
  return m;
}

function IntegrityPill({ verdict }) {
  if (verdict !== "clean" && verdict !== "unknown") return null;
  const clean = verdict === "clean";
  const label = clean
    ? "Integrity guard: clean"
    : "Integrity guard: unknown";
  const Icon = clean ? ShieldCheck : ShieldAlert;
  return (
    <div
      data-testid="ship-integrity-pill"
      data-verdict={verdict}
      style={{
        display: "inline-flex", alignItems: "center", gap: 6,
        padding: "4px 10px",
        background: clean
          ? "rgba(52, 211, 153, 0.12)"
          : "rgba(250, 204, 21, 0.10)",
        border: `1px solid ${clean
          ? "rgba(52, 211, 153, 0.45)"
          : "rgba(250, 204, 21, 0.45)"}`,
        borderRadius: 999,
        fontSize: 10.5, fontWeight: 700, letterSpacing: 0.25,
        color: clean ? "#4ade80" : "#facc15",
        fontFamily: "inherit",
      }}
      title={clean
        ? "Pre-ship integrity guard (Iter 318) passed: no elision markers, no >70% size shrink, no byte-count violations."
        : "Pre-ship integrity guard verdict unknown for this ship (guard did not run or state was rehydrated). Review the diff before approving."}
    >
      <Icon size={12} />
      {label}
    </div>
  );
}

function FileDiffChip({ row }) {
  if (!row) return null;
  const { additions = 0, deletions = 0, is_new, delta_bytes, diff_source } = row;
  // If line-level counts are both zero AND we have no signal → don't
  // render (avoid a misleading "+0 −0" chip for cache-miss cases).
  if (additions === 0 && deletions === 0 && diff_source !== "line" && !is_new) {
    // Show byte-delta only if we have it.
    if (typeof delta_bytes === "number" && delta_bytes !== 0) {
      const sign = delta_bytes > 0 ? "+" : "";
      return (
        <span
          data-testid="ship-file-diff-chip"
          data-diff-source={diff_source || "unknown"}
          style={{
            fontSize: 9.5, fontWeight: 700, letterSpacing: 0.2,
            color: "var(--text-faint, #888)",
            fontFamily: "inherit",
            padding: "1px 5px",
            borderRadius: 4,
            background: "rgba(255,255,255,0.04)",
          }}
          title={`Byte delta (line diff unavailable): ${sign}${delta_bytes} bytes`}
        >
          {sign}{delta_bytes}B
        </span>
      );
    }
    return null;
  }
  return (
    <span
      data-testid="ship-file-diff-chip"
      data-diff-source={diff_source || "unknown"}
      style={{
        display: "inline-flex", alignItems: "center", gap: 4,
        fontSize: 9.5, fontWeight: 700, letterSpacing: 0.2,
        fontFamily: "inherit",
      }}
    >
      {is_new && (
        <span
          data-testid="ship-file-new-badge"
          style={{
            fontSize: 8.5, fontWeight: 800,
            padding: "1px 4px", borderRadius: 3,
            background: "rgba(96, 165, 250, 0.15)",
            border: "1px solid rgba(96, 165, 250, 0.4)",
            color: "#60a5fa",
          }}
        >NEW</span>
      )}
      {additions > 0 && (
        <span style={{ color: "#4ade80" }}>+{additions}</span>
      )}
      {deletions > 0 && (
        <span style={{ color: "#f87171" }}>−{deletions}</span>
      )}
    </span>
  );
}

export default function ShipPendingCard({ pending, busy, onConfirm }) {
  const [expanded, setExpanded] = useState(false);
  if (!pending) return null;
  const {
    owner, repo, branch, files = [], file_count, commit_message,
    files_diff, integrity_verdict,
  } = pending;
  const totalFiles = file_count || files.length;
  const showFiles = expanded ? files : files.slice(0, 3);
  const diffMap = _buildDiffMap(files_diff);

  // Aggregate +/- across all files for a headline chip.
  let totalAdd = 0, totalDel = 0;
  for (const row of diffMap.values()) {
    totalAdd += row.additions || 0;
    totalDel += row.deletions || 0;
  }
  const hasAnyDiff = diffMap.size > 0 && (totalAdd + totalDel) > 0;

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
            {hasAnyDiff && (
              <span
                data-testid="ship-total-diff-chip"
                style={{ marginLeft: 8, fontFamily: "inherit" }}
              >
                <span style={{ color: "#4ade80", fontWeight: 700 }}>
                  +{totalAdd}
                </span>{" "}
                <span style={{ color: "#f87171", fontWeight: 700 }}>
                  −{totalDel}
                </span>
              </span>
            )}
          </div>
        </div>
      </div>

      {/* Iter 328 · pre-approval safety pill. Only renders when the
          backend actually shipped a verdict — never invent one. */}
      {integrity_verdict && (
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          <IntegrityPill verdict={integrity_verdict} />
        </div>
      )}

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
                data-testid="ship-pending-file-row"
                data-file-path={f}
                style={{
                  display: "flex", alignItems: "center", gap: 6,
                  fontSize: 10.5, color: "var(--text-dim, #ccc)",
                  paddingLeft: 14,
                }}
              >
                <FileText size={10} style={{ flexShrink: 0, color: "#FF6608" }} />
                <span style={{
                  flex: 1, minWidth: 0,
                  overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                }}>{f}</span>
                <FileDiffChip row={diffMap.get(f)} />
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
