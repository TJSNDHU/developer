/**
 * pages/admin/PersonalTrackAdmin.jsx — Iter 212m-240
 *
 * Consolidated admin dashboard for Personal Track (non-technical users).
 * Single-pane-of-glass for:
 *   - Draft status counts (draft / materialized / blocked_by_scan)
 *   - Blocked-by-scan drafts + founder-override CTA
 *   - Materialized Personal Track projects (repo, live URL, DB tier)
 *   - Pending Supabase downgrades + escalated rows (with rearm CTA)
 *   - LLM Parliament health probe
 *   - dev_users created_at type-drift health
 *
 * Founder / admin only route: /admin/personal-track
 */
import React, { useEffect, useState, useCallback } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  ArrowLeft, RefreshCw, ShieldAlert, ShieldCheck, Package,
  Database, Wand2, Cpu, ExternalLink, AlertTriangle, CheckCircle2,
  Clock, Loader2, GitBranch,
} from "lucide-react";
import { api } from "../../lib/api";
import { toast } from "../../components/Toast";

const cardBase =
  "rounded-2xl bg-white/[0.03] border border-white/10 backdrop-blur-xl " +
  "p-5 shadow-[0_1px_0_rgba(255,255,255,0.06)_inset]";

function StatTile({ icon: Icon, label, value, sub, tone = "default", testid }) {
  const tones = {
    default: "text-white/90",
    good:    "text-emerald-300",
    warn:    "text-amber-300",
    bad:     "text-rose-300",
  };
  return (
    <div className={cardBase} data-testid={testid}>
      <div className="flex items-center gap-2 text-white/60 text-xs uppercase tracking-wider">
        <Icon size={14} />
        <span>{label}</span>
      </div>
      <div className={`mt-2 text-3xl font-semibold ${tones[tone]}`}>{value}</div>
      {sub && <div className="mt-1 text-xs text-white/50">{sub}</div>}
    </div>
  );
}

function SectionHeader({ icon: Icon, title, right }) {
  return (
    <div className="flex items-center justify-between mb-3">
      <h2 className="flex items-center gap-2 text-lg font-medium text-white/90">
        <Icon size={18} className="text-white/60" />
        {title}
      </h2>
      {right}
    </div>
  );
}

