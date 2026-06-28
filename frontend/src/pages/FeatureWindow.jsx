/**
 * pages/FeatureWindow.jsx — Iter 212m-64
 *
 * Live system map for founders.  Single GET /feature-window/status
 * call powers every section.  No polling, no mock data: if a Mongo
 * count fails the backend returns the literal string "UNSURE" which
 * we render as-is.
 */
import React, { useEffect, useState, useCallback, useMemo } from "react";
import { useNavigate } from "react-router-dom";
import {
  Activity, RefreshCw, Loader2, AlertTriangle, ShieldCheck,
  Boxes, ChevronDown, ChevronRight, Map as MapIcon,
} from "lucide-react";
import { api } from "../lib/api";

const STATUS_COLORS = {
  live:       { bg: "rgba(34,197,94,0.10)",  fg: "#86efac", border: "rgba(34,197,94,0.45)",  dot: "#22c55e" },
  built:      { bg: "rgba(250,204,21,0.10)", fg: "#fde68a", border: "rgba(250,204,21,0.45)", dot: "#facc15" },
  degraded:   { bg: "rgba(249,115,22,0.10)", fg: "#fdba74", border: "rgba(249,115,22,0.45)", dot: "#f97316" },
  broken:     { bg: "rgba(239,68,68,0.10)",  fg: "#fca5a5", border: "rgba(239,68,68,0.45)",  dot: "#ef4444" },
  not_built:  { bg: "rgba(148,163,184,0.10)", fg: "#cbd5e1", border: "rgba(148,163,184,0.40)", dot: "#94a3b8" },
};
const SEV_COLORS = {
  error:   { fg: "#fca5a5", bg: "rgba(239,68,68,0.10)",  border: "rgba(239,68,68,0.45)" },
  warning: { fg: "#fdba74", bg: "rgba(249,115,22,0.10)", border: "rgba(249,115,22,0.45)" },
  info:    { fg: "#bae6fd", bg: "rgba(125,211,252,0.10)", border: "rgba(125,211,252,0.40)" },
};

export default function FeatureWindow() {
  const navigate = useNavigate();
  const [data, setData] = useState(null);
  const [err,  setErr]  = useState(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true); setErr(null);
    try {
      const r = await api.get("/feature-window/status");
      setData(r?.data || r);
    } catch (e) {
      const code = e?.response?.status;
      if (code === 403) { navigate("/dashboard"); return; }
      setErr(e?.response?.data?.detail || e?.message || "Failed to load");
    } finally {
      setLoading(false);
    }
  }, [navigate]);

  useEffect(() => { load(); }, [load]);

  if (loading && !data) return <Frame><Loader2 size={28} className="anim-spin" /> Loading system map…</Frame>;
  if (err) return <Frame><AlertTriangle color="#fca5a5" /> {err}</Frame>;
  if (!data) return null;

  return (
    <div
      data-testid="feature-window-page"
      style={{
        minHeight: "100vh", padding: "28px 32px",
        background: "#0a0e1a", color: "#e8ecf3",
        fontFamily: "Inter, system-ui, sans-serif",
      }}
    >
      {/* Back to dashboard — Iter 212m-98 */}
      <div style={{ marginBottom: 16 }}>
        <button
          data-testid="fw-back-dashboard"
          onClick={() => navigate("/dashboard")}
          style={{
            display: "inline-flex", alignItems: "center", gap: 6,
            padding: "6px 12px", borderRadius: 8,
            background: "transparent",
            border: "1px solid rgba(255,255,255,0.12)",
            color: "#cbd5e1",
            fontSize: 12, fontFamily: "'JetBrains Mono', monospace",
            cursor: "pointer",
          }}
        >
          ← Back to dashboard
        </button>
      </div>
      <Header data={data} loading={loading} onRefresh={load} />
      <StatusPills integrations={data.integrations} />
      <Section title="Modes" id="modes">
        <ModesGrid modes={data.modes} />
      </Section>
      <Section title="Tools" id="tools" subtitle={`${data.tools.total} total`}>
        <ToolsAccordion tools={data.tools} />
      </Section>
      <Section title="Vanguard security" id="vanguard">
        <VanguardPanel v={data.vanguard} />
      </Section>
      <Section title="Loop Mode" id="loop">
        <LoopTimeline lm={data.loop_mode} />
      </Section>
      <Section title="Integrations" id="integrations">
        <IntegrationsTable rows={data.integrations} />
      </Section>
      <Section title="Issues" id="issues">
        <IssuesList issues={data.issues} />
      </Section>
      <Section title="Database — live counts" id="db">
        <DbStats stats={data.db_stats} />
      </Section>
      <style>{`
        .anim-spin { animation: spin 1s linear infinite; }
        @keyframes spin { to { transform: rotate(360deg); } }
      `}</style>
    </div>
  );
}

