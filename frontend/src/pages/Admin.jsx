/**
 * pages/Admin.jsx — AuremCTO Admin Panel
 * Guarded route: only users with is_admin in localStorage 'aurem_user'.
 * All data lives under /api/aurem-dev/admin/*.
 */
import React, { useState, useEffect, useCallback, useMemo } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  LayoutDashboard, Users, MessageCircle, Folder, ListChecks,
  Cpu, CreditCard, Network as SitemapIcon, Settings as SettingsIcon,
  LogOut, ExternalLink, ArrowLeft, Loader2, Brain, Eye, Terminal,
  Mail, Activity, Plug, GitBranch, Zap, ShieldAlert, DollarSign, ShieldCheck,
} from "lucide-react";
import { api } from "../lib/api";
import { toast } from "../components/Toast";
import AuremAdminPanel from "../components/AuremAdminPanel";
import AdminOverview from "./AdminOverview";
import AgentTokenPanel from "../components/AgentTokenPanel";
import AdminThinkingHints from "../components/AdminThinkingHints";
import TwoFactorCard from "../components/TwoFactorCard";  // Iter 212m-20
import AdminHouseRules from "../components/AdminHouseRules";  // Iter 212m-24

// ── Helpers ────────────────────────────────────────────────────────────
const fmt = (n) => (n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n ?? 0));
const fmtMoney = (n) => `$${(n || 0).toFixed(2)}`;
// Iter 212m-96 — accept BOTH numeric epoch seconds AND ISO date strings.
// Backend returns ISO strings for newer users (Iter 211+) and Unix epoch
// numbers for legacy rows. Either way we normalize to epoch seconds.
const ago = (v) => {
  if (v == null || v === "") return "—";
  let sec;
  if (typeof v === "number") {
    sec = v;
  } else {
    const t = new Date(v).getTime();
    if (isNaN(t)) return "—";
    sec = Math.floor(t / 1000);
  }
  const s = Math.floor(Date.now() / 1000 - sec);
  if (s < 0) return "—";
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
};

const STATUS_COLOR = {
  done: "var(--ok)",
  failed: "var(--danger)",
  running: "var(--accent-2)",
  queued: "var(--text-faint)",
  active: "var(--ok)",
  suspended: "var(--danger)",
};

function Badge({ children, color }) {
  return (
    <span style={{
      display: "inline-block", padding: "2px 8px",
      fontSize: 10, letterSpacing: "0.05em",
      fontFamily: "'JetBrains Mono', monospace",
      background: "var(--bg-elev)",
      border: `1px solid ${color || "var(--border)"}`,
      borderRadius: 4, color: color || "var(--text-dim)",
    }}>
      {children}
    </span>
  );
}

function Card({ children, style }) {
  return (
    <div style={{
      background: "var(--panel-2)",
      border: "1px solid var(--border)",
      borderRadius: 4,
      ...style,
    }}>
      {children}
    </div>
  );
}

function MCard({ label, value, sub, accent }) {
  return (
    <Card style={{ padding: "14px 16px" }}>
      <div style={{ fontSize: 10, letterSpacing: "0.1em",
                     color: "var(--text-faint)", textTransform: "uppercase" }}>
        {label}
      </div>
      <div style={{ fontSize: 26, fontWeight: 600, marginTop: 6,
                     color: accent || "var(--text)",
                     fontFamily: "'JetBrains Mono', monospace" }}>
        {value}
      </div>
      {sub && <div style={{ fontSize: 11, marginTop: 4, color: "var(--text-faint)" }}>{sub}</div>}
    </Card>
  );
}

function Table({ cols, rows, onRowClick }) {
  if (!rows || rows.length === 0) {
    return <div style={{ padding: 24, textAlign: "center", fontSize: 12, color: "var(--text-faint)" }}>
      No data yet.
    </div>;
  }
  return (
    <div className="aurem-table-wrap">
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12, minWidth: 560 }}>
      <thead>
        <tr>{cols.map((c, i) => (
          <th key={i} style={{
            textAlign: "left", padding: "8px 12px", fontSize: 10,
            color: "var(--text-faint)", textTransform: "uppercase",
            letterSpacing: "0.1em", borderBottom: "1px solid var(--border)",
            background: "var(--bg-elev)", whiteSpace: "nowrap",
          }}>{c}</th>
        ))}</tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <tr key={i}
              onClick={onRowClick ? () => onRowClick(i) : undefined}
              style={{ cursor: onRowClick ? "pointer" : "default" }}
              onMouseEnter={(e) => onRowClick && (e.currentTarget.style.background = "var(--bg-elev)")}
              onMouseLeave={(e) => onRowClick && (e.currentTarget.style.background = "")}>
            {r.map((cell, j) => (
              <td key={j} style={{ padding: "10px 12px",
                                    borderBottom: "1px solid var(--border)",
                                    verticalAlign: "middle" }}>
                {cell}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
    </div>
  );
}

// ── Pages ──────────────────────────────────────────────────────────────
function Dashboard() {
  const [data, setData] = useState(null);
  useEffect(() => {
    api.get("/admin/dashboard").then((r) => setData(r.data)).catch(() => {});
  }, []);
  if (!data) return <div style={{ padding: 24, color: "var(--text-faint)" }}><Loader2 size={14} className="spin" /> Loading…</div>;

  return (
    <div style={{ padding: 24 }}>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: 12, marginBottom: 18 }}>
        <MCard label="Users" value={data.total_users} sub={`${data.total_projects} projects`} />
        <MCard label="Tasks today" value={data.tasks_today} sub={`${data.success_rate}% success`} />
        <MCard label="Done / Failed" value={`${data.done_tasks}/${data.failed_tasks}`} accent="var(--ok)" />
        <MCard label="Chat sessions" value={data.total_sessions} sub="lifetime" />
      </div>

      <h3 style={{ fontSize: 12, letterSpacing: "0.1em", textTransform: "uppercase",
                    color: "var(--text-faint)", margin: "0 0 8px" }}>
        Recent tasks
      </h3>
      <Card>
        <Table
          cols={["Task", "User", "Status", "Commit", "Time"]}
          rows={(data.recent_tasks || []).map((t) => [
            <span key="task" style={{ maxWidth: 320, overflow: "hidden", textOverflow: "ellipsis",
                            whiteSpace: "nowrap", display: "block" }}>{t.task}</span>,
            <span key="user" style={{ color: "var(--text-faint)",
                            fontFamily: "'JetBrains Mono', monospace", fontSize: 11 }}>
              {(t.user_id || "").slice(0, 10)}
            </span>,
            <Badge key="status" color={STATUS_COLOR[t.status]}>{t.status}</Badge>,
            t.commit_sha ? <span key="commit" style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 11 }}>{t.commit_sha}</span> : "—",
            <span key="time" style={{ color: "var(--text-faint)" }}>{ago(t.created_at)}</span>,
          ])}
        />
      </Card>

      <h3 style={{ fontSize: 12, letterSpacing: "0.1em", textTransform: "uppercase",
                    color: "var(--text-faint)", margin: "20px 0 8px" }}>
        Recent users
      </h3>
      <Card>
        <Table
          cols={["Email", "Name", "Tier", "Joined"]}
          rows={(data.recent_users || []).map((u) => [
            u.email,
            u.name || "—",
            <Badge key="tier">{u.tier || "free"}</Badge>,
            <span key="time" style={{ color: "var(--text-faint)" }}>{ago(u.created_at)}</span>,
          ])}
        />
      </Card>
    </div>
  );
}

