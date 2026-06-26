/**
 * pages/AdminVanguard.jsx — Vanguard Audit Dashboard (iter 112).
 *
 * Shows every commit blocked by the Vanguard verify pipeline:
 *   - "X commits blocked this week"
 *   - Top rule (most-frequent vulnerability slug)
 *   - Per-rule, per-project, per-severity breakdowns
 *   - Day-bucketed sparkline
 *   - Recent-blocks table (last 25 by default)
 *
 * Built on /api/aurem-dev/admin/vanguard/{stats,recent}.
 */
import React, { useEffect, useState, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../lib/api";
import VanguardConfigPanel from "../components/VanguardConfigPanel";

const SEV_COLOR = {
  CRITICAL: "#ff6b6b",
  HIGH:     "#ffa55b",
  MEDIUM:   "#ffd166",
  LOW:      "#9aa3b2",
};

function timeSince(iso) {
  if (!iso) return "—";
  const s = Math.max(1, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  if (s < 60)    return `${s}s ago`;
  if (s < 3600)  return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}

export default function AdminVanguard() {
  const nav = useNavigate();
  const [stats,  setStats]  = useState(null);
  const [recent, setRecent] = useState([]);
  const [days,   setDays]   = useState(7);
  const [busy,   setBusy]   = useState(false);
  const [err,    setErr]    = useState("");

  const load = useCallback(async () => {
    setBusy(true); setErr("");
    try {
      const [s, r] = await Promise.all([
        api.get(`/admin/vanguard/stats?days=${days}`),
        api.get(`/admin/vanguard/recent?limit=25`),
      ]);
      setStats(s.data);
      setRecent(r.data?.rows || []);
    } catch (e) {
      const status = e?.response?.status;
      setErr(e?.response?.data?.detail || e?.message || String(e));
      if (status === 403) nav("/dashboard");
    } finally {
      setBusy(false);
    }
  }, [days, nav]);

  useEffect(() => { load(); }, [load]);

  const total       = stats?.total_blocked || 0;
  const topRule     = stats?.top_rule || null;
  const byRule      = stats?.by_rule || [];
  const byProject   = stats?.by_project || [];
  const bySeverity  = stats?.by_severity || [];
  const byDay       = stats?.by_day || [];
  const maxDay      = Math.max(1, ...byDay.map(d => d.count));

  return (
    <div data-testid="admin-vanguard-page" style={{
      minHeight: "100vh", padding: "32px 28px 80px",
      color: "var(--ink, #f3ecdc)",
      background: "var(--bg, #07080b)",
      fontFamily: "system-ui, -apple-system, sans-serif",
    }}>
      <header style={{ maxWidth: 1240, margin: "0 auto 24px",
                       display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div>
          <div style={{ fontSize: 11, color: "#ff8a2a", fontWeight: 700,
                        letterSpacing: "0.16em" }}>🛡️ VANGUARD AUDIT</div>
          <h1 style={{ margin: "4px 0 0", fontSize: 26, fontWeight: 600,
                       letterSpacing: "-0.02em" }}>Blocked Commits</h1>
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          {[1, 7, 30, 90].map(d => (
            <button key={d}
              data-testid={`vanguard-window-${d}d`}
              onClick={() => setDays(d)}
              style={{
                padding: "6px 12px", borderRadius: 8,
                fontSize: 12, fontWeight: 500,
                background: days === d ? "rgba(255,138,42,0.18)" : "transparent",
                color:      days === d ? "#ff8a2a" : "var(--text-faint, #888)",
                border: `1px solid ${days === d ? "#ff8a2a55" : "rgba(255,255,255,0.06)"}`,
                cursor: "pointer",
              }}>{d}d</button>
          ))}
          <button data-testid="vanguard-refresh" onClick={load} disabled={busy}
            style={{ marginLeft: 8, padding: "6px 14px", borderRadius: 8,
                     background: "rgba(255,138,42,0.10)",
                     border: "1px solid rgba(255,138,42,0.35)",
                     color: "#ff8a2a", fontSize: 12, fontWeight: 600,
                     cursor: busy ? "wait" : "pointer" }}>
            {busy ? "Loading…" : "Refresh"}
          </button>
        </div>
      </header>

      {/* Iter 212m-42 — admin selector for per-mode Vanguard config.
          Mounted directly under the header so the operator hits the
          control they need first, with the audit dashboard below. */}
      <VanguardConfigPanel />

      {err && (
        <div data-testid="vanguard-err" style={{ maxWidth: 1240, margin: "0 auto 16px",
                       color: "#ff6b6b", fontSize: 13 }}>{err}</div>
      )}

      {/* Hero KPIs */}
      <div style={{ maxWidth: 1240, margin: "0 auto 28px",
                    display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
                    gap: 16 }}>
        <Kpi
          testid="kpi-total"
          label={`Commits blocked · last ${days}d`}
          value={total}
          accent="#ff8a2a"
        />
        <Kpi
          testid="kpi-top-rule"
          label="Top rule triggered"
          value={topRule || "—"}
          accent="#ffb347"
          isText
        />
        <Kpi
          testid="kpi-projects"
          label="Projects affected"
          value={byProject.length}
          accent="#6dd4a1"
        />
        <Kpi
          testid="kpi-critical"
          label="Critical findings"
          value={(bySeverity.find(s => s.severity === "CRITICAL") || {}).count || 0}
          accent="#ff6b6b"
        />
      </div>

      {/* Sparkline */}
      <Section title="Daily volume">
        <div data-testid="vanguard-sparkline"
             style={{ display: "flex", alignItems: "flex-end", gap: 4, height: 80,
                      padding: "0 4px" }}>
          {byDay.length === 0 ? (
            <div style={{ color: "var(--text-faint, #666)", fontSize: 12, padding: 20 }}>
              No blocks in this window — your pipeline is clean. 🎉
            </div>
          ) : byDay.map((d, i) => (
            <div key={i} title={`${d.day}: ${d.count}`}
                 style={{
                   flex: 1, minWidth: 8,
                   height: `${(d.count / maxDay) * 100}%`,
                   background: "linear-gradient(180deg, #ff8a2a, #ff5e1a)",
                   borderRadius: "3px 3px 0 0",
                   minHeight: 4,
                 }} />
          ))}
        </div>
      </Section>

      <div style={{ maxWidth: 1240, margin: "0 auto",
                    display: "grid", gridTemplateColumns: "1fr 1fr",
                    gap: 16 }}>
        <Section title="By rule">
          <BreakdownList rows={byRule}
            getLabel={r => r.rule}
            getCount={r => r.count}
            testid="vanguard-by-rule"/>
        </Section>
        <Section title="By project">
          <BreakdownList rows={byProject}
            getLabel={r => r.project}
            getCount={r => r.count}
            testid="vanguard-by-project"/>
        </Section>
      </div>

      <Section title="By severity">
        <div data-testid="vanguard-severity-pills"
             style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
          {bySeverity.length === 0 ? (
            <span style={{ color: "var(--text-faint, #666)", fontSize: 12 }}>No findings yet.</span>
          ) : bySeverity.map((s, i) => (
            <span key={i}
              style={{
                padding: "5px 12px", borderRadius: 999, fontSize: 11,
                fontWeight: 700, letterSpacing: "0.06em",
                color: SEV_COLOR[s.severity] || "#9aa3b2",
                background: (SEV_COLOR[s.severity] || "#9aa3b2") + "20",
                border: `1px solid ${(SEV_COLOR[s.severity] || "#9aa3b2")}55`,
              }}>
              {s.severity} · {s.count}
            </span>
          ))}
        </div>
      </Section>

      <Section title="Recent blocks">
        {recent.length === 0 ? (
          <div style={{ color: "var(--text-faint, #666)", fontSize: 13, padding: 12 }}>
            No commits have been blocked yet — Vanguard is watching but quiet.
          </div>
        ) : (
          <table data-testid="vanguard-recent-table"
                 style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
            <thead>
              <tr style={{ color: "var(--text-faint, #888)",
                           textTransform: "uppercase", fontSize: 10, letterSpacing: "0.1em" }}>
                <th style={th}>When</th>
                <th style={th}>User</th>
                <th style={th}>Project</th>
                <th style={th}>Rule</th>
                <th style={th}>Layers</th>
                <th style={{ ...th, textAlign: "right" }}>#</th>
              </tr>
            </thead>
            <tbody>
              {recent.map((r, i) => (
                <tr key={i} data-testid={`vanguard-row-${i}`}
                    style={{ borderTop: "1px solid rgba(255,255,255,0.04)" }}>
                  <td style={td}>{timeSince(r.ts)}</td>
                  <td style={td}>{(r.user_id || "—").slice(0, 12)}</td>
                  <td style={td}>{r.project || "—"}</td>
                  <td style={{ ...td, color: "#ff8a2a", fontWeight: 600 }}>
                    {r.rule_triggered || "unknown"}
                  </td>
                  <td style={td}>{(r.layers_blocked || []).join(", ") || "—"}</td>
                  <td style={{ ...td, textAlign: "right" }}>{r.total_findings || 0}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Section>
    </div>
  );
}

const th = { textAlign: "left", padding: "8px 10px", fontWeight: 600 };
const td = { padding: "10px", color: "var(--ink, #ddd)" };

function Kpi({ testid, label, value, accent, isText }) {
  return (
    <div data-testid={testid} style={{
      padding: 18, borderRadius: 14,
      background: "linear-gradient(180deg, #11141c, #0c0f15)",
      border: `1px solid ${accent}30`,
    }}>
      <div style={{ fontSize: 11, color: "var(--text-faint, #888)",
                    textTransform: "uppercase", letterSpacing: "0.1em" }}>
        {label}
      </div>
      <div style={{ marginTop: 6, fontSize: isText ? 18 : 32, fontWeight: 700,
                    color: accent, letterSpacing: "-0.02em",
                    wordBreak: isText ? "break-word" : "normal" }}>
        {value}
      </div>
    </div>
  );
}

function Section({ title, children }) {
  return (
    <section style={{
      maxWidth: 1240, margin: "0 auto 18px",
      padding: 18, borderRadius: 14,
      background: "rgba(13,16,24,0.6)",
      border: "1px solid rgba(255,200,120,0.08)",
    }}>
      <div style={{ fontSize: 11, color: "var(--text-faint, #888)",
                    textTransform: "uppercase", letterSpacing: "0.12em",
                    marginBottom: 12 }}>{title}</div>
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
    <div data-testid={testid} style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      {rows.map((r, i) => (
        <div key={i} style={{ display: "flex", alignItems: "center", gap: 10, fontSize: 12 }}>
          <div style={{ flex: "0 0 50%", color: "var(--ink, #ccc)", overflow: "hidden",
                        textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {getLabel(r)}
          </div>
          <div style={{ flex: 1, height: 6, borderRadius: 3,
                        background: "rgba(255,255,255,0.04)", overflow: "hidden" }}>
            <div style={{
              width: `${(getCount(r) / max) * 100}%`, height: "100%",
              background: "linear-gradient(90deg, #ff8a2a, #ff5e1a)",
            }} />
          </div>
          <div style={{ flex: "0 0 32px", textAlign: "right",
                        color: "#ff8a2a", fontWeight: 600 }}>
            {getCount(r)}
          </div>
        </div>
      ))}
    </div>
  );
}
