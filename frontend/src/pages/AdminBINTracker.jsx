/**
 * pages/AdminBINTracker.jsx — Iter 212m-171
 *
 * User + Project + PAT explorer for admins.  Search a BIN (user_id or
 * email), expand to see all their projects with live PAT validity, and
 * one-click change their tier.
 *
 * Backend: /admin/users (search) + /admin/bin/{bin_id}/projects
 *          + /admin/users/{bin_id}/tier
 */
import React, { useState, useEffect, useCallback } from "react";
import { api } from "../lib/api";
import { toast } from "../components/Toast";
import { Search, ChevronRight, ChevronDown, Loader2, Check, X, AlertTriangle } from "lucide-react";

const TIERS = ["free", "starter", "pro", "team", "founder"];

const PAT_BADGE = {
  valid:          { bg: "#15803d20", fg: "#4ade80", label: "✓ Valid" },
  invalid:        { bg: "#dc262620", fg: "#f87171", label: "✗ Invalid" },
  repo_not_found: { bg: "#dc262620", fg: "#f87171", label: "✗ Repo 404" },
  missing:        { bg: "#f59e0b20", fg: "#fbbf24", label: "⚠ No PAT" },
  probe_error:    { bg: "#f59e0b20", fg: "#fbbf24", label: "⚠ Probe fail" },
  no_repo:        { bg: "#37415120", fg: "#9ca3af", label: "— No repo" },
};

function PATBadge({ status }) {
  const b = PAT_BADGE[status] || { bg: "#37415120", fg: "#9ca3af", label: status };
  return (
    <span data-testid={`pat-badge-${status}`}
          style={{
            padding: "2px 8px", borderRadius: 3, fontSize: 10,
            fontFamily: "'JetBrains Mono', monospace",
            background: b.bg, color: b.fg,
            border: `1px solid ${b.fg}30`,
          }}>{b.label}</span>
  );
}

function ProjectRow({ p }) {
  return (
    <div style={{
      display: "grid",
      gridTemplateColumns: "160px 1fr 100px 100px 90px",
      gap: 8, padding: "6px 12px", fontSize: 11,
      alignItems: "center",
      borderTop: "1px dashed #37415140",
    }}>
      <span style={{ fontFamily: "'JetBrains Mono', monospace",
                     color: "var(--text-faint)" }}>
        {p.project_id?.slice(0, 12)}
      </span>
      <span style={{ color: "var(--text)" }}>
        {p.github_owner ? `${p.github_owner}/${p.github_repo}` : "—"}
        {p.branch && <span style={{ color: "var(--text-faint)", marginLeft: 6 }}>
          @{p.branch}
        </span>}
      </span>
      <PATBadge status={p.pat_status} />
      <span style={{ color: "var(--text-faint)", fontSize: 10 }}>
        {p.pat_last4 ? `••••${p.pat_last4}` : "—"}
      </span>
      <span style={{ color: "var(--text-faint)", fontSize: 10 }}>
        {p.tasks_done ?? 0} tasks
      </span>
    </div>
  );
}

function BINDetail({ bin, onClose, onTierChange }) {
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    setData(null);
    api.get(`/admin/bin/${bin.user_id}/projects`)
      .then((r) => setData(r.data))
      .catch(() => setData({ error: "load-failed" }));
  }, [bin.user_id]);

  const changeTier = async (tier) => {
    setBusy(true);
    try {
      const r = await api.post(`/admin/users/${bin.user_id}/tier`, { tier });
      toast({ message: `Tier: ${r.data.prev_tier} → ${r.data.new_tier}`, kind: "success" });
      setData((d) => d ? { ...d, tier: r.data.new_tier } : d);
      onTierChange && onTierChange(bin.user_id, r.data.new_tier);
    } catch (e) {
      toast({ message: "Tier change failed", kind: "error" });
    } finally {
      setBusy(false);
    }
  };

  if (!data) return (
    <div style={{ padding: 20, color: "var(--text-faint)", fontSize: 11 }}>
      <Loader2 size={12} className="spin" style={{ marginRight: 6, verticalAlign: "middle" }} />
      Loading BIN details…
    </div>
  );
  if (data.error) return (
    <div style={{ padding: 20, color: "#f87171", fontSize: 11 }}
         data-testid="bin-detail-error">
      Failed to load projects for {bin.user_id}
    </div>
  );

  return (
    <div data-testid={`bin-detail-${bin.user_id}`}
         style={{
           padding: 12, background: "var(--bg-elev)",
           border: "1px solid var(--border)",
           borderRadius: 4, marginTop: 4,
         }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12,
                     marginBottom: 12, flexWrap: "wrap" }}>
        <div style={{ fontFamily: "'JetBrains Mono', monospace",
                       fontSize: 11, color: "var(--text-faint)" }}>
          bin_id: {data.bin_id}
        </div>
        <div style={{ fontSize: 11 }}>{data.email}</div>
        <div style={{ marginLeft: "auto", display: "flex", gap: 6,
                      alignItems: "center" }}>
          <span style={{ fontSize: 10, color: "var(--text-faint)" }}>Tier:</span>
          <select value={data.tier || "free"}
                  disabled={busy}
                  data-testid={`bin-tier-select-${bin.user_id}`}
                  onChange={(e) => changeTier(e.target.value)}
                  style={{
                    background: "var(--panel-2)", color: "var(--text)",
                    border: "1px solid var(--border)",
                    padding: "3px 8px", borderRadius: 3, fontSize: 11,
                  }}>
            {TIERS.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
        </div>
      </div>
      <div style={{ fontSize: 10, color: "var(--text-faint)",
                    padding: "0 12px", marginBottom: 4 }}>
        PROJECTS ({data.project_count})
      </div>
      {data.project_count === 0
        ? <div style={{ padding: 12, color: "var(--text-faint)",
                        fontSize: 11, fontStyle: "italic" }}>
            No projects connected yet.
          </div>
        : data.projects.map((p) => <ProjectRow key={p.project_id} p={p} />)}
    </div>
  );
}

