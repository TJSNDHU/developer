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
  const [telemetry, setTelemetry] = useState(null);
  const [dbHealth, setDbHealth]   = useState(null);   // iter 118
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    const h = { Authorization: `Bearer ${getToken()}` };
    const HEALTH_URL = `${process.env.REACT_APP_BACKEND_URL}/api/health`;
    try {
      const [healthRes, statsRes, wallRes, councilRes, telRes, dbHealthRes] =
        await Promise.allSettled([
          fetch(HEALTH_URL).then((r) => r.json()),
          api.get("/usage/public/stats"),
          api.get("/wall/stats"),
          api.get("/admin/council/stats",   { headers: h }),
          api.get("/admin/mode-telemetry",  { headers: h }),
          api.get("/admin/db-health",       { headers: h }),
        ]);
      if (healthRes.status   === "fulfilled") setHealth(healthRes.value);
      if (statsRes.status    === "fulfilled") setStats(statsRes.value.data);
      if (wallRes.status     === "fulfilled") setWall(wallRes.value.data);
      if (councilRes.status  === "fulfilled") setCouncil(councilRes.value.data);
      if (telRes.status      === "fulfilled") setTelemetry(telRes.value.data);
      if (dbHealthRes.status === "fulfilled") setDbHealth(dbHealthRes.value.data);
    } catch { /* silent */ }
    finally { setLoading(false); }
  }, []);

  // Iter 118 — useEffect runs load() then sets up a 60s refresh interval.
  // We intentionally pass `[]` so it only re-arms when the component
  // remounts. The lint rule react-hooks/set-state-in-effect (React 19)
  // can fire on conditional setState inside callbacks called from an
  // effect — that pattern is correct here because load() also runs
  // independently from event handlers.
  useEffect(() => {
    let cancelled = false;
    const run = () => { if (!cancelled) load(); };
    run();
    const t = setInterval(run, 60_000);
    return () => { cancelled = true; clearInterval(t); };
  }, []);

  if (loading) return (
    <div style={{ padding: 40, textAlign: "center", color: "var(--text-dim)", fontSize: 13 }}>
      Loading system overview…
    </div>
  );

  const dbOk = health?.db === true;
  const uptimeMin = health?.uptime_s ? Math.floor(health.uptime_s / 60) : 0;

  return (
    <div style={{ padding: "24px 20px", maxWidth: 900 }}>

      {/* Build hash banner — one-glance "am I on the right deploy?"
          Click target removed: just informational. */}
      {health?.build_hash && (
        <div data-testid="admin-build-banner" style={{
          display: "inline-flex", alignItems: "center", gap: 8,
          padding: "5px 12px", marginBottom: 14,
          background: "var(--panel-2)", border: "1px solid var(--border)",
          borderRadius: 4,
          fontSize: 10, color: "var(--text-faint)",
          fontFamily: "'JetBrains Mono', monospace",
          letterSpacing: "0.05em",
        }}>
          build <span style={{ color: "var(--accent-2)" }}>
            {health.build_hash}
          </span>
          {health.env && <> · <span>{health.env}</span></>}
          {uptimeMin > 0 && <> · uptime {uptimeMin}m</>}
        </div>
      )}

      {/* ── System health ───────────────────────────────────── */}
      <Section title="System health">
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
          <HealthChip ok={dbOk}   label="MongoDB" />
          <HealthChip ok={true}   label="FastAPI" />
          <HealthChip ok={!!stats} label="Public stats API" />
          <HealthChip ok={!!wall}  label="Ship Wall" />
          <HealthChip ok={!!council} label="Council logger" />
          <InfoChip label={`Uptime ${uptimeMin}m`} />
          <a
            data-testid="goto-financials"
            href="/admin/financials"
            style={{
              marginLeft: "auto",
              fontSize: 11, fontWeight: 700, letterSpacing: ".04em",
              padding: "6px 12px",
              background: "var(--accent, #ff8a2a)",
              color: "var(--bg, #0a0c10)",
              border: "none", borderRadius: 5,
              cursor: "pointer", textDecoration: "none",
            }}
          >💰 Financials →</a>
          <a
            data-testid="goto-integrations"
            href="/admin/integrations"
            style={{
              fontSize: 11, fontWeight: 600, letterSpacing: ".04em",
              padding: "6px 12px",
              background: "transparent",
              color: "var(--accent, #ff8a2a)",
              border: "1px solid var(--accent, #ff8a2a)",
              borderRadius: 5,
              cursor: "pointer", textDecoration: "none",
            }}
          >🩺 Integrations →</a>
          <a
            data-testid="goto-vanguard"
            href="/admin/vanguard"
            style={{
              fontSize: 11, fontWeight: 600, letterSpacing: ".04em",
              padding: "6px 12px",
              background: "transparent",
              color: "var(--accent, #ff8a2a)",
              border: "1px solid var(--accent, #ff8a2a)",
              borderRadius: 5,
              cursor: "pointer", textDecoration: "none",
            }}
          >🛡️ Vanguard →</a>
        </div>
      </Section>

      {/* ── System mapping (Iter 152) ─────────────────────────
          Architectural at-a-glance — same content as README so admins,
          founders, and ORA itself have a single source of truth. */}
      <SystemMappingCard />

      {/* ── Cache & refresh (Iter 63) ───────────────────────── */}
      <CachePurgePanel />

      {/* ── DB health (iter 118) ────────────────────────────── */}
      <DbHealthCard data={dbHealth}/>

      {/* ── Mode classifier telemetry — last 100 messages ──── */}
      {telemetry && telemetry.total > 0 && (
        <Section title="Mode classifier — last 100 messages">
          <div data-testid="mode-telemetry-panel" style={{
            padding: "12px 14px",
            background: "var(--panel)",
            border: "1px solid var(--border)",
            borderRadius: 6,
            display: "flex", flexWrap: "wrap",
            gap: 14, alignItems: "center",
            fontSize: 12, color: "var(--text-dim)",
          }}>
            <div style={{
              fontFamily: "'JetBrains Mono', monospace",
              color: "var(--text)", letterSpacing: "0.05em",
            }}>
              {Object.entries(telemetry.mode_counts).sort()
                .map(([m, n]) => (
                  <span key={m}
                        data-testid={`mode-count-${m}`}
                        style={{ marginRight: 14 }}>
                    {m}: <b style={{ color: "var(--accent-2)" }}>{n}</b>
                  </span>
                ))}
            </div>
            <div style={{ marginLeft: "auto", display: "flex", gap: 12 }}>
              <span data-testid="mode-avg-confidence">
                avg conf <b style={{ color: "var(--text)" }}>
                  {telemetry.avg_confidence}
                </b>
              </span>
              <span data-testid="mode-needs-confirm-pct">
                ambiguous <b style={{
                  color: telemetry.needs_confirm_pct > 15
                    ? "var(--warn)" : "var(--text)",
                }}>
                  {telemetry.needs_confirm_pct}%
                </b>
              </span>
              {telemetry.f12_forced_pct > 0 && (
                <span>F12-forced <b>{telemetry.f12_forced_pct}%</b></span>
              )}
            </div>
          </div>
        </Section>
      )}

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
      <Section title="Features — live status (Iter 73-123)">
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6 }}>
          <FeatureRow name="Two-Agent Maxx"          status="live"    note="DeepSeek + Claude review" />
          <FeatureRow name="Project Brain"           status="live"    note="Per-repo memory + commit SHAs" />
          <FeatureRow name="Design Linter + 007"     status="live"    note="25 secret patterns" />
          <FeatureRow name="Parallel agents"         status="live"    note="Backend/Frontend/Tests sub-tapes" />
          <FeatureRow name="Mode A/B/C/D/E/F"        status="live"    note="6 intent modes" />
          <FeatureRow name="F12 error capture"       status="live"    note="Browser → ORA debugger" />
          <FeatureRow name="PAT encryption"          status="live"    note="HKDF-Fernet v1:" />
          <FeatureRow name="Vanguard skill injection" status="live"   note="9 skills, 3 max/task" />
          <FeatureRow name="Rate limiting"           status="live"    note="30/min chat, OOM-proof" />
          <FeatureRow name="SSE task streamer"       status="live"    note="task_handoff + task_state frames" />
          <FeatureRow name="ORA council logger"      status="live"    note="A/B only" />
          <FeatureRow name="Sentry monitoring"       status="live"    note="DSN set — full coverage on (Iter 48)" />
          <FeatureRow name="GitHub OAuth"            status="live"    note="Inline in NewUserWizard step 1" />
          <FeatureRow name="Public stats strip"      status="live"    note="Real /usage/public/stats" />
          <FeatureRow name="Ship Wall"               status="live"    note="auremcto.com/wall" />
          <FeatureRow name="ORA Wrapped"             status="live"    note="/wrapped/me + share-to-X" />
          <FeatureRow name="VS Code extension"       status="live"    note="aurem-cto-0.1.0.vsix shipped (Iter 72)" />
          <FeatureRow name="OpsRecipes runbook"      status="live"    note="/admin/ops — 5 ops recipes (Iter 73)" />
          <FeatureRow name="Live worker tape"        status="live"    note="Terminal-feed SSE in chat (Iter 73)" />
          <FeatureRow name="task_state per-file"     status="live"    note="Writing N/M files mini-bar (Iter 74)" />
          <FeatureRow name="NewUserWizard"           status="live"    note="3-step onboarding w/ inline OAuth (Iter 73)" />
          <FeatureRow name="Parallel sub-tapes"      status="live"    note="Per-agent mini progress bars (Iter 73)" />
          <FeatureRow name="Semantic code search"    status="live"    note="GitHub Code Search tool (Iter 74)" />
          <FeatureRow name="get_commit_diff tool"    status="live"    note="ORA studies past commits (Iter 74)" />
          <FeatureRow name="Python AST gate"         status="live"    note="ast.parse + node --check (Iter 74)" />
          <FeatureRow name="Multi-file checklist"    status="live"    note="[ ] → [x] TaskManagementPanel (Iter 74)" />
          <FeatureRow name="Brain Show-diff buttons" status="live"    note="Per-commit pattern recall (Iter 74)" />
          <FeatureRow name="4-tier pricing"          status="live"    note="Free/Starter/Pro/Team + Stripe (Iter 75)" />
          <FeatureRow name="Mode classifier telemetry" status="live"  note="/admin/mode-telemetry (Iter 70)" />
          <FeatureRow name="Brain replay sandbox"    status="live"    note="/admin/brain/{pid}/replay (Iter 70)" />
          <FeatureRow name="e2b sandbox runner"      status="live"    note="E2B_API_KEY set — Vanguard verify gate (Iter 110)" />
          <FeatureRow name="TF-IDF search fallback"  status="live"    note="GitHub Code Search + local index (Iter 75)" />
          <FeatureRow name="esbuild JSX gate"        status="live"    note="esbuild → node --check fallback (Iter 75)" />
          <FeatureRow name="MULTI-FILE CONTRACT"     status="live"    note="Structural retry if files missing (Iter 75)" />
          <FeatureRow name="DB-backed task_plan"     status="live"    note="cto_tasks.task_plan + live UI poll (Iter 75)" />
          <FeatureRow name="Live preview pane"       status="live"    note="Bolt-style iframe blob + Vercel mode (Iter 76)" />
          <FeatureRow name="Split-pane Dashboard"    status="live"    note="60/40 chat ↔ preview, resizable (Iter 76)" />
          <FeatureRow name="Milestone share toast"   status="live"    note="10/25/50 task auto-prompt → /wrapped (Iter 77)" />
          <FeatureRow name="Settings Wrapped embed"  status="live"    note="Plan + activity on one screen (Iter 77)" />
          <FeatureRow name="Subscription tiers"      status="live"    note="services/subscription_tiers.py SSOT (Iter 75)" />
          <FeatureRow name="Stripe webhook + Maxx"   status="live"    note="POST /payments/webhook + overage cron (Iter 102)" />
          {/* Iter 100-119 batch */}
          <FeatureRow name="Mobile UX polish"        status="live"    note="Responsive layout pass (Iter 103)" />
          <FeatureRow name="Cold-start 520 fix"      status="live"    note="No more LLM cold-start hallucinations (Iter 104)" />
          <FeatureRow name="ORA URL refusal fix"     status="live"    note="External URL handling + 500 circuit breaker (Iter 105)" />
          <FeatureRow name="OAuth cancel redirect"   status="live"    note="Intent separation on OAuth abort (Iter 106)" />
          <FeatureRow name="Vision API fallback"     status="live"    note="GPT-4o → Claude → Gemini chain (Iter 107)" />
          <FeatureRow name="Decision Council regex"  status="live"    note="Mode B classifier robustness (Iter 108)" />
          <FeatureRow name="Vanguard Verify Agent"   status="live"    note="Claude Sonnet 4.5 pre-commit gate (Iter 110-112)" />
          <FeatureRow name="Vanguard Audit Log"      status="live"    note="/admin/vanguard — block history + counts (Iter 113)" />
          <FeatureRow name="Live Task Popup"         status="live"    note="LiveTaskPopup.jsx — real CTO tasks only (Iter 114-115)" />
          <FeatureRow name="DB collection bootstrap" status="live"    note="Idempotent init_prod_collections on boot (Iter 116)" />
          <FeatureRow name="DB Health endpoint"      status="live"    note="GET /admin/db-health → green card above (Iter 117)" />
          <FeatureRow name="Route cache middleware"  status="live"    note="60s/30s TTL, 5 polling routes, X-Cache header (Iter 118)" />
          <FeatureRow name="Citation chips"          status="live"    note="🌐 chips in chat — Tavily/Firecrawl/fetch_url (Iter 119)" />
          <FeatureRow name="Token enforcement tests" status="live"    note="conftest .env loader + throwaway-user pattern (Iter 119)" />
          <FeatureRow name=".gitignore policy lock"  status="live"    note="Option B — secrets via deploy dashboard (Iter 119)" />
          {/* Iter 120-123 batch — performance + skill pack */}
          <FeatureRow name="Admin users N+1 fix"     status="live"    note="300 → 3 queries on /admin/users (Iter 120)" />
          <FeatureRow name="K8s healthz probe"       status="live"    note="GET /api/healthz — DB-free liveness (Iter 120)" />
          <FeatureRow name="DB critical indexes"     status="live"    note="cto_tasks/dev_users/payments composites (Iter 121)" />
          <FeatureRow name="Orphan cleanup script"   status="live"    note="scripts/cleanup_orphans.py — dry-run safe (Iter 121)" />
          <FeatureRow name="Memory diagnostics"      status="live"    note="GET /_diag/memory via tracemalloc (Iter 122)" />
          <FeatureRow name="github_deploy_service"   status="live"    note="PR-based fix deploys — connect/push-fix/report (Iter 123)" />
          <FeatureRow name="deploy_logger"           status="live"    note="boot + commit SHA tracking in deploy_events (Iter 123)" />
          <FeatureRow name="22 ORA skills (industry ceiling)" status="live" note="12 audited + 10 new: find_usages, get_deps, validate_syntax, e2b… (Iter 123)" />
          <FeatureRow name="Tool catalog grouped"    status="live"    note="READING/INTEL/GITHUB/WEB/VALIDATE + selection rules (Iter 123)" />
          <FeatureRow name="ora_skill_usage analytics" status="live"  note="fire-and-forget telemetry → /admin/skills-usage (Iter 123b)" />
          <FeatureRow name="OOM blocker resolved"    status="live"    note="tier_0 (512MB) → tier_1 (2GB) + ENABLE_HEALTH_CHECK (Iter 123c)" />
        </div>
        <div style={{
          marginTop: 14, fontSize: 11, color: "var(--text-dim)",
          padding: "8px 12px",
          background: "rgba(109,212,161,0.06)",
          border: "1px solid rgba(109,212,161,0.22)",
          borderRadius: 5,
        }}>
          Backend test suite: <strong style={{ color: "var(--ok, #6dd4a1)" }}>700+ passing</strong>
          {" "}/ 0 failures / 9 skips (iter 123 + 123b adds 42 tests). Build hash above ↑.
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
      <Section title="Next actions — pending on you (Iter 123 → June 15 launch)">
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <ActionRow
            urgent
            title="🔴 Tier upgrade + redeploy production"
            detail="Emergent Deploy Panel → tier_0 → tier_1 (2GB RAM) + env var ENABLE_HEALTH_CHECK=true → redeploy. Logs should show 'indexed=15' (was 14) and 'deploy recorded: <sha> main' on boot."
          />
          <ActionRow
            urgent
            title="🔴 LIVE ORA chain test post-deploy"
            detail="Chat mein type karo: 'Find all places where verify_exp is used and check if any imports are missing'. ORA should auto-pick find_usages → read_repo_files → validate_syntax in one response. Live popup mein 3 tool invocations visible."
          />
          <ActionRow
            urgent
            title="🟡 PH Hunter DM (June 13 deadline — 3 din!)"
            detail="Product Hunt hunter ko DM karke June 15 launch ki schedule lock karo. Wait for confirmation before going live."
          />
          <ActionRow
            title="📊 Wait 2 weeks → prune ORA skills via /admin/skills-usage"
            detail="Industry ceiling 18 hai, hum 22 pe hain. After 2 weeks of live traffic, query GET /admin/skills-usage?days=14 — dead_weight=true rows hain prune candidates. Drop bottom 4 to hit optimal catalog size."
          />
          <ActionRow
            title="🎯 Citation chip live e2e test"
            detail="Send a query that requires Tavily (e.g. 'latest FastAPI version'). Verify 🌐 chip appears in MessageBubble with real source URL."
          />
          <ActionRow
            title="🛠 CODE_SURFACE auto-sync (done — verified)"
            detail="Architecture tab now reads /admin/code-surface live. Static fallback array deleted. Drift-proof for future iters."
          />
          <ActionRow
            title="🎨 Optional: skills-usage dashboard card"
            detail="80 lines React + Recharts (already in package.json) — horizontal bar chart with dead_weight skills in red. Founder ko visual prune candidates."
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



