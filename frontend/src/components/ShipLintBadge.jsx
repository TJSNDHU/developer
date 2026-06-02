/**
 * components/ShipLintBadge.jsx — Iter 47
 *
 * Pre-flight lint indicator next to "Ship via CTO" button. Calls a
 * cheap backend endpoint that runs design_linter + Vanguard 007 scanner
 * on the handoff BRIEF text itself (not the generated code — that's
 * checked again server-side at commit time).
 *
 * Renders one of:
 *   - clean ✓        (green)
 *   - 2 warnings ⚠️  (amber)
 *   - blocked ⛔     (red)
 *
 * Falls back to nothing if the endpoint fails — never blocks the UI.
 */
import { useEffect, useState } from "react";
import { api } from "../lib/api";

export default function ShipLintBadge({ brief, testidSuffix }) {
  const [state, setState] = useState({ loading: true, result: null, error: null });

  useEffect(() => {
    let alive = true;
    if (!brief) {
      setState({ loading: false, result: null, error: null });
      return;
    }
    (async () => {
      try {
        const r = await api.post("/lint/preview", { brief });
        if (!alive) return;
        setState({ loading: false, result: r.data, error: null });
      } catch (e) {
        if (!alive) return;
        setState({ loading: false, result: null, error: e?.message || "lint failed" });
      }
    })();
    return () => { alive = false; };
  }, [brief]);

  if (state.loading || state.error || !state.result) return null;
  const r = state.result;
  let bg = "rgba(34, 197, 94, 0.15)";
  let fg = "#22c55e";
  let border = "rgba(34, 197, 94, 0.35)";
  let label = "clean ✓";
  let title = "Brief passes all linter rules.";

  if (r.blocked) {
    bg = "rgba(239, 68, 68, 0.15)";
    fg = "#ef4444";
    border = "rgba(239, 68, 68, 0.4)";
    label = "blocked ⛔";
    title = (r.block_reasons || []).slice(0, 3).join("\n") || "Linter blocked this brief.";
  } else if (r.warnings && r.warnings > 0) {
    bg = "rgba(245, 158, 11, 0.15)";
    fg = "#f59e0b";
    border = "rgba(245, 158, 11, 0.4)";
    label = `${r.warnings} warning${r.warnings === 1 ? "" : "s"} ⚠️`;
    title = (r.warning_list || []).slice(0, 3).join("\n") || "Non-blocking issues found.";
  }

  return (
    <span
      data-testid={`ship-lint-badge-${testidSuffix}`}
      title={title}
      style={{
        padding: "3px 8px",
        borderRadius: 4,
        fontSize: 10,
        fontWeight: 700,
        letterSpacing: "0.05em",
        fontFamily: "'JetBrains Mono', monospace",
        background: bg,
        color: fg,
        border: `1px solid ${border}`,
      }}
    >
      {label}
    </span>
  );
}
