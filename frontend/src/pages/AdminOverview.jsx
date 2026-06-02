/**
 * pages/AdminOverview.jsx
 *
 * AUREM System Overview — admin panel main tab.
 * Shows: live health, all features status, user metrics,
 *        ship wall stats, ORA council logs, Sentry health.
 *
 * Add to AuremAdminPanel.jsx as the first tab: "Overview"
 */
import React, { useEffect, useState, useCallback } from "react";
import { api, getToken } from "../lib/api";

export default function AdminOverview() {
  const [health,  setHealth]  = useState(null);
  const [stats,   setStats]   = useState(null);
  const [wall,    setWall]    = useState(null);
  const [council, setCouncil] = useState(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    const h = { Authorization: `Bearer ${getToken()}` };
    // /health lives at the app root (`/api/health`), not under
    // /api/aurem-dev. Use a direct fetch so we hit the right URL.
    const HEALTH_URL = `${process.env.REACT_APP_BACKEND_URL}/api/health`;
    try {
      const [healthRes, statsRes, wallRes, councilRes] = await Promise.allSettled([
        fetch(HEALTH_URL).then((r) => r.json()),
        api.get("/usage/public/stats"),
        api.get("/wall/stats"),
        api.get("/admin/council/stats", { headers: h }),
      ]);
      if (healthRes.status  === "fulfilled") setHealth(healthRes.value);
      if (statsRes.status   === "fulfilled") setStats(statsRes.value.data);
      if (wallRes.status    === "fulfilled") setWall(wallRes.value.data);
      if (councilRes.status === "fulfilled") setCouncil(councilRes.value.data);
    } catch { /* silent */ }
    finally { setLoading(false); }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 60_000);
    return () => clearInterval(t);
  }, [load]);

  if (loading) return (
    <div style={{ padding: 40, textAlign: "center", color: "var(--text-dim)", fontSize: 13 }}>
      Loading system overview…
    </div>
  );

  const dbOk = health?.db === true;
  const uptimeMin = health?.uptime_s ? Math.floor(health.uptime_s / 60) : 0;

  return (
    <div style={{ padding: "24px 20px", maxWidth: 900 }}>

      {/* ── System health ───────────────────────────────────── */}
      <Section title="System health">
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
          <HealthChip ok={dbOk}   label="MongoDB" />
          <HealthChip ok={true}   label="FastAPI" />
          <HealthChip ok={!!stats} label="Public stats API" />
          <HealthChip ok={!!wall}  label="Ship Wall" />
          <HealthChip ok={!!council} label="Council logger" />
          <InfoChip label={`Uptime ${uptimeMin}m`} />
        </div>
      </Section>

      {/* ── User metrics ────────────────────────────────────── */}
      <Section title="Users & ships">
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(130px, 1fr))", gap: 10 }}>
          <MetricCard label="Developers"     value={stats?.developers   ?? "—"} />
          <MetricCard label="Tasks shipped"  value={wall?.total_ships   ?? stats?.tasks_shipped ?? "—"} />
          <MetricCard label="Active repos"   value={wall?.total_repos   ?? "—"} />
          <MetricCard label="Claude corrected" value={stats?.claude_corrected_pct != null ? `${stats.claude_corrected_pct}%` : "—"} />
          <MetricCard label="Lint blocks"    value={stats?.lint_blocks_caught ?? "—"} />
        </div>
      </Section>

      {/* ── Features checklist ──────────────────────────────── */}
      <Section title="Features — live status">
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6 }}>
          <FeatureRow name="Two-Agent Maxx"         status="live"    note="DeepSeek + Claude review" />
          <FeatureRow name="Project Brain"           status="live"    note="Per-repo memory" />
          <FeatureRow name="Design Linter + 007"    status="live"    note="25 secret patterns" />
          <FeatureRow name="GitHub Issues context"  status="live"    note="1hr TTL cache" />
          <FeatureRow name="Parallel agents"        status="live"    note="asyncio.gather" />
          <FeatureRow name="Mode A/B/C/D/E"         status="live"    note="All 5 intent modes" />
          <FeatureRow name="F12 error capture"      status="live"    note="Browser → ORA" />
          <FeatureRow name="Mode D→C real handoff"  status="live"    note="Real cto_tasks row" />
          <FeatureRow name="PAT encryption"         status="live"    note="HKDF-Fernet v1:" />
          <FeatureRow name="Vanguard skill injection" status="live"  note="7 skills, 3 max/task" />
          <FeatureRow name="Rate limiting"          status="live"    note="30/min chat, 10/min tasks" />
          <FeatureRow name="Free tier cap"          status="live"    note="10 tasks/30 days" />
          <FeatureRow name="SSE task streamer"      status="live"    note="task_handoff frame" />
          <FeatureRow name="ORA council logger"     status="live"    note="All 5 modes logged" />
          <FeatureRow name="Daily JSONL export"     status="live"    note="ORA training data" />
          <FeatureRow name="Sentry monitoring"      status="needs-dsn" note="SDK wired, set SENTRY_DSN" />
          <FeatureRow name="GitHub OAuth"           status="needs-key" note="Set GITHUB_OAUTH_CLIENT_ID" />
          <FeatureRow name="Public stats strip"     status="live"    note="Landing page" />
          <FeatureRow name="Ship Wall"              status="live"    note="auremcto.com/wall" />
          <FeatureRow name="ORA Wrapped"            status="live"    note="/wrapped/me" />
          <FeatureRow name="VS Code extension"      status="pending" note="Build + publish" />
          <FeatureRow name="SWE-bench score"        status="pending" note="Run benchmark" />
        </div>
      </Section>

      {/* ── ORA Council stats ───────────────────────────────── */}
      {council && (
        <Section title="ORA council logs (training data)">
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(120px, 1fr))", gap: 10 }}>
            <MetricCard label="Total logs"    value={council.total_logs   ?? "—"} />
            <MetricCard label="Mode A"        value={council.mode_a       ?? "—"} />
            <MetricCard label="Mode B"        value={council.mode_b       ?? "—"} />
            <MetricCard label="Mode C tasks"  value={council.mode_c       ?? "—"} />
            <MetricCard label="Mode D debug"  value={council.mode_d       ?? "—"} />
            <MetricCard label="Mode E audits" value={council.mode_e       ?? "—"} />
            <MetricCard label="Corrections"   value={council.corrections  ?? "—"} />
            <MetricCard label="Lint blocks"   value={council.lint_blocked ?? "—"} />
          </div>
          <div style={{ marginTop: 10, fontSize: 11, color: "var(--text-dim)" }}>
            ORA fine-tune target: 1000 logs. Current: {council.total_logs ?? 0}.
            {(council.total_logs ?? 0) < 1000 && (
              <span style={{ color: "#EF9F27" }}> {1000 - (council.total_logs ?? 0)} more needed.</span>
            )}
            {(council.total_logs ?? 0) >= 1000 && (
              <span style={{ color: "#1D9E75" }}> Ready for fine-tune! Run HuggingFace SFT.</span>
            )}
          </div>
        </Section>
      )}

      {/* ── Next actions ────────────────────────────────────── */}
      <Section title="Next actions — pending on you">
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <ActionRow
            urgent
            title="Set SENTRY_DSN in production"
            detail="sentry.io → free → FastAPI project → copy DSN → Emergent dashboard env vars"
          />
          <ActionRow
            urgent
            title="Create GitHub OAuth App"
            detail="github.com/settings/developers → OAuth Apps → callback: auremcto.com/api/aurem-dev/github/oauth/callback"
          />
          <ActionRow
            title="Record 60-second demo video"
            detail="Real repo, type a task, watch ORA ship it. Post on X. This is your biggest marketing lever."
          />
          <ActionRow
            title="Find 5 beta developers"
            detail="Real repos, no hand-holding. Ask 3 of your 29 signups why they haven't shipped yet."
          />
          <ActionRow
            title="Publish VS Code extension"
            detail="marketplace.visualstudio.com — code is done, needs publisher account + vsce publish"
          />
        </div>
      </Section>

    </div>
  );
}