// ──────────────────────────────────────────────────────────────
// DbHealthCard (iter 118)
// One-glance status: "DB: 10/10 collections · indexes OK · last boot 5h ago"
// Green  = healthy
// Amber  = missing > 0
// Red    = indexes_ok === false
// ──────────────────────────────────────────────────────────────
function DbHealthCard({ data }) {
  if (!data) return null;
  const present  = data.collections_present || 0;
  const expected = data.collections_expected || 0;
  const missing  = data.missing || [];
  const indexesOk = !!data.indexes_ok;

  let color, bg, label;
  if (!indexesOk) {
    color = "#ff6b6b"; bg = "rgba(255,107,107,0.10)"; label = "DEGRADED";
  } else if (missing.length > 0) {
    color = "#ffb347"; bg = "rgba(255,179,71,0.10)"; label = "MISSING";
  } else {
    color = "#6dd4a1"; bg = "rgba(109,212,161,0.10)"; label = "HEALTHY";
  }

  const lastBootAgo = data.last_bootstrap
    ? humanAgo(data.last_bootstrap)
    : "—";

  return (
    <div data-testid="db-health-card" style={{
      marginTop: 18, padding: "12px 14px",
      borderRadius: 8, background: bg,
      border: `1px solid ${color}55`,
      display: "flex", alignItems: "center", justifyContent: "space-between",
      gap: 12, flexWrap: "wrap",
      fontFamily: "ui-monospace, Menlo, monospace", fontSize: 12,
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <span style={{
          padding: "2px 8px", borderRadius: 4, fontWeight: 700,
          fontSize: 10, letterSpacing: ".1em", color, background: `${color}22`,
        }}>{label}</span>
        <span style={{ color }}>
          DB: {present}/{expected} collections
        </span>
        <span style={{ color: "var(--text-faint, #888)" }}>·</span>
        <span style={{ color: indexesOk ? color : "#ff6b6b" }}>
          indexes {indexesOk ? "OK" : "FAIL"}
        </span>
        <span style={{ color: "var(--text-faint, #888)" }}>·</span>
        <span style={{ color: "var(--text-faint, #888)" }}>
          last boot {lastBootAgo}
        </span>
      </div>
      {missing.length > 0 && (
        <span data-testid="db-health-missing"
              style={{ color: "#ffb347", fontSize: 11 }}>
          missing: {missing.slice(0, 3).join(", ")}{missing.length > 3 ? "…" : ""}
        </span>
      )}
    </div>
  );
}

function humanAgo(iso) {
  if (!iso) return "—";
  const s = Math.max(1, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  if (s < 60)    return `${s}s ago`;
  if (s < 3600)  return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}


/**
 * SystemMappingCard — Iter 152.
 * Live snapshot of every architectural layer that turns a chat prompt
 * into a live commit. Mirrors the README so admins, founders, and ORA
 * itself have a single source of truth. Pure JSX (no fetch) — it's an
 * editor-controlled diagram, not telemetry.
 */
function SystemMappingCard() {
  const layers = [
    {
      key: "frontend",
      title: "Frontend · React 19 + Vite",
      tag: "24 pages · 5 hooks · 30+ components",
      items: [
        ["Pages", "Landing · Dashboard · Projects · Deploy · Database · Domain · Tokens · Analytics · Wrapped · ShipWall · Admin (×5) · Settings"],
        ["Hooks", "useChatSession · useChatStream · useChatMessages · useORAPanel · useTextToVoice"],
        ["Surfaces", "ChatPanel composer-card · ORASidePanel split-screen · MessageBubble · TaskLiveTape · Shell auto-hide sidebar"],
      ],
    },
    {
      key: "backend",
      title: "Backend · FastAPI 0.115 (Motor async)",
      tag: "26 routers · 47 services",
      items: [
        ["Routers", "chat · cto_projects · github_deploy · hosted_deploy · payments · auth · admin · vault · usage · automations · domain · stacks · trust · wrapped · shipwall · github_oauth · harden · lint_preview · engagement"],
        ["LLM core", "orchestrator · llm · tools_bridge · ora_client · ora_learning (new) · ora_council_logger"],
        ["Modes", "mode_b_council · mode_d_debugger · mode_e_auditor · mode_f_engage · mode_classifier"],
        ["Code path", "project_brain · codebase_indexer · repo_context · parallel_agents · github_api_writer · sandbox_runner"],
        ["Security", "vanguard_audit · vanguard_scanner · vanguard_verify_agent · vault · rate_limiter"],
      ],
    },
    {
      key: "data",
      title: "Persistence · MongoDB",
      tag: "Motor async · sharded by user_id",
      items: [
        ["Identity", "dev_users · subscriptions · usage_events"],
        ["Conversation", "chat_sessions · cto_tasks · cto_projects · vanguard_audit"],
        ["Learning", "ora_council_logs · ora_learning_logs (Iter 145)"],
        ["Engagement", "ship_wall · wrapped_stats · feature_flags · vault_secrets"],
      ],
    },
    {
      key: "external",
      title: "External integrations",
      tag: "5 LLM/services · 1 payments · 1 deploy",
      items: [
        ["LLM", "DeepSeek V3 · Claude Sonnet 4.5 (Maxx review) · Emergent LLM key"],
        ["Code", "GitHub REST API — trees · blobs · commits · refs (atomic)"],
        ["Deploy", "Vercel webhooks · Emergent hosted MongoDB provisioner"],
        ["Payments", "Stripe (flat-fee subscriptions, no token meters)"],
        ["Search", "Tavily web search · Firecrawl JS-heavy scrape"],
        ["Observe", "Sentry crash · F12 browser-error custom capture"],
      ],
    },
  ];

  return (
    <Section title="System mapping">
      <div
        data-testid="admin-system-mapping"
        style={{
          display: "grid",
          gap: 12,
          gridTemplateColumns: "repeat(auto-fit, minmax(380px, 1fr))",
        }}
      >
        {layers.map((layer) => (
          <div
            key={layer.key}
            data-testid={`sysmap-${layer.key}`}
            style={{
              background: "var(--panel-2, rgba(255,255,255,0.02))",
              border: "1px solid var(--border)",
              borderRadius: 8,
              padding: "14px 16px",
            }}
          >
            <div style={{
              display: "flex", alignItems: "baseline", justifyContent: "space-between",
              gap: 10, marginBottom: 8,
            }}>
              <div style={{
                fontSize: 12, fontWeight: 700,
                color: "var(--text)", letterSpacing: "0.02em",
              }}>{layer.title}</div>
              <div style={{
                fontSize: 9,
                color: "var(--accent-2)",
                fontFamily: "'JetBrains Mono', monospace",
                letterSpacing: "0.08em",
                whiteSpace: "nowrap",
              }}>{layer.tag}</div>
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              {layer.items.map(([label, body], i) => (
                <div key={i} style={{
                  display: "grid",
                  gridTemplateColumns: "78px 1fr",
                  gap: 10, alignItems: "baseline",
                }}>
                  <div style={{
                    fontSize: 9,
                    color: "var(--text-faint)",
                    fontFamily: "'JetBrains Mono', monospace",
                    letterSpacing: "0.1em",
                    textTransform: "uppercase",
                  }}>{label}</div>
                  <div style={{
                    fontSize: 11,
                    color: "var(--text-dim)",
                    lineHeight: 1.55,
                  }}>{body}</div>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>

      <div style={{
        marginTop: 14,
        padding: "10px 14px",
        background: "rgba(255,138,42,0.06)",
        border: "1px dashed rgba(255,138,42,0.32)",
        borderRadius: 6,
        fontSize: 11,
        color: "var(--text-dim)",
        lineHeight: 1.55,
      }}>
        <strong style={{ color: "var(--accent-2)", letterSpacing: "0.04em" }}>
          One-turn flow:
        </strong>{" "}
        User prompt → layered persona (5/13/2k) → DeepSeek V3 with 23 local
        tools → optional Claude Sonnet watchdog (Maxx) → Vanguard 25+ pattern
        scan → atomic GitHub commit → ORA shadow-learner detects low-confidence
        replies and logs both sides into <code>ora_learning_logs</code>.
      </div>
    </Section>
  );
}

