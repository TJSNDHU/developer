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

      {/* ── Cache & refresh (Iter 63) ───────────────────────── */}
      <CachePurgePanel />

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
          <FeatureRow name="Project Brain"          status="live"    note="Per-repo memory + commit log surfacing (Iter 57)" />
          <FeatureRow name="Design Linter + 007"    status="live"    note="25 secret patterns" />
          <FeatureRow name="GitHub Issues context"  status="live"    note="1hr TTL cache" />
          <FeatureRow name="Parallel agents"        status="live"    note="asyncio.gather" />
          <FeatureRow name="Mode A/B/C/D/E/F"       status="live"    note="6 intent modes (F = Engage)" />
          <FeatureRow name="F12 error capture"      status="live"    note="Browser → ORA" />
          <FeatureRow name="Mode D→C real handoff"  status="live"    note="Real cto_tasks row" />
          <FeatureRow name="PAT encryption"         status="live"    note="HKDF-Fernet v1:" />
          <FeatureRow name="Vanguard skill injection" status="live"  note="9 skills (PCI + Privacy), 3 max/task" />
          <FeatureRow name="Rate limiting + bucket cap" status="live" note="30/min chat, OOM-proof (Iter 52)" />
          <FeatureRow name="Free tier cap"          status="live"    note="10 tasks/30d (failed excluded)" />
          <FeatureRow name="SSE task streamer"      status="live"    note="task_handoff frame" />
          <FeatureRow name="ORA council logger"     status="live"    note="A/B only (no D/E pollution)" />
          <FeatureRow name="Daily JSONL export"     status="live"    note="ORA training data" />
          <FeatureRow name="Sentry monitoring"      status="needs-dsn" note="SDK wired, set SENTRY_DSN" />
          <FeatureRow name="GitHub OAuth"           status="needs-key" note="Set GITHUB_OAUTH_CLIENT_ID" />
          <FeatureRow name="Public stats strip"     status="live"    note="Landing page" />
          <FeatureRow name="Ship Wall"              status="live"    note="auremcto.com/wall" />
          <FeatureRow name="ORA Wrapped"            status="live"    note="/wrapped/me + share-to-X" />
          <FeatureRow name="Post-commit wrap-up"    status="live"    note="Auto follow-up (Iter 53)" />
          <FeatureRow name="Tool-call leak fix"     status="live"    note="Synth summary + loop guard (Iter 55)" />
          <FeatureRow name="90s timeout streamer"   status="live"    note="Graceful summary not red error" />
          <FeatureRow name="OAuth runtime origin"   status="live"    note="window.location.origin (Iter 56)" />
          <FeatureRow name="Mandatory tool-use"     status="live"    note="ORA must read_repo_file (Iter 57)" />
          <FeatureRow name="Truncated tree rescue"  status="live"    note="Contents-API walk fallback (Iter 58)" />
          <FeatureRow name="Upload vision OCR"      status="live"    note="Gemini 2.5 + visible pills (Iter 59)" />
          <FeatureRow name="Hosted deploy"          status="live"    note="Vercel/Netlify hooks (Iter 60)" />
          <FeatureRow name="Mode F — Engage"        status="live"    note="Grounded market/copy advice (Iter 60)" />
          <FeatureRow name="VS Code extension"      status="pending" note="Build + publish" />
          <FeatureRow name="SWE-bench score"        status="pending" note="Run benchmark" />
          <FeatureRow name="Per-step task_progress" status="pending" note="Real-time worker tape (replace 2s polling)" />
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
            <MetricCard label="Mode F engage" value={council.mode_f       ?? "—"} />
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
            title="Redeploy production"
            detail="Iter 53–60 saari changes preview pe live hain — production redeploy karke 8 iterations of fixes + features push karo (post-commit wrap-up, Ship Wall, Wrapped, Admin Overview, tool_call leak fix, OAuth origin, repo scan + brain memory, tree-truncation rescue, upload vision OCR, hosted deploy, Mode F Engage)."
          />
          <ActionRow
            urgent
            title="Set production env vars"
            detail="Emergent dashboard → env vars: SENTRY_DSN, GITHUB_OAUTH_CLIENT_ID, GITHUB_OAUTH_CLIENT_SECRET, ALLOWED_ORIGINS=https://auremcto.com,https://www.auremcto.com"
          />
          <ActionRow
            urgent
            title="Create GitHub OAuth App"
            detail="github.com/settings/developers → OAuth Apps → callback: auremcto.com/api/aurem-dev/github/oauth/callback"
          />
          <ActionRow
            title="Connect a deploy hook to your demo project"
            detail="Vercel → Project → Settings → Git → Deploy Hooks → Create → paste URL into AUREM project's 'Connect deploy'. Now 'Ship to Live' works end-to-end."
          />
          <ActionRow
            title="Record 60-second demo video"
            detail="Type a task → AUREM ships → click 'Ship to Live' → URL goes live. One bubble, prompt to production. Post on X."
          />
          <ActionRow
            title="Find 5 beta developers"
            detail="Real repos, no hand-holding. Ask 3 of your 29 signups why they haven't shipped yet."
          />
          <ActionRow
            title="Publish VS Code extension"
            detail="marketplace.visualstudio.com — code is done, needs publisher account + vsce publish"
          />
          <ActionRow
            title="Per-step SSE task_progress frames"
            detail="Replace 2s polling with real-time worker tape — chat bubble updates instantly on each tool call."
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