/* ── Sub-components ──────────────────────────────────────────── */

function Section({ title, children }) {
  return (
    <div style={{ marginBottom: 28 }}>
      <div style={{
        fontSize: 11, fontWeight: 500, color: "var(--text-dim)",
        textTransform: "uppercase", letterSpacing: ".06em",
        marginBottom: 10,
      }}>
        {title}
      </div>
      {children}
    </div>
  );
}

function MetricCard({ label, value }) {
  return (
    <div style={{
      background: "rgba(255,255,255,0.04)",
      border: "1px solid rgba(255,255,255,0.07)",
      borderRadius: 8, padding: "10px 12px",
    }}>
      <div style={{ fontSize: 20, fontWeight: 500, color: "var(--text)" }}>{value}</div>
      <div style={{ fontSize: 11, color: "var(--text-dim)", marginTop: 3 }}>{label}</div>
    </div>
  );
}

function HealthChip({ ok, label }) {
  return (
    <div style={{
      fontSize: 11, padding: "4px 10px", borderRadius: 20,
      background: ok ? "rgba(29,158,117,0.15)" : "rgba(226,75,74,0.15)",
      border: `1px solid ${ok ? "rgba(29,158,117,0.3)" : "rgba(226,75,74,0.3)"}`,
      color: ok ? "#1D9E75" : "#E24B4A",
      display: "flex", alignItems: "center", gap: 5,
    }}>
      <span style={{ fontSize: 8 }}>{ok ? "●" : "●"}</span>
      {label}
    </div>
  );
}