const Frame = ({ children }) => (
  <div style={{
    minHeight: "100vh", display: "flex", gap: 10,
    alignItems: "center", justifyContent: "center",
    background: "#0a0e1a", color: "#e8ecf3",
    fontFamily: "'JetBrains Mono', monospace",
  }}>{children}</div>
);


function Header({ data, loading, onRefresh }) {
  const { system, scan_timestamp } = data;
  const ts = new Date(scan_timestamp).toLocaleString();
  return (
    <header style={{ marginBottom: 24 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 4 }}>
        <MapIcon size={20} color="#e8a020" />
        <h1 data-testid="fw-title" style={{
          margin: 0, fontSize: 22, fontWeight: 700, letterSpacing: 0.3,
        }}>ORA System Internals</h1>
        <span style={{
          padding: "3px 8px", borderRadius: 999,
          fontSize: 10, fontWeight: 700, letterSpacing: "0.08em",
          textTransform: "uppercase",
          background: "rgba(255,102,8,0.10)",
          border: "1px solid rgba(255,102,8,0.35)",
          color: "#FF6608",
          fontFamily: "'JetBrains Mono', monospace",
        }}>Admin · Platform</span>
        <button
          type="button"
          data-testid="fw-refresh"
          onClick={onRefresh}
          disabled={loading}
          style={{
            marginLeft: "auto",
            display: "inline-flex", alignItems: "center", gap: 6,
            padding: "6px 12px",
            background: "transparent", color: "#e8a020",
            border: "1px solid rgba(232,160,32,0.45)",
            borderRadius: 8, fontSize: 11.5, cursor: loading ? "wait" : "pointer",
            fontFamily: "'JetBrains Mono', monospace",
          }}
        >
          {loading ? <Loader2 size={12} className="anim-spin" /> : <RefreshCw size={12} />}
          Refresh
        </button>
      </div>
      <div style={{
        fontSize: 11, color: "#9aa3b2", marginBottom: 14,
        fontFamily: "'JetBrains Mono', monospace",
      }}>
        Live scan · {ts}
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
        <StatPill label="routes"     value={system.backend_routes} />
        <StatPill label="components" value={system.frontend_components} />
        <StatPill label="pages"      value={system.frontend_pages} />
        <StatPill label="collections" value={system.mongo_collections} />
        <StatPill label="tools" value={data.tools.total} />
      </div>
    </header>
  );
}

const StatPill = ({ label, value }) => (
  <div style={{
    display: "inline-flex", alignItems: "baseline", gap: 6,
    padding: "5px 12px", borderRadius: 999,
    background: "rgba(232,160,32,0.08)",
    border: "1px solid rgba(232,160,32,0.30)",
    fontFamily: "'JetBrains Mono', monospace",
  }}>
    <span style={{ fontSize: 14, fontWeight: 700, color: "#e8a020" }}>{value}</span>
    <span style={{ fontSize: 10.5, color: "#9aa3b2", textTransform: "uppercase", letterSpacing: 0.5 }}>{label}</span>
  </div>
);


function StatusPills({ integrations }) {
  return (
    <div data-testid="fw-status-pills"
         style={{ display: "flex", flexWrap: "wrap", gap: 6, margin: "8px 0 20px" }}>
      {integrations.map((it) => {
        const c = STATUS_COLORS[it.status] || STATUS_COLORS.broken;
        return (
          <a key={it.name} href={`#integrations`} style={{
            display: "inline-flex", alignItems: "center", gap: 5,
            padding: "4px 10px",
            background: c.bg, color: c.fg,
            border: `1px solid ${c.border}`,
            borderRadius: 999,
            fontSize: 10.5,
            fontFamily: "'JetBrains Mono', monospace",
            textDecoration: "none",
          }}>
            <span style={{ width: 6, height: 6, borderRadius: "50%",
                           background: c.dot, boxShadow: `0 0 6px ${c.dot}` }} />
            {it.name}
          </a>
        );
      })}
    </div>
  );
}