function UsersList({ onSelect }) {
  const [users, setUsers] = useState([]);
  const [buckets, setBuckets] = useState({ "24h": 0, "7d": 0, "30d": 0, "all": 0 });
  const [search, setSearch] = useState("");
  // Iter 194 — signup-window filter. Default "all"; pills above the
  // table let admin scope the list to "joined in 24 h", "7 d", "30 d".
  const [windowSel, setWindowSel] = useState("all");
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState(() => new Set());
  const [composerOpen, setComposerOpen] = useState(false);
  const [deletingId, setDeletingId] = useState(null);

  const load = useCallback(async (s, w) => {
    setLoading(true);
    try {
      const r = await api.get("/admin/users", { params: { search: s, window: w } });
      setUsers(r.data.users || []);
      if (r.data.bucket_counts) setBuckets(r.data.bucket_counts);
    } finally { setLoading(false); }
  }, []);

  useEffect(() => {
    const t = setTimeout(() => load(search, windowSel), 250);
    return () => clearTimeout(t);
  }, [search, windowSel, load]);

  const toggleOne = (uid) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(uid)) next.delete(uid); else next.add(uid);
      return next;
    });
  };
  const toggleAll = () => {
    setSelected((prev) => {
      if (prev.size === users.length) return new Set();
      return new Set(users.map((u) => u.user_id));
    });
  };

  async function deleteUser(u) {
    if (!window.confirm(`Delete ${u.email}? This cascades to projects, tasks, payments, API keys. No undo.`)) {
      return;
    }
    setDeletingId(u.user_id);
    try {
      const r = await api.delete(`/admin/users/${u.user_id}`);
      const dels = r.data?.deletions || {};
      toast({
        message: `Deleted ${u.email} · users:${dels.users ?? "?"} projects:${dels.cto_projects ?? "?"} tasks:${dels.cto_tasks ?? "?"}`,
        kind: "success",
      });
      setSelected((prev) => {
        const next = new Set(prev);
        next.delete(u.user_id);
        return next;
      });
      load(search, windowSel);
    } catch (e) {
      toast({
        message: e?.response?.data?.detail || "Delete failed",
        kind: "error",
      });
    } finally { setDeletingId(null); }
  }

  return (
    <div style={{ padding: 24 }}>
      {/* Iter 65 — Agent token P&L widget, top of Users tab. */}
      <AgentTokenPanel />
      <div style={{ display: "flex", justifyContent: "space-between",
                     alignItems: "center", marginBottom: 14, gap: 12, flexWrap: "wrap" }}>
        <h3 style={{ fontSize: 12, letterSpacing: "0.1em", textTransform: "uppercase",
                      color: "var(--text-faint)", margin: 0 }}>
          Users ({users.length}){selected.size > 0 && ` · ${selected.size} selected`}
        </h3>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          {selected.size > 0 && (
            <button
              data-testid="admin-users-send-offer"
              className="btn-primary"
              style={{ fontSize: 12, padding: "6px 14px" }}
              onClick={() => setComposerOpen(true)}
            >
              <Mail size={12} style={{ verticalAlign: "middle", marginRight: 6 }} />
              Send offer email ({selected.size})
            </button>
          )}
          <input
            data-testid="admin-users-search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search email / name…"
            className="input"
            style={{ width: 260, maxWidth: "100%" }}
          />
        </div>
      </div>

      {/* Iter 194 — signup-window filter pills. Clicking a pill scopes
          the table to users that joined in that bucket; the count next
          to each pill is the raw bucket_count from /admin/users so the
          admin sees "how many today" before scoping in. */}
      <div data-testid="admin-users-window-pills"
           style={{ display: "flex", gap: 6, flexWrap: "wrap",
                    marginBottom: 14, alignItems: "center" }}>
        {[
          { id: "24h", label: "Last 24 h" },
          { id: "7d",  label: "Last 7 days" },
          { id: "30d", label: "Last 30 days" },
          { id: "all", label: "All time" },
        ].map((p) => {
          const active = windowSel === p.id;
          return (
            <button
              key={p.id}
              data-testid={`admin-users-window-${p.id}`}
              onClick={() => setWindowSel(p.id)}
              style={{
                padding: "5px 12px",
                fontSize: 11,
                fontFamily: "'JetBrains Mono', monospace",
                fontWeight: 600,
                letterSpacing: "0.04em",
                background: active ? "var(--accent, #ff8a2a)" : "transparent",
                color: active ? "#0a0e1a" : "var(--text-faint)",
                border: `1px solid ${active ? "var(--accent, #ff8a2a)" : "var(--line, rgba(255,255,255,0.08))"}`,
                borderRadius: 999,
                cursor: "pointer",
                transition: "all 0.15s",
              }}
            >
              {p.label} · {buckets[p.id] ?? 0}
            </button>
          );
        })}
      </div>
      <Card>
        {loading ? <div style={{ padding: 24, color: "var(--text-faint)" }}><Loader2 size={14} className="spin" /> Loading…</div> : (
          <Table
            cols={[
              <input
                key="all"
                data-testid="admin-users-select-all"
                type="checkbox"
                checked={users.length > 0 && selected.size === users.length}
                onChange={toggleAll}
                style={{ cursor: "pointer" }}
              />,
              "Email", "Name", "Tier", "Projects", "Tasks", "Status", "Joined", "",
            ]}
            rows={users.map((u) => [
              <input
                key={`cb-${u.user_id}`}
                data-testid={`admin-user-cb-${u.user_id}`}
                type="checkbox"
                checked={selected.has(u.user_id)}
                onChange={() => toggleOne(u.user_id)}
                onClick={(e) => e.stopPropagation()}
                style={{ cursor: "pointer" }}
              />,
              u.email,
              u.name || "—",
              <Badge key="tier">{u.tier || "free"}</Badge>,
              u.project_count ?? 0,
              u.task_count ?? 0,
              <Badge key="status" color={STATUS_COLOR[u.status || "active"]}>{u.status || "active"}</Badge>,
              // Iter 194 — Joined column. Shows "x ago" + absolute
              // date as a tooltip so admin can scan recency and
              // hover for the exact timestamp.
              // Iter 212m-96 — defensive date parse: backend returns
              // either a Unix epoch (number) or an ISO string depending
              // on user vintage. Either form crashed `new Date(undefined*1000)`
              // → "Invalid time value" which crashed the entire Users
              // tab. Now we handle both safely.
              <span
                key="joined"
                title={(() => {
                  try {
                    const v = u.created_at;
                    if (v == null) return "unknown";
                    const d = typeof v === "number"
                      ? new Date(v * 1000)
                      : new Date(v);
                    return isNaN(d.getTime()) ? "unknown" : d.toISOString();
                  } catch { return "unknown"; }
                })()}
                style={{
                  fontSize: 11,
                  color: "var(--text-faint)",
                  fontFamily: "'JetBrains Mono', monospace",
                }}
              >
                {u.created_at ? ago(u.created_at) : "—"}
              </span>,
              <div key="act" style={{ display: "flex", gap: 6 }}>
                <button
                  data-testid={`admin-user-view-${u.user_id}`}
                  className="btn-ghost" style={{ padding: "4px 10px", fontSize: 11 }}
                  onClick={() => onSelect(u)}>
                  view →
                </button>
                <button
                  data-testid={`admin-user-delete-${u.user_id}`}
                  className="btn-ghost"
                  style={{ padding: "4px 10px", fontSize: 11, color: "#fca5a5" }}
                  disabled={deletingId === u.user_id}
                  onClick={() => deleteUser(u)}
                  title="Delete user (cascades to all owned data)"
                >
                  {deletingId === u.user_id ? "…" : "delete"}
                </button>
              </div>,
            ])}
          />
        )}
      </Card>

      {composerOpen && (
        <EmailOfferComposer
          userIds={Array.from(selected)}
          recipients={users.filter((u) => selected.has(u.user_id))
                          .map((u) => ({ email: u.email, name: u.name }))}
          onClose={() => setComposerOpen(false)}
          onSent={() => {
            setComposerOpen(false);
            setSelected(new Set());
          }}
        />
      )}
    </div>
  );
}

// Iter 193 — Modal composer for bulk offer emails. Subject + HTML body
// with `{{name}}` and `{{email}}` substitutions handled server-side.
// Shows recipient count up front and a confirm summary before sending
// so an admin can't fat-finger a 500-user blast.
function EmailOfferComposer({ userIds, recipients, onClose, onSent }) {
  const [subject, setSubject] = useState("");
  const [bodyHtml, setBodyHtml] = useState(
    "<p>Hi {{name}},</p>\n<p>We've got something special for you...</p>\n<p>— The ORA team</p>"
  );
  const [sending, setSending] = useState(false);

  async function send() {
    if (!subject.trim() || !bodyHtml.trim()) return;
    if (!window.confirm(`Send "${subject}" to ${userIds.length} user(s)? This cannot be undone.`)) {
      return;
    }
    setSending(true);
    try {
      const r = await api.post("/admin/users/email-offer", {
        user_ids:  userIds,
        subject:   subject.trim(),
        body_html: bodyHtml,
      });
      const dryRun = r.data?.dry_run;
      const sent = r.data?.sent ?? 0;
      const failed = r.data?.failed ?? 0;
      toast({
        message: dryRun
          ? `Dry-run (RESEND_API_KEY missing) · ${recipients.length} would receive`
          : `Sent ${sent} email(s)${failed ? ` · ${failed} failed` : ""}`,
        kind: failed === 0 ? "success" : "info",
      });
      onSent();
    } catch (e) {
      toast({
        message: e?.response?.data?.detail || "Send failed",
        kind: "error",
      });
    } finally { setSending(false); }
  }

  return (
    <div
      data-testid="admin-email-composer"
      style={{
        position: "fixed", inset: 0,
        background: "rgba(0,0,0,0.65)",
        display: "flex", alignItems: "center", justifyContent: "center",
        zIndex: 9000,
        padding: 24,
      }}
      onClick={onClose}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          background: "var(--bg-1, #0a0e1a)",
          border: "1px solid var(--line, rgba(255,255,255,0.08))",
          borderRadius: 12,
          width: "min(720px, 100%)",
          maxHeight: "90vh",
          overflowY: "auto",
          padding: 22,
        }}
      >
        <div style={{ display: "flex", justifyContent: "space-between",
                       alignItems: "center", marginBottom: 14 }}>
          <h3 style={{ fontSize: 14, margin: 0 }}>Send offer email</h3>
          <button onClick={onClose}
                  style={{ background: "none", border: "none",
                           color: "var(--text-faint)", cursor: "pointer", fontSize: 18 }}>
            ×
          </button>
        </div>

        <div style={{ fontSize: 11, color: "var(--text-faint)", marginBottom: 14,
                       fontFamily: "'JetBrains Mono', monospace" }}>
          Recipients: {recipients.length} · Templating: <code>{`{{name}}`}</code>, <code>{`{{email}}`}</code>
        </div>

        <div data-testid="admin-offer-replyto-hint"
             style={{ fontSize: 11, color: "var(--text-faint)", marginBottom: 14,
                       padding: "6px 10px",
                       background: "rgba(34,197,94,0.07)",
                       border: "1px solid rgba(34,197,94,0.18)",
                       borderRadius: 6 }}>
          ↩ Replies will land in <strong style={{ color: "#22c55e" }}>polarisbuiltinc@gmail.com</strong>
        </div>

        <label style={{ fontSize: 11, color: "var(--text-faint)", display: "block",
                         marginBottom: 4, fontFamily: "'JetBrains Mono', monospace",
                         textTransform: "uppercase", letterSpacing: "0.06em" }}>
          Subject
        </label>
        <input
          data-testid="admin-offer-subject"
          className="input"
          value={subject}
          onChange={(e) => setSubject(e.target.value)}
          placeholder="Special offer just for you"
          style={{ width: "100%", marginBottom: 12 }}
        />

        <label style={{ fontSize: 11, color: "var(--text-faint)", display: "block",
                         marginBottom: 4, fontFamily: "'JetBrains Mono', monospace",
                         textTransform: "uppercase", letterSpacing: "0.06em" }}>
          Body (HTML)
        </label>
        <textarea
          data-testid="admin-offer-body"
          className="input"
          value={bodyHtml}
          onChange={(e) => setBodyHtml(e.target.value)}
          rows={10}
          style={{ width: "100%", fontFamily: "'JetBrains Mono', monospace",
                   fontSize: 12, lineHeight: 1.5, resize: "vertical" }}
        />

        <div style={{ marginTop: 14, padding: "8px 12px",
                       background: "rgba(255,138,42,0.08)",
                       border: "1px solid rgba(255,138,42,0.2)",
                       borderRadius: 6, fontSize: 11,
                       color: "var(--text-faint)" }}>
          Preview recipients: {recipients.slice(0, 5).map((r) => r.email).join(", ")}
          {recipients.length > 5 && ` …and ${recipients.length - 5} more`}
        </div>

        <div style={{ marginTop: 14, display: "flex", gap: 8, justifyContent: "flex-end" }}>
          <button
            onClick={onClose}
            className="btn-secondary"
            disabled={sending}
            style={{ fontSize: 12 }}>
            Cancel
          </button>
          <button
            data-testid="admin-offer-send"
            onClick={send}
            disabled={sending || !subject.trim() || !bodyHtml.trim()}
            className="btn-primary"
            style={{ fontSize: 12 }}>
            {sending ? "Sending…" : `Send to ${recipients.length}`}
          </button>
        </div>
      </div>
    </div>
  );
}