function InfoChip({ label }) {
  return (
    <div style={{
      fontSize: 11, padding: "4px 10px", borderRadius: 20,
      background: "rgba(255,255,255,0.05)",
      border: "1px solid rgba(255,255,255,0.1)",
      color: "var(--text-dim)",
    }}>
      {label}
    </div>
  );
}

const STATUS_COLORS = {
  live:       { bg: "rgba(29,158,117,0.12)", border: "rgba(29,158,117,0.25)", text: "#1D9E75", dot: "●" },
  pending:    { bg: "rgba(255,255,255,0.04)", border: "rgba(255,255,255,0.08)", text: "var(--text-dim)", dot: "○" },
  "needs-dsn":  { bg: "rgba(239,159,39,0.1)",  border: "rgba(239,159,39,0.25)",  text: "#EF9F27", dot: "◐" },
  "needs-key":  { bg: "rgba(239,159,39,0.1)",  border: "rgba(239,159,39,0.25)",  text: "#EF9F27", dot: "◐" },
};

function FeatureRow({ name, status, note }) {
  const c = STATUS_COLORS[status] || STATUS_COLORS.pending;
  return (
    <div style={{
      background: c.bg, border: `1px solid ${c.border}`,
      borderRadius: 7, padding: "7px 10px",
      display: "flex", alignItems: "center", gap: 8,
    }}>
      <span style={{ fontSize: 10, color: c.text }}>{c.dot}</span>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 12, fontWeight: 500, color: "var(--text)" }}>{name}</div>
        <div style={{ fontSize: 10, color: "var(--text-dim)", marginTop: 1 }}>{note}</div>
      </div>
    </div>
  );
}

function ActionRow({ title, detail, urgent }) {
  return (
    <div style={{
      padding: "9px 12px", borderRadius: 8,
      background: urgent ? "rgba(239,159,39,0.08)" : "rgba(255,255,255,0.03)",
      border: `1px solid ${urgent ? "rgba(239,159,39,0.2)" : "rgba(255,255,255,0.07)"}`,
    }}>
      <div style={{
        fontSize: 12, fontWeight: 500,
        color: urgent ? "#EF9F27" : "var(--text)",
        marginBottom: 3,
      }}>
        {urgent ? "⚡ " : ""}{title}
      </div>
      <div style={{ fontSize: 11, color: "var(--text-dim)", lineHeight: 1.5 }}>{detail}</div>
    </div>
  );
}