const Section = ({ title, subtitle, id, children }) => (
  <section id={id} style={{ margin: "26px 0" }}>
    <h2 style={{
      margin: "0 0 12px",
      fontSize: 12, fontWeight: 700, color: "#e8a020",
      letterSpacing: 1, textTransform: "uppercase",
      fontFamily: "'JetBrains Mono', monospace",
      display: "flex", gap: 10, alignItems: "baseline",
    }}>
      {title}
      {subtitle && <span style={{ color: "#9aa3b2", letterSpacing: 0.4 }}>· {subtitle}</span>}
    </h2>
    {children}
  </section>
);


function ModesGrid({ modes }) {
  return (
    <div style={{
      display: "grid", gap: 10,
      gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
    }}>
      {modes.map((m) => {
        const c = STATUS_COLORS[m.status] || STATUS_COLORS.broken;
        return (
          <div key={m.name} data-testid={`fw-mode-${m.name.toLowerCase()}`}
               style={{
                 padding: 14, borderRadius: 10,
                 background: "rgba(255,255,255,0.025)",
                 border: "1px solid rgba(255,255,255,0.08)",
               }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
              <strong style={{ fontSize: 14 }}>{m.name}</strong>
              <span style={{
                padding: "2px 7px", borderRadius: 999,
                background: c.bg, color: c.fg,
                border: `1px solid ${c.border}`,
                fontSize: 9.5, fontFamily: "'JetBrains Mono', monospace",
                marginLeft: "auto",
              }}>{m.status}</span>
            </div>
            <div style={{ fontSize: 11, color: "#cbd5e1", marginBottom: 4 }}>{m.model}</div>
            <div style={{ fontSize: 10.5, color: "#9aa3b2", fontFamily: "'JetBrains Mono', monospace" }}>
              {m.tier} · {m.price}
            </div>
          </div>
        );
      })}
    </div>
  );
}


function ToolsAccordion({ tools }) {
  const sections = [
    { key: "repo_tools", label: "Repo tools",   list: tools.repo_tools },
    { key: "dev_skills", label: "Dev skills",   list: tools.dev_skills },
    { key: "web_skills", label: "Web skills",   list: tools.web_skills },
  ];
  const [open, setOpen] = useState({});
  return (
    <div>
      {sections.map((s) => (
        <div key={s.key} data-testid={`fw-tools-${s.key}`}
             style={{
               marginBottom: 8, borderRadius: 8,
               background: "rgba(255,255,255,0.025)",
               border: "1px solid rgba(255,255,255,0.08)",
             }}>
          <button type="button"
                  onClick={() => setOpen((o) => ({ ...o, [s.key]: !o[s.key] }))}
                  style={{
                    width: "100%", display: "flex", alignItems: "center", gap: 8,
                    padding: "10px 14px",
                    background: "transparent", border: "none",
                    color: "#e8ecf3", fontSize: 12, cursor: "pointer",
                    fontFamily: "'JetBrains Mono', monospace",
                  }}>
            {open[s.key] ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
            <strong>{s.list.length} {s.label.toLowerCase()}</strong>
          </button>
          {open[s.key] && (
            <div style={{ padding: "0 14px 12px", display: "flex", flexWrap: "wrap", gap: 6 }}>
              {s.list.map((t) => (
                <code key={t} style={{
                  padding: "3px 8px", borderRadius: 6,
                  background: "rgba(232,160,32,0.06)",
                  color: "#e8a020", fontSize: 10.5,
                  fontFamily: "'JetBrains Mono', monospace",
                  border: "1px solid rgba(232,160,32,0.22)",
                }}>
                  {t}
                  {t === "e2b_run_code" && (
                    <span style={{ marginLeft: 6, color: "#fca5a5" }}>
                      ⚠ E2B_API_KEY not set
                    </span>
                  )}
                </code>
              ))}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}


function VanguardBadge({ label, tone = "info" }) {
  const palette = {
    good: { bg: "rgba(34,197,94,0.12)",  br: "rgba(34,197,94,0.45)",  fg: "#86efac" },
    warn: { bg: "rgba(245,158,11,0.12)", br: "rgba(245,158,11,0.45)", fg: "#fbbf24" },
    info: { bg: "rgba(56,189,248,0.10)", br: "rgba(56,189,248,0.40)", fg: "#7dd3fc" },
  }[tone] || { bg: "rgba(148,163,184,0.10)", br: "rgba(148,163,184,0.40)", fg: "#cbd5e1" };
  return (
    <span style={{
      padding: "4px 10px", borderRadius: 999, fontSize: 11,
      fontFamily: "'JetBrains Mono', monospace",
      background: palette.bg, border: `1px solid ${palette.br}`,
      color: palette.fg, whiteSpace: "nowrap",
    }}>{label}</span>
  );
}


function VanguardPanel({ v }) {
  // Iter 212m-66 — render the new two-round + AI remediation badges
  // when the backend exposes them. Existing 25-pattern stats stay
  // exactly where they were so legacy UI users see no shift.
  const t = v.two_round_budget || {};
  return (
    <div>
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginBottom: 10 }}>
        {[
          { n: v.total_patterns,          label: "patterns total" },
          { n: v.secret_patterns,         label: "secret patterns" },
          { n: v.dangerous_code_patterns, label: "dangerous code" },
          { n: v.chain_detection_rules ?? "—", label: "chain rules" },
        ].map((b) => (
          <div key={b.label} style={{
            padding: "12px 16px", borderRadius: 10,
            background: "rgba(34,197,94,0.06)",
            border: "1px solid rgba(34,197,94,0.30)",
            minWidth: 160,
          }} data-testid={`vanguard-stat-${b.label.replace(/\s+/g, "-")}`}>
            <div style={{ fontSize: 22, fontWeight: 700, color: "#86efac" }}>{b.n}</div>
            <div style={{ fontSize: 10.5, color: "#9aa3b2",
                          textTransform: "uppercase", letterSpacing: 0.4,
                          fontFamily: "'JetBrains Mono', monospace" }}>{b.label}</div>
          </div>
        ))}
      </div>
      <div style={{ fontSize: 11, color: "#cbd5e1", marginBottom: 8 }}>
        + <strong>{v.scanner_extra_rules}</strong> rules in <code>{v.source_file}</code> +
        sibling <code>routers/security_scan.py</code>
      </div>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}
           data-testid="vanguard-iter212m66-badges">
        <VanguardBadge label={`Two-round scan: ${v.two_round_scan || "—"}`}
               tone={v.two_round_scan === "complete" ? "good" : "warn"} />
        <VanguardBadge label={`AI report: ${v.ai_remediation_report || "—"}`}
               tone={v.ai_remediation_report === "complete" ? "good" : "warn"} />
        <VanguardBadge label={`Auto-PR: ${v.auto_draft_pr || "—"}`}
               tone={v.auto_draft_pr === "complete" ? "good" : "warn"} />
        {t.round1_s && (
          <VanguardBadge label={`Budget: R1 ${t.round1_s}s · R2 ${t.round2_s}s · total ${t.total_s}s`}
                 tone="info" />
        )}
        {v.ai_report_provider && (
          <VanguardBadge label={`LLM: ${v.ai_report_provider} · max_tokens ${v.ai_report_max_tokens}`}
                 tone="info" />
        )}
      </div>
    </div>
  );
}


function LoopTimeline({ lm }) {
  const phases = [
    { id: "a", label: "Phase A — UI shell",     status: lm.phase_a },
    { id: "b", label: "Phase B — Engine",       status: lm.phase_b },
    { id: "c", label: "Phase C — Verify + heal", status: lm.phase_c },
    { id: "d", label: "Phase D — Self-heal UI", status: lm.phase_d },
  ];
  const color = (s) => s === "complete" ? "#22c55e"
                    : s === "partial"  ? "#facc15"
                    : s === "pending"  ? "#94a3b8" : "#ef4444";
  return (
    <div>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap",
                    alignItems: "center", marginBottom: 10 }}>
        {phases.map((p, i) => (
          <React.Fragment key={p.id}>
            <div data-testid={`fw-loop-phase-${p.id}`}
                 title={p.id === "d" ? lm.phase_d_note : ""}
                 style={{
                   display: "inline-flex", alignItems: "center", gap: 6,
                   padding: "6px 12px", borderRadius: 999,
                   background: `rgba(${color(p.status) === "#22c55e" ? "34,197,94" :
                                       color(p.status) === "#facc15" ? "250,204,21" :
                                       color(p.status) === "#94a3b8" ? "148,163,184" : "239,68,68"},0.10)`,
                   border: `1px solid ${color(p.status)}66`,
                   color: color(p.status), fontSize: 11,
                   fontFamily: "'JetBrains Mono', monospace",
                 }}>
              <span style={{ width: 6, height: 6, borderRadius: "50%",
                             background: color(p.status) }} />
              {p.label} · {p.status}
            </div>
            {i < phases.length - 1 && <span style={{ color: "#9aa3b2" }}>→</span>}
          </React.Fragment>
        ))}
      </div>
      <div data-testid="fw-loop-frontend-warning" style={{
        padding: "8px 12px", borderRadius: 8,
        background: "rgba(249,115,22,0.06)",
        border: "1px solid rgba(249,115,22,0.40)",
        color: "#fdba74", fontSize: 11.5, marginBottom: 8,
      }}>
        ⚠ {lm.frontend_note}
      </div>
      <div style={{ fontSize: 11, color: "#9aa3b2", fontFamily: "'JetBrains Mono', monospace" }}>
        loop_sessions: <strong style={{ color: "#e8ecf3" }}>{String(lm.loop_sessions_count)}</strong>
        {" · "}
        loop_plans: <strong style={{ color: "#e8ecf3" }}>{String(lm.loop_plans_count)}</strong>
      </div>
    </div>
  );
}


function IntegrationsTable({ rows }) {
  return (
    <div style={{ overflowX: "auto" }}>
      <table data-testid="fw-integrations-table"
             style={{ width: "100%", borderCollapse: "collapse",
                      fontSize: 11.5, fontFamily: "'JetBrains Mono', monospace" }}>
        <thead>
          <tr style={{ color: "#9aa3b2", textAlign: "left" }}>
            <th style={th}>Name</th><th style={th}>Status</th>
            <th style={th}>File</th><th style={th}>Note</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const c = STATUS_COLORS[r.status] || STATUS_COLORS.broken;
            return (
              <tr key={r.name} style={{
                borderTop: "1px solid rgba(255,255,255,0.05)",
              }}>
                <td style={td}>{r.name}</td>
                <td style={td}>
                  <span style={{
                    display: "inline-flex", alignItems: "center", gap: 4,
                    padding: "2px 8px", borderRadius: 999,
                    background: c.bg, color: c.fg, border: `1px solid ${c.border}`,
                    fontSize: 10,
                  }}>
                    <span style={{ width: 5, height: 5, borderRadius: "50%", background: c.dot }} />
                    {r.status}
                  </span>
                </td>
                <td style={{ ...td, color: "#9aa3b2" }}>{r.file || "—"}</td>
                <td style={{ ...td, color: "#cbd5e1" }}>{r.note || "—"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
const th = { padding: "6px 8px", fontWeight: 600, letterSpacing: 0.3,
             textTransform: "uppercase", fontSize: 10 };
const td = { padding: "8px", verticalAlign: "top" };


function IssuesList({ issues }) {
  const sorted = useMemo(() => {
    const order = { error: 0, warning: 1, info: 2 };
    return [...issues].sort((a, b) => (order[a.severity] ?? 9) - (order[b.severity] ?? 9));
  }, [issues]);
  return (
    <div data-testid="fw-issues" style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      {sorted.map((i, idx) => {
        const c = SEV_COLORS[i.severity] || SEV_COLORS.info;
        return (
          <div key={idx} style={{
            display: "flex", alignItems: "flex-start", gap: 10,
            padding: "8px 12px", borderRadius: 8,
            background: c.bg, border: `1px solid ${c.border}`,
            fontSize: 11.5,
          }}>
            <span style={{
              padding: "1px 7px", borderRadius: 4,
              background: c.fg + "20", color: c.fg, border: `1px solid ${c.border}`,
              fontSize: 9.5, textTransform: "uppercase", letterSpacing: 0.4,
              fontFamily: "'JetBrains Mono', monospace",
              flexShrink: 0,
            }}>{i.severity}</span>
            <div>
              <div style={{ fontWeight: 600, color: "#e8ecf3" }}>{i.item}</div>
              <div style={{ color: "#cbd5e1", marginTop: 2 }}>{i.note}</div>
            </div>
          </div>
        );
      })}
    </div>
  );
}


function DbStats({ stats }) {
  return (
    <div style={{
      display: "grid", gap: 10,
      gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
    }}>
      {Object.entries(stats).map(([k, v]) => (
        <div key={k} data-testid={`fw-db-${k}`} style={{
          padding: "12px 14px", borderRadius: 10,
          background: "rgba(255,255,255,0.025)",
          border: "1px solid rgba(255,255,255,0.08)",
        }}>
          <div style={{ fontSize: 22, fontWeight: 700, color: "#e8a020",
                        fontFamily: "'JetBrains Mono', monospace" }}>
            {String(v)}
          </div>
          <div style={{ fontSize: 10.5, color: "#9aa3b2",
                        textTransform: "uppercase", letterSpacing: 0.4,
                        fontFamily: "'JetBrains Mono', monospace" }}>{k}</div>
        </div>
      ))}
    </div>
  );
}
