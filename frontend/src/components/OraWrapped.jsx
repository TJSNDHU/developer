/**
 * components/OraWrapped.jsx
 *
 * ORA Wrapped — monthly stats card. Shareable on X/LinkedIn.
 * Used inside Dashboard or Settings. Shows this month by default.
 */
import React, { useEffect, useState } from "react";
import { api } from "../lib/api";

export default function OraWrapped({ defaultPeriod = "this_month" }) {
  const [period, setPeriod]   = useState(defaultPeriod);
  const [data, setData]       = useState(null);
  const [loading, setLoading] = useState(true);
  const [copied, setCopied]   = useState(false);

  useEffect(() => {
    setLoading(true);
    api.get(`/wrapped/me?period=${period}`)
      .then((r) => setData(r.data))
      .catch(() => setData(null))
      .finally(() => setLoading(false));
  }, [period]);

  const stats = data?.stats;

  function copyShareText() {
    if (!data?.share_text) return;
    navigator.clipboard.writeText(data.share_text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  const tweetUrl = data?.share_text
    ? `https://twitter.com/intent/tweet?text=${encodeURIComponent(data.share_text)}`
    : "#";

  return (
    <div style={{
      background: "rgba(20,20,28,0.55)",
      backdropFilter: "blur(10px)",
      border: "1px solid rgba(255,255,255,0.07)",
      borderRadius: 12, padding: "20px 22px",
    }}>
      {/* Title row */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 16 }}>
        <div>
          <div style={{ fontSize: 13, fontWeight: 500, color: "var(--text)" }}>ORA Wrapped</div>
          <div style={{ fontSize: 11, color: "var(--text-dim)", marginTop: 2 }}>
            {stats?.period_label || "…"}
          </div>
        </div>
        <div style={{ display: "flex", gap: 4 }}>
          {["this_month", "last_month", "all"].map((p) => (
            <button
              key={p}
              onClick={() => setPeriod(p)}
              style={{
                fontSize: 10, padding: "3px 8px", borderRadius: 6, cursor: "pointer",
                background: period === p ? "rgba(127,119,221,0.25)" : "rgba(255,255,255,0.05)",
                border: `1px solid ${period === p ? "rgba(127,119,221,0.5)" : "rgba(255,255,255,0.1)"}`,
                color: period === p ? "#a59ff0" : "var(--text-dim)",
              }}
            >
              {p === "this_month" ? "This month" : p === "last_month" ? "Last month" : "All time"}
            </button>
          ))}
        </div>
      </div>

      {loading && (
        <div style={{ textAlign: "center", padding: "20px 0", color: "var(--text-dim)", fontSize: 13 }}>
          Loading…
        </div>
      )}

      {!loading && stats && (
        <>
          {/* Main stat grid */}
          <div style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(110px, 1fr))",
            gap: 10, marginBottom: 16,
          }}>
            <WrappedStat
              value={stats.tasks_shipped}
              label="tasks shipped"
              accent="#7F77DD"
              glow="rgba(127,119,221,0.15)"
            />
            <WrappedStat
              value={`~${stats.hours_saved}h`}
              label="time saved"
              accent="#1D9E75"
              glow="rgba(29,158,117,0.12)"
            />
            <WrappedStat
              value={stats.repos_touched}
              label="repos touched"
              accent="#EF9F27"
              glow="rgba(239,159,39,0.12)"
            />
            <WrappedStat
              value={stats.ship_streak_days}
              label="day streak"
              accent="#D4537E"
              glow="rgba(212,83,126,0.12)"
            />
          </div>

          {/* Secondary stats */}
          <div style={{
            display: "flex", gap: 16, flexWrap: "wrap",
            borderTop: "1px solid rgba(255,255,255,0.07)",
            paddingTop: 12, marginBottom: 16,
          }}>
            <SmallStat label="Maxx mode tasks" value={stats.maxx_tasks} />
            <SmallStat label="Claude corrections" value={stats.claude_corrections} />
            <SmallStat label="Top mode" value={stats.top_mode} />
          </div>

          {/* No data message */}
          {!data.has_data && (
            <div style={{
              textAlign: "center", padding: "8px 0 4px",
              fontSize: 12, color: "var(--text-dim)",
            }}>
              No ships yet this period. Ship your first task to fill this in.
            </div>
          )}

          {/* Share row */}
          {data.has_data && (
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              <a
                href={tweetUrl}
                target="_blank"
                rel="noopener noreferrer"
                style={{
                  fontSize: 11, padding: "6px 14px", borderRadius: 6,
                  background: "#000", color: "#fff", textDecoration: "none",
                }}
              >
                Post on X
              </a>
              <button
                onClick={copyShareText}
                style={{
                  fontSize: 11, padding: "6px 14px", borderRadius: 6, cursor: "pointer",
                  background: "rgba(255,255,255,0.07)",
                  border: "1px solid rgba(255,255,255,0.12)",
                  color: "var(--text-dim)",
                }}
              >
                {copied ? "Copied!" : "Copy text"}
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}

function WrappedStat({ value, label, accent, glow }) {
  return (
    <div style={{
      background: glow, borderRadius: 8,
      border: `1px solid ${accent}33`,
      padding: "10px 12px",
    }}>
      <div style={{ fontSize: 22, fontWeight: 500, color: accent, lineHeight: 1 }}>
        {value}
      </div>
      <div style={{ fontSize: 11, color: "var(--text-dim)", marginTop: 4 }}>{label}</div>
    </div>
  );
}

function SmallStat({ label, value }) {
  return (
    <div>
      <div style={{ fontSize: 15, fontWeight: 500, color: "var(--text)" }}>{value}</div>
      <div style={{ fontSize: 11, color: "var(--text-dim)" }}>{label}</div>
    </div>
  );
}
