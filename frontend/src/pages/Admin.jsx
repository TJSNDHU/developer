/**
 * pages/Admin.jsx — AuremCTO Admin Panel
 * Guarded route: only users with is_admin in localStorage 'aurem_user'.
 * All data lives under /api/aurem-dev/admin/*.
 */
import { useState, useEffect, useCallback } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  LayoutDashboard, Users, MessageCircle, Folder, ListChecks,
  Cpu, CreditCard, Network as SitemapIcon, Settings as SettingsIcon,
  LogOut, ExternalLink, ArrowLeft, Loader2, Brain, Eye, Terminal,
} from "lucide-react";
import { api } from "../lib/api";
import { toast } from "../components/Toast";
import AuremAdminPanel from "../components/AuremAdminPanel";
import AdminOverview from "./AdminOverview";
import AgentTokenPanel from "../components/AgentTokenPanel";

// ── Helpers ────────────────────────────────────────────────────────────
const fmt = (n) => (n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n ?? 0));
const fmtMoney = (n) => `$${(n || 0).toFixed(2)}`;
const ago = (sec) => {
  if (!sec) return "—";
  const s = Math.floor(Date.now() / 1000 - sec);
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
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);

  const load = useCallback(async (s) => {
    setLoading(true);
    try {
      const r = await api.get("/admin/users", { params: { search: s } });
      setUsers(r.data.users || []);
    } finally { setLoading(false); }
  }, []);

  useEffect(() => {
    const t = setTimeout(() => load(search), 250);
    return () => clearTimeout(t);
  }, [search, load]);

  return (
    <div style={{ padding: 24 }}>
      {/* Iter 65 — Agent token P&L widget, top of Users tab.
          Answers: "kya Claude/Maxx ka extra cost worth hai?" */}
      <AgentTokenPanel />
      <div style={{ display: "flex", justifyContent: "space-between",
                     alignItems: "center", marginBottom: 14, gap: 12, flexWrap: "wrap" }}>
        <h3 style={{ fontSize: 12, letterSpacing: "0.1em", textTransform: "uppercase",
                      color: "var(--text-faint)", margin: 0 }}>
          Users ({users.length})
        </h3>
        <input
          data-testid="admin-users-search"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search email / name…"
          className="input"
          style={{ width: 260, maxWidth: "100%" }}
        />
      </div>
      <Card>
        {loading ? <div style={{ padding: 24, color: "var(--text-faint)" }}><Loader2 size={14} className="spin" /> Loading…</div> : (
          <Table
            cols={["Email", "Name", "Tier", "Projects", "Tasks", "Status", ""]}
            rows={users.map((u) => [
              u.email,
              u.name || "—",
              <Badge key="tier">{u.tier || "free"}</Badge>,
              u.project_count ?? 0,
              u.task_count ?? 0,
              <Badge key="status" color={STATUS_COLOR[u.status || "active"]}>{u.status || "active"}</Badge>,
              <button
                key="act"
                data-testid={`admin-user-view-${u.user_id}`}
                className="btn-ghost" style={{ padding: "4px 10px", fontSize: 11 }}
                onClick={() => onSelect(u)}>
                view →
              </button>,
            ])}
          />
        )}
      </Card>
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
  useEffect(() => {
    api.get("/admin/projects").then((r) => setData(r.data.projects || [])).catch(() => {});
  }, []);
  return (
    <div style={{ padding: 24 }}>
      <h3 style={{ fontSize: 12, letterSpacing: "0.1em", textTransform: "uppercase",
                    color: "var(--text-faint)", margin: "0 0 8px" }}>
        All projects ({data.length})
      </h3>
      <Card>
        <Table
          cols={["Name", "Repo", "Branch", "Stack", "Tasks", "User", "Created"]}
          rows={data.map((p) => [
            p.name,
            <span key="repo" style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 11 }}>
              {p.github_owner}/{p.github_repo}
            </span>,
            p.branch,
            <Badge key="stack">{p.tech_stack || "auto"}</Badge>,
            p.tasks_done ?? 0,
            <span key="user" style={{ color: "var(--text-faint)", fontSize: 11 }}>
              {(p.user_id || "").slice(0, 10)}
            </span>,
            <span key="time" style={{ color: "var(--text-faint)" }}>{ago(p.created_at)}</span>,
          ])}
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

