/**
 * Automations.jsx — schedule + GitHub-push triggered tasks.
 *
 * Closes the last red gap vs Cursor Automations. Users connect a GitHub
 * webhook + a task template; pushes auto-create tasks scoped to their
 * project.  Backend: routers/automations.py.
 */
import React, { useEffect, useState } from "react";
import { Plus, Trash2, Zap, Copy, Check, GitBranch, Play } from "lucide-react";
import Shell, { PageHeader } from "../components/Shell";
import { api, API_BASE } from "../lib/api";

const WEBHOOK_PATH = "/automations/webhook/github";

export default function Automations() {
  const [rows, setRows] = useState([]);
  const [form, setForm] = useState({
    name: "", repo_full_name: "", trigger: "push",
    branch_filter: "main",
    task_template:
      "Review the recent commits on {branch}.\n{commit_messages}\n\nApply the review notes.",
  });
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const [err, setErr] = useState("");

  async function load() {
    try { const r = await api.get("/automations/list");
          setRows(r.data?.automations || []); }
    catch { setRows([]); }
  }
  useEffect(() => { load(); }, []);

  async function create(e) {
    e.preventDefault();
    setErr(""); setBusy(true);
    try {
      await api.post("/automations/create", form);
      setForm((f) => ({ ...f, name: "", repo_full_name: "" }));
      await load();
    } catch (e2) {
      setErr(e2?.response?.data?.detail || e2?.message || "Create failed.");
    } finally { setBusy(false); }
  }

  async function toggle(id) {
    try { await api.post(`/automations/${id}/toggle`); await load(); }
    catch { /* noop */ }
  }
  async function runNow(id) {
    try {
      const r = await api.post(`/automations/${id}/run`);
      if (r?.data?.task_id) {
        alert(`Task queued: ${r.data.task_id}`);
      }
      await load();
    } catch (e) {
      alert(e?.response?.data?.detail || "Run failed");
    }
  }
  async function del(id) {
    if (!confirm("Delete this automation?")) return;
    try { await api.delete(`/automations/${id}`); await load(); }
    catch { /* noop */ }
  }

  const webhookUrl = `${API_BASE}${WEBHOOK_PATH}`;
  async function copyHook() {
    try { await navigator.clipboard.writeText(webhookUrl);
          setCopied(true); setTimeout(() => setCopied(false), 1500); }
    catch { /* noop */ }
  }

  return (
    <Shell requireAuth>
      <PageHeader
        eyebrow="automations"
        title="Auto-trigger tasks from GitHub pushes"
        sub="Set up a webhook on any repo. Every push runs your task template against the matching project."
      />

      <div data-testid="automations-page"
           style={{ display: "grid", gap: 18, maxWidth: 920 }}>
        <section className="card">
          <div style={{ fontSize: 11, color: "var(--text-faint)",
                         textTransform: "uppercase", letterSpacing: ".07em",
                         marginBottom: 6 }}>
            Webhook URL — paste into GitHub → Settings → Webhooks
          </div>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <code data-testid="webhook-url" style={{
              flex: 1, fontSize: 12,
              fontFamily: "'JetBrains Mono', monospace",
              background: "var(--bg-elev)", padding: "8px 10px",
              borderRadius: 4, color: "var(--text)",
              overflow: "auto", whiteSpace: "nowrap",
            }}>{webhookUrl}</code>
            <button onClick={copyHook} data-testid="copy-webhook"
                    className="btn-ghost" style={{ padding: "6px 12px" }}>
              {copied ? <Check size={12} /> : <Copy size={12} />}
              {copied ? "Copied" : "Copy"}
            </button>
          </div>
          <p style={{ fontSize: 11, color: "var(--text-faint)",
                       marginTop: 10, lineHeight: 1.6 }}>
            Content type <code>application/json</code> · trigger on the
            push event · set <code>GITHUB_WEBHOOK_SECRET</code> in prod
            for signature verification.
          </p>
        </section>

        <section className="card">
          <h3 style={{ fontSize: 14, margin: 0, marginBottom: 14,
                        display: "flex", gap: 8, alignItems: "center" }}>
            <Plus size={14} /> New automation
          </h3>
          <form onSubmit={create} style={{ display: "grid", gap: 10 }}>
            <input data-testid="auto-name" required placeholder="Name (e.g. Review my main branch)"
                   value={form.name}
                   onChange={(e) => setForm({ ...form, name: e.target.value })}
                   style={input} />
            <input data-testid="auto-repo" required placeholder="owner/repo (e.g. octocat/Hello-World)"
                   value={form.repo_full_name}
                   onChange={(e) => setForm({ ...form, repo_full_name: e.target.value })}
                   style={input} />
            <div style={{ display: "flex", gap: 10 }}>
              <select data-testid="auto-trigger" value={form.trigger}
                       onChange={(e) => setForm({ ...form, trigger: e.target.value })}
                       style={{ ...input, flex: 1 }}>
                <option value="push">on push</option>
                <option value="manual">manual only</option>
                <option value="cron">cron (scheduled)</option>
              </select>
              <input data-testid="auto-branch" placeholder="branch (or blank = any)"
                     value={form.branch_filter}
                     onChange={(e) => setForm({ ...form, branch_filter: e.target.value })}
                     style={{ ...input, flex: 1 }} />
            </div>
            <textarea data-testid="auto-template" required rows={4}
                       placeholder="Task template — supports {branch} {pusher} {commit_messages} {commit_count} {repo}"
                       value={form.task_template}
                       onChange={(e) => setForm({ ...form, task_template: e.target.value })}
                       style={{ ...input, fontFamily: "'JetBrains Mono', monospace",
                                 resize: "vertical" }} />
            {err && <div data-testid="auto-err" style={errStyle}>{err}</div>}
            <button data-testid="auto-save" type="submit" disabled={busy}
                    className="btn-primary" style={{ alignSelf: "flex-start" }}>
              {busy ? "Saving…" : "Create automation"}
            </button>
          </form>
        </section>

        <section className="card">
          <h3 style={{ fontSize: 14, margin: 0, marginBottom: 12,
                        display: "flex", gap: 8, alignItems: "center" }}>
            <Zap size={14} /> Your automations · {rows.length}
          </h3>
          {rows.length === 0 ? (
            <p style={{ fontSize: 12, color: "var(--text-faint)" }}>
              None yet — create one above.
            </p>
          ) : (
            <ul style={{ listStyle: "none", padding: 0, margin: 0,
                          display: "grid", gap: 8 }}>
              {rows.map((r) => (
                <li key={r._id} data-testid={`auto-row-${r._id}`} style={rowStyle}>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: 13, color: "var(--text)" }}>{r.name}</div>
                    <div style={{ fontSize: 11, color: "var(--text-faint)",
                                   fontFamily: "'JetBrains Mono', monospace",
                                   display: "flex", gap: 10, marginTop: 3 }}>
                      <span><GitBranch size={10} style={{ verticalAlign: "middle" }} /> {r.repo_full_name}{r.branch_filter ? `@${r.branch_filter}` : ""}</span>
                      <span>·</span>
                      <span>{r.trigger}</span>
                      {r.trigger_count > 0 && <><span>·</span><span>{r.trigger_count} runs</span></>}
                    </div>
                  </div>
                  <button onClick={() => runNow(r._id)} className="btn-ghost"
                          data-testid={`run-${r._id}`}
                          style={{ padding: "4px 10px", fontSize: 11,
                                    display: "inline-flex", gap: 4,
                                    alignItems: "center" }}>
                    <Play size={10} /> Run now
                  </button>
                  <button onClick={() => toggle(r._id)} className="btn-ghost"
                          data-testid={`toggle-${r._id}`}
                          style={{ padding: "4px 10px", fontSize: 11 }}>
                    {r.enabled ? "Enabled" : "Disabled"}
                  </button>
                  <button onClick={() => del(r._id)} className="btn-ghost"
                          data-testid={`del-${r._id}`}
                          style={{ padding: 4, color: "var(--danger)" }}>
                    <Trash2 size={12} />
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </Shell>
  );
}

const input = {
  padding: "9px 12px", fontSize: 13, borderRadius: 5,
  background: "var(--bg-elev)", color: "var(--text)",
  border: "1px solid var(--border, rgba(255,200,120,0.16))",
  outline: "none", fontFamily: "var(--font-sans, system-ui)",
};
const errStyle = {
  fontSize: 11, color: "var(--danger, #ff6b6b)",
  background: "rgba(255,107,107,0.06)",
  border: "1px solid rgba(255,107,107,0.2)",
  padding: "8px 10px", borderRadius: 4,
};
const rowStyle = {
  display: "flex", alignItems: "center", gap: 10,
  padding: "10px 12px",
  background: "var(--panel-2, #161a25)",
  border: "1px solid var(--border, rgba(255,200,120,0.10))",
  borderRadius: 5,
};