export default function PersonalTrackAdmin() {
  const nav = useNavigate();
  const [loading,        setLoading]        = useState(true);
  const [refreshing,     setRefreshing]     = useState(false);
  const [summary,        setSummary]        = useState(null);
  const [health,         setHealth]         = useState(null);
  const [blocked,        setBlocked]        = useState([]);
  const [projects,       setProjects]       = useState([]);
  const [downgrades,     setDowngrades]     = useState(null);
  const [llmHealth,      setLlmHealth]      = useState(null);

  const load = useCallback(async () => {
    setRefreshing(true);
    try {
      const [sumRes, healthRes, blockedRes, projRes, dgRes, llmRes] =
        await Promise.allSettled([
          api.get("/scaffold/admin/draft-summary"),
          api.get("/admin/dev-users/created-at-health"),
          api.get("/scaffold/admin/blocked-drafts"),
          api.get("/scaffold/admin/personal-projects"),
          api.get("/supabase/admin/pending-downgrades"),
          api.get("/scaffold/admin/llm-health"),
        ]);
      if (sumRes.status     === "fulfilled") setSummary(sumRes.value.data);
      if (healthRes.status  === "fulfilled") setHealth(healthRes.value.data);
      if (blockedRes.status === "fulfilled") setBlocked(blockedRes.value.data?.rows || []);
      if (projRes.status    === "fulfilled") setProjects(projRes.value.data?.rows || []);
      if (dgRes.status      === "fulfilled") setDowngrades(dgRes.value.data);
      if (llmRes.status     === "fulfilled") setLlmHealth(llmRes.value.data);
    } catch (e) {
      toast.error("Load failed");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const override = async (draftId) => {
    const reason = window.prompt(
      "Enter a bypass reason (min 8 chars) — this is audit-logged:",
    );
    if (!reason || reason.trim().length < 8) return;
    try {
      await api.post(`/scaffold/${draftId}/founder-override`, { reason });
      toast.success("Override applied. Draft can now be materialized.");
      load();
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Override failed");
    }
  };

  const rearm = async (appId) => {
    try {
      await api.post(`/supabase/admin/rearm/${appId}`);
      toast.success("Row rearmed. Sweeper will pick it up on next run.");
      load();
    } catch (e) {
      toast.error("Rearm failed");
    }
  };

  const sweepNow = async () => {
    try {
      const res = await api.post("/supabase/admin/sweep-now");
      toast.success(`Sweep complete: ${JSON.stringify(res.data).slice(0, 100)}`);
      load();
    } catch (e) {
      toast.error("Sweep failed");
    }
  };

  // ── Derived stats ────────────────────────────────────────────
  const s = summary?.drafts_by_status || {};
  const dbHealthy = health?.healthy === true;
  const llmReachable = llmHealth?.llm_reachable === true;
  const escalatedCount = (downgrades?.escalated || []).length;
  const dgTotal = downgrades?.count ?? 0;

  if (loading) {
    return (
      <div className="min-h-screen bg-[#0b0d10] flex items-center justify-center">
        <Loader2 className="animate-spin text-white/60" size={32} />
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gradient-to-b from-[#0a0c0f] via-[#0b0d10] to-[#0a0c0f] text-white/90">
      <div className="max-w-7xl mx-auto px-6 py-8">
        {/* Header */}
        <div className="flex items-center justify-between mb-8">
          <div className="flex items-center gap-3">
            <button
              onClick={() => nav("/admin")}
              className="text-white/60 hover:text-white/90 transition"
              data-testid="pt-admin-back"
            >
              <ArrowLeft size={18} />
            </button>
            <div>
              <div className="text-xs text-white/40 uppercase tracking-wider">
                Admin
              </div>
              <h1 className="text-2xl font-semibold">Personal Track Ops</h1>
            </div>
          </div>
          <button
            onClick={load}
            disabled={refreshing}
            className="flex items-center gap-2 px-4 py-2 rounded-full bg-white/5 border border-white/10 hover:bg-white/10 transition text-sm"
            data-testid="pt-admin-refresh"
          >
            <RefreshCw size={14} className={refreshing ? "animate-spin" : ""} />
            Refresh
          </button>
        </div>

        {/* Headline tiles */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8">
          <StatTile
            icon={Wand2}
            label="Drafts"
            value={s.draft || 0}
            sub="active"
            testid="pt-tile-drafts"
          />
          <StatTile
            icon={Package}
            label="Materialized"
            value={summary?.personal_projects || 0}
            sub={`${s.materialized || 0} drafts shipped`}
            testid="pt-tile-materialized"
          />
          <StatTile
            icon={ShieldAlert}
            label="Blocked by scan"
            value={s.blocked_by_scan || 0}
            sub="needs override"
            tone={(s.blocked_by_scan || 0) > 0 ? "warn" : "default"}
            testid="pt-tile-blocked"
          />
          <StatTile
            icon={Database}
            label="Pending downgrades"
            value={dgTotal}
            sub={escalatedCount > 0 ? `${escalatedCount} escalated` : "healthy"}
            tone={escalatedCount > 0 ? "bad" : (dgTotal > 0 ? "warn" : "good")}
            testid="pt-tile-downgrades"
          />
        </div>

        {/* Second row: infra health */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-10">
          <div className={cardBase} data-testid="pt-health-dev-users">
            <SectionHeader
              icon={dbHealthy ? ShieldCheck : ShieldAlert}
              title="dev_users.created_at health"
            />
            <div className="flex items-baseline gap-3">
              <span className={`text-2xl font-semibold ${dbHealthy ? "text-emerald-300" : "text-rose-300"}`}>
                {dbHealthy ? "healthy" : "needs fix"}
              </span>
              <span className="text-xs text-white/50">
                {health?.total_users || 0} users • {health?.datetime_typed || 0} legacy • {health?.missing_field || 0} missing
              </span>
            </div>
            <div className="mt-3 text-xs text-white/40">
              Types: {Object.entries(health?.by_type || {}).map(([k, v]) => `${k}=${v}`).join(", ") || "—"}
            </div>
          </div>

          <div className={cardBase} data-testid="pt-health-llm">
            <SectionHeader
              icon={Cpu}
              title="Parliament LLM (scaffold canary)"
            />
            <div className="flex items-baseline gap-3">
              <span className={`text-2xl font-semibold ${llmReachable ? "text-emerald-300" : "text-amber-300"}`}>
                {llmReachable ? "reachable" : (llmHealth?.fallback ? "fallback active" : "unavailable")}
              </span>
              <span className="text-xs text-white/50">
                {llmHealth?.file_count || 0} files • {llmHealth?.elapsed_ms || 0}ms
              </span>
            </div>
            <div className="mt-3 text-xs text-white/40">
              {llmReachable
                ? "Real customised generation is firing."
                : "Generated apps use the heuristic boilerplate (still runnable)."}
            </div>
          </div>
        </div>

        {/* Blocked drafts */}
        <section className="mb-10">
          <SectionHeader
            icon={ShieldAlert}
            title={`Blocked by security gate (${blocked.length})`}
          />
          {blocked.length === 0 ? (
            <div className={`${cardBase} text-sm text-white/50 flex items-center gap-2`}>
              <CheckCircle2 size={16} className="text-emerald-400" />
              No blocked drafts. Security gate is clean.
            </div>
          ) : (
            <div className={cardBase}>
              <table className="w-full text-sm">
                <thead className="text-white/50 text-xs uppercase tracking-wider">
                  <tr>
                    <th className="text-left py-2">Draft</th>
                    <th className="text-left py-2">User</th>
                    <th className="text-left py-2">Stack</th>
                    <th className="text-left py-2">Findings</th>
                    <th className="text-right py-2">Action</th>
                  </tr>
                </thead>
                <tbody>
                  {blocked.map((d) => (
                    <tr key={d.draft_id} className="border-t border-white/5" data-testid={`pt-blocked-${d.draft_id}`}>
                      <td className="py-3 pr-2 max-w-[280px]">
                        <div className="font-mono text-xs text-white/60">{d.draft_id.slice(0, 8)}</div>
                        <div className="text-white/80 line-clamp-2 text-xs mt-1">{d.brief}</div>
                      </td>
                      <td className="py-3 pr-2 font-mono text-xs text-white/60">
                        {d.user_id?.slice(0, 8) || "—"}
                      </td>
                      <td className="py-3 pr-2 text-white/70 text-xs">{d.stack_detected}</td>
                      <td className="py-3 pr-2">
                        <div className="flex gap-2 text-xs">
                          {d.scan_summary?.critical > 0 && (
                            <span className="px-2 py-0.5 rounded-full bg-rose-500/20 text-rose-300">
                              {d.scan_summary.critical} critical
                            </span>
                          )}
                          {d.scan_summary?.high > 0 && (
                            <span className="px-2 py-0.5 rounded-full bg-amber-500/20 text-amber-300">
                              {d.scan_summary.high} high
                            </span>
                          )}
                        </div>
                      </td>
                      <td className="py-3 text-right">
                        <button
                          onClick={() => override(d.draft_id)}
                          className="px-3 py-1.5 rounded-full bg-amber-500/20 hover:bg-amber-500/30 text-amber-200 text-xs transition"
                          data-testid={`pt-override-${d.draft_id}`}
                        >
                          Founder override
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        {/* Personal Track projects */}
        <section className="mb-10">
          <SectionHeader
            icon={Package}
            title={`Materialized projects (${projects.length})`}
          />
          {projects.length === 0 ? (
            <div className={`${cardBase} text-sm text-white/50`}>
              No Personal Track projects yet.
            </div>
          ) : (
            <div className={cardBase}>
              <table className="w-full text-sm">
                <thead className="text-white/50 text-xs uppercase tracking-wider">
                  <tr>
                    <th className="text-left py-2">Name</th>
                    <th className="text-left py-2">User</th>
                    <th className="text-left py-2">Stack</th>
                    <th className="text-left py-2">Repo</th>
                    <th className="text-left py-2">DB tier</th>
                    <th className="text-left py-2">Live URL</th>
                  </tr>
                </thead>
                <tbody>
                  {projects.map((p) => (
                    <tr key={p.project_id} className="border-t border-white/5" data-testid={`pt-project-${p.project_id}`}>
                      <td className="py-3 pr-2 max-w-[220px] text-white/80 line-clamp-1">{p.name || "—"}</td>
                      <td className="py-3 pr-2 font-mono text-xs text-white/60">{p.user_id?.slice(0, 8)}</td>
                      <td className="py-3 pr-2 text-white/70 text-xs">{p.stack || "—"}</td>
                      <td className="py-3 pr-2">
                        {p.github_repo ? (
                          <span className="flex items-center gap-1 text-xs text-white/60">
                            <GitBranch size={12} />
                            {p.github_owner}/{p.github_repo}
                            {p.repo_transferred && (
                              <span className="ml-2 px-1.5 py-0.5 rounded bg-sky-500/20 text-sky-200">
                                → {p.repo_transferred_to}
                              </span>
                            )}
                          </span>
                        ) : "—"}
                      </td>
                      <td className="py-3 pr-2">
                        <span className={`px-2 py-0.5 rounded-full text-xs ${
                          p.storage_tier === "supabase_dedicated"
                            ? "bg-emerald-500/20 text-emerald-200"
                            : "bg-white/10 text-white/60"
                        }`}>
                          {p.storage_tier === "supabase_dedicated" ? "Supabase" : "shared"}
                        </span>
                      </td>
                      <td className="py-3">
                        {p.live_url ? (
                          <a
                            href={p.live_url}
                            target="_blank" rel="noreferrer"
                            className="flex items-center gap-1 text-xs text-sky-300 hover:text-sky-200"
                          >
                            <ExternalLink size={12} />
                            open
                          </a>
                        ) : (
                          <span className="text-xs text-white/40">—</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        {/* Pending downgrades */}
        <section className="mb-10">
          <SectionHeader
            icon={Database}
            title={`Pending Supabase downgrades (${dgTotal})`}
            right={
              dgTotal > 0 && (
                <button
                  onClick={sweepNow}
                  className="px-3 py-1.5 rounded-full bg-white/5 border border-white/10 hover:bg-white/10 text-xs transition"
                  data-testid="pt-sweep-now"
                >
                  Sweep now
                </button>
              )
            }
          />
          {dgTotal === 0 ? (
            <div className={`${cardBase} text-sm text-white/50 flex items-center gap-2`}>
              <CheckCircle2 size={16} className="text-emerald-400" />
              No pending downgrades.
            </div>
          ) : (
            <div className={cardBase}>
              <table className="w-full text-sm">
                <thead className="text-white/50 text-xs uppercase tracking-wider">
                  <tr>
                    <th className="text-left py-2">App</th>
                    <th className="text-left py-2">Policy</th>
                    <th className="text-left py-2">Grace</th>
                    <th className="text-left py-2">Attempts</th>
                    <th className="text-left py-2">Status</th>
                    <th className="text-right py-2">Action</th>
                  </tr>
                </thead>
                <tbody>
                  {(downgrades?.rows || []).map((r) => (
                    <tr key={r.app_id} className="border-t border-white/5" data-testid={`pt-downgrade-${r.app_id}`}>
                      <td className="py-3 pr-2 font-mono text-xs text-white/70">{r.app_id?.slice(0, 12)}</td>
                      <td className="py-3 pr-2 text-white/60 text-xs">{r.downgrade_policy || "—"}</td>
                      <td className="py-3 pr-2 text-xs">
                        {r.grace_expired ? (
                          <span className="text-rose-300 flex items-center gap-1">
                            <Clock size={12} /> expired
                          </span>
                        ) : (
                          <span className="text-white/60">
                            {Math.round((r.seconds_to_grace || 0) / 3600)}h left
                          </span>
                        )}
                      </td>
                      <td className="py-3 pr-2 text-white/60 text-xs">{r.sweep_attempts || 0}</td>
                      <td className="py-3 pr-2">
                        {r.sweep_status === "needs_founder" ? (
                          <span className="px-2 py-0.5 rounded-full bg-rose-500/20 text-rose-300 text-xs flex items-center gap-1 w-fit">
                            <AlertTriangle size={10} /> escalated
                          </span>
                        ) : (
                          <span className="text-white/50 text-xs">pending</span>
                        )}
                      </td>
                      <td className="py-3 text-right">
                        {r.sweep_status === "needs_founder" && (
                          <button
                            onClick={() => rearm(r.app_id)}
                            className="px-3 py-1.5 rounded-full bg-sky-500/20 hover:bg-sky-500/30 text-sky-200 text-xs transition"
                            data-testid={`pt-rearm-${r.app_id}`}
                          >
                            Rearm
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        {/* Footer hint */}
        <div className="text-xs text-white/40 py-6 text-center border-t border-white/5">
          Personal Track admin data · refresh for latest ·{" "}
          <Link to="/admin" className="text-white/60 hover:text-white/90">
            back to main admin
          </Link>
        </div>
      </div>
    </div>
  );
}