function PaymentsPage() {
  const [d, setD] = useState(null);
  useEffect(() => {
    api.get("/admin/payments").then((r) => setD(r.data)).catch(() => {});
  }, []);
  if (!d) return <div style={{ padding: 24, color: "var(--text-faint)" }}>Loading…</div>;
  return (
    <div style={{ padding: 24 }}>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 12, marginBottom: 18 }}>
        <MCard label="Total revenue" value={fmtMoney(d.total_revenue)} accent="var(--ok)" />
        <MCard label="Transactions" value={d.count} />
        <MCard label="Pending" value={(d.payments || []).filter(p => p.payment_status !== 'paid').length} />
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

function CodeSurfaceLive() {
  const [data, setData] = useState(null);
  useEffect(() => {
    api.get("/admin/code-surface")
      .then((r) => setData(r.data))
      .catch(() => setData(null));
  }, []);
  // Fall back to the static map if the endpoint isn't reachable (e.g.
  // running against a build that pre-dates the endpoint).
  const surface = data?.surface || Object.fromEntries(
    CODE_SURFACE.map((c) => [c.title.toLowerCase(), c.items.map((i) => ({
      file: i.name, desc: i.note, lines: 0,
    }))]),
  );
  const columns = [
    { key: "routers",    title: "Routers" },
    { key: "services",   title: "Services" },
    { key: "pages",      title: "Pages" },
    { key: "components", title: "Components" },
  ];
  return (
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
                          display: "grid", gap: 4 }}>
              {items.map((it) => (
                <li key={it.file || it.name} style={{
                  fontSize: 11.5, color: "var(--text-dim)",
                  fontFamily: "'JetBrains Mono', monospace",
                  display: "flex", justifyContent: "space-between",
                  gap: 8,
                }}>
                  <span style={{ overflowWrap: "anywhere" }}>{it.file || it.name}</span>
                  {(it.lines > 0 || it.desc) && (
                    <span style={{
                      color: "var(--text-faint)", fontSize: 10,
                      whiteSpace: "nowrap", flexShrink: 0,
                    }}>{it.lines > 0 ? `${it.lines}L` : (it.desc || it.note || "")}</span>
                  )}
                </li>
              ))}
            </ul>
          </Card>
        );
      })}
    </div>
  );
}

