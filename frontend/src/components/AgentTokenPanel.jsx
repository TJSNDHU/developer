/**
 * AgentTokenPanel.jsx — Iter 65 — Per-agent token consumption widget
 * shown above the Users list in the admin panel.
 *
 * Renders for each agent (DeepSeek, Maxx/Claude review, Groq):
 *   • Total tokens in the selected window
 *   • Total USD cost
 *   • Avg cost per finished task
 *   • A "Claude vs DeepSeek delta" callout — answers Teji's question
 *     "kya Claude ka extra cost worth hai ya nahi?"
 *
 * Range selector: 24h | 7d | 30d | 90d | 365d.
 * Lightweight inline bar chart (no charting lib) for the time series.
 */
import React, { useEffect, useState, useMemo } from "react";
import { api, getToken } from "../lib/api";

const RANGES = [
  { id: "24h",  label: "24h",  desc: "hourly" },
  { id: "7d",   label: "7d",   desc: "daily" },
  { id: "30d",  label: "30d",  desc: "daily" },
  { id: "90d",  label: "90d",  desc: "weekly" },
  { id: "365d", label: "1y",   desc: "monthly" },
];

// Color per agent — orange for the primary (DeepSeek), amber for the
// expensive reviewer (Claude/Maxx), green for cheap (Groq).
const AGENT_META = {
  deepseek: { color: "#ff8a2a", label: "DeepSeek" },
  maxx:     { color: "#ffc560", label: "Claude (Maxx)" },
  claude:   { color: "#ffc560", label: "Claude" },
  groq:     { color: "#6dd4a1", label: "Groq" },
};

