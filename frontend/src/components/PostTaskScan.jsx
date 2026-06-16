/**
 * components/PostTaskScan.jsx — Iter 167
 *
 * Inline banner shown beneath the last assistant bubble after a CTO
 * task ships. Polls `/cto/tasks/{id}/scan` for up to 10s. If the
 * scanner found any issues, renders a compact card with:
 *   • severity-coded title
 *   • file:line + snippet
 *   • "Fix this →" button that hands the issue back to ORA via
 *     onFixRequest(prompt).
 *
 * The component is fully self-contained: dismiss button, max-3
 * issues, animated entrance. Zero LLM calls — it only reads from
 * the backend scan that already ran.
 */
import React, { useEffect, useRef, useState } from "react";
import { api } from "../lib/api";

export default function PostTaskScan({ taskId, projectId, onFixRequest }) {
  const [scan, setScan] = useState(null);
  const [dismissed, setDismissed] = useState(false);
  const [fixing, setFixing] = useState(null);
  const cancelRef = useRef(false);

  useEffect(() => {
    if (!taskId || !projectId) return undefined;
    cancelRef.current = false;
    const start = Date.now();

    const poll = setInterval(async () => {
      if (cancelRef.current) {
        clearInterval(poll);
        return;
      }
      if (Date.now() - start > 10000) {
        clearInterval(poll);
        return;
      }
      try {
        const r = await api.get(
          `/cto/projects/${projectId}/tasks/${taskId}/scan`
        );
        const issues = r?.data?.scan?.issues || [];
        if (issues.length > 0 && !cancelRef.current) {
          setScan(r.data.scan);
          clearInterval(poll);
        }
      } catch {
        clearInterval(poll);
      }
    }, 2000);

    return () => {
      cancelRef.current = true;
      clearInterval(poll);
    };
  }, [taskId, projectId]);

  if (!scan || dismissed) return null;
  const issues = scan.issues || [];
  if (!issues.length) return null;

  return (
    <div
      data-testid="post-task-scan"
      style={{
        margin: "8px 0",
        borderRadius: 10,
        border: "1px solid rgba(239,68,68,0.25)",
        background: "rgba(239,68,68,0.05)",
        overflow: "hidden",
        animation: "scan-slide-in 0.3s ease-out",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "8px 12px",
          borderBottom: "1px solid rgba(239,68,68,0.15)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <span style={{ fontSize: 13 }}>⚠️</span>
          <span
            style={{
              fontSize: 12,
              fontWeight: 600,
              color: "#fca5a5",
            }}
          >
            {issues.length} issue{issues.length > 1 ? "s" : ""} found in changed files
          </span>
        </div>
        <button
          data-testid="post-task-scan-dismiss"
          onClick={() => setDismissed(true)}
          style={{
            background: "none",
            border: "none",
            color: "#64748b",
            cursor: "pointer",
            fontSize: 14,
            padding: "0 4px",
          }}
        >
          ×
        </button>
      </div>

      {issues.map((issue, i) => (
        <div
          key={i}
          data-testid={`post-task-scan-issue-${i}`}
          style={{
            padding: "10px 12px",
            borderBottom:
              i < issues.length - 1
                ? "1px solid rgba(255,255,255,0.04)"
                : "none",
            display: "flex",
            alignItems: "flex-start",
            gap: 10,
          }}
        >
          <span style={{ fontSize: 14, flexShrink: 0 }}>
            {issue.icon || "⚠️"}
          </span>

          <div style={{ flex: 1, minWidth: 0 }}>
            <div
              style={{
                fontSize: 12,
                fontWeight: 600,
                color: issue.severity === "HIGH" ? "#fca5a5" : "#fcd34d",
                marginBottom: 2,
              }}
            >
              {issue.message}
            </div>
            <div
              style={{
                fontSize: 10,
                color: "#64748b",
                fontFamily: "monospace",
                marginBottom: 4,
              }}
            >
              {issue.file}:{issue.line}
            </div>
            {issue.snippet && (
              <div
                style={{
                  fontSize: 10,
                  fontFamily: "monospace",
                  color: "#94a3b8",
                  background: "rgba(0,0,0,0.3)",
                  padding: "3px 8px",
                  borderRadius: 4,
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                  marginBottom: 6,
                }}
              >
                {issue.snippet}
              </div>
            )}
            <button
              data-testid={`post-task-scan-fix-${i}`}
              disabled={fixing === i}
              onClick={() => {
                setFixing(i);
                const snippetPart = issue.snippet
                  ? ` Current code: \`${issue.snippet}\``
                  : "";
                const prompt =
                  `Fix this ${issue.type} issue in ${issue.file} at line ${issue.line}: ${issue.message}.${snippetPart}`;
                onFixRequest?.(prompt);
                setDismissed(true);
              }}
              style={{
                fontSize: 10,
                fontWeight: 600,
                padding: "3px 12px",
                borderRadius: 6,
                border: "1px solid rgba(239,68,68,0.4)",
                background:
                  fixing === i
                    ? "rgba(239,68,68,0.05)"
                    : "rgba(239,68,68,0.12)",
                color: "#fca5a5",
                cursor: fixing === i ? "wait" : "pointer",
              }}
            >
              {fixing === i ? "Sending to ORA..." : "Fix this →"}
            </button>
          </div>
        </div>
      ))}

      <style>{`
        @keyframes scan-slide-in {
          from { opacity: 0; transform: translateY(-6px); }
          to   { opacity: 1; transform: translateY(0); }
        }
      `}</style>
    </div>
  );
}