export default function AdminBINTracker() {
  const [users, setUsers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [expanded, setExpanded] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await api.get("/admin/users", { params: { search, window: "all" } });
      setUsers(r.data.users || []);
    } catch {
      setUsers([]);
    } finally {
      setLoading(false);
    }
  }, [search]);

  useEffect(() => { load(); }, [load]);

  const onTierChange = (uid, newTier) => {
    setUsers((cur) => cur.map((u) =>
      u.user_id === uid ? { ...u, tier: newTier } : u));
  };

  return (
    <div style={{ padding: "24px 20px", maxWidth: 1200 }}
         data-testid="bin-tracker-page">
      <div style={{ marginBottom: 16 }}>
        <h1 style={{ fontSize: 20, fontWeight: 600, margin: 0,
                     color: "var(--text)" }}>BIN Tracker</h1>
        <div style={{ fontSize: 11, color: "var(--text-faint)",
                       marginTop: 4 }}>
          Per-user boundary explorer — expand a BIN to see projects + live PAT validity.
        </div>
      </div>
      <div style={{ display: "flex", gap: 8, marginBottom: 12,
                     alignItems: "center" }}>
        <div style={{ position: "relative", flex: "0 0 320px" }}>
          <Search size={12} style={{
            position: "absolute", left: 8, top: "50%",
            transform: "translateY(-50%)", color: "var(--text-faint)",
          }} />
          <input
            data-testid="bin-search"
            type="text" placeholder="Search by email or BIN…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            style={{
              width: "100%", padding: "6px 8px 6px 26px",
              background: "var(--panel-2)", border: "1px solid var(--border)",
              color: "var(--text)", fontSize: 12, borderRadius: 3,
            }}
          />
        </div>
        <div style={{ fontSize: 11, color: "var(--text-faint)" }}>
          {loading ? "loading…" : `${users.length} BINs`}
        </div>
      </div>
      <div style={{
        background: "var(--panel-2)", border: "1px solid var(--border)",
        borderRadius: 4, overflow: "hidden",
      }}>
        <div style={{
          display: "grid",
          gridTemplateColumns: "24px 1fr 90px 90px 100px 100px",
          gap: 8, padding: "8px 12px",
          fontSize: 10, letterSpacing: "0.08em", textTransform: "uppercase",
          color: "var(--text-faint)",
          borderBottom: "1px solid var(--border)",
        }}>
          <span></span><span>Email / BIN</span><span>Tier</span>
          <span>PIDs</span><span>Tasks</span><span>Sessions</span>
        </div>
        {users.map((u) => {
          const isOpen = expanded === u.user_id;
          return (
            <React.Fragment key={u.user_id}>
              <div
                data-testid={`bin-row-${u.user_id}`}
                onClick={() => setExpanded(isOpen ? null : u.user_id)}
                style={{
                  display: "grid",
                  gridTemplateColumns: "24px 1fr 90px 90px 100px 100px",
                  gap: 8, padding: "8px 12px",
                  fontSize: 12, alignItems: "center", cursor: "pointer",
                  borderTop: "1px solid var(--border)",
                  background: isOpen ? "var(--bg-elev)" : "transparent",
                }}>
                {isOpen ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
                <div>
                  <div style={{ color: "var(--text)" }}>{u.email || "—"}</div>
                  <div style={{ fontSize: 10, color: "var(--text-faint)",
                                 fontFamily: "'JetBrains Mono', monospace" }}>
                    {u.user_id?.slice(0, 12)}
                  </div>
                </div>
                <span style={{ fontSize: 11, color: "var(--text-dim)",
                               textTransform: "capitalize" }}>{u.tier || "free"}</span>
                <span style={{ fontSize: 11, color: "var(--text-dim)" }}>
                  {u.project_count ?? 0}
                </span>
                <span style={{ fontSize: 11, color: "var(--text-dim)" }}>
                  {u.task_count ?? 0}
                </span>
                <span style={{ fontSize: 11, color: "var(--text-dim)" }}>
                  {u.session_count ?? 0}
                </span>
              </div>
              {isOpen && (
                <div style={{ padding: "4px 12px 12px 40px",
                              background: "var(--bg-elev)" }}>
                  <BINDetail bin={u} onClose={() => setExpanded(null)}
                             onTierChange={onTierChange} />
                </div>
              )}
            </React.Fragment>
          );
        })}
      </div>
    </div>
  );
}