export default function AgentTokenPanel() {
  const [range, setRange] = useState("7d");
  const [data,  setData]  = useState(null);
  const [error, setError] = useState(null);
  const [busy,  setBusy]  = useState(false);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setBusy(true);
      setError(null);
      try {
        const r = await api.get(`/admin/agent-tokens?range=${range}`, {
          headers: { Authorization: `Bearer ${getToken()}` },
        });
        if (!cancelled) setData(r.data);
      } catch (e) {
        if (!cancelled) setError(e?.response?.data?.detail || e.message);
      } finally {
        if (!cancelled) setBusy(false);
      }
    }
    load();
    return () => { cancelled = true; };
  }, [range]);

  // Pre-compute chart bar max so heights normalize
  const maxBucketTotal = useMemo(() => {
    if (!data?.series) return 0;
    return data.series.reduce((m, b) =>
      Math.max(m,
        (b.deepseek || 0) + (b.maxx || 0) + (b.claude || 0) + (b.groq || 0)),
      0);
  }, [data]);

  return (
    <div data-testid="agent-token-panel" style={{
      background: "var(--panel)",
      border: "1px solid var(--border)",
      borderRadius: 8,
      padding: "14px 16px",
      marginBottom: 18,
    }}>
      {/* Header + range selector */}
      <div style={{
        display: "flex", alignItems: "center", gap: 12,
        flexWrap: "wrap", marginBottom: 14,
      }}>
        <div>
          <div style={{ fontSize: 13, fontWeight: 600, color: "var(--text)" }}>
            Agent token P&amp;L
          </div>
          <div style={{ fontSize: 10, color: "var(--text-faint)",
                         letterSpacing: "0.05em", marginTop: 2 }}>
            Compare DeepSeek vs Claude (Maxx) — is the extra cost worth it?
          </div>
        </div>
        <div style={{ marginLeft: "auto", display: "flex", gap: 4,
                       flexWrap: "wrap" }}>
          {RANGES.map((r) => (
            <button
              key={r.id}
              data-testid={`agent-tokens-range-${r.id}`}
              onClick={() => setRange(r.id)}
              style={{
                padding: "5px 11px", fontSize: 11,
                fontFamily: "'JetBrains Mono', monospace",
                border: `1px solid ${range === r.id
                  ? "var(--accent-2)" : "var(--border)"}`,
                background: range === r.id
                  ? "var(--accent-soft)" : "transparent",
                color: range === r.id
                  ? "var(--accent-2)" : "var(--text-dim)",
                borderRadius: 4, cursor: "pointer",
              }}
            >
              {r.label}
            </button>
          ))}
        </div>
      </div>

      {error && (
        <div data-testid="agent-tokens-error" style={{
          padding: "8px 10px",
          background: "rgba(226,75,74,0.1)",
          border: "1px solid rgba(226,75,74,0.3)",
          borderRadius: 6, fontSize: 11, color: "var(--danger)",
        }}>
          {error}
        </div>
      )}

      {busy && !data && (
        <div style={{ fontSize: 11, color: "var(--text-faint)",
                       fontStyle: "italic" }}>Loading…</div>
      )}

      {data && (
        <>
          {/* Per-agent summary cards */}
          <div style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))",
            gap: 10, marginBottom: 14,
          }}>
            {Object.entries(AGENT_META).map(([agent, meta]) => {
              const tokens = data.totals_tokens?.[agent] || 0;
              const cost = data.costs_usd?.[agent] || 0;
              const tasks = data.task_counts?.[agent] || 0;
              const avg = data.avg_per_task?.[agent] || {};
              return (
                <div key={agent} data-testid={`agent-card-${agent}`} style={{
                  padding: "10px 12px",
                  background: "var(--panel-2)",
                  border: "1px solid var(--border)",
                  borderLeft: `3px solid ${meta.color}`,
                  borderRadius: 4,
                }}>
                  <div style={{ fontSize: 11, color: "var(--text-dim)",
                                 letterSpacing: "0.05em" }}>
                    {meta.label}
                  </div>
                  <div style={{ fontSize: 16, fontWeight: 600,
                                 color: "var(--text)", marginTop: 4,
                                 fontFamily: "'JetBrains Mono', monospace" }}>
                    ${cost.toFixed(2)}
                  </div>
                  <div style={{ fontSize: 10, color: "var(--text-faint)",
                                 fontFamily: "'JetBrains Mono', monospace" }}>
                    {tokens.toLocaleString()} tok · {tasks} task{tasks === 1 ? "" : "s"}
                  </div>
                  {tasks > 0 && (
                    <div style={{ fontSize: 9, color: "var(--text-faint)",
                                   marginTop: 4 }}>
                      avg ${avg.cost_avg_usd?.toFixed(4) || "0.0000"}/task
                    </div>
                  )}
                </div>
              );
            })}
          </div>

          {/* Claude-vs-DeepSeek delta — the headline answer */}
          {data.claude_vs_deepseek && (
            <div data-testid="claude-vs-deepseek" style={{
              padding: "10px 12px", marginBottom: 14,
              background: "rgba(255,138,42,0.05)",
              border: "1px solid rgba(255,138,42,0.25)",
              borderRadius: 4,
              fontSize: 11, color: "var(--text-dim)",
              fontFamily: "'JetBrains Mono', monospace",
              overflowWrap: "anywhere",
            }}>
              <span style={{ color: "var(--accent-2)", fontWeight: 600 }}>
                Claude/Maxx vs DeepSeek delta:{" "}
              </span>
              <span style={{ color: "var(--text)" }}>
                ${data.claude_vs_deepseek.delta_usd_per_task.toFixed(4)} extra per task
              </span>
              {data.claude_vs_deepseek.delta_multiplier && (
                <> · <span style={{ color: "var(--accent-2)" }}>
                  {data.claude_vs_deepseek.delta_multiplier}×
                </span> the DeepSeek cost</>
              )}
              {data.claude_corrections > 0 && (
                <span style={{ color: "var(--ok)" }}>
                  {" "}· Claude corrected DeepSeek in {data.claude_corrections} task(s)
                </span>
              )}
            </div>
          )}

          {/* Stacked-bar series — no chart lib, pure CSS */}
          {data.series && data.series.length > 0 && (
            <div data-testid="agent-tokens-chart" style={{
              display: "flex", alignItems: "flex-end", gap: 6,
              height: 90, padding: "0 4px",
              borderBottom: "1px solid var(--border)",
              overflowX: "auto", overflowY: "hidden",
            }}>
              {data.series.map((bucket, i) => {
                const total = (bucket.deepseek || 0) +
                              (bucket.maxx || 0) +
                              (bucket.claude || 0) +
                              (bucket.groq || 0);
                const heightPct = maxBucketTotal
                  ? (total / maxBucketTotal) * 100 : 0;
                return (
                  <div key={i} style={{
                    display: "flex", flexDirection: "column",
                    alignItems: "center", gap: 3, minWidth: 18, flex: 1,
                  }}>
                    <div style={{
                      width: "100%", height: `${heightPct}%`,
                      minHeight: total > 0 ? 2 : 0,
                      display: "flex", flexDirection: "column-reverse",
                      borderRadius: "2px 2px 0 0", overflow: "hidden",
                    }}
                    title={`${bucket.label}: $${(
                      (bucket.deepseek / 1000) * 0.30 +
                      ((bucket.maxx + bucket.claude) / 1000) * 0.65 +
                      (bucket.groq / 1000) * 0.03
                    ).toFixed(3)}`}>
                      {["deepseek", "maxx", "claude", "groq"].map((a) => {
                        const v = bucket[a] || 0;
                        if (!v) return null;
                        return (
                          <div key={a} style={{
                            background: AGENT_META[a].color,
                            height: `${(v / total) * 100}%`,
                            opacity: 0.9,
                          }} />
                        );
                      })}
                    </div>
                    <div style={{ fontSize: 8, color: "var(--text-faint)",
                                   whiteSpace: "nowrap" }}>
                      {bucket.label}
                    </div>
                  </div>
                );
              })}
            </div>
          )}

          <div style={{ marginTop: 10, fontSize: 10, color: "var(--text-faint)",
                         display: "flex", gap: 14, flexWrap: "wrap" }}>
            <span><b style={{ color: "var(--text)" }}>
              Total: ${data.total_cost_usd?.toFixed(4)}
            </b></span>
            <span>Range: {data.range} · {data.bucket} buckets · {data.buckets_count} pts</span>
            <span>Rates: DS $0.30 · Maxx/Claude $0.65 · Groq $0.03 per 1k tok</span>
          </div>
        </>
      )}
    </div>
  );
}