// Static map of the codebase surface — pairs with AdminOverview's
// feature checklist. Hand-maintained when shape changes meaningfully.
// Last refresh: iter 119 (Feb 2026).
const CODE_SURFACE = [
  {
    title: "Routers",
    items: [
      { name: "auth.py",            note: "JWT + signup + founder allowlist" },
      { name: "chat.py",            note: "SSE stream + citation chips (iter 119)" },
      { name: "cto_projects.py",    note: "tasks + brain + Vanguard hook" },
      { name: "admin.py",           note: "ops + telemetry + db-health (iter 117)" },
      { name: "github_oauth.py",    note: "PAT + OAuth + cancel-redirect (iter 106)" },
      { name: "payments.py",        note: "Stripe 4-tier + Maxx overage" },
      { name: "shipwall.py",        note: "ship feed (cached, iter 118)" },
      { name: "wrapped.py",         note: "user stats" },
      { name: "deploy.py",          note: "Vercel/Netlify" },
      { name: "hosted_deploy.py",   note: "Emergent native deploy" },
      { name: "upload.py",          note: "chunked uploads + Cloudinary" },
      { name: "usage.py",           note: "/usage/me + plan limits" },
      { name: "support.py",         note: "ticket inbox" },
      { name: "automations.py",     note: "scheduled jobs" },
      { name: "harden.py",          note: "security advice tool" },
      { name: "trust.py",           note: "signals + nav surface" },
      { name: "lint_preview.py",    note: "esbuild + AST gate" },
    ],
  },
  {
    title: "Services",
    items: [
      { name: "orchestrator.py",       note: "persona + tools + web sources" },
      { name: "local_tools.py",        note: "9 LLM tools + Vanguard skills" },
      { name: "web_skills.py",         note: "Tavily + Firecrawl + fetch_url" },
      { name: "project_brain.py",      note: "per-repo memory" },
      { name: "vanguard_scanner.py",   note: "AST + 25 patterns" },
      { name: "vanguard_verify_agent.py", note: "Claude 4.5 gate (iter 110)" },
      { name: "vanguard_audit.py",     note: "audit log writer (iter 113)" },
      { name: "task_diff.py",          note: "pre-commit diff capture" },
      { name: "mode_b_council.py",     note: "decision council (iter 108)" },
      { name: "ora_client.py",         note: "ORA LLM wrapper + circuit breaker" },
      { name: "parallel_agents.py",    note: "Back/Front/Tests" },
      { name: "mode_classifier.py",    note: "A→F router" },
      { name: "sandbox_runner.py",     note: "e2b validate" },
      { name: "subscription_tiers.py", note: "tier SSOT" },
      { name: "route_cache.py",        note: "in-mem TTL cache (iter 118)" },
      { name: "daily_digest.py",       note: "06:00 UTC admin 1-pager" },
      { name: "ora_council_logger.py", note: "training-data writer" },
      { name: "codebase_indexer.py",   note: "TF-IDF embedder" },
    ],
  },
  {
    title: "Pages",
    items: [
      { name: "Landing.jsx",        note: "marketing" },
      { name: "Dashboard.jsx",      note: "split pane" },
      { name: "Settings.jsx",       note: "plan + wrapped" },
      { name: "Wrapped.jsx",        note: "/wrapped" },
      { name: "ShipWall.jsx",       note: "/wall (cached)" },
      { name: "BrainDump.jsx",      note: "diff buttons" },
      { name: "OpsRecipes.jsx",     note: "/admin/ops" },
      { name: "AdminOverview.jsx",  note: "feature audit + DB health" },
      { name: "AdminVanguard.jsx",  note: "Vanguard block log (iter 113)" },
      { name: "AdminFinancials.jsx", note: "MRR + Maxx P&L" },
      { name: "AdminIntegrations.jsx", note: "key health grid" },
      { name: "Admin.jsx",          note: "tabs + arch + code surface" },
      { name: "Projects.jsx",       note: "repo CRUD + GitHub PAT" },
      { name: "Login.jsx",          note: "JWT + Google OAuth" },
    ],
  },
  {
    title: "Components",
    items: [
      { name: "ChatPanel.jsx",           note: "SSE chat + sources" },
      { name: "MessageBubble.jsx",       note: "rich render + 🌐 chips (iter 119)" },
      { name: "TaskLiveTape.jsx",        note: "terminal feed" },
      { name: "TaskProgressCard.jsx",    note: "ship + rollback" },
      { name: "TaskManagementPanel.jsx", note: "checklist" },
      { name: "PreviewPane.jsx",         note: "iframe blob" },
      { name: "NewUserWizard.jsx",       note: "onboarding" },
      { name: "PricingCards.jsx",        note: "4-tier grid" },
      { name: "OraWrapped.jsx",          note: "share card" },
      { name: "Toast.jsx",               note: "milestones" },
      { name: "LiveTaskPopup.jsx",       note: "live task tape (iter 114)" },
      { name: "DbHealthCard",            note: "inlined in AdminOverview (iter 117-118)" },
    ],
  },
];

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
    <div style={{ padding: 24, maxWidth: 560 }}>
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
    </div>
  );
}

// ── Shell ──────────────────────────────────────────────────────────────
const NAV = [
  { id: "overview", label: "Overview", Icon: Eye },
  { id: "dash", label: "Dashboard", Icon: LayoutDashboard },
  { id: "users", label: "Users", Icon: Users },
  { id: "projects", label: "Projects", Icon: Folder },
  { id: "tasks", label: "Tasks", Icon: ListChecks },
  { id: "tokens", label: "Token P&L", Icon: Cpu },
  { id: "payments", label: "Payments", Icon: CreditCard },
  { id: "support", label: "Support", Icon: MessageCircle },
  { id: "arch", label: "Architecture", Icon: SitemapIcon },
  { id: "ora", label: "ORA Council", Icon: Brain },
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
      case "payments": return <PaymentsPage />;
      case "support": return <SupportPage />;
      case "arch": return <Architecture />;
      case "ora": return <AuremAdminPanel />;
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
        <Link
          to="/admin/ops"
          data-testid="admin-nav-ops"
          className="btn-ghost"
          style={{ display: "flex", alignItems: "center", gap: 10,
                    padding: "8px 10px", marginBottom: 2,
                    background: "transparent", color: "var(--text-dim)",
                    border: "none", fontSize: 12, textAlign: "left",
                    width: "100%", borderRadius: 4, textDecoration: "none",
                    whiteSpace: "nowrap" }}>
          <Terminal size={14} /> Ops recipes
        </Link>
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