function UserDetail({ user, onBack }) {
  const [d, setD] = useState(user);
  const [busy, setBusy] = useState(false);
  const [grantOpen, setGrantOpen] = useState(false);
  const [grantTokens, setGrantTokens] = useState("");
  const [grantReason, setGrantReason] = useState("");
  const [granting, setGranting] = useState(false);

  useEffect(() => {
    api.get(`/admin/users/${user.user_id}`).then((r) => setD(r.data)).catch(() => {});
  }, [user.user_id]);

  async function toggleSuspend() {
    const wantSuspend = d.status !== "suspended";
    if (!window.confirm(`${wantSuspend ? "Suspend" : "Unsuspend"} ${d.email}?`)) return;
    setBusy(true);
    try {
      await api.post(`/admin/users/${user.user_id}/suspend`, { suspend: wantSuspend });
      setD((prev) => ({ ...prev, status: wantSuspend ? "suspended" : "active" }));
      toast({ message: wantSuspend ? "Suspended" : "Unsuspended", kind: "success" });
    } catch (e) {
      toast({ message: e?.response?.data?.detail || "Failed", kind: "error" });
    } finally { setBusy(false); }
  }

  async function submitGrant() {
    const n = parseInt(grantTokens, 10);
    if (!n || n <= 0) {
      toast({ message: "Enter a positive token amount", kind: "error" });
      return;
    }
    setGranting(true);
    try {
      const r = await api.post(`/admin/users/${user.user_id}/grant-tokens`, {
        tokens: n,
        reason: grantReason.trim(),
      });
      toast({ message: `Granted ${n.toLocaleString()} tokens ✓`, kind: "success" });
      setD((prev) => ({ ...prev, usage: r.data.usage, token_grants: [
        { tokens: n, reason: grantReason.trim(), granted_at: Date.now() / 1000, granted_by: "you" },
        ...(prev.token_grants || []),
      ] }));
      setGrantOpen(false);
      setGrantTokens("");
      setGrantReason("");
    } catch (e) {
      toast({ message: e?.response?.data?.detail || "Grant failed", kind: "error" });
    } finally { setGranting(false); }
  }

  return (
    <div style={{ padding: 24 }}>
      <button className="btn-ghost" style={{ padding: "4px 10px", fontSize: 11, marginBottom: 14 }}
              onClick={onBack}>
        <ArrowLeft size={11} /> Users
      </button>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginBottom: 14 }}>
        <Card style={{ padding: 16 }}>
          <h3 style={{ fontSize: 13, margin: "0 0 12px" }}>{d.email}</h3>
          <div style={{ fontSize: 12, color: "var(--text-dim)", lineHeight: 1.8 }}>
            <div><b>Name:</b> {d.name || "—"}</div>
            <div><b>User ID:</b> <code style={{ fontSize: 11 }}>{d.user_id}</code></div>
            <div><b>Tier:</b> {d.tier || "free"}</div>
            <div><b>Status:</b> <Badge color={STATUS_COLOR[d.status || "active"]}>{d.status || "active"}</Badge></div>
            <div><b>Tokens remaining:</b> {fmt(d.tokens_remaining || 0)}</div>
            {d.usage && (
              <div data-testid="admin-user-usage" style={{ marginTop: 6, padding: "6px 8px",
                background: "rgba(255,255,255,0.03)", borderRadius: 6 }}>
                <div><b>Plan limit:</b> {fmt(d.usage.plan_limit)} · <b>Granted:</b> {fmt(d.usage.tokens_granted)}</div>
                <div><b>Effective:</b> {fmt(d.usage.effective_limit)} · <b>Used:</b> {fmt(d.usage.used)} ({d.usage.pct_used}%)</div>
                <div style={{ color: d.usage.is_exhausted ? "var(--danger)" : "var(--ok)" }}>
                  <b>Remaining:</b> {fmt(d.usage.remaining)}{d.usage.is_exhausted ? " — EXHAUSTED" : ""}
                </div>
              </div>
            )}
            <div><b>Joined:</b> {ago(d.created_at)}</div>
            <div><b>Projects:</b> {d.project_count ?? 0} · <b>Tasks:</b> {d.task_count ?? 0} · <b>Sessions:</b> {d.session_count ?? 0}</div>
          </div>
          <div style={{ marginTop: 14, display: "flex", gap: 8, flexWrap: "wrap" }}>
            <button
              data-testid="admin-user-suspend"
              className="btn-ghost"
              disabled={busy}
              style={{ padding: "6px 12px", fontSize: 11,
                       borderColor: "rgba(255,107,107,0.3)", color: "var(--danger)" }}
              onClick={toggleSuspend}>
              {d.status === "suspended" ? "Unsuspend" : "Suspend"}
            </button>
            <button
              data-testid="admin-user-grant-open"
              className="btn-ghost"
              disabled={granting}
              style={{ padding: "6px 12px", fontSize: 11,
                       borderColor: "rgba(120,200,255,0.35)", color: "var(--accent-2)" }}
              onClick={() => setGrantOpen((v) => !v)}>
              {grantOpen ? "Cancel grant" : "Grant tokens"}
            </button>
          </div>
          {grantOpen && (
            <div data-testid="admin-user-grant-form" style={{
              marginTop: 12, padding: 10,
              background: "rgba(120,200,255,0.06)",
              border: "1px solid rgba(120,200,255,0.25)",
              borderRadius: 8, display: "flex", flexDirection: "column", gap: 8,
            }}>
              <input
                data-testid="admin-grant-tokens-input"
                type="number"
                min="1"
                placeholder="Tokens to grant (e.g. 5000)"
                value={grantTokens}
                onChange={(e) => setGrantTokens(e.target.value)}
                className="input"
                style={{ fontSize: 12, padding: "6px 8px" }}
              />
              <input
                data-testid="admin-grant-reason-input"
                type="text"
                placeholder="Reason (e.g. support credit)"
                value={grantReason}
                onChange={(e) => setGrantReason(e.target.value)}
                className="input"
                style={{ fontSize: 12, padding: "6px 8px" }}
              />
              <button
                data-testid="admin-grant-submit"
                className="btn-primary"
                onClick={submitGrant}
                disabled={granting || !grantTokens}
                style={{ padding: "6px 12px", fontSize: 11 }}>
                {granting ? "Granting…" : "Grant tokens"}
              </button>
            </div>
          )}
          {(d.token_grants || []).length > 0 && (
            <div style={{ marginTop: 12, fontSize: 11, color: "var(--text-dim)" }}>
              <b style={{ fontSize: 11 }}>Recent grants ({d.token_grants.length})</b>
              <div style={{ maxHeight: 110, overflowY: "auto", marginTop: 4 }}>
                {d.token_grants.map((g, i) => (
                  <div key={i} style={{ padding: "3px 0", borderBottom: "1px solid var(--border)" }}>
                    +{fmt(g.tokens)} · {g.reason || "no reason"} · <span style={{ color: "var(--text-faint)" }}>{ago(g.granted_at)}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </Card>
        <Card style={{ padding: 16 }}>
          <h3 style={{ fontSize: 13, margin: "0 0 10px" }}>Projects ({(d.projects || []).length})</h3>
          <div style={{ maxHeight: 200, overflowY: "auto", fontSize: 12 }}>
            {(d.projects || []).map((p) => (
              <div key={p.project_id} style={{ padding: "6px 0", borderBottom: "1px solid var(--border)" }}>
                <b>{p.name}</b> · {p.github_owner}/{p.github_repo}@{p.branch}
                {" "}<Badge>{p.tech_stack || "auto"}</Badge>
              </div>
            ))}
            {(!d.projects || d.projects.length === 0) && <div style={{ color: "var(--text-faint)" }}>No projects.</div>}
          </div>
        </Card>
      </div>
      <h3 style={{ fontSize: 12, letterSpacing: "0.1em", textTransform: "uppercase",
                    color: "var(--text-faint)", margin: "0 0 8px" }}>Recent tasks</h3>
      <Card>
        <Table
          cols={["Task", "Status", "Commit", "Time"]}
          rows={(d.recent_tasks || []).map((t) => [
            <span key="task" style={{ maxWidth: 400, overflow: "hidden", textOverflow: "ellipsis",
                            whiteSpace: "nowrap", display: "block" }}>{t.task}</span>,
            <Badge key="status" color={STATUS_COLOR[t.status]}>{t.status}</Badge>,
            t.commit_sha ? <span key="commit" style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 11 }}>{t.commit_sha}</span> : "—",
            <span key="time" style={{ color: "var(--text-faint)" }}>{ago(t.created_at)}</span>,
          ])}
        />
      </Card>
    </div>
  );
}

function ProjectsPage() {
  const [data, setData] = useState([]);
  const [graph, setGraph] = useState([]);
  useEffect(() => {
    api.get("/admin/projects").then((r) => setData(r.data.projects || [])).catch(() => {});
    // Iter 192 — merged Graph Status into Projects. The /graph-status
    // endpoint returns has_graph + graph_node_count + graph_built_at
    // per project; we left-join in the table so each project row shows
    // its graph state without a second page.
    api.get("/admin/graph-status", { params: { limit: 200 } })
      .then((r) => setGraph(r.data?.rows || []))
      .catch(() => {});
  }, []);
  const graphByProject = useMemo(() => {
    const m = {};
    for (const g of graph) m[g.project_id] = g;
    return m;
  }, [graph]);
  const built = graph.filter((g) => g.has_graph).length;
  return (
    <div style={{ padding: 24 }}>
      {/* Graph coverage summary (merged from the old Graph Status tab). */}
      {!!graph.length && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)",
                      gap: 12, marginBottom: 14 }}>
          <MCard label="Projects with graph" value={built} sub={`of ${graph.length}`} />
          <MCard label="Graph coverage"
                  value={graph.length ? `${Math.round(100 * built / graph.length)}%` : "—"}
                  accent={graph.length && built / graph.length >= 0.6 ? "var(--ok)" : "var(--warn)"} />
          <MCard label="Total projects" value={data.length} />
        </div>
      )}
      <h3 style={{ fontSize: 12, letterSpacing: "0.1em", textTransform: "uppercase",
                    color: "var(--text-faint)", margin: "0 0 8px" }}>
        All projects ({data.length})
      </h3>
      <Card>
        <Table
          cols={["Name", "Repo", "Branch", "Stack", "Tasks", "Graph", "Nodes", "Graph built", "User", "Created"]}
          rows={data.map((p) => {
            const g = graphByProject[p.project_id] || {};
            return [
              p.name,
              <span key="repo" style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 11 }}>
                {p.github_owner}/{p.github_repo}
              </span>,
              p.branch,
              <Badge key="stack">{p.tech_stack || "auto"}</Badge>,
              p.tasks_done ?? 0,
              <Badge key="graph"
                      color={g.has_graph ? "var(--ok)" : "var(--text-faint)"}>
                {g.has_graph ? "yes" : "no"}
              </Badge>,
              <span key="nodes" style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 11 }}>
                {g.graph_node_count ?? "—"}
              </span>,
              <span key="gbuilt" style={{ color: "var(--text-faint)" }}>
                {g.graph_built_at ? ago(g.graph_built_at) : "—"}
              </span>,
              <span key="user" style={{ color: "var(--text-faint)", fontSize: 11 }}>
                {(p.user_id || "").slice(0, 10)}
              </span>,
              <span key="time" style={{ color: "var(--text-faint)" }}>{ago(p.created_at)}</span>,
            ];
          })}
        />
      </Card>
    </div>
  );
}