// ── Iter 63 — Cache & refresh panel ──────────────────────────────────
//
// Real, fully-wired cache buster. Click "Purge & hard-refresh" and:
//   1. Backend purges Cloudflare edge cache (if creds set), drops Mongo
//      TTL caches, clears in-process lru_caches. Returns a structured
//      report we render below.
//   2. Frontend unregisters any service workers, blows away every
//      CacheStorage entry (`caches.delete()`), then forces a true
//      cache-bypass reload via `location.replace(?_=ts)`.
function CachePurgePanel() {
  const [busy,   setBusy]   = useState(false);
  const [report, setReport] = useState(null);
  const [error,  setError]  = useState(null);

  async function purgeEverything() {
    if (busy) return;
    const ok = window.confirm(
      "Purge ALL caches?\n\n" +
      "• Cloudflare edge cache (if CLOUDFLARE_API_TOKEN set)\n" +
      "• Mongo repo/issues/index caches\n" +
      "• In-process LRU caches\n" +
      "• This browser: service workers + CacheStorage + hard reload\n\n" +
      "Every user worldwide will hit the origin once on next request.\n" +
      "Proceed?"
    );
    if (!ok) return;
    setBusy(true);
    setError(null);
    setReport(null);
    try {
      // 1. Backend purge
      const h = { Authorization: `Bearer ${getToken()}` };
      const r = await api.post("/admin/cache/purge", {}, { headers: h });
      setReport(r.data?.report || null);

      // 2. Client-side blast
      try {
        if ("serviceWorker" in navigator) {
          const regs = await navigator.serviceWorker.getRegistrations();
          await Promise.all(regs.map((reg) => reg.unregister()));
        }
      } catch { /* non-fatal */ }
      try {
        if ("caches" in window) {
          const keys = await caches.keys();
          await Promise.all(keys.map((k) => caches.delete(k)));
        }
      } catch { /* non-fatal */ }

      // 3. Give the user 1.5s to read the report, then hard-reload with a
      //    cache-bust query so the browser can't serve a stale doc.
      setTimeout(() => {
        const url = new URL(window.location.href);
        url.searchParams.set("_purge", Date.now().toString(36));
        window.location.replace(url.toString());
      }, 1500);
    } catch (e) {
      setError(e?.response?.data?.detail || e?.message || "Purge failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Section title="Cache & refresh (Iter 63)">
      <div style={{
        background: "rgba(255,138,42,0.04)",
        border: "1px solid rgba(255,138,42,0.18)",
        borderRadius: 8, padding: "14px 16px",
      }}>
        <div style={{
          display: "flex", alignItems: "center",
          justifyContent: "space-between", gap: 12, flexWrap: "wrap",
        }}>
          <div style={{ flex: "1 1 320px", minWidth: 260 }}>
            <div style={{ fontSize: 13, fontWeight: 500, color: "var(--text)", marginBottom: 4 }}>
              Force fresh build for everyone
            </div>
            <div style={{ fontSize: 11, color: "var(--text-dim)", lineHeight: 1.55 }}>
              Cloudflare edge + Mongo TTL caches + in-process LRU +
              THIS browser&apos;s service workers & CacheStorage. Then a
              true cache-bypass reload. Use this when a deploy looks stale.
            </div>
          </div>
          <button
            data-testid="admin-cache-purge-btn"
            onClick={purgeEverything}
            disabled={busy}
            className="btn-primary"
            style={{
              padding: "10px 18px", fontSize: 12, fontWeight: 600,
              fontFamily: "'JetBrains Mono', monospace",
              letterSpacing: "0.05em", whiteSpace: "nowrap",
              opacity: busy ? 0.6 : 1,
              cursor: busy ? "wait" : "pointer",
            }}
          >
            {busy ? "Purging…" : "🧹 Purge & hard-refresh"}
          </button>
        </div>

        {error && (
          <div data-testid="admin-cache-purge-error" style={{
            marginTop: 12, padding: "8px 10px",
            background: "rgba(226,75,74,0.1)",
            border: "1px solid rgba(226,75,74,0.3)",
            borderRadius: 6, fontSize: 11, color: "#E24B4A",
          }}>
            {error}
          </div>
        )}

        {report && (
          <div data-testid="admin-cache-purge-report" style={{
            marginTop: 14, fontSize: 11,
            fontFamily: "'JetBrains Mono', monospace",
            color: "var(--text-dim)",
          }}>
            <ReportLine
              label="Cloudflare edge"
              status={report.cloudflare?.status}
              detail={report.cloudflare?.detail}
            />
            <ReportLine
              label="In-process LRU"
              status={report.lru_cache?.status}
              detail={report.lru_cache?.detail}
            />
            {report.mongo_caches && typeof report.mongo_caches === "object" &&
              Object.entries(report.mongo_caches).map(([coll, info]) => (
                info && typeof info === "object" ? (
                  <ReportLine
                    key={coll}
                    label={`Mongo · ${coll}`}
                    status={info.status}
                    detail={info.deleted != null
                      ? `${info.deleted} docs deleted`
                      : (info.detail || "")}
                  />
                ) : null
              ))
            }
            <div style={{
              marginTop: 8, color: "var(--accent-2)",
              fontStyle: "italic", fontSize: 10,
            }}>
              ↻ Reloading this tab in 1.5s with cache bypass…
            </div>
          </div>
        )}
      </div>
    </Section>
  );
}

function ReportLine({ label, status, detail }) {
  const colorMap = {
    ok:      "#1D9E75",
    error:   "#E24B4A",
    skipped: "var(--text-faint)",
  };
  const symbol = { ok: "✓", error: "✗", skipped: "·" }[status] || "·";
  return (
    <div style={{
      display: "flex", gap: 8, padding: "3px 0",
      alignItems: "baseline",
    }}>
      <span style={{ color: colorMap[status] || "var(--text-faint)", width: 12 }}>
        {symbol}
      </span>
      <span style={{ color: "var(--text)", minWidth: 160 }}>{label}</span>
      <span style={{ color: "var(--text-faint)", flex: 1 }}>
        {detail || status}
      </span>
    </div>
  );
}
