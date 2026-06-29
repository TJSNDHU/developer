/**
 * pages/SystemStatsPage.jsx — Iter 212m-153
 *
 * Production observability dashboard for AUREM CTO.
 *
 * Consumes GET /api/aurem-dev/admin/system-stats which aggregates:
 *   • Parliament — multi-agent council runs, success rate,
 *     per-member wins, circuit breaker state, avg quality score
 *   • Intent Gateway — casual/query/agentic/clarify distribution,
 *     avg confidence, LLM fallback rate
 *   • Tool Router — keyword-group call distribution
 *   • Syntax Gate — block counts by language (populated by future iter)
 *   • Quality Monitor — avg score, low-score count, unacked drift
 *     alerts
 *
 * Refreshes every 60s.  Window selector: 1h / 24h / 7d / 30d.
 * Admin-only — uses /admin/* JWT path.
 */
import React, { useEffect, useState, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../lib/api";

const ACCENT = "#7dd3fc";   // sky-300 — calmer than Vanguard's orange
const ACCENT_DIM = "#7dd3fc55";
const POSITIVE = "#6dd4a1";
const WARN     = "#ffd166";
const BAD      = "#ff6b6b";

const WINDOW_OPTIONS = [
  { hours: 1,    label: "1h"  },
  { hours: 24,   label: "24h" },
  { hours: 168,  label: "7d"  },
  { hours: 720,  label: "30d" },
];

function fmt(n) {
  if (n === null || n === undefined) return "—";
  if (typeof n !== "number") return String(n);
  if (n >= 1000) return n.toLocaleString();
  if (Number.isInteger(n)) return String(n);
  return n.toFixed(2);
}

function pct(n) {
  if (n === null || n === undefined) return "—";
  return `${Number(n).toFixed(1)}%`;
}

export default function SystemStatsPage() {
  const nav = useNavigate();
  const [stats,        setStats]     = useState(null);
  const [windowHours,  setWindowHrs] = useState(24);
  const [busy,         setBusy]      = useState(false);
  const [err,          setErr]       = useState("");
  const [lastLoaded,   setLastLoaded] = useState(null);

  const load = useCallback(async () => {
    setBusy(true); setErr("");
    try {
      const r = await api.get(`/admin/system-stats?window_hours=${windowHours}`);
      setStats(r.data);
      setLastLoaded(new Date());
    } catch (e) {
      const status = e?.response?.status;
      setErr(e?.response?.data?.detail || e?.message || String(e));
      if (status === 401 || status === 403) {
        // Send admins back to login if they don't have the cookie.
        nav("/dashboard");
      }
    } finally {
      setBusy(false);
    }
  }, [windowHours, nav]);

  useEffect(() => { load(); }, [load]);

  // 60s auto-refresh while the tab is visible.
  useEffect(() => {
    const t = setInterval(() => {
      if (document.visibilityState === "visible") load();
    }, 60_000);
    return () => clearInterval(t);
  }, [load]);

  const parl    = stats?.parliament    || {};
  const intent  = stats?.intent_gateway || {};
  const tools   = stats?.tool_router    || {};
  const sgate   = stats?.syntax_gate    || {};
  const quality = stats?.quality        || {};

  const cbState     = parl.circuit_breaker_opens_24h ?? 0;
  const cbColor     = cbState > 0 ? BAD : POSITIVE;
  const successRate = parl.success_rate_pct ?? 0;
  const successColor = successRate >= 90 ? POSITIVE :
                       successRate >= 70 ? WARN : BAD;

  return (
    <div
      data-testid="system-stats-page"
      style={{
        minHeight: "100vh",
        padding: "32px 28px 80px",
        color: "var(--ink, #f3ecdc)",
        background: "var(--bg, #07080b)",
        fontFamily: "system-ui, -apple-system, sans-serif",
      }}
    >
      <header
        style={{
          maxWidth: 1240, margin: "0 auto 24px",
          display: "flex", alignItems: "center", justifyContent: "space-between",
        }}
      >
        <div>
          <div style={{ fontSize: 11, color: ACCENT, fontWeight: 700,
                        letterSpacing: "0.16em" }}>
            SYSTEM OBSERVABILITY
          </div>
          <h1 style={{ margin: "4px 0 0", fontSize: 26, fontWeight: 600,
                       letterSpacing: "-0.02em" }}>
            Parliament · Intent Gateway · Quality
          </h1>
          {lastLoaded && (
            <div style={{ marginTop: 6, fontSize: 11, color: "var(--text-faint, #888)" }}>
              Updated {lastLoaded.toLocaleTimeString()} · auto-refresh 60s
            </div>
          )}
        </div>

        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          {WINDOW_OPTIONS.map(opt => (
            <button
              key={opt.hours}
              data-testid={`system-stats-window-${opt.label}`}
              onClick={() => setWindowHrs(opt.hours)}
              style={{
                padding: "6px 12px", borderRadius: 8,
                fontSize: 12, fontWeight: 500,
                background: windowHours === opt.hours ? "rgba(125,211,252,0.16)" : "transparent",
                color:      windowHours === opt.hours ? ACCENT : "var(--text-faint, #888)",
                border: `1px solid ${windowHours === opt.hours ? ACCENT_DIM : "rgba(255,255,255,0.06)"}`,
                cursor: "pointer",
              }}
            >
              {opt.label}
            </button>
          ))}
          <button
            data-testid="system-stats-refresh"
            onClick={load}
            disabled={busy}
            style={{
              marginLeft: 8, padding: "6px 14px", borderRadius: 8,
              background: "rgba(125,211,252,0.10)",
              border: `1px solid ${ACCENT}55`,
              color: ACCENT, fontSize: 12, fontWeight: 600,
              cursor: busy ? "wait" : "pointer",
            }}
          >
            {busy ? "Loading…" : "Refresh"}
          </button>
        </div>
      </header>

      {err && (
        <div
          data-testid="system-stats-err"
          style={{
            maxWidth: 1240, margin: "0 auto 16px",
            color: BAD, fontSize: 13,
          }}
        >
          {err}
        </div>
      )}

      {/* Hero KPIs */}
      <div
        style={{
          maxWidth: 1240, margin: "0 auto 28px",
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
          gap: 16,
        }}
      >
        <Kpi
          testid="kpi-parliament-runs"
          label="Parliament runs"
          value={fmt(parl.total_runs ?? 0)}
          accent={ACCENT}
        />
        <Kpi
          testid="kpi-parliament-success"
          label="Council success rate"
          value={pct(successRate)}
          accent={successColor}
        />
        <Kpi
          testid="kpi-circuit-breaker"
          label="Circuit breaker opens · 24h"
          value={fmt(cbState)}
          accent={cbColor}
        />
        <Kpi
          testid="kpi-avg-score"
          label="Avg winner score"
          value={fmt(parl.avg_score ?? 0)}
          accent={WARN}
        />
        <Kpi
          testid="kpi-intent-confidence"
          label="Intent confidence"
          value={fmt(intent.avg_confidence ?? 0)}
          accent={ACCENT}
        />
        <Kpi
          testid="kpi-quality-score"
          label="Quality avg · 24h"
          value={fmt(quality.avg_score_24h ?? 0)}
          accent={quality.avg_score_24h >= 0.7 ? POSITIVE :
                  quality.avg_score_24h >= 0.45 ? WARN : BAD}
        />
        <Kpi
          testid="kpi-drift-alerts"
          label="Drift alerts unacked"
          value={fmt(quality.drift_alerts_unacked ?? 0)}
          accent={quality.drift_alerts_unacked > 0 ? BAD : POSITIVE}
        />
        <Kpi
          testid="kpi-manual-review"
          label="Manual review queue"
          value={fmt(parl.manual_review_queue_count ?? 0)}
          accent={parl.manual_review_queue_count > 0 ? WARN : POSITIVE}
        />
      </div>

      {/* Council A member win distribution */}
      <Section title="Council A — winner distribution">
        <BreakdownList
          testid="council-a-winners"
          rows={Object.entries(parl.council_A_win_by_member || {})
                       .map(([k, v]) => ({ k, v }))}
          getLabel={r => r.k}
          getCount={r => r.v}
        />
      </Section>

      {/* Two-column row */}
      <div
        style={{
          maxWidth: 1240, margin: "0 auto",
          display: "grid", gridTemplateColumns: "1fr 1fr",
          gap: 16,
        }}
      >
        <Section title="Intent Gateway — tier distribution">
          <BreakdownList
            testid="intent-tier-dist"
            rows={Object.entries(intent.tier_distribution || {})
                         .map(([k, v]) => ({ k, v }))}
            getLabel={r => r.k}
            getCount={r => r.v}
          />
          <div
            style={{
              marginTop: 12, paddingTop: 12,
              borderTop: "1px solid rgba(255,255,255,0.05)",
              display: "flex", justifyContent: "space-between",
              fontSize: 12, color: "var(--text-faint, #888)",
            }}
          >
            <span>LLM fallback rate</span>
            <span style={{ color: ACCENT, fontWeight: 600 }}>
              {pct(intent.llm_fallback_rate_pct ?? 0)}
            </span>
          </div>
        </Section>

        <Section title="Tool Router — calls by group">
          <BreakdownList
            testid="tool-router-groups"
            rows={Object.entries(tools.calls_by_group || {})
                         .map(([k, v]) => ({ k, v }))}
            getLabel={r => r.k}
            getCount={r => r.v}
          />
        </Section>
      </div>

      {/* Quality + Syntax gate row */}
      <div
        style={{
          maxWidth: 1240, margin: "0 auto",
          display: "grid", gridTemplateColumns: "1fr 1fr",
          gap: 16,
        }}
      >
        <Section title="Quality monitor">
          <KvRow k="Avg score (24h)" v={fmt(quality.avg_score_24h ?? 0)} />
          <KvRow k="Low-score count" v={fmt(quality.low_score_count ?? 0)}
                 accent={quality.low_score_count > 0 ? WARN : "var(--ink)"} />
          <KvRow k="Drift alerts unacked"
                 v={fmt(quality.drift_alerts_unacked ?? 0)}
                 accent={quality.drift_alerts_unacked > 0 ? BAD : POSITIVE} />
          {Array.isArray(quality.top_flags) && quality.top_flags.length > 0 && (
            <div style={{ marginTop: 10, paddingTop: 10,
                          borderTop: "1px solid rgba(255,255,255,0.05)" }}>
              <div style={{ fontSize: 10, color: "var(--text-faint, #888)",
                            textTransform: "uppercase", letterSpacing: "0.1em",
                            marginBottom: 6 }}>
                top flags
              </div>
              {quality.top_flags.map((f, i) => (
                <div key={i} style={{ fontSize: 12, color: BAD,
                                       padding: "2px 0" }}>· {f}</div>
              ))}
            </div>
          )}
        </Section>

        <Section title="Syntax gate (last 24h)">
          <KvRow k="Total checks"     v={fmt(sgate.total_checks ?? 0)} />
          <KvRow k="Blocked commits"  v={fmt(sgate.blocked_commits ?? 0)}
                 accent={sgate.blocked_commits > 0 ? WARN : "var(--ink)"} />
          <KvRow k="Block rate"       v={pct(sgate.block_rate_pct ?? 0)} />
          <div style={{ marginTop: 10, paddingTop: 10,
                        borderTop: "1px solid rgba(255,255,255,0.05)",
                        display: "flex", gap: 16, flexWrap: "wrap" }}>
            {Object.entries(sgate.by_language || {}).map(([lang, n]) => (
              <div key={lang} style={{ fontSize: 11,
                                        color: "var(--text-faint, #888)" }}>
                {lang} <span style={{ color: ACCENT, fontWeight: 600 }}>{fmt(n)}</span>
              </div>
            ))}
          </div>
        </Section>
      </div>

      <Section title="Raw payload">
        <pre
          data-testid="system-stats-raw"
          style={{
            fontSize: 11, lineHeight: 1.4,
            color: "var(--text-faint, #aaa)",
            background: "rgba(0,0,0,0.25)",
            padding: 12, borderRadius: 8,
            border: "1px solid rgba(255,255,255,0.04)",
            overflow: "auto", maxHeight: 280,
            fontFamily: "'JetBrains Mono', monospace",
          }}
        >
          {stats ? JSON.stringify(stats, null, 2) : "—"}
        </pre>
      </Section>
    </div>
  );
}


// ─── Reusable bits (mirroring AdminVanguard for consistency) ─────────

function Kpi({ testid, label, value, accent }) {
  return (
    <div
      data-testid={testid}
      style={{
        padding: 18, borderRadius: 14,
        background: "linear-gradient(180deg, #11141c, #0c0f15)",
        border: `1px solid ${accent}30`,
      }}
    >
      <div style={{ fontSize: 11, color: "var(--text-faint, #888)",
                    textTransform: "uppercase", letterSpacing: "0.1em" }}>
        {label}
      </div>
      <div style={{ marginTop: 6, fontSize: 28, fontWeight: 700,
                    color: accent, letterSpacing: "-0.02em",
                    wordBreak: "break-word" }}>
        {value}
      </div>
    </div>
  );
}

function Section({ title, children }) {
  return (
    <section
      style={{
        maxWidth: 1240, margin: "0 auto 18px",
        padding: 18, borderRadius: 14,
        background: "rgba(13,16,24,0.6)",
        border: "1px solid rgba(125,211,252,0.08)",
      }}
    >
      <div style={{ fontSize: 11, color: "var(--text-faint, #888)",
                    textTransform: "uppercase", letterSpacing: "0.12em",
                    marginBottom: 12 }}>
        {title}
      </div>
      {children}
    </section>
  );
}

function BreakdownList({ rows, getLabel, getCount, testid }) {
  if (!rows || rows.length === 0) {
    return <div style={{ color: "var(--text-faint, #666)", fontSize: 12 }}>—</div>;
  }
  const max = Math.max(1, ...rows.map(getCount));
  return (
    <div
      data-testid={testid}
      style={{ display: "flex", flexDirection: "column", gap: 6 }}
    >
      {rows.map((r, i) => (
        <div key={i}
             style={{ display: "flex", alignItems: "center", gap: 10, fontSize: 12 }}>
          <div style={{ flex: "0 0 50%", color: "var(--ink, #ccc)",
                        overflow: "hidden", textOverflow: "ellipsis",
                        whiteSpace: "nowrap" }}>
            {getLabel(r)}
          </div>
          <div style={{ flex: 1, height: 6, borderRadius: 3,
                        background: "rgba(255,255,255,0.04)", overflow: "hidden" }}>
            <div style={{
              width: `${(getCount(r) / max) * 100}%`, height: "100%",
              background: "linear-gradient(90deg, #7dd3fc, #38bdf8)",
            }} />
          </div>
          <div style={{ flex: "0 0 40px", textAlign: "right",
                        color: ACCENT, fontWeight: 600 }}>
            {getCount(r)}
          </div>
        </div>
      ))}
    </div>
  );
}

function KvRow({ k, v, accent }) {
  return (
    <div style={{
      display: "flex", justifyContent: "space-between",
      padding: "6px 0", fontSize: 12,
      borderBottom: "1px dashed rgba(255,255,255,0.04)",
    }}>
      <span style={{ color: "var(--text-faint, #888)" }}>{k}</span>
      <span style={{ color: accent || "var(--ink, #ddd)", fontWeight: 600 }}>{v}</span>
    </div>
  );
}