function TasksPage() {
  const [data, setData] = useState([]);
  const [status, setStatus] = useState("");

  useEffect(() => {
    api.get("/admin/tasks", { params: { status, limit: 100 } })
      .then((r) => setData(r.data.tasks || [])).catch(() => {});
  }, [status]);

  return (
    <div style={{ padding: 24 }}>
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 14 }}>
        <h3 style={{ fontSize: 12, letterSpacing: "0.1em", textTransform: "uppercase",
                      color: "var(--text-faint)", margin: 0 }}>
          Tasks ({data.length})
        </h3>
        <select
          data-testid="admin-tasks-status"
          value={status} onChange={(e) => setStatus(e.target.value)}
          className="input" style={{ width: 140 }}>
          <option value="">All</option>
          <option value="done">Done</option>
          <option value="failed">Failed</option>
          <option value="running">Running</option>
          <option value="queued">Queued</option>
        </select>
      </div>
      <Card>
        <Table
          cols={["Task ID", "Task", "User", "Status", "Commit", "Created"]}
          rows={data.map((t) => [
            <span key="id" style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 11 }}>
              {t.task_id?.slice(0, 12)}
            </span>,
            <span key="task" style={{ maxWidth: 280, overflow: "hidden", textOverflow: "ellipsis",
                            whiteSpace: "nowrap", display: "block" }}>{t.task}</span>,
            <span key="user" style={{ color: "var(--text-faint)", fontSize: 11 }}>
              {(t.user_id || "").slice(0, 10)}
            </span>,
            <Badge key="status" color={STATUS_COLOR[t.status]}>{t.status}</Badge>,
            t.commit_sha ? <span key="commit" style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 11 }}>{t.commit_sha}</span> : "—",
            <span key="time" style={{ color: "var(--text-faint)" }}>{ago(t.created_at)}</span>,
          ])}
        />
      </Card>
    </div>
  );
}

function TokenPnL() {
  const [d, setD] = useState(null);
  useEffect(() => {
    api.get("/admin/token-pnl").then((r) => setD(r.data)).catch(() => {});
  }, []);
  if (!d) return <div style={{ padding: 24, color: "var(--text-faint)" }}>Loading…</div>;
  return (
    <div style={{ padding: 24 }}>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 12, marginBottom: 18 }}>
        <MCard label="Revenue (mo)" value={fmtMoney(d.revenue_month)} sub="Stripe pending" />
        <MCard label="AI cost (mo)" value={fmtMoney(d.ai_cost_month)} accent="var(--danger)" />
        <MCard label="Net profit" value={fmtMoney(d.net_profit)}
                accent={d.net_profit >= 0 ? "var(--ok)" : "var(--danger)"} />
        <MCard label="Tasks done (mo)" value={d.tasks_done_month} sub={`${d.tasks_done_today} today`} />
      </div>
      {d._note && (
        <Card style={{ padding: 12, fontSize: 12, color: "var(--text-dim)" }}>
          <b>Note:</b> {d._note}
        </Card>
      )}
    </div>
  );
}

function ComingSoon({ title, note }) {
  return (
    <div style={{ padding: 24 }}>
      <Card style={{ padding: 24, textAlign: "center", color: "var(--text-faint)" }}>
        <h3 style={{ fontSize: 14, marginBottom: 8 }}>{title}</h3>
        <div style={{ fontSize: 12 }}>{note}</div>
      </Card>
    </div>
  );
}

// ── Iter 188 — new admin sections ──────────────────────────────────────
// Each section maps 1:1 to a backend endpoint added in the same iter:
//   /admin/agent-performance, /admin/mcp-usage, /admin/warm-start-stats,
//   /admin/graph-status, /admin/postscan-issues, /admin/overview-metrics.
// All pages share the same loading-skeleton + Card/Table primitives
// used by the existing tabs so the UI stays consistent.

function AgentPerformancePage() {
  const [d, setD] = useState(null);
  useEffect(() => {
    api.get("/admin/agent-performance").then((r) => setD(r.data)).catch(() => setD({ per_model_30d: [] }));
  }, []);
  if (!d) return <div style={{ padding: 24, color: "var(--text-faint)" }}>Loading…</div>;
  const rows = d.per_model_30d || [];
  return (
    <div style={{ padding: 24 }}>
      <h3 style={{ fontSize: 12, letterSpacing: "0.1em", textTransform: "uppercase",
                    color: "var(--text-faint)", margin: "0 0 8px" }}>
        Agent performance — last 30 days ({rows.length} models)
      </h3>
      <Card>
        <Table
          cols={["Model", "Calls", "Done", "Success", "Avg latency"]}
          rows={rows.map((r) => [
            <span key="m" style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 11 }}>
              {r.model || "—"}
            </span>,
            r.calls,
            r.done,
            r.calls ? `${Math.round(100 * r.done / r.calls)}%` : "—",
            r.avg_secs ? `${r.avg_secs}s` : "—",
          ])}
        />
      </Card>
      {!rows.length && (
        <Card style={{ padding: 16, marginTop: 12, color: "var(--text-faint)", fontSize: 12 }}>
          No model usage yet in the last 30 days. Run a task to populate this view.
        </Card>
      )}
    </div>
  );
}

function McpUsagePage() {
  const [d, setD] = useState(null);
  useEffect(() => {
    api.get("/admin/mcp-usage", { params: { limit: 100 } })
      .then((r) => setD(r.data)).catch(() => setD({ rows: [] }));
  }, []);
  if (!d) return <div style={{ padding: 24, color: "var(--text-faint)" }}>Loading…</div>;
  const rows = d.rows || [];
  return (
    <div style={{ padding: 24 }}>
      {/* Iter 192 — API Keys CTA. Keys ARE MCP credentials, so the
          quick-link to the full key management page lives here
          (was a separate Overview button before). */}
      <div style={{ display: "flex", justifyContent: "space-between",
                    alignItems: "center", marginBottom: 12, gap: 12 }}>
        <h3 style={{ fontSize: 12, letterSpacing: "0.1em", textTransform: "uppercase",
                      color: "var(--text-faint)", margin: 0 }}>
          MCP API keys ({rows.length})
        </h3>
        <a
          data-testid="goto-api-keys"
          href="/admin/api-keys"
          style={{
            fontSize: 11, fontWeight: 600, letterSpacing: ".04em",
            padding: "6px 12px",
            background: "transparent",
            color: "var(--accent, #ff8a2a)",
            border: "1px solid var(--accent, #ff8a2a)",
            borderRadius: 5,
            textDecoration: "none",
          }}
        >🔑 Manage API Keys →</a>
      </div>
      <Card>
        <Table
          cols={["Key", "User", "Client", "Scope", "Last used", "Created"]}
          rows={rows.map((r) => [
            <span key="k" style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 11 }}>
              …{r.key_tail || "??????"}
            </span>,
            <span key="u" style={{ color: "var(--text-faint)", fontSize: 11 }}>
              {(r.user_id || "").slice(0, 12)}
            </span>,
            <Badge key="c">{r.client_id || "—"}</Badge>,
            <span key="s" style={{ fontSize: 11, color: "var(--text-dim)" }}>{r.scope || "mcp"}</span>,
            <span key="lu" style={{ color: "var(--text-faint)" }}>
              {r.last_used_at ? ago(r.last_used_at) : "never"}
            </span>,
            <span key="ca" style={{ color: "var(--text-faint)" }}>{ago(r.created_at)}</span>,
          ])}
        />
      </Card>
      {!rows.length && (
        <Card style={{ padding: 16, marginTop: 12, color: "var(--text-faint)", fontSize: 12 }}>
          No MCP keys yet. Users mint keys from Settings → MCP keys.
        </Card>
      )}
    </div>
  );
}

// Iter 192 — Reliability page merges the old Warm Start and Post-scan
// Issues tabs into one operational-health surface. Top half is warm
// start (cold-boot speed + success), bottom half is Vanguard post-scan
// findings (security regressions caught after commit).
function ReliabilityPage() {
  const [warm, setWarm] = useState(null);
  const [scan, setScan] = useState(null);
  useEffect(() => {
    api.get("/admin/warm-start-stats")
      .then((r) => setWarm(r.data))
      .catch(() => setWarm({ breakdown_7d: {} }));
    api.get("/admin/postscan-issues", { params: { limit: 100 } })
      .then((r) => setScan(r.data))
      .catch(() => setScan({ rows: [] }));
  }, []);

  if (!warm || !scan) {
    return <div style={{ padding: 24, color: "var(--text-faint)" }}>Loading…</div>;
  }

  const breakdown = warm.breakdown_7d || {};
  const warmTotal = Object.values(breakdown).reduce((a, b) => a + b, 0);
  const warmDone  = breakdown.done || 0;
  const warmPct   = warmTotal ? Math.round(100 * warmDone / warmTotal) : 0;
  const issues = scan.rows || [];
  const critical = issues.filter((r) => r.severity === "critical").length;
  const warning  = issues.filter((r) => ["warning", "warn"].includes(r.severity)).length;

  return (
    <div style={{ padding: 24 }}>
      {/* Warm Start */}
      <h3 style={{ fontSize: 12, letterSpacing: "0.1em", textTransform: "uppercase",
                    color: "var(--text-faint)", margin: "0 0 8px" }}>
        Warm Start — last 24 h / 7 d / 30 d
      </h3>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 12, marginBottom: 14 }}>
        <MCard label="Avg warm time" value={warm.avg_seconds ? `${warm.avg_seconds}s` : "—"}
                sub="Last 100 done jobs · 30 d" />
        <MCard label="Success rate (7 d)" value={warmTotal ? `${warmPct}%` : "—"}
                accent={warmPct >= 90 ? "var(--ok)" : warmPct >= 70 ? "var(--warn)" : "var(--danger)"}
                sub={`${warmDone}/${warmTotal} jobs`} />
        <MCard label="Total jobs (7 d)" value={warmTotal} sub={`${warm.window_days}-day window`} />
      </div>
      <Card style={{ marginBottom: 28 }}>
        <Table
          cols={["Status", "Count"]}
          rows={Object.entries(breakdown).map(([k, v]) => [
            <Badge key="s" color={k === "done" ? "var(--ok)" : k === "failed" ? "var(--danger)" : undefined}>{k}</Badge>,
            v,
          ])}
        />
      </Card>

      {/* Post-scan */}
      <h3 style={{ fontSize: 12, letterSpacing: "0.1em", textTransform: "uppercase",
                    color: "var(--text-faint)", margin: "0 0 8px" }}>
        Post-scan issues — Vanguard 007 findings
      </h3>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 12, marginBottom: 14 }}>
        <MCard label="Critical findings" value={critical} accent="var(--danger)"
                sub="Commit blockers" />
        <MCard label="Warnings" value={warning} accent="var(--warn)" />
        <MCard label="Total shown" value={issues.length} />
      </div>
      <Card>
        <Table
          cols={["Severity", "Rule", "File", "Match", "Task", "When"]}
          rows={issues.map((r) => [
            <Badge key="s" color={r.severity === "critical" ? "var(--danger)" : "var(--warn)"}>
              {r.severity}
            </Badge>,
            <span key="r" style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 11 }}>
              {r.rule || "—"}
            </span>,
            <span key="f" style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 11,
                                    maxWidth: 220, overflow: "hidden", textOverflow: "ellipsis",
                                    whiteSpace: "nowrap", display: "block" }}>
              {r.file || "—"}
            </span>,
            <span key="m" style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 10,
                                    color: "var(--text-faint)",
                                    maxWidth: 260, overflow: "hidden", textOverflow: "ellipsis",
                                    whiteSpace: "nowrap", display: "block" }}>
              {r.match || ""}
            </span>,
            <span key="t" style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 11 }}>
              {(r.task_id || "").slice(0, 10)}
            </span>,
            <span key="w" style={{ color: "var(--text-faint)" }}>{ago(r.created_at)}</span>,
          ])}
        />
      </Card>
      {!issues.length && (
        <Card style={{ padding: 16, marginTop: 12, color: "var(--text-faint)", fontSize: 12 }}>
          No post-scan findings recorded. Vanguard 007 runs on every commit — empty here
          means clean ships.
        </Card>
      )}
    </div>
  );
}

