/**
 * Analytics.jsx — Real product analytics dashboard.
 *
 * Iter 139 — replaces the previous Mode-Council debate log view.
 * Shows DAU/WAU/MAU, mode distribution, task success rate,
 * token burn, and a 14-day DAU trend bar chart.
 *
 * Backend: GET /api/aurem-dev/admin/product-analytics?days=N
 * Auth:    admin only (token validated server-side via _require_admin)
 */
import React, { useEffect, useState, useCallback } from "react";
import {
  Users, Zap, CheckCircle, TrendingUp,
  Activity, RefreshCw, AlertCircle
} from "lucide-react";
import Shell, { PageHeader } from "../components/Shell";
import { api } from "../lib/api";

const MODE_COLORS = {
  C: "#6366f1", D: "#f59e0b", B: "#10b981",
  A: "#3b82f6", E: "#ef4444", F: "#8b5cf6",
};

export default function Analytics() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [days, setDays] = useState(30);

  const load = useCallback((d) => {
    setLoading(true);
    setError(null);
    api.get(`/admin/product-analytics?days=${d}`)
      .then((r) => { setData(r.data); setLoading(false); })
      .catch((e) => {
        const msg = e?.response?.data?.detail
          || e?.message
          || "Failed to load analytics";
        setError(msg);
        setLoading(false);
      });
  }, []);

  useEffect(() => { load(days); }, [days, load]);

  const trendRows = data?.trend?.dau_14d || [];
  let maxTrend = 1;
  for (const row of trendRows) {
    if (row.users > maxTrend) maxTrend = row.users;
  }

  return (
    <Shell requireAuth>
      <PageHeader
        eyebrow="product analytics"
        title="Analytics"
        sub="Real user behaviour, mode usage, and task performance."
      />

      {/* Period selector */}
      <div
        data-testid="analytics-period-selector"
        style={{ display: "flex", gap: 8, marginBottom: 24, alignItems: "center" }}
      >
        {[7, 30, 90].map((d) => (
          <button
            key={d}
            data-testid={`analytics-period-${d}d`}
            onClick={() => setDays(d)}
            style={{
              padding: "4px 14px",
              borderRadius: 6,
              border: "1px solid var(--border)",
              background: days === d ? "var(--accent-2)" : "transparent",
              color: days === d ? "#fff" : "var(--text-faint)",
              cursor: "pointer", fontSize: 12,
              fontFamily: "'JetBrains Mono', monospace",
            }}
          >
            {d}d
          </button>
        ))}
        <button
          data-testid="analytics-refresh"
          onClick={() => load(days)}
          style={{
            marginLeft: "auto", display: "flex", alignItems: "center",
            gap: 4, padding: "4px 10px", borderRadius: 6,
            border: "1px solid var(--border)", background: "transparent",
            color: "var(--text-faint)", cursor: "pointer", fontSize: 11,
          }}
        >
          <RefreshCw size={11} /> refresh
        </button>
      </div>

      {error && (
        <div
          data-testid="analytics-error"
          style={{
            display: "flex", gap: 8, alignItems: "center",
            color: "var(--danger)", marginBottom: 16, fontSize: 13,
          }}
        >
          <AlertCircle size={14} /> {error}
        </div>
      )}

      {loading && !data && (
        <div
          data-testid="analytics-loading"
          style={{ color: "var(--text-faint)", fontSize: 13 }}
        >
          Loading…
        </div>
      )}

      {data && (
        <>
          {/* Top KPIs */}
          <div
            data-testid="analytics-kpi-grid"
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))",
              gap: 12, marginBottom: 24,
            }}
          >
            <KPI
              icon={Users} label="Total Users"
              value={(data.users.total || 0).toLocaleString()}
              testid="kpi-total-users"
            />
            <KPI
              icon={Activity} label="DAU"
              value={data.users.dau} sub="active today"
              testid="kpi-dau"
            />
            <KPI
              icon={TrendingUp} label="WAU"
              value={data.users.wau} sub="active this week"
              testid="kpi-wau"
            />
            <KPI
              icon={Users} label="MAU"
              value={data.users.mau} sub={`last ${days}d`}
              testid="kpi-mau"
            />
            <KPI
              icon={CheckCircle} label="Success Rate"
              value={`${data.tasks.success_rate_pct}%`}
              sub={`${data.tasks.done}/${data.tasks.total} tasks`}
              color={data.tasks.success_rate_pct >= 80 ? "var(--ok)" : "var(--warning)"}
              testid="kpi-success-rate"
            />
            <KPI
              icon={Zap} label="Tokens Burned"
              value={(data.tasks.tokens_burned || 0).toLocaleString()}
              sub={`last ${days}d`}
              testid="kpi-tokens-burned"
            />
          </div>

          {/* DAU 14-day trend */}
          <div
            data-testid="analytics-dau-trend"
            className="card"
            style={{ marginBottom: 20, padding: 20 }}
          >
            <div
              style={{
                fontSize: 11, fontFamily: "'JetBrains Mono', monospace",
                color: "var(--text-faint)", textTransform: "uppercase",
                letterSpacing: "0.15em", marginBottom: 16,
              }}
            >
              DAU — 14-day trend
            </div>
            <div style={{ display: "flex", gap: 4, alignItems: "flex-end", height: 80 }}>
              {trendRows.map((d) => (
                <div
                  key={d.date}
                  style={{
                    flex: 1, display: "flex",
                    flexDirection: "column", alignItems: "center", gap: 4,
                  }}
                >
                  <div
                    style={{
                      width: "100%",
                      height: `${Math.max(4, (d.users / maxTrend) * 72)}px`,
                      background: "var(--accent-2)", borderRadius: 3,
                      opacity: 0.8, transition: "height 0.3s",
                    }}
                    title={`${d.date}: ${d.users} users`}
                  />
                  <span
                    style={{
                      fontSize: 8, color: "var(--text-faint)",
                      transform: "rotate(-45deg)", whiteSpace: "nowrap",
                    }}
                  >
                    {d.date.split(" ")[1]}
                  </span>
                </div>
              ))}
            </div>
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, marginBottom: 20 }}>
            {/* Mode distribution */}
            <div
              data-testid="analytics-mode-distribution"
              className="card"
              style={{ padding: 20 }}
            >
              <div
                style={{
                  fontSize: 11, fontFamily: "'JetBrains Mono', monospace",
                  color: "var(--text-faint)", textTransform: "uppercase",
                  letterSpacing: "0.15em", marginBottom: 16,
                }}
              >
                Mode distribution
              </div>
              {(data.modes?.top_features || []).length === 0 && (
                <div style={{ color: "var(--text-faint)", fontSize: 12 }}>
                  No mode usage in this window.
                </div>
              )}
              {(data.modes?.top_features || []).map((f) => {
                const total = (data.modes?.top_features || [])
                  .reduce((s, x) => s + x.count, 0) || 1;
                const pct = Math.round(f.count / total * 100);
                return (
                  <div key={f.mode} style={{ marginBottom: 10 }}>
                    <div
                      style={{
                        display: "flex", justifyContent: "space-between",
                        marginBottom: 4, fontSize: 12,
                      }}
                    >
                      <span style={{ color: "var(--text-main)" }}>
                        <span
                          style={{
                            color: MODE_COLORS[f.mode], fontWeight: 600,
                            fontFamily: "'JetBrains Mono', monospace",
                          }}
                        >
                          {f.mode}
                        </span> {f.label}
                      </span>
                      <span style={{ color: "var(--text-faint)", fontSize: 11 }}>
                        {f.count} ({pct}%)
                      </span>
                    </div>
                    <div style={{ height: 4, background: "var(--border)", borderRadius: 2 }}>
                      <div
                        style={{
                          width: `${pct}%`, height: "100%",
                          background: MODE_COLORS[f.mode] || "var(--accent-2)",
                          borderRadius: 2, transition: "width 0.4s",
                        }}
                      />
                    </div>
                  </div>
                );
              })}
            </div>

            {/* Tier breakdown + task stats */}
            <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              <div
                data-testid="analytics-tier-breakdown"
                className="card"
                style={{ padding: 20 }}
              >
                <div
                  style={{
                    fontSize: 11, fontFamily: "'JetBrains Mono', monospace",
                    color: "var(--text-faint)", textTransform: "uppercase",
                    letterSpacing: "0.15em", marginBottom: 12,
                  }}
                >
                  Users by tier
                </div>
                {Object.keys(data.users?.by_tier || {}).length === 0 && (
                  <div style={{ color: "var(--text-faint)", fontSize: 12 }}>
                    No tier data yet.
                  </div>
                )}
                {Object.entries(data.users?.by_tier || {}).map(([tier, count]) => (
                  <div
                    key={tier}
                    style={{
                      display: "flex", justifyContent: "space-between",
                      padding: "4px 0", fontSize: 12,
                      borderBottom: "1px solid var(--border-faint)",
                    }}
                  >
                    <span style={{ textTransform: "capitalize", color: "var(--text-main)" }}>
                      {tier}
                    </span>
                    <span
                      style={{
                        fontFamily: "'JetBrains Mono', monospace",
                        color: "var(--accent-2)",
                      }}
                    >
                      {count}
                    </span>
                  </div>
                ))}
              </div>
              <div
                data-testid="analytics-task-health"
                className="card"
                style={{ padding: 20 }}
              >
                <div
                  style={{
                    fontSize: 11, fontFamily: "'JetBrains Mono', monospace",
                    color: "var(--text-faint)", textTransform: "uppercase",
                    letterSpacing: "0.15em", marginBottom: 12,
                  }}
                >
                  Task health
                </div>
                {[
                  ["Completed", data.tasks.done, "var(--ok)"],
                  ["Failed", data.tasks.failed, "var(--danger)"],
                  ["Maxx mode", data.tasks.maxx_mode, "var(--accent-2)"],
                  ["New users / wk", data.users.new_this_week, "var(--text-main)"],
                ].map(([label, val, color]) => (
                  <div
                    key={label}
                    style={{
                      display: "flex", justifyContent: "space-between",
                      padding: "4px 0", fontSize: 12,
                      borderBottom: "1px solid var(--border-faint)",
                    }}
                  >
                    <span style={{ color: "var(--text-main)" }}>{label}</span>
                    <span
                      style={{ fontFamily: "'JetBrains Mono', monospace", color }}
                    >
                      {val}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </>
      )}
    </Shell>
  );
}

function KPI({ icon: Icon, label, value, sub, color, testid }) {
  return (
    <div data-testid={testid} className="card" style={{ padding: 16 }}>
      <div
        style={{
          display: "flex", alignItems: "center", gap: 6,
          fontFamily: "'JetBrains Mono', monospace", fontSize: 9,
          color: "var(--text-faint)", textTransform: "uppercase",
          letterSpacing: "0.18em", marginBottom: 10,
        }}
      >
        <Icon size={10} /> {label}
      </div>
      <div
        className="serif"
        style={{ fontSize: 26, color: color || "var(--accent-2)", lineHeight: 1 }}
      >
        {value}
      </div>
      {sub && (
        <div style={{ fontSize: 10, color: "var(--text-faint)", marginTop: 4 }}>
          {sub}
        </div>
      )}
    </div>
  );
}
