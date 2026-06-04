/**
 * components/TaskManagementPanel.jsx
 *
 * Shows ORA's internal task checklist when working on multi-file tasks.
 * Parses checklist format from ORA messages:
 *   [ ] backend/auth.py — add rate limiting
 *   [/] backend/middleware.py — in progress
 *   [x] frontend/Login.jsx — done
 *
 * Wire into MessageBubble.jsx — render when message contains checklist lines.
 */
import React, { useEffect, useMemo, useState } from "react";
import { api } from "../lib/api";

const ITEM_RE = /^\s*\[([x/\s])\]\s+(.+)$/;

function parseChecklist(text) {
  if (!text) return [];
  const lines = text.split("\n");
  const items = [];
  for (const line of lines) {
    const m = line.match(ITEM_RE);
    if (m) {
      const state = m[1].trim();
      items.push({
        status: state === "x" ? "done"
               : state === "/" ? "active"
               : "pending",
        label: m[2].trim(),
      });
    }
  }
  return items;
}

export function hasChecklist(text) {
  if (!text) return false;
  return text.split("\n").some(l => ITEM_RE.test(l));
}

export default function TaskManagementPanel({ text, taskId }) {
  // If a taskId is provided we poll the DB-backed plan (source of truth
  // for multi-file tasks). Otherwise we fall back to parsing the
  // assistant message — same behaviour as before so single-file tasks
  // still render their inline checklist.
  const [dbPlan, setDbPlan] = useState(null);

  useEffect(() => {
    if (!taskId) return;
    let cancelled = false;
    let timer = null;
    const poll = async () => {
      try {
        const r = await api.get(`/cto/tasks/${taskId}`);
        const plan = r.data?.task?.task_plan || r.data?.task_plan;
        if (!cancelled && Array.isArray(plan) && plan.length) {
          setDbPlan(plan);
          // Stop polling once everything is done.
          if (plan.every((p) => p.status === "done")) return;
        }
      } catch { /* ignore — keep polling */ }
      if (!cancelled) timer = setTimeout(poll, 3000);
    };
    poll();
    return () => { cancelled = true; if (timer) clearTimeout(timer); };
  }, [taskId]);

  const items = useMemo(() => {
    if (dbPlan && dbPlan.length) {
      return dbPlan.map((p) => ({
        status: p.status === "done" ? "done"
              : p.status === "active" ? "active"
              : "pending",
        label: p.file || p.label || "",
      }));
    }
    return parseChecklist(text);
  }, [dbPlan, text]);

  if (!items.length) return null;

  const done    = items.filter(i => i.status === "done").length;
  const total   = items.length;
  const pct     = total > 0 ? Math.round((done / total) * 100) : 0;

  return (
    <div data-testid="task-management-panel" style={{
      marginTop: 10,
      background: "var(--panel-2, #161a25)",
      border: "1px solid var(--border, rgba(255,200,120,0.10))",
      borderRadius: 8,
      padding: "10px 14px",
      fontSize: 12,
    }}>
      {/* Header */}
      <div style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        marginBottom: 8,
      }}>
        <span style={{
          fontSize: 10,
          fontWeight: 600,
          color: "var(--text-faint, #6b6557)",
          textTransform: "uppercase",
          letterSpacing: ".07em",
          fontFamily: "var(--mono, monospace)",
        }}>
          Task plan
        </span>
        <span data-testid="task-management-panel-count" style={{
          fontSize: 10,
          color: "var(--text-dim, #a39d8a)",
          fontFamily: "var(--mono, monospace)",
        }}>
          {done}/{total}
        </span>
      </div>

      {/* Progress bar */}
      <div style={{
        height: 2,
        background: "var(--panel, #11141d)",
        borderRadius: 2,
        overflow: "hidden",
        marginBottom: 10,
      }}>
        <div style={{
          height: "100%",
          width: `${pct}%`,
          background: pct === 100
            ? "var(--ok, #6dd4a1)"
            : "var(--accent, #ff8a2a)",
          borderRadius: 2,
          transition: "width .4s ease",
        }} />
      </div>

      {/* Items */}
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        {items.map((item, i) => (
          <div key={i} style={{
            display: "flex",
            alignItems: "flex-start",
            gap: 8,
            padding: "3px 0",
          }}>
            {/* Status icon */}
            <span style={{
              fontSize: 11,
              fontFamily: "var(--mono, monospace)",
              flexShrink: 0,
              marginTop: 1,
              color: item.status === "done"   ? "var(--ok, #6dd4a1)"
                   : item.status === "active" ? "var(--accent, #ff8a2a)"
                   : "var(--text-faint, #6b6557)",
            }}>
              {item.status === "done"   ? "✓"
             : item.status === "active" ? "⟳"
             : "○"}
            </span>

            {/* Label */}
            <span style={{
              fontSize: 11,
              fontFamily: "var(--mono, monospace)",
              color: item.status === "done"
                ? "var(--text-faint, #6b6557)"
                : "var(--text-dim, #a39d8a)",
              textDecoration: item.status === "done" ? "line-through" : "none",
              lineHeight: 1.4,
            }}>
              {item.label}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