// Iter 192 — Payments & Revenue merged. Top: revenue/profit metric
// cards (was the standalone Revenue tab). Bottom: per-transaction
// Stripe ledger (was the Payments tab).
function PaymentsPage() {
  const [d, setD] = useState(null);
  const [m, setM] = useState(null);
  const [pnl, setPnl] = useState(null);
  useEffect(() => {
    api.get("/admin/payments").then((r) => setD(r.data)).catch(() => setD({ payments: [], total_revenue: 0, count: 0 }));
    api.get("/admin/overview-metrics").then((r) => setM(r.data)).catch(() => setM({}));
    api.get("/admin/token-pnl").then((r) => setPnl(r.data)).catch(() => setPnl({}));
  }, []);
  if (!d || !m || !pnl) return <div style={{ padding: 24, color: "var(--text-faint)" }}>Loading…</div>;
  const pending = (d.payments || []).filter((p) => p.payment_status !== "paid").length;
  return (
    <div style={{ padding: 24 }}>
      {/* Revenue snapshot (was standalone Revenue tab in iter 188). */}
      <h3 style={{ fontSize: 12, letterSpacing: "0.1em", textTransform: "uppercase",
                    color: "var(--text-faint)", margin: "0 0 8px" }}>
        Stripe revenue
      </h3>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 12, marginBottom: 18 }}>
        <MCard label="Revenue (30 d)" value={fmtMoney(m.revenue_30d || 0)}
                accent="var(--ok)" sub="paid checkouts" />
        <MCard label="Revenue (mo)" value={fmtMoney(pnl.revenue_month || 0)}
                sub="Stripe pending" />
        <MCard label="AI cost (mo)" value={fmtMoney(pnl.ai_cost_month || 0)}
                accent="var(--danger)" />
        <MCard label="Net profit" value={fmtMoney(pnl.net_profit || 0)}
                accent={(pnl.net_profit || 0) >= 0 ? "var(--ok)" : "var(--danger)"} />
      </div>

      {/* Transaction ledger. */}
      <h3 style={{ fontSize: 12, letterSpacing: "0.1em", textTransform: "uppercase",
                    color: "var(--text-faint)", margin: "0 0 8px" }}>
        Transactions
      </h3>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 12, marginBottom: 14 }}>
        <MCard label="Lifetime revenue" value={fmtMoney(d.total_revenue)} accent="var(--ok)" />
        <MCard label="Transactions" value={d.count} />
        <MCard label="Pending" value={pending} />
      </div>
      <Card>
        <Table
          cols={["Tier", "User", "Amount", "Status", "When"]}
          rows={(d.payments || []).map((p) => [
            <Badge key="tier">{p.tier}</Badge>,
            <span key="user" style={{ fontSize: 11, color: "var(--text-faint)" }}>{p.user_email}</span>,
            fmtMoney(p.amount),
            <Badge key="status" color={p.payment_status === "paid" ? "var(--ok)" : "var(--text-faint)"}>
              {p.payment_status || p.status}
            </Badge>,
            <span key="time" style={{ color: "var(--text-faint)" }}>{ago(p.created_at)}</span>,
          ])}
        />
      </Card>
    </div>
  );
}

function SupportPage() {
  const [tickets, setTickets] = useState([]);
  const [selected, setSelected] = useState(null);
  const [reply, setReply] = useState("");
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await api.get("/admin/support");
      setTickets(r.data.tickets || []);
      if (r.data.tickets?.length && !selected) setSelected(r.data.tickets[0]);
    } finally { setLoading(false); }
  }, [selected]);

  useEffect(() => { load(); }, []); // eslint-disable-line

  async function sendReply() {
    if (!reply.trim() || !selected) return;
    try {
      await api.post(`/admin/support/${selected.ticket_id}/reply`, { message: reply });
      setReply("");
      toast({ message: "Reply sent", kind: "success" });
      await load();
    } catch (e) {
      toast({ message: e?.response?.data?.detail || "Send failed", kind: "error" });
    }
  }

  async function resolve(tid) {
    if (!window.confirm("Mark as resolved?")) return;
    try {
      await api.post(`/admin/support/${tid}/resolve`);
      toast({ message: "Resolved", kind: "success" });
      await load();
    } catch (e) {
      toast({ message: "Failed", kind: "error" });
    }
  }

  if (loading) return <div style={{ padding: 24, color: "var(--text-faint)" }}>Loading…</div>;

  return (
    <div style={{ padding: 24 }}>
      <h3 style={{ fontSize: 12, letterSpacing: "0.1em", textTransform: "uppercase",
                    color: "var(--text-faint)", margin: "0 0 12px" }}>
        Support inbox ({tickets.filter(t => t.status === "open").length} open)
      </h3>
      {tickets.length === 0 ? (
        <Card style={{ padding: 24, textAlign: "center", color: "var(--text-faint)" }}>
          No tickets yet.
        </Card>
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: "280px 1fr",
                       gap: 12, height: 520 }}>
          <Card style={{ overflowY: "auto" }}>
            {tickets.map((t) => (
              <div
                key={t.ticket_id}
                data-testid={`admin-support-ticket-${t.ticket_id}`}
                onClick={() => setSelected(t)}
                style={{
                  padding: "10px 12px", borderBottom: "1px solid var(--border)",
                  cursor: "pointer",
                  background: selected?.ticket_id === t.ticket_id ? "var(--bg-elev)" : "",
                }}>
                <div style={{ fontSize: 12, fontWeight: 600 }}>{t.subject}</div>
                <div style={{ fontSize: 11, color: "var(--text-faint)", marginTop: 2 }}>
                  {t.user_email} · {ago(t.created_at)}
                </div>
                <div style={{ marginTop: 4 }}>
                  <Badge color={STATUS_COLOR[t.status === "resolved" ? "done" : (t.status === "open" ? "failed" : "running")]}>
                    {t.status}
                  </Badge>
                </div>
              </div>
            ))}
          </Card>
          {selected && (
            <Card style={{ display: "flex", flexDirection: "column" }}>
              <div style={{ padding: "12px 14px", borderBottom: "1px solid var(--border)",
                             display: "flex", justifyContent: "space-between" }}>
                <div>
                  <div style={{ fontSize: 13, fontWeight: 600 }}>{selected.subject}</div>
                  <div style={{ fontSize: 11, color: "var(--text-faint)" }}>
                    {selected.user_email} · {ago(selected.created_at)}
                  </div>
                </div>
                {selected.status !== "resolved" && (
                  <button
                    data-testid={`admin-support-resolve-${selected.ticket_id}`}
                    className="btn-ghost" style={{ padding: "4px 10px", fontSize: 11 }}
                    onClick={() => resolve(selected.ticket_id)}>
                    resolve
                  </button>
                )}
              </div>
              <div style={{ flex: 1, overflowY: "auto", padding: 12,
                             display: "flex", flexDirection: "column", gap: 8 }}>
                {(selected.messages || []).map((m, i) => (
                  <div key={i} style={{
                    alignSelf: m.sender === "admin" ? "flex-end" : "flex-start",
                    maxWidth: "80%",
                    padding: "8px 12px", borderRadius: 4, fontSize: 12,
                    background: m.sender === "admin"
                      ? "rgba(255,138,42,0.1)" : "var(--bg-elev)",
                    border: "1px solid var(--border)",
                  }}>
                    <div style={{ fontSize: 10, color: "var(--text-faint)", marginBottom: 4 }}>
                      {m.sender} · {ago(m.ts)}
                    </div>
                    {m.message}
                  </div>
                ))}
              </div>
              {selected.status !== "resolved" && (
                <div style={{ padding: 10, borderTop: "1px solid var(--border)",
                               display: "flex", gap: 8 }}>
                  <input
                    data-testid="admin-support-reply-input"
                    value={reply}
                    onChange={(e) => setReply(e.target.value)}
                    placeholder="Type reply…"
                    className="input"
                    onKeyDown={(e) => e.key === "Enter" && sendReply()}
                    style={{ flex: 1 }}
                  />
                  <button
                    data-testid="admin-support-reply-send"
                    className="btn-primary" onClick={sendReply}>
                    send
                  </button>
                </div>
              )}
            </Card>
          )}
        </div>
      )}
    </div>
  );
}

function Architecture() {
  const [d, setD] = useState(null);
  useEffect(() => {
    api.get("/admin/architecture").then((r) => setD(r.data)).catch(() => {});
  }, []);
  if (!d) return <div style={{ padding: 24, color: "var(--text-faint)" }}>Loading…</div>;
  // Iter 64 — sort: live → degraded → unreachable so green stays on top
  const order = { live: 0, degraded: 1, unreachable: 2, down: 3 };
  const services = Object.entries(d.services).sort(
    ([, a], [, b]) => (order[a.status] ?? 9) - (order[b.status] ?? 9)
  );
  return (
    <div style={{ padding: 24 }}>
      <PersonaQualityTile />
      <h3 style={{ fontSize: 12, letterSpacing: "0.1em", textTransform: "uppercase",
                    color: "var(--text-faint)", margin: "0 0 8px" }}>External services</h3>
      <div style={{
        display: "grid",
        gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
        gap: 12, marginBottom: 18,
      }}>
        {services.map(([name, info]) => (
          <Card key={name} style={{ padding: 14 }}>
            <div style={{ display: "flex", justifyContent: "space-between",
                          alignItems: "center", gap: 8, flexWrap: "wrap" }}>
              <b style={{ fontSize: 13, overflowWrap: "anywhere" }}>{name}</b>
              <Badge color={
                info.status === "live" ? "var(--ok)" :
                info.status === "degraded" ? "var(--warn, #ffc560)" :
                "var(--danger)"
              }>
                {info.status}
              </Badge>
            </div>
            <div style={{ fontSize: 11, color: "var(--text-faint)", marginTop: 6,
                           fontFamily: "'JetBrains Mono', monospace" }}>
              {info.latency_ms != null ? `${info.latency_ms}ms` : "—"}
              {info.note ? ` · ${info.note}` : ""}
            </div>
          </Card>
        ))}
      </div>
      <h3 style={{ fontSize: 12, letterSpacing: "0.1em", textTransform: "uppercase",
                    color: "var(--text-faint)", margin: "0 0 8px" }}>Integrations</h3>
      <Card style={{ padding: 14 }}>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
          {Object.entries(d.integrations).map(([k, v]) => (
            <Badge key={k} color={v ? "var(--ok)" : "var(--text-faint)"}>
              {k} · {v ? "OK" : "missing"}
            </Badge>
          ))}
        </div>
        {d.note && (
          <div style={{ marginTop: 12, fontSize: 11, color: "var(--text-dim)", lineHeight: 1.6 }}>
            {d.note}
          </div>
        )}
      </Card>

      <h3 style={{ fontSize: 12, letterSpacing: "0.1em", textTransform: "uppercase",
                    color: "var(--text-faint)", margin: "22px 0 8px" }}>
        Code surface · routers · services · pages
      </h3>
      <CodeSurfaceLive />
    </div>
  );
}

