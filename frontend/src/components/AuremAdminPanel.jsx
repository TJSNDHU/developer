/**
 * components/AuremAdminPanel.jsx
 * ================================
 * Admin panel showing all 5 upgrade features live:
 *   1. Project Brain — memory per repo
 *   2. Parallel Agents — task split stats
 *   3. Copilot Trust Guard — lint blocks caught
 *   4. Issues Context — open tickets count
 *   5. ORA Council — learning progress toward fine-tune
 *
 * Add to Admin.jsx:
 *   import AuremAdminPanel from './components/AuremAdminPanel';
 *   // inside your admin JSX:
 *   <AuremAdminPanel />
 */

import { useState, useEffect, useCallback } from "react";

const API = process.env.REACT_APP_BACKEND_URL || "";

// ─── time-ago helper for the "last updated" pill ─────────────────
function _relTime(ms) {
  if (!ms) return "—";
  const s = Math.max(0, Math.round((Date.now() - ms) / 1000));
  if (s < 5)   return "just now";
  if (s < 60)  return `${s} s ago`;
  const m = Math.round(s / 60);
  if (m < 60)  return `${m} min ago`;
  return new Date(ms).toLocaleTimeString();
}

// ─── tiny fetch helper ───────────────────────────────────────────
async function apiFetch(path, opts = {}) {
  const token = localStorage.getItem("aurem_token");
  const res = await fetch(`${API}${path}`, {
    ...opts,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(opts.headers || {}),
    },
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

// ─── stat card ───────────────────────────────────────────────────
function StatCard({ label, value, sub, color = "#ff8a2a", icon }) {
  return (
    <div style={{
      background: "rgba(255, 255, 255, 0.04)",
      backdropFilter: "blur(12px) saturate(140%)",
      WebkitBackdropFilter: "blur(12px) saturate(140%)",
      border: `1px solid ${color}33`,
      borderRadius: 12,
      padding: "18px 22px",
      minWidth: 160,
      flex: "1 1 160px",
    }}>
      <div style={{ fontSize: 22, marginBottom: 4 }}>{icon}</div>
      <div style={{ fontSize: 28, fontWeight: 700, color, lineHeight: 1 }}>{value}</div>
      <div style={{ fontSize: 13, color: "#888", marginTop: 6 }}>{label}</div>
      {sub && <div style={{ fontSize: 11, color: "#555", marginTop: 4 }}>{sub}</div>}
    </div>
  );
}

// ─── section wrapper ─────────────────────────────────────────────
function Section({ title, badge, children, color = "#ff8a2a" }) {
  return (
    <div style={{ marginBottom: 32 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 14 }}>
        <h3 style={{ margin: 0, fontSize: 15, fontWeight: 600, color: "#e2e8f0" }}>{title}</h3>
        {badge && (
          <span style={{
            background: `${color}22`,
            color,
            border: `1px solid ${color}44`,
            borderRadius: 20,
            padding: "2px 10px",
            fontSize: 11,
            fontWeight: 600,
          }}>{badge}</span>
        )}
      </div>
      {children}
    </div>
  );
}

// ─── progress bar ────────────────────────────────────────────────
function ProgressBar({ value, max, color = "#ff8a2a", label }) {
  const pct = Math.min(100, Math.round((value / Math.max(max, 1)) * 100));
  return (
    <div style={{ marginBottom: 10 }}>
      {label && <div style={{ fontSize: 12, color: "#888", marginBottom: 4 }}>{label}</div>}
      <div style={{ background: "#ffffff11", borderRadius: 6, height: 8, overflow: "hidden" }}>
        <div style={{
          width: `${pct}%`,
          height: "100%",
          background: color,
          borderRadius: 6,
          transition: "width 0.6s ease",
        }} />
      </div>
      <div style={{ fontSize: 11, color: "#555", marginTop: 3 }}>{value} / {max} ({pct}%)</div>
    </div>
  );
}

// ─── brain memory display ────────────────────────────────────────
function BrainMemory({ brain }) {
  if (!brain) return <div style={{ color: "#555", fontSize: 13 }}>No brain data yet. Brain builds from conversations.</div>;

  return (
    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
      {[
        { label: "Tech stack", items: brain.tech_stack || [], color: "#06b6d4" },
        { label: "Team preferences", items: brain.team_preferences || [], color: "#ff8a2a" },
      ].map(({ label, items, color }) => (
        <div key={label} style={{ background: "#ffffff08", borderRadius: 8, padding: 12 }}>
          <div style={{ fontSize: 12, color: "#888", marginBottom: 8 }}>{label} ({items.length})</div>
          {items.length === 0
            ? <div style={{ fontSize: 12, color: "#444" }}>None recorded yet</div>
            : items.slice(0, 6).map((item, i) => (
                <div key={i} style={{
                  display: "inline-block",
                  background: `${color}22`,
                  color,
                  border: `1px solid ${color}44`,
                  borderRadius: 12,
                  padding: "2px 8px",
                  fontSize: 11,
                  margin: "2px 3px 2px 0",
                }}>{item}</div>
              ))
          }
        </div>
      ))}

      <div style={{ background: "#ffffff08", borderRadius: 8, padding: 12, gridColumn: "span 2" }}>
        <div style={{ fontSize: 12, color: "#888", marginBottom: 8 }}>
          Recent decisions ({(brain.decisions || []).length})
        </div>
        {(brain.decisions || []).slice(-3).reverse().map((d, i) => (
          <div key={i} style={{ fontSize: 12, color: "#94a3b8", marginBottom: 6, paddingLeft: 10, borderLeft: "2px solid rgba(255,138,42,0.2)" }}>
            <strong style={{ color: "#e2e8f0" }}>{d.title}</strong>
            <span style={{ color: "#475569" }}> — {d.reason}</span>
            <span style={{ color: "#334155", marginLeft: 6, fontSize: 10 }}>{d.date}</span>
          </div>
        ))}
        {(brain.decisions || []).length === 0 &&
          <div style={{ fontSize: 12, color: "#444" }}>No decisions recorded yet</div>
        }
      </div>

      {(brain.recurring_bugs || []).length > 0 && (
        <div style={{ background: "#ef444411", borderRadius: 8, padding: 12, gridColumn: "span 2", border: "1px solid #ef444422" }}>
          <div style={{ fontSize: 12, color: "#f87171", marginBottom: 8 }}>
            Recurring issues ({brain.recurring_bugs.length})
          </div>
          {brain.recurring_bugs.slice(0, 3).map((b, i) => (
            <div key={i} style={{ fontSize: 12, color: "#fca5a5", marginBottom: 4 }}>
              <strong>×{b.count}</strong> — {b.description}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ─── main panel ──────────────────────────────────────────────────
export default function AuremAdminPanel() {
  const [stats,     setStats]     = useState(null);
  const [brain,     setBrain]     = useState(null);
  const [loading,   setLoading]   = useState(true);
  const [error,     setError]     = useState(null);
  const [projectId, setProjectId] = useState("");
  const [tab,       setTab]       = useState("overview");
  // Iter 88 — live-update affordances.
  const [refreshing,  setRefreshing]  = useState(false);
  const [lastUpdated, setLastUpdated] = useState(null);

  const fetchStats = useCallback(async () => {
    try {
      setError(null);
      const s = await apiFetch("/api/aurem-dev/admin/ora-stats");
      setStats(s);
      setLastUpdated(Date.now());
    } catch (e) {
      setError(e.message);
    }
  }, []);

  const fetchBrain = useCallback(async (pid) => {
    if (!pid) return;
    try {
      const b = await apiFetch(`/api/aurem-dev/admin/project-brain/${pid}`);
      setBrain(b);
    } catch {
      setBrain(null);
    }
  }, []);

  // Manual refresh — gives the user explicit feedback (spinner + state
  // disable + lastUpdated bump) so the click feels alive.
  const refreshNow = useCallback(async () => {
    setRefreshing(true);
    try {
      await fetchStats();
      // If the brain tab is active and a project_id is loaded, refresh
      // that too so the entire visible surface stays current.
      if (tab === "brain" && projectId) await fetchBrain(projectId);
    } finally {
      setRefreshing(false);
    }
  }, [fetchStats, fetchBrain, tab, projectId]);

  // Auto-poll every 30s when the tab is visible. Pauses cleanly on
  // background tabs to save the user's battery + our API budget.
  useEffect(() => {
    setLoading(true);
    fetchStats().finally(() => setLoading(false));

    let interval = null;
    const start = () => {
      if (interval) return;
      interval = setInterval(() => {
        if (document.visibilityState === "visible") fetchStats();
      }, 30000);
    };
    const stop = () => {
      if (interval) { clearInterval(interval); interval = null; }
    };
    start();
    const onVis = () => {
      if (document.visibilityState === "visible") {
        fetchStats(); // catch up immediately on tab refocus
        start();
      } else {
        stop();
      }
    };
    document.addEventListener("visibilitychange", onVis);
    return () => {
      stop();
      document.removeEventListener("visibilitychange", onVis);
    };
  }, [fetchStats]);

  const tabs = ["overview", "brain", "council"];

  return (
    <div
      className="glass-pane"
      style={{
        color: "#e2e8f0",
        borderRadius: 16,
        padding: 28,
        fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
        maxWidth: 900,
      }}
    >
      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 24 }}>
        <div>
          <h2 style={{ margin: 0, fontSize: 18, fontWeight: 700, color: "#ffc560" }}>
            AUREM Intelligence Dashboard
          </h2>
          <div style={{ fontSize: 12, color: "#475569", marginTop: 4 }}>
            ORA learning · parallel agents · design linter · project brain
          </div>
        </div>
        <button
          onClick={refreshNow}
          disabled={refreshing}
          data-testid="admin-panel-refresh"
          style={{
            background: "rgba(255,138,42,0.13)",
            border: "1px solid rgba(255,138,42,0.27)",
            borderRadius: 8,
            color: "#ffc560",
            padding: "6px 14px",
            fontSize: 12,
            cursor: refreshing ? "wait" : "pointer",
            opacity: refreshing ? 0.6 : 1,
            display: "inline-flex",
            alignItems: "center",
            gap: 6,
          }}
        >
          {refreshing && (
            <span style={{
              display: "inline-block", width: 10, height: 10,
              border: "2px solid rgba(255,197,96,0.25)",
              borderTopColor: "#ffc560",
              borderRadius: "50%",
              animation: "auremspin 0.7s linear infinite",
            }} />
          )}
          {refreshing ? "Refreshing…" : "Refresh"}
        </button>
      </div>
      {/* live-update indicator */}
      <div
        data-testid="admin-panel-last-updated"
        style={{
          fontSize: 11,
          color: "#475569",
          marginTop: -16,
          marginBottom: 18,
          letterSpacing: "0.04em",
          fontFamily: "'JetBrains Mono', monospace",
        }}
      >
        {lastUpdated
          ? `Live · last updated ${_relTime(lastUpdated)} · auto-refresh 30 s`
          : "Live · waiting for first sync"}
      </div>
      <style>{`@keyframes auremspin { to { transform: rotate(360deg); } }`}</style>

      {/* Tab bar */}
      <div style={{ display: "flex", gap: 4, marginBottom: 24, borderBottom: "1px solid #ffffff0a", paddingBottom: 12 }}>
        {tabs.map(t => (
          <button
            key={t}
            onClick={() => setTab(t)}
            style={{
              background: tab === t ? "rgba(255,138,42,0.13)" : "transparent",
              border: tab === t ? "1px solid rgba(255,138,42,0.27)" : "1px solid transparent",
              borderRadius: 8,
              color: tab === t ? "#ffc560" : "#475569",
              padding: "6px 16px",
              fontSize: 12,
              cursor: "pointer",
              textTransform: "capitalize",
            }}
          >{t}</button>
        ))}
      </div>

      {loading && (
        <div style={{ textAlign: "center", color: "#475569", padding: 40 }}>
          Loading AUREM intelligence data...
        </div>
      )}

      {error && (
        <div style={{
          background: "#ef444411",
          border: "1px solid #ef444433",
          borderRadius: 8,
          padding: 12,
          color: "#f87171",
          fontSize: 13,
          marginBottom: 20,
        }}>
          Error: {error} — Check that admin endpoints are deployed.
        </div>
      )}

      {/* OVERVIEW TAB */}
      {tab === "overview" && stats && (
        <>
          <Section title="ORA Council — Learning Progress" badge={stats.ready_for_finetune ? "Ready to fine-tune" : "Collecting data"} color="#ffc560">
            <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 16 }}>
              <StatCard label="Total interactions" value={stats.total_interactions}      icon="🧠" color="#ffc560" />
              <StatCard label="Chat logs (A)"      value={stats.by_mode?.A_chat ?? 0}   icon="💬" color="#06b6d4" />
              <StatCard label="Advice logs (B)"    value={stats.by_mode?.B_advice ?? 0} icon="💡" color="#f59e0b" />
              <StatCard label="Code tasks (C)"     value={stats.by_mode?.C_code ?? 0}   icon="⚡" color="#10b981" />
              <StatCard label="Debug sessions (D)" value={stats.by_mode?.D_debug ?? 0}  icon="🐞" color="#f97316" />
              <StatCard label="Audit reports (E)"  value={stats.by_mode?.E_audit ?? 0}  icon="🔬" color="#ff8a2a" />
            </div>
            <ProgressBar
              value={stats.total_interactions}
              max={1000}
              color="#ffc560"
              label={`Fine-tune threshold: ${stats.finetune_tip}`}
            />
          </Section>

          <Section title="Two-Agent Maxx — Code Quality" color="#10b981">
            <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 16 }}>
              <StatCard label="Claude corrections"  value={stats.corrections_applied ?? 0} icon="🔍" color="#10b981" sub={`${stats.correction_rate_pct ?? 0}% of code tasks`} />
              <StatCard label="Lint blocks caught"  value={stats.lint_blocks_caught ?? 0}  icon="🛡️" color="#f59e0b" sub="Bad code stopped before push" />
              <StatCard label="Parallel tasks run"  value={stats.parallel_tasks_run ?? 0}  icon="⚡" color="#06b6d4" sub="Multi-agent executions" />
              <StatCard label="Exported for training" value={stats.exported_for_training ?? 0} icon="📤" color="#ff8a2a" sub={`${stats.pending_export ?? 0} pending`} />
            </div>
          </Section>
        </>
      )}

      {/* BRAIN TAB */}
      {tab === "brain" && (
        <>
          <Section title="Project Brain" badge="Per-repo memory" color="#f59e0b">
            <div style={{ display: "flex", gap: 8, marginBottom: 16 }}>
              <input
                value={projectId}
                onChange={e => setProjectId(e.target.value)}
                placeholder="Enter project_id to inspect brain..."
                style={{
                  flex: 1,
                  background: "#ffffff0a",
                  border: "1px solid #ffffff15",
                  borderRadius: 8,
                  padding: "8px 12px",
                  color: "#e2e8f0",
                  fontSize: 13,
                  fontFamily: "inherit",
                }}
              />
              <button
                onClick={() => fetchBrain(projectId)}
                style={{
                  background: "#f59e0b22",
                  border: "1px solid #f59e0b44",
                  borderRadius: 8,
                  color: "#fbbf24",
                  padding: "8px 16px",
                  cursor: "pointer",
                  fontSize: 13,
                }}
              >
                Load Brain
              </button>
            </div>
            <BrainMemory brain={brain} />
          </Section>
        </>
      )}

      {/* COUNCIL TAB */}
      {tab === "council" && stats && (
        <>
          <Section title="ORA Learning — Detailed Breakdown" color="#ff8a2a">
            <div style={{ background: "#ffffff05", borderRadius: 10, padding: 18 }}>
              {[
                { label: "Mode A (casual chat)", value: stats.by_mode?.A_chat ?? 0,   color: "#06b6d4" },
                { label: "Mode B (advice/suggestions)", value: stats.by_mode?.B_advice ?? 0, color: "#f59e0b" },
                { label: "Mode C (code tasks)", value: stats.by_mode?.C_code ?? 0,    color: "#10b981" },
                { label: "Mode D (debug sessions)", value: stats.by_mode?.D_debug ?? 0, color: "#f97316" },
                { label: "Mode E (audit reports)",  value: stats.by_mode?.E_audit ?? 0, color: "#ff8a2a" },
              ].map(({ label, value, color }) => (
                <ProgressBar
                  key={label}
                  value={value}
                  max={Math.max(stats.total_interactions, 1)}
                  color={color}
                  label={label}
                />
              ))}

              <div style={{
                marginTop: 20,
                padding: 14,
                background: stats.ready_for_finetune ? "#10b98111" : "#f59e0b11",
                border: `1px solid ${stats.ready_for_finetune ? "#10b98133" : "#f59e0b33"}`,
                borderRadius: 8,
              }}>
                <div style={{ fontSize: 13, color: stats.ready_for_finetune ? "#34d399" : "#fbbf24", fontWeight: 600 }}>
                  {stats.ready_for_finetune ? "✅ Ready to fine-tune ORA" : "⏳ Collecting training data"}
                </div>
                <div style={{ fontSize: 12, color: "#64748b", marginTop: 4 }}>
                  {stats.finetune_tip}
                </div>
              </div>
            </div>
          </Section>
        </>
      )}
    </div>
  );
}