function PersonaQualityTile() {
  const [d, setD] = useState(null);
  useEffect(() => {
    api.get("/admin/eval-quality").then((r) => setD(r.data)).catch(() => {});
  }, []);
  if (!d) return null;
  const t = d.totals || {};
  const latest = d.latest || {};
  const score = latest.total
    ? Math.round(100 * (latest.passed / latest.total))
    : null;
  const blocked = (latest.hard_fails || 0) > 0;
  const color = blocked ? "var(--danger)"
              : score == null ? "var(--text-faint)"
              : score >= 90 ? "var(--ok)"
              : score >= 75 ? "var(--warn, #ffc560)"
              : "var(--danger)";
  return (
    <div data-testid="persona-quality-tile" style={{ marginBottom: 18 }}>
      <h3 style={{ fontSize: 12, letterSpacing: "0.1em",
        textTransform: "uppercase", color: "var(--text-faint)",
        margin: "0 0 8px" }}>Persona Quality Score · last 30 days</h3>
      <Card style={{ padding: 16 }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 14, flexWrap: "wrap" }}>
          <div style={{ fontSize: 30, fontWeight: 700, color, fontFamily: "'JetBrains Mono', monospace" }}>
            {score == null ? "—" : `${score}/100`}
          </div>
          <div style={{ fontSize: 11, color: "var(--text-faint)" }}>
            latest: {latest.passed ?? 0}/{latest.total ?? 0} pass ·
            hard fails {latest.hard_fails ?? 0} · runs {t.runs ?? 0}
          </div>
          <div style={{ marginLeft: "auto", display: "flex", gap: 3, alignItems: "flex-end", height: 22 }}>
            {(d.trend || []).slice(-30).map((p, i) => (
              <div key={i} title={`${p.ts} — ${p.score}/100 · ${p.hard_fails} hard fail(s)`}
                style={{
                  width: 5,
                  height: Math.max(3, Math.round((p.score / 100) * 22)),
                  background: p.hard_fails > 0 ? "var(--danger)"
                            : p.score >= 90 ? "var(--ok)"
                            : "var(--warn, #ffc560)",
                  borderRadius: 1,
                }} />
            ))}
          </div>
        </div>
      </Card>
    </div>
  );
}


function CodeSurfaceLive() {
  const [data, setData] = useState(null);
  const [err, setErr]   = useState(null);
  useEffect(() => {
    api.get("/admin/code-surface")
      .then((r) => setData(r.data))
      .catch((e) => setErr(e?.response?.data?.detail || e?.message || "unreachable"));
  }, []);
  if (err) {
    return (
      <div data-testid="arch-code-surface-error" style={{
        padding: 14,
        border: "1px solid rgba(226,75,74,0.3)",
        background: "rgba(226,75,74,0.08)",
        borderRadius: 8,
        color: "var(--text-dim)",
        fontSize: 12,
      }}>
        Code surface unreachable: <code>{err}</code>
      </div>
    );
  }
  if (!data) {
    return (
      <div style={{ padding: 14, color: "var(--text-faint)", fontSize: 12 }}>
        Loading code surface…
      </div>
    );
  }
  const surface = data.surface || {};
  const columns = [
    { key: "routers",    title: "Routers" },
    { key: "services",   title: "Services" },
    { key: "pages",      title: "Pages" },
    { key: "components", title: "Components" },
  ];
  return (
    <>
      <div style={{
        fontSize: 11, color: "var(--text-faint)", marginBottom: 10,
      }}>
        Live · {data.total_files} files across 4 surfaces · auto-walked from disk
        {" · "}drift-proof (no hand-maintained list)
      </div>
      <div data-testid="arch-code-surface" style={{
        display: "grid",
        gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))",
        gap: 12,
      }}>
        {columns.map((col) => {
          const items = surface[col.key] || [];
          return (
            <Card key={col.key} style={{ padding: 14 }}>
              <div style={{
                fontSize: 10, letterSpacing: "0.08em", textTransform: "uppercase",
                color: "var(--accent-2, #ffb347)", marginBottom: 8,
                fontWeight: 600,
              }}>{col.title} · {items.length}</div>
              <ul style={{ listStyle: "none", margin: 0, padding: 0,
                            display: "grid", gap: 4,
                            maxHeight: 360, overflowY: "auto" }}>
                {items.map((it) => (
                  <li key={it.file || it.name}
                      title={it.desc || ""}
                      style={{
                    fontSize: 11.5, color: "var(--text-dim)",
                    fontFamily: "'JetBrains Mono', monospace",
                    display: "flex", justifyContent: "space-between",
                    gap: 8,
                  }}>
                    <span style={{ overflowWrap: "anywhere" }}>{it.file || it.name}</span>
                    {it.lines > 0 && (
                      <span style={{
                        color: "var(--text-faint)", fontSize: 10,
                        whiteSpace: "nowrap", flexShrink: 0,
                      }}>{it.lines}L</span>
                    )}
                  </li>
                ))}
              </ul>
            </Card>
          );
        })}
      </div>
    </>
  );
}

function SettingsPage() {
  const [s, setS] = useState(null);
  const [busy, setBusy] = useState(false);
  const [upgrading, setUpgrading] = useState(null);  // tier id while in flight
  useEffect(() => {
    api.get("/admin/settings").then((r) => setS(r.data)).catch(() => {});
    // After Stripe redirect, poll status once
    const params = new URLSearchParams(window.location.search);
    const sid = params.get("session_id");
    if (sid) {
      api.get(`/payments/status/${sid}`).then((r) => {
        if (r.data.payment_status === "paid") {
          toast({ message: `Upgraded to ${r.data.tier} ✓`, kind: "success" });
        } else {
          toast({ message: `Payment ${r.data.payment_status}`, kind: "info" });
        }
        window.history.replaceState({}, "", "/admin");
      }).catch(() => {});
    }
  }, []);

  function upgrade(tier) {
    setUpgrading(tier);
    api.post("/payments/checkout", {
      tier,
      origin_url: window.location.origin,
    })
      .then((r) => { window.location.href = r.data.url; })
      .catch((e) => {
        toast({ message: e?.response?.data?.detail || "Could not start checkout", kind: "error" });
        setUpgrading(null);
      });
  }

  if (!s) return <div style={{ padding: 24, color: "var(--text-faint)" }}>Loading…</div>;

  async function save() {
    setBusy(true);
    try {
      await api.post("/admin/settings", s);
      toast({ message: "Settings saved", kind: "success" });
    } catch (e) {
      toast({ message: e?.response?.data?.detail || "Save failed", kind: "error" });
    } finally { setBusy(false); }
  }

  return (
    <div style={{ padding: 24, maxWidth: 960 }}>
      <h3 style={{ fontSize: 13, margin: "0 0 14px" }}>Upgrade your plan</h3>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, marginBottom: 20 }}>
        {[
          { id: "pro", label: "Pro", price: "$29/mo" },
          { id: "team", label: "Team", price: "$99/mo" },
        ].map((p) => (
          <Card key={p.id} style={{ padding: 14 }}>
            <div style={{ fontSize: 14, fontWeight: 600 }}>{p.label}</div>
            <div style={{ fontSize: 11, color: "var(--text-faint)", marginBottom: 10 }}>{p.price}</div>
            <button
              data-testid={`upgrade-${p.id}`}
              onClick={() => upgrade(p.id)}
              disabled={upgrading === p.id}
              className="btn-primary"
              style={{ width: "100%" }}>
              {upgrading === p.id ? "redirecting…" : `Upgrade → ${p.label}`}
            </button>
          </Card>
        ))}
      </div>

      <h3 style={{ fontSize: 13, margin: "20px 0 14px" }}>Token limits per plan</h3>
      {["free", "pro", "team"].map((plan) => (
        <div key={plan} style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 10 }}>
          <span style={{ width: 80, textTransform: "capitalize", fontSize: 12 }}>{plan}</span>
          <input
            data-testid={`admin-limit-${plan}`}
            type="number" className="input"
            value={s.token_limits?.[plan] || 0}
            onChange={(e) => setS({
              ...s, token_limits: { ...s.token_limits, [plan]: +e.target.value }
            })} />
        </div>
      ))}
      <h3 style={{ fontSize: 13, margin: "20px 0 14px" }}>Pricing ($/mo)</h3>
      {["free", "pro", "team"].map((plan) => (
        <div key={plan} style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 10 }}>
          <span style={{ width: 80, textTransform: "capitalize", fontSize: 12 }}>{plan}</span>
          <input
            data-testid={`admin-price-${plan}`}
            type="number" className="input"
            value={s.pricing?.[plan] || 0}
            onChange={(e) => setS({
              ...s, pricing: { ...s.pricing, [plan]: +e.target.value }
            })} />
        </div>
      ))}
      <button
        data-testid="admin-settings-save"
        onClick={save} disabled={busy}
        className="btn-primary" style={{ marginTop: 14 }}>
        {busy ? "Saving…" : "Save settings"}
      </button>

      {/* Iter 212m-20 — Admin 2FA enrollment card. Place this BEFORE
          the Stripe card so a brand-new admin is nudged toward the
          security best practice first. */}
      <TwoFactorCard />

      {/* Iter 191 — Stripe API key card with edit/save + live ping
          (green/red status light, account info, error reason). */}
      <StripeApiKeyCard />

      {/* Iter 158 — thinking-hint manager (tier-aware upsell pills
          shown next to the chat spinner). Full CRUD + global toggle
          + delay slider. */}
      <ThinkingHintsConfigCard />
      <AdminThinkingHints />

      {/* Iter 195 — ORA Council moved into Settings (was its own
          sidebar tab). Council settings live alongside other admin
          tunables (Stripe key, thinking hints) so configuration
          surfaces are in one place. */}
      <div style={{ marginTop: 28, paddingTop: 20,
                     borderTop: "1px solid var(--line, rgba(255,255,255,0.06))" }}>
        <h3 style={{ fontSize: 13, margin: "0 0 14px",
                      display: "flex", alignItems: "center", gap: 8 }}>
          <Brain size={14} style={{ color: "var(--accent, #ff8a2a)" }} />
          ORA Council
        </h3>
        <AuremAdminPanel />
      </div>
    </div>
  );
}

// ─── Iter 191 — Stripe API key card ──────────────────────────────────
// Live status indicator (green = key verified via Account.retrieve,
// red = the exact reason returned by Stripe). Edit/Save flow validates
// the new key BEFORE persisting so a broken key can never overwrite a
// working one.
function StripeApiKeyCard() {
  const [data, setData] = useState(null);
  const [editing, setEditing] = useState(false);
  const [newKey, setNewKey] = useState("");
  const [saving, setSaving] = useState(false);

  async function refresh() {
    try {
      const r = await api.get("/admin/stripe-config");
      setData(r.data);
    } catch (e) {
      setData({ configured: false, status: "error",
                error: e?.response?.data?.detail || "Could not load Stripe config" });
    }
  }

  useEffect(() => { refresh(); }, []);

  async function save() {
    if (!newKey.trim()) return;
    setSaving(true);
    try {
      await api.post("/admin/stripe-config", { api_key: newKey.trim() });
      toast({ message: "Stripe key validated & saved ✓", kind: "success" });
      setNewKey("");
      setEditing(false);
      await refresh();
    } catch (e) {
      toast({
        message: e?.response?.data?.detail || "Validation failed",
        kind: "error",
      });
    } finally { setSaving(false); }
  }

  if (!data) {
    return (
      <Card style={{ padding: 18, marginTop: 24 }}>
        <div style={{ color: "var(--text-faint)", fontSize: 12 }}>
          Loading Stripe status…
        </div>
      </Card>
    );
  }

  const ok = data.status === "ok";
  const dot = ok ? "#22c55e" : "#ef4444";
  const dotShadow = ok ? "rgba(34,197,94,0.35)" : "rgba(239,68,68,0.35)";
  const acct = data.account || {};

  return (
    <Card style={{ padding: 18, marginTop: 24 }} data-testid="admin-stripe-card">
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between",
                    marginBottom: 12, gap: 10 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <span
            data-testid="admin-stripe-status-dot"
            style={{
              width: 12, height: 12, borderRadius: "50%",
              background: dot,
              boxShadow: `0 0 0 4px ${dotShadow}, 0 0 12px ${dot}`,
              animation: ok ? "pulseDot 2.4s ease-in-out infinite" : "none",
              flexShrink: 0,
            }}
          />
          <h3 style={{ fontSize: 13, margin: 0 }}>Stripe API key</h3>
          {data.mode && data.mode !== "unknown" && (
            <Badge color={data.mode === "live" ? "var(--ok)" : "var(--warn)"}>
              {data.mode}
            </Badge>
          )}
          {data.source && (
            <span style={{ fontSize: 10, color: "var(--text-faint)",
                            fontFamily: "'JetBrains Mono', monospace",
                            textTransform: "uppercase", letterSpacing: "0.06em" }}>
              source: {data.source}
            </span>
          )}
        </div>
        {!editing && (
          <button
            data-testid="admin-stripe-edit"
            className="btn-secondary"
            style={{ fontSize: 12, padding: "6px 14px" }}
            onClick={() => { setNewKey(""); setEditing(true); }}
          >
            Edit
          </button>
        )}
      </div>

      {!editing && (
        <>
          {ok ? (
            <div data-testid="admin-stripe-ok"
                 style={{
                   padding: "10px 12px", marginBottom: 8,
                   background: "rgba(34,197,94,0.06)",
                   border: "1px solid rgba(34,197,94,0.18)",
                   borderRadius: 8,
                   fontSize: 12, color: "#86efac",
                   fontFamily: "'JetBrains Mono', monospace",
                 }}>
              ● Connected — sk_{data.mode}_…{data.last4}
            </div>
          ) : (
            <div data-testid="admin-stripe-err"
                 style={{
                   padding: "10px 12px", marginBottom: 8,
                   background: "rgba(239,68,68,0.06)",
                   border: "1px solid rgba(239,68,68,0.2)",
                   borderRadius: 8,
                   fontSize: 12, color: "#fca5a5",
                   lineHeight: 1.5,
                 }}>
              <div style={{ fontWeight: 600, marginBottom: 4 }}>● Not working</div>
              <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 11 }}>
                {data.error || "Unknown error"}
              </div>
            </div>
          )}

          {ok && (
            <div style={{ display: "grid", gridTemplateColumns: "120px 1fr",
                          gap: "6px 14px", fontSize: 11,
                          color: "var(--text-faint)",
                          fontFamily: "'JetBrains Mono', monospace",
                          marginTop: 4 }}>
              <span>Account</span><span style={{ color: "var(--text)" }}>{acct.id || "—"}</span>
              <span>Business</span><span style={{ color: "var(--text)" }}>{acct.business_name || "—"}</span>
              <span>Email</span><span style={{ color: "var(--text)" }}>{acct.email || "—"}</span>
              <span>Country</span><span style={{ color: "var(--text)" }}>{acct.country || "—"}</span>
              <span>Charges</span>
              <span style={{ color: acct.charges_enabled ? "var(--ok)" : "var(--danger)" }}>
                {acct.charges_enabled ? "enabled" : "disabled"}
              </span>
              <span>Payouts</span>
              <span style={{ color: acct.payouts_enabled ? "var(--ok)" : "var(--danger)" }}>
                {acct.payouts_enabled ? "enabled" : "disabled"}
              </span>
            </div>
          )}
        </>
      )}

      {editing && (
        <div style={{ marginTop: 8 }}>
          <label style={{ fontSize: 11, color: "var(--text-faint)",
                          display: "block", marginBottom: 6,
                          fontFamily: "'JetBrains Mono', monospace",
                          textTransform: "uppercase", letterSpacing: "0.08em" }}>
            Paste new key (sk_live_… or sk_test_…)
          </label>
          <input
            data-testid="admin-stripe-key-input"
            className="input"
            type="password"
            value={newKey}
            onChange={(e) => setNewKey(e.target.value)}
            placeholder="sk_live_……"
            autoFocus
            style={{ width: "100%", fontFamily: "'JetBrains Mono', monospace",
                     fontSize: 12 }}
          />
          <div style={{ marginTop: 10, display: "flex", gap: 8 }}>
            <button
              data-testid="admin-stripe-save"
              className="btn-primary"
              onClick={save}
              disabled={saving || !newKey.trim()}
              style={{ fontSize: 12 }}>
              {saving ? "Validating with Stripe…" : "Save"}
            </button>
            <button
              data-testid="admin-stripe-cancel"
              className="btn-secondary"
              onClick={() => { setEditing(false); setNewKey(""); }}
              disabled={saving}
              style={{ fontSize: 12 }}>
              Cancel
            </button>
          </div>
          <div style={{ marginTop: 10, fontSize: 10, color: "var(--text-faint)",
                        lineHeight: 1.5 }}>
            Key is validated via a live <code>Account.retrieve()</code> call
            before saving. If Stripe rejects it, nothing is persisted and the
            old key keeps working.
          </div>
        </div>
      )}

      <style>{`
        @keyframes pulseDot {
          0%, 100% { box-shadow: 0 0 0 4px ${dotShadow}, 0 0 12px ${dot}; }
          50%      { box-shadow: 0 0 0 8px ${dotShadow}, 0 0 18px ${dot}; }
        }
      `}</style>
    </Card>
  );
}

function ThinkingHintsConfigCard() {
  const [cfg, setCfg] = useState(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    api.get("/admin/thinking-hints-config")
      .then((r) => setCfg({
        enabled: r.data?.enabled ?? true,
        delay_ms: r.data?.delay_ms ?? 600,
      }))
      .catch(() => setCfg({ enabled: true, delay_ms: 600 }));
  }, []);

  if (!cfg) return null;

  async function save() {
    setSaving(true);
    try {
      await api.post("/admin/thinking-hints-config", cfg);
      toast({ message: "Hint config saved", kind: "success" });
    } catch (e) {
      toast({
        message: e?.response?.data?.detail || "Save failed",
        kind: "error",
      });
    } finally { setSaving(false); }
  }

  return (
    <div
      data-testid="hints-config-card"
      style={{
        marginTop: 28, padding: 14, borderRadius: 10,
        background: "rgba(255,255,255,0.02)",
        border: "1px solid var(--border)",
      }}
    >
      <h3 style={{ fontSize: 13, margin: "0 0 6px" }}>
        💡 Thinking-Hint Global Config
      </h3>
      <p style={{ fontSize: 11, color: "var(--text-faint)", margin: "0 0 14px" }}>
        Master kill-switch + delay tuner. Per-hint copy is managed below.
      </p>
      <label style={{
        display: "flex", alignItems: "center", gap: 10, fontSize: 12,
        marginBottom: 14, cursor: "pointer",
      }}>
        <input
          data-testid="hints-config-enabled"
          type="checkbox"
          checked={!!cfg.enabled}
          onChange={(e) => setCfg({ ...cfg, enabled: e.target.checked })}
        />
        Show thinking hints to users
        <span style={{
          marginLeft: 8, fontSize: 10, letterSpacing: "0.1em",
          padding: "2px 8px", borderRadius: 999,
          background: cfg.enabled
            ? "rgba(110, 231, 183, 0.12)"
            : "rgba(255, 80, 80, 0.10)",
          color: cfg.enabled ? "var(--ok, #6ee7b7)" : "var(--danger, #ef4444)",
          border: `1px solid ${cfg.enabled
            ? "rgba(110, 231, 183, 0.35)"
            : "rgba(255, 80, 80, 0.35)"}`,
        }}>
          {cfg.enabled ? "ENABLED" : "DISABLED"}
        </span>
      </label>
      <div style={{ marginBottom: 10 }}>
        <div style={{
          display: "flex", justifyContent: "space-between",
          fontSize: 11, color: "var(--text-dim)", marginBottom: 4,
        }}>
          <span>Delay before hint appears</span>
          <span data-testid="hints-config-delay-value"
                style={{ fontFamily: "'JetBrains Mono', monospace" }}>
            {cfg.delay_ms} ms
          </span>
        </div>
        <input
          data-testid="hints-config-delay"
          type="range"
          min={200} max={5000} step={100}
          value={cfg.delay_ms}
          onChange={(e) => setCfg({ ...cfg, delay_ms: +e.target.value })}
          style={{ width: "100%" }}
        />
        <div style={{
          display: "flex", justifyContent: "space-between",
          fontSize: 10, color: "var(--text-faint)", marginTop: 2,
        }}>
          <span>200ms (instant)</span><span>5000ms (slow)</span>
        </div>
      </div>
      <button
        data-testid="hints-config-save"
        onClick={save} disabled={saving} className="btn-primary"
        style={{ fontSize: 11 }}
      >
        {saving ? "Saving…" : "Save config"}
      </button>
    </div>
  );
}

// ── Shell ──────────────────────────────────────────────────────────────
// Iter 192 — sidebar consolidated per founder feedback:
//   • API Keys: lived as a separate top-level page → now opened from
//     inside MCP Usage (keys ARE MCP credentials).
//   • Revenue + Payments: merged into one "Payments & Revenue" tab.
//   • Architecture: was its own tab → now lives inside Overview.
//   • Graph Status: was its own tab → folded into Projects (per-project
//     row already shows graph state).
// ──────────────────────────────────────────────────────────────────
// Iter 210 — Audit feed page
// Hits GET /admin/audit and renders one row per ORA turn. Plain
// dark-theme table matching the rest of the panel.
// ──────────────────────────────────────────────────────────────────
function AuditPage() {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState(null);
  const [err, setErr] = useState("");

  async function load() {
    setLoading(true); setErr("");
    try {
      const r = await api.get("/admin/audit?limit=100");
      setRows(r.data?.rows || []);
    } catch (e) {
      setErr(e?.response?.data?.detail || e.message || "Failed to load audit feed");
    } finally { setLoading(false); }
  }
  useEffect(() => { load(); }, []);

  return (
    <div data-testid="admin-audit-page" style={{ padding: 24 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 14 }}>
        <h2 style={{ margin: 0, fontSize: 16, color: "var(--text)" }}>Audit feed</h2>
        <button onClick={load} data-testid="admin-audit-refresh"
                className="btn-ghost" style={{ fontSize: 11 }}>
          Refresh
        </button>
      </div>
      {loading && <div style={{ color: "var(--text-faint)", fontSize: 12 }}>Loading…</div>}
      {err && <div style={{ color: "#ef4444", fontSize: 12 }}>{err}</div>}
      {!loading && rows.length === 0 && !err && (
        <div style={{ color: "var(--text-faint)", fontSize: 12 }}>
          No audit rows yet. Have a user chat with ORA and refresh.
        </div>
      )}
      {rows.length > 0 && (
        <div style={{ overflowX: "auto" }}>
        <table data-testid="admin-audit-table" style={{
          width: "100%", borderCollapse: "collapse", fontSize: 11,
          fontFamily: "'JetBrains Mono', monospace",
        }}>
          <thead>
            <tr style={{ color: "var(--text-faint)", textAlign: "left",
                          borderBottom: "1px solid var(--border)" }}>
              <th style={th}>Timestamp</th>
              <th style={th}>User</th>
              <th style={th}>Project</th>
              <th style={th}>Tools</th>
              <th style={th} title="Citation guard triggered?">🛡️</th>
              <th style={th}>⚠️ Signals</th>
              <th style={th}>Model</th>
              <th style={th}>Retry</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const isOpen = expanded === r.turn_id;
              const sigs = r.system_signals_emitted || [];
              return (
                <React.Fragment key={r.turn_id}>
                  <tr
                    data-testid={`admin-audit-row-${r.turn_id}`}
                    onClick={() => setExpanded(isOpen ? null : r.turn_id)}
                    style={{
                      cursor: "pointer",
                      borderBottom: "1px solid rgba(255,255,255,0.04)",
                      background: isOpen ? "var(--bg-elev)" : "transparent",
                      color: "var(--text-dim)",
                    }}
                  >
                    <td style={td}>{(r.timestamp || "").slice(0, 19).replace("T", " ")}</td>
                    <td style={td} title={r.user_id}>{(r.user_id || "").slice(0, 10) + "…"}</td>
                    <td style={td} title={r.project_id || ""}>{(r.project_id || "—").slice(0, 14)}</td>
                    <td style={td}>{(r.tools_called || []).length}</td>
                    <td style={td}>
                      {r.citation_guard_triggered
                        ? <span style={{ color: "#f59e0b" }}>YES</span>
                        : <span style={{ color: "#22c55e" }}>—</span>}
                    </td>
                    <td style={td}>
                      {sigs.length === 0
                        ? <span style={{ color: "var(--text-faint)" }}>—</span>
                        : <span style={{ color: "#ef4444" }}>{sigs.join(", ")}</span>}
                    </td>
                    <td style={td}>{r.llm_model || "—"}</td>
                    <td style={td}>{r.was_retry ? "↻" : "—"}</td>
                  </tr>
                  {isOpen && (
                    <tr data-testid={`admin-audit-detail-${r.turn_id}`}>
                      <td colSpan={8} style={{
                        padding: "10px 14px",
                        background: "var(--bg-elev)",
                        borderBottom: "1px solid var(--border)",
                        color: "var(--text-dim)",
                        whiteSpace: "pre-wrap", wordBreak: "break-word",
                      }}>
                        <div><strong style={{ color: "var(--text)" }}>turn_id:</strong> {r.turn_id}</div>
                        <div><strong style={{ color: "var(--text)" }}>tools_called:</strong> {(r.tools_called || []).join(" · ") || "—"}</div>
                        <div><strong style={{ color: "var(--text)" }}>citation_guard_paths_fetched:</strong> {(r.citation_guard_paths_fetched || []).join(", ") || "—"}</div>
                        <div><strong style={{ color: "var(--text)" }}>citation_guard_unverified:</strong> {(r.citation_guard_unverified || []).join(", ") || "—"}</div>
                        <div><strong style={{ color: "var(--text)" }}>response_tokens:</strong> {r.response_tokens || 0}</div>
                        {r.extra ? <div><strong style={{ color: "var(--text)" }}>extra:</strong> {JSON.stringify(r.extra)}</div> : null}
                      </td>
                    </tr>
                  )}
                </React.Fragment>
              );
            })}
          </tbody>
        </table>
        </div>
      )}
    </div>
  );
}
const th = { padding: "8px 10px", fontWeight: 500 };
const td = { padding: "7px 10px" };



//   • Warm Start + Post-scan Issues: merged into one "Reliability" tab.
const NAV = [
  { id: "overview", label: "Overview", Icon: Eye },
  { id: "dash", label: "Dashboard", Icon: LayoutDashboard },
  { id: "users", label: "Users", Icon: Users },
  { id: "projects", label: "Projects", Icon: Folder },
  { id: "tasks", label: "Tasks", Icon: ListChecks },
  { id: "tokens", label: "Token P&L", Icon: Cpu },
  { id: "agent_perf", label: "Agent Performance", Icon: Activity },
  { id: "mcp", label: "MCP Usage", Icon: Plug },
  { id: "reliability", label: "Reliability", Icon: ShieldAlert },
  { id: "payments", label: "Payments & Revenue", Icon: DollarSign },
  { id: "support", label: "Support Emails", Icon: Mail },
  { id: "audit", label: "Audit", Icon: ShieldAlert },
  { id: "house_rules", label: "House Rules", Icon: ShieldCheck },
  { id: "settings", label: "Settings", Icon: SettingsIcon },
];

export default function Admin({ initialTab = "overview" }) {
  const navigate = useNavigate();
  const [page, setPage] = useState(initialTab);
  const [selectedUser, setSelectedUser] = useState(null);
  const [me, setMe] = useState(null);

  useEffect(() => {
    // Guard order matters: NO token at all → bounce to /login with a
    // `next=/admin` so we come right back after sign-in. With a token
    // but no admin → /dashboard (the regular app surface).
    // Previously we always went to /dashboard which, for unauthed users,
    // re-bounced to / — making /admin look like it "doesn't exist".
    const tok = localStorage.getItem("aurem_token");
    if (!tok) {
      navigate("/login?next=/admin", { replace: true });
      return;
    }
    api.get("/admin/me")
      .then((r) => setMe(r.data))
      .catch((err) => {
        const status = err?.response?.status;
        if (status === 401) {
          // Token expired / invalid — clear it and bounce to login
          localStorage.removeItem("aurem_token");
          localStorage.removeItem("aurem_user");
          navigate("/login?next=/admin", { replace: true });
        } else {
          // 403 or anything else — user is signed in but not admin
          toast({ message: "Admin access required", kind: "error" });
          navigate("/dashboard", { replace: true });
        }
      });
  }, [navigate]);

  // Toggle the network-mesh glass bg on body while the admin page is
  // mounted (Admin.jsx renders its own layout — it doesn't wrap in
  // Shell — so we need to opt-in to the same body class Shell uses).
  useEffect(() => {
    document.body.classList.add("aurem-glass");
    return () => { document.body.classList.remove("aurem-glass"); };
  }, []);

  if (!me) return (
    <div style={{ minHeight: "100vh", display: "grid", placeItems: "center",
                   background: "transparent", color: "var(--text-faint)" }}>
      <Loader2 size={18} className="spin" />
    </div>
  );

  function logout() {
    localStorage.removeItem("aurem_token");
    localStorage.removeItem("aurem_user");
    navigate("/login");
  }

  function go(id) { setPage(id); setSelectedUser(null); }

  const renderPage = () => {
    if (page === "users" && selectedUser) {
      return <UserDetail user={selectedUser} onBack={() => setSelectedUser(null)} />;
    }
    switch (page) {
      case "overview": return <AdminOverview />;
      case "dash": return <Dashboard />;
      case "users": return <UsersList onSelect={setSelectedUser} />;
      case "projects": return <ProjectsPage />;
      case "tasks": return <TasksPage />;
      case "tokens": return <TokenPnL />;
      case "agent_perf": return <AgentPerformancePage />;
      case "mcp": return <McpUsagePage />;
      case "reliability": return <ReliabilityPage />;
      case "payments": return <PaymentsPage />;
      case "support": return <SupportPage />;
      case "audit": return <AuditPage />;
      case "house_rules": return <AdminHouseRules />;
      case "settings": return <SettingsPage />;
      default: return <Dashboard />;
    }
  };

  return (
    <div className="aurem-admin-shell" style={{
      height: "100vh", maxHeight: "100vh", overflow: "hidden",
      display: "grid",
      gridTemplateColumns: "220px 1fr",
      background: "transparent",
    }}>
      <aside
        className="glass-sidebar"
        style={{
          padding: "20px 12px",
          display: "flex", flexDirection: "column",
          height: "100vh", overflow: "hidden", minHeight: 0,
        }}
      >
        <div style={{ padding: "0 8px 16px",
                       borderBottom: "1px solid var(--border)", marginBottom: 12,
                       flexShrink: 0 }}>
          <div style={{ fontWeight: 600, fontSize: 14, color: "var(--text)" }}>
            AUREM CTO
          </div>
          <div style={{ fontSize: 10, color: "var(--text-faint)",
                         letterSpacing: "0.1em", textTransform: "uppercase" }}>
            Admin Panel
          </div>
        </div>
        <div className="aurem-rail-scroll" data-testid="admin-nav-scroll">
        {NAV.map(({ id, label, Icon }) => {
          const active = page === id;
          return (
            <button
              key={id}
              data-testid={`admin-nav-${id}`}
              onClick={() => go(id)}
              className="btn-ghost"
              style={{
                display: "flex", alignItems: "center", gap: 10,
                padding: "8px 10px", marginBottom: 2,
                background: active ? "var(--bg-elev)" : "transparent",
                color: active ? "var(--text)" : "var(--text-dim)",
                border: "none", fontSize: 12, textAlign: "left",
                width: "100%", cursor: "pointer", borderRadius: 4,
                whiteSpace: "nowrap",
              }}>
              <Icon size={14} /> {label}
            </button>
          );
        })}
        </div>
        <div style={{ marginTop: "auto", paddingTop: 12, flexShrink: 0,
                       borderTop: "1px solid var(--border)" }}>
          <div style={{ fontSize: 11, color: "var(--text-faint)",
                         padding: "0 8px", marginBottom: 8,
                         overflowWrap: "anywhere" }}>
            {me.email}
          </div>
          <Link to="/dashboard"
                style={{ display: "flex", alignItems: "center", gap: 8,
                          padding: "6px 8px", fontSize: 11,
                          color: "var(--text-faint)", textDecoration: "none" }}>
            <ExternalLink size={11} /> back to app
          </Link>
          <button onClick={logout} className="btn-ghost"
                  data-testid="admin-logout"
                  style={{ display: "flex", alignItems: "center", gap: 8,
                            padding: "6px 8px", fontSize: 11,
                            border: "none", color: "var(--text-faint)",
                            background: "transparent", width: "100%",
                            cursor: "pointer", textAlign: "left" }}>
            <LogOut size={11} /> sign out
          </button>
        </div>
      </aside>
      <main style={{ overflow: "auto", height: "100vh", maxHeight: "100vh", minWidth: 0 }}>
        {renderPage()}
      </main>
    </div>
  );
}
